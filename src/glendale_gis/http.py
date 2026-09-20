"""The hosted (streamable HTTP) app: health check, shared-secret auth and rate limiting.

The MCP endpoint is ``/mcp``; ``/health`` is open so Cloud Run and load balancers can probe it.
Requests carry the shared hackathon secret as ``Authorization: Bearer <key>``. Keys are never
logged. Host and Origin checks (DNS-rebinding protection) and the request size limit come from
the MCP SDK, configured here.
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from collections import deque
from collections.abc import Sequence

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from glendale_gis import __version__
from glendale_gis.core.config import ConfigError, Settings
from glendale_gis.server import AppState

log = logging.getLogger(__name__)

MCP_PATH = "/mcp"
HEALTH_PATH = "/health"
OPEN_PATHS = frozenset({HEALTH_PATH})


def check_hosted_settings(settings: Settings) -> None:
    """Refuse to serve the world without a shared secret."""
    if settings.is_public_bind and not settings.api_keys:
        raise ConfigError(
            f"Refusing to listen on {settings.http_host} without an API key. Set "
            "GLENDALE_GIS_API_KEYS (comma-separated) to the shared hackathon secret, or bind "
            "to 127.0.0.1 for local use."
        )


def build_app(settings: Settings, mcp: MCPServer, state: AppState) -> Starlette:
    """Wrap the MCP streamable HTTP app with /health, auth and rate limiting."""
    check_hosted_settings(settings)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(settings.http_allowed_hosts),
        allowed_hosts=list(settings.http_allowed_hosts),
        allowed_origins=list(settings.http_allowed_origins),
    )
    mcp_app = mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        transport_security=security,
        max_request_body_size=settings.max_request_bytes,
        host=settings.http_host,
    )

    async def health(_: Request) -> JSONResponse:
        return JSONResponse(health_payload(state))

    middleware = [
        Middleware(AuthMiddleware, keys=settings.api_keys),
        Middleware(RateLimitMiddleware, per_minute=settings.rate_limit_per_minute),
    ]
    return Starlette(
        routes=[Route(HEALTH_PATH, health, methods=["GET"]), Mount("/", app=mcp_app)],
        middleware=middleware,
        # Mounted apps don't get their own lifespan, and the session manager needs one.
        lifespan=lambda _: mcp_app.router.lifespan_context(mcp_app),
    )


def health_payload(state: AppState) -> dict:
    snapshot = state.snapshot
    payload: dict = {
        "status": "ok" if snapshot is not None else "degraded",
        "version": __version__,
        "snapshot": None,
    }
    if snapshot is not None:
        payload["snapshot"] = {
            "built_at": snapshot.built_at,
            "source": state.snapshot_source,
            "stale": snapshot.stale,
            "layers": len(snapshot.manifest.get("layers") or {}),
            "layers_unavailable": sorted(snapshot.errors),
        }
        if snapshot.errors or snapshot.stale:
            payload["status"] = "degraded"
    if state.snapshot_error:
        payload["detail"] = state.snapshot_error
    return payload


# --------------------------------------------------------------------------------------------
# Middleware
# --------------------------------------------------------------------------------------------


def _path(scope: Scope) -> str:
    return scope.get("path", "")


def _client_key(scope: Scope) -> str:
    """Who to rate limit: the API key if there is one, else the client address."""
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
    token = headers.get("authorization", "")
    if token:
        return f"key:{hash(token)}"
    # Cloud Run and other proxies put the caller first in X-Forwarded-For.
    forwarded = headers.get("x-forwarded-for", "")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    client = scope.get("client")
    return f"ip:{client[0] if client else 'unknown'}"


async def _reject(send: Send, status: int, message: str, headers: dict | None = None) -> None:
    body = json.dumps({"error": message}).encode()
    raw = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    raw += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": body})


class AuthMiddleware:
    """Require the shared secret as a bearer token. No keys configured means no check."""

    def __init__(self, app: ASGIApp, keys: Sequence[str] = ()) -> None:
        self.app = app
        self.keys = tuple(keys)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.keys or _path(scope) in OPEN_PATHS:
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
        header = headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            await _reject(
                send,
                401,
                "Send the shared key as 'Authorization: Bearer <key>'.",
                {"WWW-Authenticate": "Bearer"},
            )
            return
        if not any(hmac.compare_digest(token, key) for key in self.keys):
            log.warning("Rejected a request with an invalid key")  # never log the key itself
            await _reject(send, 401, "That key isn't valid for this server.")
            return
        await self.app(scope, receive, send)


class RateLimitMiddleware:
    """Allow ``per_minute`` requests per key (or client address) in a sliding minute."""

    def __init__(self, app: ASGIApp, per_minute: int = 0, clock=time.monotonic) -> None:
        self.app = app
        self.per_minute = per_minute
        self.clock = clock
        self._seen: dict[str, deque[float]] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.per_minute or _path(scope) in OPEN_PATHS:
            await self.app(scope, receive, send)
            return
        now = self.clock()
        hits = self._seen.setdefault(_client_key(scope), deque())
        while hits and now - hits[0] >= 60:
            hits.popleft()
        if len(hits) >= self.per_minute:
            retry_after = max(1, int(60 - (now - hits[0])))
            await _reject(
                send,
                429,
                f"Too many requests: the limit is {self.per_minute} per minute. "
                f"Try again in {retry_after} seconds.",
                {"Retry-After": str(retry_after)},
            )
            return
        hits.append(now)
        self._prune(now)
        await self.app(scope, receive, send)

    def _prune(self, now: float) -> None:
        """Forget callers that have gone quiet, so the table can't grow without bound."""
        if len(self._seen) > 10_000:
            self._seen = {k: v for k, v in self._seen.items() if v and now - v[-1] < 60}
