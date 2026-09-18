"""MCP server definition. Tools are added in Phase 5; keep this a thin wrapper over ``core``."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from glendale_gis import __version__
from glendale_gis.core.config import Settings

# Sent to every client when it connects. Keep it short, and keep the data warnings in step with
# the catalog descriptions and the notes in core/hazards.py.
INSTRUCTIONS = """\
Read-only access to hazard zones for Glendale, California (CAL FIRE, California Geological \
Survey, FEMA, DWR Division of Safety of Dams, USGS) and City of Glendale GIS layers. It returns \
source data with citations (ref, _meta). It does not score or rank risk or give preparedness \
advice; do not present its results as a safety assessment of a property.

It has NO real-time data: no active fires, evacuation orders, weather warnings or earthquakes. \
Never suggest a location is safe right now. For current conditions, send people to official \
sources: Glendale's Everbridge alerts, Alert LA County, Genasys Protect (evacuation zones and \
status), CAL FIRE incidents, National Weather Service alerts and MyShake. See \
docs/real-time-sources.md in the project repository.

Reading results:
- status is in_zone, not_in_zone or unavailable. unavailable means no answer (layer missing, or \
the point is outside the covered area: Glendale plus 2 km). Never report it as "not in a zone".
- nearest.distance_m is straight-line meters. exact: false means a closer zone outside the \
covered area can't be ruled out.
- These are regulatory hazard maps, not site-specific assessments. Outside a mapped zone does \
not mean no hazard. Pass the notes and disclaimer on to residents.

What each hazard layer means:
- Wildfire (CAL FIRE Fire Hazard Severity Zones, 2025): rates the long-term physical hazard of \
the landscape, not the risk to a building. NonWildland means unzoned, NOT safe from wildfire: \
embers and house-to-house spread reach unzoned areas. About two-thirds of Glendale is Very High. \
Zones take effect when the city adopts them. Not an evacuation map.
- Flood (FEMA): every FEMA zone counts as in_zone, including X. Read FLD_ZONE, ZONE_SUBTY and \
SFHA_TF (T = Special Flood Hazard Area). Zone D means not studied, not safe. -9999 means no value.
- Fault, liquefaction, landslide (CGS): being inside the polygon is the hazard signal; the \
attributes only describe the map.
- Dam inundation (DWR): one dam can have several features (failure scenarios); report them all. \
HazardCl rates the consequences of a failure, not its likelihood. Federal dams are not included. \
Not an evacuation map.
- Debris flow (USGS): covers only recently burned areas. No result does not mean no risk.

City of Glendale data is for general information, not a substitute for legal descriptions or \
surveys. Nearest-resource distances are straight-line (not travel) and include only places \
inside Glendale.
"""


def create_server(settings: Settings) -> MCPServer:
    return MCPServer(name="glendale-gis", version=__version__, instructions=INSTRUCTIONS)


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
