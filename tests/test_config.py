from pathlib import Path

import pytest

from glendale_gis import __version__
from glendale_gis.core.config import ConfigError, Settings


def test_defaults():
    s = Settings.from_env({})
    assert s.city_buffer_m == 100.0
    assert s.hazard_buffer_m == 2000.0
    assert s.max_snapshot_mb == 100.0
    assert s.contact == "ryan@hacker.fund"
    assert s.max_concurrency_per_host == 2
    assert s.http_host == "127.0.0.1"
    assert s.http_port == 8000
    assert s.api_keys == ()
    assert s.rate_limit_per_minute == 120
    assert s.max_request_bytes == 4 * 1024 * 1024
    assert s.is_public_bind is False
    assert s.snapshot_path is None
    assert "glendale-gis-mcp" in str(s.cache_dir)


def test_user_agent_includes_version_and_contact():
    s = Settings.from_env({"GLENDALE_GIS_CONTACT": "team@example.org"})
    assert s.user_agent == f"GlendaleGisMcp/{__version__} (team@example.org)"


def test_env_overrides_are_parsed():
    s = Settings.from_env(
        {
            "GLENDALE_GIS_HAZARD_BUFFER_M": "3000",
            "GLENDALE_GIS_HTTP_PORT": "9001",
            "GLENDALE_GIS_API_KEYS": "secret, second ,",
            "GLENDALE_GIS_HTTP_ALLOWED_HOSTS": "glendale.example.run.app",
            "GLENDALE_GIS_HTTP_HOST": "0.0.0.0",
            "GLENDALE_GIS_SNAPSHOT_PATH": "~/snap",
        }
    )
    assert s.hazard_buffer_m == 3000.0
    assert s.http_port == 9001
    assert s.api_keys == ("secret", "second")
    assert s.http_allowed_hosts == ("glendale.example.run.app",)
    assert s.is_public_bind is True
    assert s.snapshot_path == Path("~/snap").expanduser()


def test_empty_values_keep_defaults():
    s = Settings.from_env({"GLENDALE_GIS_HTTP_PORT": "  ", "GLENDALE_GIS_API_KEYS": ""})
    assert s.http_port == 8000
    assert s.api_keys == ()


@pytest.mark.parametrize(
    "env",
    [
        {"GLENDALE_GIS_HTTP_PORT": "abc"},
        {"GLENDALE_GIS_HTTP_PORT": "70000"},
        {"GLENDALE_GIS_HAZARD_BUFFER_M": "-1"},
        {"GLENDALE_GIS_MAX_CONCURRENCY_PER_HOST": "0"},
        {"GLENDALE_GIS_REQUEST_TIMEOUT_S": "0"},
    ],
)
def test_invalid_values_raise(env):
    with pytest.raises(ConfigError):
        Settings.from_env(env)
