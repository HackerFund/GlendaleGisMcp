"""Glendale, CA hazard and city GIS data for MCP clients."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("glendale-gis-mcp")
except PackageNotFoundError:  # running from a source tree without installing
    __version__ = "0.0.0"
