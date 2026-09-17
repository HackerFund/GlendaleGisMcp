"""MCP server definition. Tools are added in Phase 5; keep this a thin wrapper over ``core``."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from glendale_gis import __version__
from glendale_gis.core.config import Settings


def create_server(settings: Settings) -> MCPServer:
    return MCPServer(
        name="glendale-gis",
        version=__version__,
        instructions=(
            "Read-only access to Glendale, California hazard zones (CAL FIRE, CGS, FEMA, "
            "DWR, USGS) and city GIS layers. Returns source data with citations; it does "
            "not interpret risk or give preparedness advice."
        ),
    )


def run(settings: Settings, *, http: bool = False) -> None:
    server = create_server(settings)
    if http:
        server.run(
            "streamable-http",
            host=settings.http_host,
            port=settings.http_port,
            stateless_http=True,
        )
    else:
        server.run("stdio")
