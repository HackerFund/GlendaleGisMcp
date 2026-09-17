"""Command-line entry point: ``glendale-gis-mcp [--http] [--host HOST] [--port PORT]``."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Sequence

from glendale_gis import __version__
from glendale_gis.core.config import ConfigError, Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="glendale-gis-mcp",
        description=(
            "MCP server for Glendale, CA hazard and city GIS data. Runs over stdio by default. "
            "Settings can also be set with GLENDALE_GIS_* environment variables."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--http", action="store_true", help="serve streamable HTTP instead of stdio"
    )
    parser.add_argument(
        "--host", help="HTTP bind host (default: GLENDALE_GIS_HTTP_HOST or 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, help="HTTP port (default: GLENDALE_GIS_HTTP_PORT or 8000)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.from_env()
        overrides = {
            k: v
            for k, v in {"http_host": args.host, "http_port": args.port}.items()
            if v is not None
        }
        if overrides:
            settings = dataclasses.replace(settings, **overrides)
    except ConfigError as exc:
        print(f"glendale-gis-mcp: configuration error: {exc}", file=sys.stderr)
        return 2

    from glendale_gis.server import run  # imported late so --help and --version stay fast

    run(settings, http=args.http)
    return 0


if __name__ == "__main__":
    sys.exit(main())
