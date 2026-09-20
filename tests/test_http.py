"""The hosted HTTP app: health, the shared-secret check, rate limiting and header checks."""

import dataclasses
import json

import httpx
import pytest

import fixture_snapshot as fx
from glendale_gis.core.config import ConfigError, Settings
from glendale_gis.core.snapshot import Snapshot
from glendale_gis.http import build_app, check_hosted_settings, health_payload
from glendale_gis.server import build_state, create_server

KEY = "shared-secret"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
MCP_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


def settings_for(tmp_path, **overrides):
    base = dataclasses.replace(
        Settings.from_env({}),
        cache_dir=tmp_path / "cache",
        snapshot_path=fx.write(tmp_path / "snap"),
        api_keys=(KEY,),
    )
    return dataclasses.replace(base, **overrides)


def app_for(settings):
    state = build_state(settings, Snapshot.load(settings.snapshot_path))
    return build_app(settings, create_server(settings, state), state), state


async def client_for(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://mcp.test")


# -- startup guard ----------------------------------------------------------------------------


def test_public_bind_needs_a_key():
    public = dataclasses.replace(Settings.from_env({}), http_host="0.0.0.0")
    with pytest.raises(ConfigError, match="Refusing to listen on 0.0.0.0 without an API key"):
        check_hosted_settings(public)
    check_hosted_settings(dataclasses.replace(public, api_keys=("k",)))  # with a key: fine
    check_hosted_settings(Settings.from_env({}))  # localhost without a key: fine


# -- health -----------------------------------------------------------------------------------


async def test_health_is_open_and_reports_the_snapshot(tmp_path):
    app, _ = app_for(settings_for(tmp_path))
    async with await client_for(app) as client:
        response = await client.get("/health")  # no key
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"  # the fixture snapshot is missing some layers
    assert body["snapshot"]["layers"] == 10
    assert "hospitals" in body["snapshot"]["layers_unavailable"]
    assert body["snapshot"]["stale"] is False
    assert body["version"]


def test_health_payload_without_a_snapshot(tmp_path):
    settings = dataclasses.replace(
        Settings.from_env({}), cache_dir=tmp_path, snapshot_path=tmp_path / "missing"
    )
    state = build_state(settings)
    payload = health_payload(state)
    assert payload["status"] == "degraded"
    assert payload["snapshot"] is None
    assert "manifest.json not found" in payload["detail"]


def test_health_payload_is_ok_for_a_complete_snapshot(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    snapshot = Snapshot.load(settings.snapshot_path)
    monkeypatch.setattr(snapshot, "errors", {})
    state = build_state(settings, snapshot)
    assert health_payload(state)["status"] == "ok"


# -- authentication ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers, message",
    [
        ({}, "Authorization: Bearer"),
        ({"authorization": "Bearer wrong"}, "isn't valid"),
        ({"authorization": KEY}, "Authorization: Bearer"),  # missing the scheme
        ({"authorization": "Basic " + KEY}, "Authorization: Bearer"),
    ],
)
async def test_requests_without_a_valid_key_are_refused(tmp_path, headers, message):
    app, _ = app_for(settings_for(tmp_path))
    async with await client_for(app) as client:
        response = await client.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **headers})
    assert response.status_code == 401
    assert message in response.json()["error"]
    if not headers:
        assert response.headers["www-authenticate"] == "Bearer"


async def test_a_valid_key_reaches_the_server(tmp_path):
    settings = settings_for(tmp_path, api_keys=("old-key", KEY))  # rotation: both work
    app, _ = app_for(settings)
    async with app.router.lifespan_context(app), await client_for(app) as client:
        for key in ("old-key", KEY):
            response = await client.post(
                "/mcp",
                json=INITIALIZE,
                headers={**MCP_HEADERS, "authorization": f"Bearer {key}"},
            )
            assert response.status_code == 200, response.text
            assert "glendale-gis" in response.text


async def test_no_keys_configured_means_no_check(tmp_path):
    app, _ = app_for(settings_for(tmp_path, api_keys=()))
    async with app.router.lifespan_context(app), await client_for(app) as client:
        response = await client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
    assert response.status_code == 200


async def test_keys_are_never_logged(tmp_path, caplog):
    app, _ = app_for(settings_for(tmp_path))
    with caplog.at_level("DEBUG"):
        async with await client_for(app) as client:
            await client.post(
                "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "authorization": "Bearer hunter2"}
            )
    assert "hunter2" not in "\n".join(r.getMessage() for r in caplog.records)


# -- rate limiting ----------------------------------------------------------------------------


async def test_rate_limit_applies_per_key(tmp_path):
    settings = settings_for(tmp_path, rate_limit_per_minute=2, api_keys=(KEY, "other-key"))
    app, _ = app_for(settings)
    async with await client_for(app) as client:

        async def call(key):
            return await client.post(
                "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "authorization": f"Bearer {key}"}
            )

        async with app.router.lifespan_context(app):
            assert (await call(KEY)).status_code == 200
            assert (await call(KEY)).status_code == 200
            limited = await call(KEY)
            assert limited.status_code == 429
            assert "limit is 2 per minute" in limited.json()["error"]
            assert int(limited.headers["retry-after"]) <= 60
            # A different key has its own budget.
            assert (await call("other-key")).status_code == 200
            # /health is never limited.
            assert (await client.get("/health")).status_code == 200


async def test_rate_limit_falls_back_to_the_client_address(tmp_path):
    settings = settings_for(tmp_path, rate_limit_per_minute=1, api_keys=())
    app, _ = app_for(settings)
    async with await client_for(app) as client:
        headers = {**MCP_HEADERS, "x-forwarded-for": "203.0.113.7, 10.0.0.1"}
        async with app.router.lifespan_context(app):
            assert (await client.post("/mcp", json=INITIALIZE, headers=headers)).status_code == 200
            assert (await client.post("/mcp", json=INITIALIZE, headers=headers)).status_code == 429
            other = {**MCP_HEADERS, "x-forwarded-for": "198.51.100.4"}
            assert (await client.post("/mcp", json=INITIALIZE, headers=other)).status_code == 200


async def test_rate_limit_off_by_default_in_tests(tmp_path):
    settings = settings_for(tmp_path, rate_limit_per_minute=0)
    app, _ = app_for(settings)
    async with app.router.lifespan_context(app), await client_for(app) as client:
        for _ in range(5):
            response = await client.post(
                "/mcp",
                json=INITIALIZE,
                headers={**MCP_HEADERS, "authorization": f"Bearer {KEY}"},
            )
            assert response.status_code == 200


# -- host and origin checks --------------------------------------------------------------------


async def test_host_and_origin_checks(tmp_path):
    settings = settings_for(
        tmp_path,
        http_allowed_hosts=("mcp.test",),
        http_allowed_origins=("https://app.example.com",),
    )
    app, _ = app_for(settings)
    auth = {**MCP_HEADERS, "authorization": f"Bearer {KEY}"}
    async with app.router.lifespan_context(app), await client_for(app) as client:
        assert (await client.post("/mcp", json=INITIALIZE, headers=auth)).status_code == 200
        wrong_host = await client.post(
            "/mcp", json=INITIALIZE, headers={**auth, "host": "evil.example.com"}
        )
        assert wrong_host.status_code == 421
        wrong_origin = await client.post(
            "/mcp", json=INITIALIZE, headers={**auth, "origin": "https://evil.example.com"}
        )
        assert wrong_origin.status_code == 403
        allowed_origin = await client.post(
            "/mcp", json=INITIALIZE, headers={**auth, "origin": "https://app.example.com"}
        )
        assert allowed_origin.status_code == 200


async def test_request_size_limit(tmp_path):
    settings = settings_for(tmp_path, max_request_bytes=2048)
    app, _ = app_for(settings)
    payload = json.dumps({**INITIALIZE, "padding": "x" * 4096})
    async with app.router.lifespan_context(app), await client_for(app) as client:
        response = await client.post(
            "/mcp",
            content=payload,
            headers={**MCP_HEADERS, "authorization": f"Bearer {KEY}"},
        )
    assert response.status_code == 413
