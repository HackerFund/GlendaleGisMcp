"""Settings read from environment variables, with defaults suited to local use.

Every variable uses the ``GLENDALE_GIS_`` prefix, e.g. ``GLENDALE_GIS_HAZARD_BUFFER_M=3000``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path

from platformdirs import user_cache_dir

from glendale_gis import __version__

ENV_PREFIX = "GLENDALE_GIS_"


class ConfigError(ValueError):
    """Raised when an environment variable has an invalid value."""


def _default_cache_dir() -> Path:
    return Path(user_cache_dir("glendale-gis-mcp", appauthor=False))


@dataclass(frozen=True)
class Settings:
    # Snapshot clipping buffers around the city boundary, in meters.
    city_buffer_m: float = 100.0
    hazard_buffer_m: float = 2000.0
    max_snapshot_mb: float = 100.0

    # Local paths. snapshot_path overrides the downloaded snapshot (useful in development).
    cache_dir: Path = field(default_factory=_default_cache_dir)
    snapshot_path: Path | None = None

    # Outbound HTTP politeness toward source servers.
    contact: str = "ryan@hacker.fund"
    max_concurrency_per_host: int = 2
    min_request_interval_s: float = 0.25
    request_timeout_s: float = 30.0
    max_retries: int = 4

    # Disk cache for live results. Geocoder answers change rarely; parcels change slowly.
    geocode_cache_ttl_s: float = 30 * 24 * 3600.0
    live_cache_ttl_s: float = 24 * 3600.0

    # Hosted (streamable HTTP) mode.
    http_host: str = "127.0.0.1"
    http_port: int = 8000
    # Shared secret(s) callers send as "Authorization: Bearer <key>". Comma-separated, so keys
    # can be rotated with an overlap. Required when binding to anything but localhost.
    api_keys: tuple[str, ...] = ()
    # Host and Origin headers allowed in hosted mode (DNS-rebinding protection). Empty turns the
    # check off, which is fine on localhost. Set the Cloud Run hostname when deployed.
    http_allowed_hosts: tuple[str, ...] = ()
    http_allowed_origins: tuple[str, ...] = ()
    rate_limit_per_minute: int = 120  # per API key, or per client IP; 0 turns it off
    max_request_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in ("city_buffer_m", "hazard_buffer_m"):
            if getattr(self, name) < 0:
                raise ConfigError(f"{name} must be >= 0")
        for name in (
            "max_snapshot_mb",
            "request_timeout_s",
            "geocode_cache_ttl_s",
            "live_cache_ttl_s",
        ):
            if getattr(self, name) <= 0:
                raise ConfigError(f"{name} must be > 0")
        if self.min_request_interval_s < 0:
            raise ConfigError("min_request_interval_s must be >= 0")
        if self.max_concurrency_per_host < 1:
            raise ConfigError("max_concurrency_per_host must be >= 1")
        if self.max_retries < 0:
            raise ConfigError("max_retries must be >= 0")
        if not 1 <= self.http_port <= 65535:
            raise ConfigError("http_port must be between 1 and 65535")
        if self.rate_limit_per_minute < 0:
            raise ConfigError("rate_limit_per_minute must be >= 0")
        if self.max_request_bytes < 1024:
            raise ConfigError("max_request_bytes must be at least 1024")
        if not self.contact.strip():
            raise ConfigError("contact must not be empty")

    @property
    def is_public_bind(self) -> bool:
        """True when the HTTP server listens on more than the local machine."""
        return self.http_host not in ("127.0.0.1", "localhost", "::1")

    @property
    def user_agent(self) -> str:
        return f"GlendaleGisMcp/{__version__} ({self.contact})"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Build settings from ``GLENDALE_GIS_*`` variables; unset or empty ones keep defaults."""
        env = os.environ if environ is None else environ
        kwargs: dict[str, object] = {}
        for f in fields(cls):
            var = ENV_PREFIX + f.name.upper()
            raw = env.get(var)
            if raw is None or raw.strip() == "":
                continue
            kwargs[f.name] = _parse(var, f.name, raw.strip())
        return cls(**kwargs)  # type: ignore[arg-type]


_FLOAT_FIELDS = {
    "city_buffer_m",
    "hazard_buffer_m",
    "max_snapshot_mb",
    "min_request_interval_s",
    "request_timeout_s",
    "geocode_cache_ttl_s",
    "live_cache_ttl_s",
}
_INT_FIELDS = {
    "max_concurrency_per_host",
    "max_retries",
    "http_port",
    "rate_limit_per_minute",
    "max_request_bytes",
}
_TUPLE_FIELDS = {"api_keys", "http_allowed_hosts", "http_allowed_origins"}
_PATH_FIELDS = {"cache_dir", "snapshot_path"}


def _parse(var: str, name: str, raw: str) -> object:
    try:
        if name in _FLOAT_FIELDS:
            return float(raw)
        if name in _INT_FIELDS:
            return int(raw)
    except ValueError:
        raise ConfigError(f"{var} must be a number, got {raw!r}") from None
    if name in _PATH_FIELDS:
        return Path(raw).expanduser()
    if name in _TUPLE_FIELDS:
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    return raw
