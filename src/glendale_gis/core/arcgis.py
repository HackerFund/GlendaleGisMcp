"""Polite, allowlisted HTTP client for ArcGIS REST services.

Every request is checked against the catalog before it is sent, throttled per host, retried with
backoff on transient failures, and returned as parsed JSON. ArcGIS often reports errors with
HTTP 200 and an ``{"error": ...}`` body, and silently truncates results at ``maxRecordCount``;
both are handled here so callers never see partial data.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from glendale_gis.core.catalog import (
    ARCGIS_ITEM_URL,
    DATASETS,
    GEOCODER,
    LAYER_OPERATIONS,
    Dataset,
    Service,
)
from glendale_gis.core.config import Settings

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_GET_URL_LENGTH = 1800  # longer requests (e.g. big geometries) are sent as form POSTs
MAX_RETRY_AFTER_S = 60.0
MAX_BACKOFF_S = 30.0

JsonDict = dict[str, Any]


class ArcGISError(Exception):
    """A request failed: an ArcGIS error body, a bad HTTP status, or an unreadable response."""

    def __init__(
        self,
        message: str,
        *,
        url: str,
        code: int | None = None,
        details: Iterable[str] = (),
    ) -> None:
        self.url = url
        self.code = code
        self.details = tuple(details)
        suffix = f" (code {code})" if code is not None else ""
        super().__init__(f"{message}{suffix}")


class NotAllowedError(ArcGISError):
    """The URL is not in the catalog allowlist; no request was sent."""


# --------------------------------------------------------------------------------------------
# Allowlist
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Rule:
    base: str  # scheme://host/path with no trailing slash
    operations: frozenset[str]
    prefix: bool = False  # base is a prefix: any service beneath it, plus its operations


def _normalize(url: str) -> str:
    return url.rstrip("/")


def build_allowlist(
    datasets: Iterable[Dataset] = DATASETS.values(),
    services: Iterable[Service] = (GEOCODER,),
) -> tuple[_Rule, ...]:
    rules: list[_Rule] = []
    for d in datasets:
        rules.append(_Rule(_normalize(d.layer_url), LAYER_OPERATIONS))
        if d.allowed_prefix:
            rules.append(_Rule(_normalize(d.allowed_prefix), LAYER_OPERATIONS, prefix=True))
        if d.item_id:
            rules.append(_Rule(ARCGIS_ITEM_URL.format(item_id=d.item_id), frozenset()))
    for s in services:
        rules.append(_Rule(_normalize(s.url), s.operations))
    return tuple(rules)


_DEFAULT_ALLOWLIST = build_allowlist()


def check_allowed(url: str, allowlist: Iterable[_Rule] = _DEFAULT_ALLOWLIST) -> str:
    """Return the normalized URL if it is allowed, otherwise raise ``NotAllowedError``."""
    parts = urlsplit(url)
    reject = NotAllowedError(
        "URL is not an allowed data source. Only catalog datasets can be queried; "
        "use list_datasets to see them.",
        url=url,
    )
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
        raise reject
    if parts.query or parts.fragment:
        raise reject  # parameters must be passed separately, never baked into the URL
    decoded = unquote(parts.path)
    if ".." in decoded.split("/") or "\\" in decoded or "//" in parts.path or decoded != parts.path:
        raise reject
    normalized = _normalize(f"https://{parts.hostname}{parts.path}")

    for rule in allowlist:
        if normalized == rule.base:
            return normalized
        if not normalized.startswith(rule.base + "/"):
            continue
        rest = normalized[len(rule.base) + 1 :].split("/")
        if not rule.prefix:
            if len(rest) == 1 and rest[0] in rule.operations:
                return normalized
            continue
        # Prefix rule: <Service>/FeatureServer|MapServer[/<layer>[/<operation>]]
        if len(rest) < 2 or len(rest) > 4 or rest[1] not in ("FeatureServer", "MapServer"):
            continue
        if len(rest) == 2:
            return normalized  # service metadata
        if rest[2].isdigit() and (len(rest) == 3 or rest[3] in rule.operations):
            return normalized
    raise reject


# --------------------------------------------------------------------------------------------
# Throttling
# --------------------------------------------------------------------------------------------


class _HostGate:
    """Limits concurrency and spaces out request starts for one host."""

    def __init__(
        self,
        concurrency: int,
        min_interval_s: float,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._lock = asyncio.Lock()
        self._min_interval_s = min_interval_s
        self._clock = clock
        self._sleep = sleep
        self._last_start: float | None = None

    async def __aenter__(self) -> None:
        await self._semaphore.acquire()
        try:
            async with self._lock:
                if self._last_start is not None:
                    wait = self._min_interval_s - (self._clock() - self._last_start)
                    if wait > 0:
                        await self._sleep(wait)
                self._last_start = self._clock()
        except BaseException:
            self._semaphore.release()
            raise

    async def __aexit__(self, *exc: object) -> None:
        self._semaphore.release()


# --------------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------------


class ArcGISClient:
    """Async client for catalog ArcGIS services. Use as ``async with ArcGISClient(settings)``."""

    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.AsyncClient | None = None,
        allowlist: Iterable[_Rule] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._settings = settings
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(
            timeout=settings.request_timeout_s, follow_redirects=False
        )
        self._allowlist = tuple(allowlist) if allowlist is not None else _DEFAULT_ALLOWLIST
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self._gates: dict[str, _HostGate] = {}

    async def __aenter__(self) -> ArcGISClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # -- low level ---------------------------------------------------------------------------

    def _gate(self, host: str) -> _HostGate:
        if host not in self._gates:
            self._gates[host] = _HostGate(
                self._settings.max_concurrency_per_host,
                self._settings.min_request_interval_s,
                self._clock,
                self._sleep,
            )
        return self._gates[host]

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), MAX_RETRY_AFTER_S)
            except ValueError:
                pass  # HTTP-date form; fall back to exponential backoff
        return min(2.0**attempt, MAX_BACKOFF_S) + self._jitter()

    async def request_json(self, url: str, params: Mapping[str, Any] | None = None) -> JsonDict:
        """Send one request (with retries) and return the JSON body.

        Raises ``NotAllowedError`` before sending if the URL isn't allowlisted, and
        ``ArcGISError`` for error bodies, non-retryable HTTP errors, or exhausted retries.
        """
        url = check_allowed(url, self._allowlist)
        encoded = _encode_params(params)
        use_post = len(url) + len(str(httpx.QueryParams(encoded))) > MAX_GET_URL_LENGTH
        headers = {"User-Agent": self._settings.user_agent, "Accept": "application/json"}
        host = urlsplit(url).hostname or ""

        attempt = 0
        while True:
            retry_after: str | None = None
            try:
                async with self._gate(host):
                    if use_post:
                        response = await self._http.post(url, data=encoded, headers=headers)
                    else:
                        response = await self._http.get(url, params=encoded, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                failure: ArcGISError = ArcGISError(f"Request failed: {type(exc).__name__}", url=url)
            else:
                result = _parse_response(response, url)
                if isinstance(result, dict):
                    return result
                failure = result
                retry_after = response.headers.get("Retry-After")
                if not _is_retryable(failure):
                    raise failure

            if attempt >= self._settings.max_retries:
                raise failure
            await self._sleep(self._backoff(attempt, retry_after))
            attempt += 1

    # -- ArcGIS helpers ----------------------------------------------------------------------

    async def layer_metadata(self, layer_url: str) -> JsonDict:
        return await self.request_json(layer_url)

    async def count(
        self,
        layer_url: str,
        *,
        where: str = "1=1",
        geometry: Mapping[str, Any] | str | None = None,
        geometry_type: str | None = None,
    ) -> int:
        params = _query_params(where=where, geometry=geometry, geometry_type=geometry_type)
        params["returnCountOnly"] = "true"
        body = await self.request_json(f"{layer_url.rstrip('/')}/query", params)
        if "count" not in body:
            raise ArcGISError("Count response had no 'count'", url=layer_url)
        return int(body["count"])

    async def query(
        self,
        layer_url: str,
        *,
        where: str = "1=1",
        out_fields: Iterable[str] | str = "*",
        geometry: Mapping[str, Any] | str | None = None,
        geometry_type: str | None = None,
        spatial_rel: str = "esriSpatialRelIntersects",
        return_geometry: bool = True,
        out_sr: int = 4326,
        max_features: int | None = None,
        extra_params: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> list[JsonDict]:
        """Return all matching features, paging past ``maxRecordCount``.

        Uses ``resultOffset`` paging when the layer supports it, otherwise fetches object IDs
        and requests them in chunks. Stops early once ``max_features`` is reached.
        """
        layer_url = layer_url.rstrip("/")
        meta = metadata if metadata is not None else await self.layer_metadata(layer_url)
        page_size = int(meta.get("maxRecordCount") or 1000)
        if max_features is not None:
            page_size = max(1, min(page_size, max_features))
        id_field = meta.get("objectIdField") or _find_oid_field(meta) or "OBJECTID"
        supports_paging = bool(
            (meta.get("advancedQueryCapabilities") or {}).get("supportsPagination")
        )

        base = _query_params(
            where=where,
            geometry=geometry,
            geometry_type=geometry_type,
            spatial_rel=spatial_rel,
            out_fields=out_fields,
            return_geometry=return_geometry,
            out_sr=out_sr,
        )
        if extra_params:
            base.update(extra_params)

        if supports_paging:
            return await self._query_by_offset(layer_url, base, id_field, page_size, max_features)
        return await self._query_by_ids(layer_url, base, page_size, max_features)

    async def _query_by_offset(
        self,
        layer_url: str,
        base: Mapping[str, Any],
        id_field: str,
        page_size: int,
        max_features: int | None,
    ) -> list[JsonDict]:
        features: list[JsonDict] = []
        offset = 0
        while True:
            params = dict(base)
            params.update(
                {
                    "resultOffset": offset,
                    "resultRecordCount": page_size,
                    "orderByFields": id_field,
                }
            )
            body = await self.request_json(f"{layer_url}/query", params)
            page = body.get("features") or []
            features.extend(page)
            if max_features is not None and len(features) >= max_features:
                return features[:max_features]
            if not page or not body.get("exceededTransferLimit"):
                return features
            offset += len(page)

    async def _query_by_ids(
        self,
        layer_url: str,
        base: Mapping[str, Any],
        page_size: int,
        max_features: int | None,
    ) -> list[JsonDict]:
        id_params = {k: v for k, v in base.items() if k not in ("outFields", "returnGeometry")}
        id_params["returnIdsOnly"] = "true"
        ids_body = await self.request_json(f"{layer_url}/query", id_params)
        ids = sorted(ids_body.get("objectIds") or [])
        if max_features is not None:
            ids = ids[:max_features]

        features: list[JsonDict] = []
        for start in range(0, len(ids), page_size):
            chunk = ids[start : start + page_size]
            params = {
                k: v
                for k, v in base.items()
                if k not in ("where", "geometry", "geometryType", "spatialRel", "inSR")
            }
            params["objectIds"] = ",".join(str(i) for i in chunk)
            body = await self.request_json(f"{layer_url}/query", params)
            features.extend(body.get("features") or [])
            if body.get("exceededTransferLimit"):
                raise ArcGISError(
                    "Server truncated an object-ID chunk; lower the page size", url=layer_url
                )
        return features

    async def resolve_item_url(self, item_id: str) -> str:
        """Look up the current service URL for an ArcGIS Online item."""
        body = await self.request_json(ARCGIS_ITEM_URL.format(item_id=item_id))
        url = body.get("url")
        if not url:
            raise ArcGISError(f"ArcGIS Online item {item_id} has no service URL", url=item_id)
        return str(url).rstrip("/")


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _encode_params(params: Mapping[str, Any] | None) -> dict[str, str]:
    encoded: dict[str, str] = {"f": "json"}
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            encoded[key] = "true" if value else "false"
        elif isinstance(value, (dict, list)):
            encoded[key] = json.dumps(value, separators=(",", ":"))
        else:
            encoded[key] = str(value)
    return encoded


def _query_params(
    *,
    where: str,
    geometry: Mapping[str, Any] | str | None,
    geometry_type: str | None,
    spatial_rel: str = "esriSpatialRelIntersects",
    out_fields: Iterable[str] | str | None = None,
    return_geometry: bool | None = None,
    out_sr: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"where": where}
    if geometry is not None:
        if geometry_type is None:
            raise ValueError("geometry_type is required when geometry is given")
        params.update(
            {
                "geometry": geometry,
                "geometryType": geometry_type,
                "spatialRel": spatial_rel,
                "inSR": 4326,
            }
        )
    if out_fields is not None:
        params["outFields"] = out_fields if isinstance(out_fields, str) else ",".join(out_fields)
    if return_geometry is not None:
        params["returnGeometry"] = return_geometry
    if out_sr is not None:
        params["outSR"] = out_sr
    return params


def _find_oid_field(meta: Mapping[str, Any]) -> str | None:
    for f in meta.get("fields") or []:
        if f.get("type") == "esriFieldTypeOID":
            return str(f.get("name"))
    return None


def _parse_response(response: httpx.Response, url: str) -> JsonDict | ArcGISError:
    if response.is_redirect:
        return ArcGISError(f"Unexpected redirect (HTTP {response.status_code})", url=url)
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        err = body["error"]
        code = err.get("code")
        return ArcGISError(
            str(err.get("message") or "ArcGIS error"),
            url=url,
            code=int(code) if isinstance(code, (int, str)) and str(code).isdigit() else None,
            details=[str(d) for d in err.get("details") or []],
        )
    if response.status_code >= 400:
        return ArcGISError(f"HTTP {response.status_code}", url=url, code=response.status_code)
    if not isinstance(body, dict):
        return ArcGISError("Response was not a JSON object", url=url)
    return body


def _is_retryable(error: ArcGISError) -> bool:
    return error.code in RETRYABLE_STATUS
