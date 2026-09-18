import pytest

from glendale_gis import __main__ as cli
from glendale_gis import __version__
from glendale_gis.server import create_server


def test_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "glendale-gis-mcp" in capsys.readouterr().out


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_bad_config_returns_error_code(monkeypatch, capsys):
    monkeypatch.setenv("GLENDALE_GIS_HTTP_PORT", "not-a-port")
    assert cli.main([]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_cli_overrides_host_and_port(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "glendale_gis.server.run",
        lambda settings, http: captured.update(settings=settings, http=http),
    )
    assert cli.main(["--http", "--host", "0.0.0.0", "--port", "9100"]) == 0
    assert captured["http"] is True
    assert captured["settings"].http_host == "0.0.0.0"
    assert captured["settings"].http_port == 9100


def test_server_builds():
    from glendale_gis.core.config import Settings

    server = create_server(Settings.from_env({}))
    assert server.name == "glendale-gis"


@pytest.mark.parametrize(
    "phrase",
    [
        "does not score or rank risk",
        'Never report it as "not in a zone"',
        "NonWildland means unzoned, NOT safe from wildfire",
        "Zone D means not studied, not safe",
        "being inside the polygon is the hazard signal",
        "HazardCl rates the consequences of a failure, not its likelihood",
        "No result does not mean no risk",
        "straight-line",
        "It has NO real-time data",
        "Genasys Protect",
    ],
)
def test_instructions_carry_the_data_warnings(phrase):
    from glendale_gis.core.config import Settings

    server = create_server(Settings.from_env({}))
    assert phrase in server.instructions
