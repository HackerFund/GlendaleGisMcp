"""MCP server: tools that are a thin wrapper over ``core``.

Every tool is read-only. Results are returned both as compact JSON text and as structured
content. Errors the caller can fix come back as ``is_error`` results with suggestions (and
candidates for ambiguous addresses), never stack traces.
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Literal, ParamSpec

from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from glendale_gis import __version__
from glendale_gis.core import datasets, hazards, resources
from glendale_gis.core.arcgis import ArcGISClient
from glendale_gis.core.cache import Cache
from glendale_gis.core.config import Settings
from glendale_gis.core.distribution import ensure_snapshot
from glendale_gis.core.docs import read_doc
from glendale_gis.core.geocode import Geocoder
from glendale_gis.core.models import (
    ActionableError,
    DatasetDescription,
    DatasetList,
    Filter,
    GeocodeResult,
    Guide,
    HazardResult,
    HazardsAtLocation,
    Location,
    Near,
    NearestResources,
    QueryResult,
    SeismicZones,
)
from glendale_gis.core.query import DEFAULT_LIMIT, MAX_LIMIT, QueryEngine
from glendale_gis.core.snapshot import Snapshot, SnapshotError

log = logging.getLogger(__name__)

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
status), CAL FIRE incidents, National Weather Service alerts and MyShake. Call \
read_guide("real_time_sources") for the list with links and data feeds.

Guides: call read_guide with "about" (what this server does and which tool answers what), \
"datasets" (every dataset), "real_time_sources" (where to find live emergency information) or \
"snapshot_data" (how to read the data). describe_dataset explains one dataset's fields. The same \
guides are also MCP resources (glendale-gis://about, glendale-gis://datasets, \
glendale-gis://datasets/{id}, glendale-gis://docs/real-time-sources, \
glendale-gis://docs/snapshot-data, glendale-gis://snapshot/manifest).

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


def quiet_http_logs() -> None:
    """httpx logs every request URL at INFO, and geocoder URLs contain the address. Never log
    addresses or coordinates, so keep HTTP client logs to warnings and errors."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


# --------------------------------------------------------------------------------------------
# State shared by all tools
# --------------------------------------------------------------------------------------------


@dataclass
class AppState:
    settings: Settings
    snapshot: Snapshot | None
    snapshot_error: str | None
    client: ArcGISClient
    cache: Cache
    geocoder: Geocoder | None
    engine: QueryEngine | None
    snapshot_source: str = "provided"  # configured, cache, downloaded, previous, none

    def require_snapshot(self) -> tuple[Snapshot, Geocoder, QueryEngine]:
        if self.snapshot is None or self.geocoder is None or self.engine is None:
            raise ActionableError(
                f"The data snapshot isn't available: {self.snapshot_error}",
                [
                    "Build it with 'python scripts/build_snapshot.py', or set "
                    "GLENDALE_GIS_SNAPSHOT_PATH to a built snapshot, then restart the server.",
                ],
            )
        return self.snapshot, self.geocoder, self.engine


def build_state(settings: Settings, snapshot: Snapshot | None = None) -> AppState:
    """Shared state for the tools. Without a snapshot given, find or download the published one."""
    error = None
    source = "provided"
    if snapshot is None:
        resolved = ensure_snapshot(settings)
        source, error = resolved.source, resolved.error
        if resolved.path is not None:
            try:
                snapshot = Snapshot.load(resolved.path, stale=resolved.stale)
            except SnapshotError as exc:
                error = str(exc)
                log.error("Snapshot unavailable: %s", exc)
    cache = Cache(settings.cache_dir / "cache.sqlite3")
    client = ArcGISClient(settings)
    geocoder = engine = None
    if snapshot is not None:
        geocoder = Geocoder(client, cache, snapshot, settings)
        engine = QueryEngine(snapshot, client, cache, settings)
    return AppState(settings, snapshot, error, client, cache, geocoder, engine, source)


# --------------------------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------------------------

P = ParamSpec("P")


def _ok(model: BaseModel) -> CallToolResult:
    text = model.model_dump_json(by_alias=True, exclude_none=True)
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=model.model_dump(mode="json", by_alias=True),
    )


def _error(exc: ActionableError) -> CallToolResult:
    text = exc.to_model().model_dump_json(exclude_none=True)
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


def _handles_errors(
    fn: Callable[P, Awaitable[CallToolResult]],
) -> Callable[P, Awaitable[CallToolResult]]:
    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> CallToolResult:
        try:
            return await fn(*args, **kwargs)
        except ActionableError as exc:
            return _error(exc)

    return wrapper


READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
READ_ONLY_OPEN = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)

LocationArg = Annotated[
    Location,
    Field(
        description=(
            'Where to look: {"address": "613 E Broadway"} for a street address in Glendale, '
            'or {"lat": 34.1466, "lon": -118.2483}. Coordinates skip geocoding.'
        )
    ),
]
GuideTopic = Literal["about", "datasets", "real_time_sources", "snapshot_data"]
# topic: (title, resource URI, doc file for static guides)
GUIDES: dict[str, tuple[str, str, str]] = {
    "about": ("About this server", "glendale-gis://about", ""),
    "datasets": ("Available datasets", "glendale-gis://datasets", ""),
    "real_time_sources": (
        "Real-time emergency information: where to find it",
        "glendale-gis://docs/real-time-sources",
        "real-time-sources.md",
    ),
    "snapshot_data": (
        "Reading the snapshot data",
        "glendale-gis://docs/snapshot-data",
        "snapshot-data.md",
    ),
}

ResourceKind = Literal[
    "fire_stations",
    "police_stations",
    "hospitals",
    "schools",
    "libraries",
    "parks",
    "bus_stops",
]


# --------------------------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------------------------


ABOUT_TEMPLATE = """\
# Glendale, California GIS MCP Server

**Covers the City of Glendale, California (Los Angeles County) only.** Read-only access to the \
geospatial data behind Glendale's emergency preparedness challenge: which mapped hazards apply \
at a location, what community resources are nearby, and the City of Glendale layers behind them.

It finds, queries and normalizes authoritative data from CAL FIRE, the California Geological \
Survey, FEMA, the California Division of Safety of Dams, USGS and the City of Glendale. It does \
**not** score or rank risk, give preparedness advice, or provide real-time information.

{data_line}

## What you can ask

| Question | Tool |
| --- | --- |
| Which hazards apply at an address or point? | `hazards_at_location`, or one of `wildfire_zone`, \
`flood_zone`, `seismic_zones`, `dam_inundation`, `debris_flow` |
| What's the nearest fire station, hospital, school, library, park, police station or bus stop? \
| `nearest_resources` |
| What's the zoning or parcel at a location? Which schools are high schools? Which bus stops \
serve route 7? | `query_dataset` |
| What data is there, and what do the fields mean? | `list_datasets`, `describe_dataset`, or \
the resources below |
| Is this a valid Glendale address? | `geocode_address` |
| What can this server do? Where do I find active fires, evacuations or alerts? | `read_guide` |

## Tools

| Tool | What it does |
| --- | --- |
{tool_rows}

## Locations

- Every tool that takes a location accepts `{{"address": "613 E Broadway"}}` or \
`{{"lat": 34.1466, "lon": -118.2483}}`.
- Addresses are matched with the City of Glendale geocoder. Ambiguous addresses return candidates \
instead of a guess. Addresses outside the city limits return an error, even though the geocoder \
also knows Burbank and unincorporated La Crescenta. Unit numbers are ignored; place names aren't \
supported.
- Hazard layers cover Glendale plus 2 km, and city layers Glendale plus 100 m. Coordinates \
farther out get `unavailable`.

## Reading results

- `status`: `in_zone`, `not_in_zone` or `unavailable` (with a `reason`). `unavailable` means no \
answer, never "not in a zone".
- `nearest`: the nearest zone with `distance_m` (straight-line, 0 when inside). Classed layers \
(wildfire, flood) give the nearest zone of each class. `exact: false` means a closer zone outside \
the covered area can't be ruled out.
- `matches`: the zone features containing the point, with their source attributes unchanged.
- `notes` and `disclaimer`: how to read the result. Pass them on to residents.
- `ref`: dataset, object ID, GlobalID and source layer URL, to fetch the full live record.
- `_meta`: source agency, URL, whether it was cached, when it was fetched, and whether it's stale.

## Not included

- **Real-time information:** active fires, evacuation orders, weather warnings, earthquakes. Call \
`read_guide("real_time_sources")` for the official sources.
- **Risk scores, rankings or advice:** the data is regulatory hazard maps, not a site-specific \
assessment. Outside a mapped zone does not mean no hazard.
- Other City of Glendale layers, such as street sweeping, historic districts and water \
pressure zones.

## Guides and resources

The guides are available two ways: the `read_guide` tool, which any client's model can call, and \
MCP resources, which a person can attach in clients that support them.

- `glendale-gis://about` or `read_guide("about")`: this page
- `glendale-gis://datasets` or `read_guide("datasets")`: every dataset, with sources, counts and \
dates
- `glendale-gis://datasets/{{id}}` or `describe_dataset`: one dataset's fields and coded values
- `glendale-gis://docs/snapshot-data` or `read_guide("snapshot_data")`: how to read the data \
(GeoJSON, fields, each hazard)
- `glendale-gis://docs/real-time-sources` or `read_guide("real_time_sources")`: where to find \
live emergency information
- `glendale-gis://snapshot/manifest`: the loaded snapshot's manifest (JSON)
"""


def overview_markdown(state: AppState, tools: list[tuple[str, str]]) -> str:
    """The glendale-gis://about page, built from the registered tools and the loaded snapshot."""
    if state.snapshot is not None:
        missing = ", ".join(sorted(state.snapshot.errors)) or "none"
        data_line = (
            f"Offline snapshot built {state.snapshot.built_at or 'at an unknown time'}. "
            f"Layers unavailable: {missing}."
        )
        if state.snapshot.stale:
            data_line += (
                f" **This is an older snapshot** because the current one couldn't be downloaded "
                f"({state.snapshot_error}); results are marked stale."
            )
    else:
        data_line = f"**The data snapshot isn't available:** {state.snapshot_error}"
    tool_rows = "\n".join(f"| `{name}` | {_first_sentence(text)} |" for name, text in tools)
    return ABOUT_TEMPLATE.format(data_line=data_line, tool_rows=tool_rows)


def _first_sentence(text: str) -> str:
    flat = " ".join(text.split())
    end = flat.find(". ")
    return (flat if end == -1 else flat[: end + 1]).replace("|", "\\|")


def create_server(settings: Settings, state: AppState | None = None) -> MCPServer:
    state = state or build_state(settings)
    mcp = MCPServer(name="glendale-gis", version=__version__, instructions=INSTRUCTIONS)
    quiet_http_logs()

    async def guide_markdown(topic: str) -> str:
        if topic == "about":
            tools = await mcp.list_tools()
            return overview_markdown(state, [(t.name, t.description or "") for t in tools])
        if topic == "datasets":
            if state.snapshot is None:
                return f"# Datasets\n\nThe data snapshot isn't available: {state.snapshot_error}\n"
            return datasets.catalog_markdown(state.snapshot)
        return read_doc(GUIDES[topic][2])

    async def located(location: Location):
        snapshot, geocoder, _ = state.require_snapshot()
        return snapshot, await geocoder.resolve(location)

    # -- catalog and generic access ----------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    @_handles_errors
    async def list_datasets() -> DatasetList:
        """List every dataset this server can query: id, source agency, category (hazard,
        resource or reference), whether it comes from the offline snapshot or live, and its
        feature count. Start here to find dataset ids for describe_dataset and query_dataset.
        For hazards or resources at a location, the dedicated tools (hazards_at_location,
        nearest_resources) are simpler."""
        snapshot, _, _ = state.require_snapshot()
        return _ok(datasets.list_datasets(snapshot))

    @mcp.tool(annotations=READ_ONLY)
    @_handles_errors
    async def read_guide(
        topic: Annotated[
            GuideTopic,
            Field(
                description=(
                    "about: what this server does, every tool, how results work. datasets: "
                    "every dataset with sources and dates. real_time_sources: where to find "
                    "active fires, evacuations, alerts and other live information. "
                    "snapshot_data: how to read the data and each hazard."
                )
            ),
        ],
    ) -> Guide:
        """Read one of the server's guides (Markdown). Call "about" first if you're unsure what
        this server can do. Call "real_time_sources" whenever someone asks about current
        conditions (active fires, evacuations, warnings, earthquakes, outages): this server has
        no real-time data, and the guide lists the official sources to send them to."""
        title, uri, _ = GUIDES[topic]
        content = await guide_markdown(topic)
        guide = Guide(topic=topic, title=title, resource_uri=uri, content=content)
        return CallToolResult(
            content=[TextContent(type="text", text=content)],
            structured_content=guide.model_dump(mode="json"),
        )

    @mcp.tool(annotations=READ_ONLY_OPEN)
    @_handles_errors
    async def describe_dataset(
        dataset: Annotated[str, Field(description="Dataset id from list_datasets")],
    ) -> DatasetDescription:
        """Describe one dataset: every field with its type, meaning and coded values (inferred
        meanings are flagged), record count, freshness (fetched_at, source_last_edit), the area
        the snapshot covers, the source URL and disclaimer. Read this before filtering with
        query_dataset, and to interpret attributes returned by other tools (e.g. FEMA FLD_ZONE
        codes, CAL FIRE FHSZ codes)."""
        snapshot, _, engine = state.require_snapshot()
        return _ok(await datasets.describe_dataset(snapshot, engine, dataset))

    @mcp.tool(annotations=READ_ONLY_OPEN)
    @_handles_errors
    async def query_dataset(
        dataset: Annotated[str, Field(description="Dataset id from list_datasets")],
        filters: Annotated[
            list[Filter] | None,
            Field(
                description=(
                    "Attribute conditions, all of which must match. Each is "
                    '{"field", "op", "value"}; op is eq, ne, lt, lte, gt, gte, in (value is a '
                    "list), contains, starts_with, is_null or not_null. Field names are "
                    "case-sensitive (see describe_dataset); text comparisons ignore case. "
                    'Example: [{"field": "School_typ", "op": "eq", "value": "High School"}]'
                )
            ),
        ] = None,
        near: Annotated[
            Near | None,
            Field(
                description=(
                    'Spatial filter: {"lat", "lon", "radius_m"} (radius up to 5000 m). '
                    "radius_m 0 returns features containing the point, e.g. the zoning or "
                    "parcel at a location. Results are sorted by distance."
                )
            ),
        ] = None,
        bbox: Annotated[
            list[float] | None,
            Field(description="Spatial filter: [min_lon, min_lat, max_lon, max_lat]"),
        ] = None,
        fields: Annotated[
            list[str] | None,
            Field(description="Attributes to return (default all); the object ID is always kept"),
        ] = None,
        limit: Annotated[int, Field(description=f"Page size, 1-{MAX_LIMIT}")] = DEFAULT_LIMIT,
        offset: Annotated[int, Field(description="Skip this many results (paging)")] = 0,
        include_geometry: Annotated[
            bool,
            Field(description="Return GeoJSON geometry; very large shapes are left out"),
        ] = False,
    ) -> QueryResult:
        """Search one dataset with attribute filters, a location (near) or a bounding box, with
        paging. Use it for questions the dedicated tools don't cover: zoning or the parcel at a
        location, schools of a type, bus stops on a route, all dams affecting an area. Results
        carry total, next_offset for the next page, and a ref for each feature. Geometry is off
        by default to save context. Parcels are queried live from the city; everything else
        comes from the offline snapshot."""
        _, _, engine = state.require_snapshot()
        result = await engine.query(
            dataset,
            filters=filters or [],
            near=near,
            bbox=bbox,
            fields=fields,
            limit=limit,
            offset=offset,
            include_geometry=include_geometry,
        )
        return _ok(result)

    @mcp.tool(annotations=READ_ONLY_OPEN)
    @_handles_errors
    async def geocode_address(
        address: Annotated[
            str, Field(min_length=1, max_length=200, description="A street address in Glendale")
        ],
    ) -> GeocodeResult:
        """Find a Glendale street address with the City of Glendale geocoder. Returns the
        matched address, coordinates and score, or several candidates when the address is
        ambiguous (e.g. '613 Broadway' matches both E and W Broadway). Addresses outside the
        city limits (the geocoder also knows Burbank and unincorporated La Crescenta) return an
        error. Unit numbers are ignored and place names aren't supported. Other tools accept an
        address directly, so call this only to check or disambiguate one."""
        _, geocoder, _ = state.require_snapshot()
        return _ok(await geocoder.geocode(address))

    # -- hazards -----------------------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY_OPEN)
    @_handles_errors
    async def hazards_at_location(location: LocationArg) -> HazardsAtLocation:
        """All mapped hazards at one location in one call: wildfire (CAL FIRE), flood (FEMA),
        fault rupture, liquefaction and earthquake-induced landslide (CGS), dam inundation
        (DWR) and post-fire debris flow (USGS). Each has a status (in_zone, not_in_zone or
        unavailable), the zones containing the point, the nearest zone with its distance, and
        notes on how to read it. These are regulatory maps of mapped hazard, not current
        conditions or a safety assessment; for active fires, evacuations and alerts call
        read_guide("real_time_sources"). Use the single-hazard tools when only one is
        needed."""
        snapshot, loc = await located(location)
        return _ok(hazards.hazards_at_location(snapshot, loc))

    @mcp.tool(annotations=READ_ONLY_OPEN)
    @_handles_errors
    async def wildfire_zone(location: LocationArg) -> HazardResult:
        """CAL FIRE Fire Hazard Severity Zone at a location (2025 map), with the nearest zone
        of each class (Very High, High, Moderate, NonWildland). The zones rate the long-term
        hazard of the landscape, not the risk to a building. NonWildland is unzoned, not safe
        from wildfire. Not an evacuation map; for active fires call
        read_guide("real_time_sources")."""
        snapshot, loc = await located(location)
        return _ok(hazards.wildfire_zone(snapshot, loc))

    @mcp.tool(annotations=READ_ONLY_OPEN)
    @_handles_errors
    async def flood_zone(location: LocationArg) -> HazardResult:
        """FEMA flood zone at a location (National Flood Hazard Layer), with the nearest zone
        of each FLD_ZONE class. Every FEMA zone counts as in_zone, including X (minimal or
        0.2%-annual-chance hazard); read FLD_ZONE, ZONE_SUBTY and SFHA_TF (T = Special Flood
        Hazard Area). Zone D means not studied, not safe."""
        snapshot, loc = await located(location)
        return _ok(hazards.flood_zone(snapshot, loc))

    @mcp.tool(annotations=READ_ONLY_OPEN)
    @_handles_errors
    async def seismic_zones(location: LocationArg) -> SeismicZones:
        """California Geological Survey earthquake zones at a location: Alquist-Priolo fault
        rupture, liquefaction and earthquake-induced landslide. Being inside a zone is the
        hazard signal; the attributes only describe the map (quadrangle, release date, report
        links). Outside a zone does not mean no earthquake hazard: all of Glendale can shake."""
        snapshot, loc = await located(location)
        return _ok(hazards.seismic_zones(snapshot, loc))

    @mcp.tool(annotations=READ_ONLY_OPEN)
    @_handles_errors
    async def dam_inundation(location: LocationArg) -> HazardResult:
        """DWR dam inundation areas at a location: where water could flood if a dam failed.
        One dam can have several features (failure scenarios or structures); all are returned.
        HazardCl rates the consequences of a failure, not its likelihood. Federally owned dams
        are not included. Not an evacuation map."""
        snapshot, loc = await located(location)
        return _ok(hazards.dam_inundation(snapshot, loc))

    @mcp.tool(annotations=READ_ONLY_OPEN)
    @_handles_errors
    async def debris_flow(location: LocationArg) -> HazardResult:
        """USGS post-fire debris-flow assessments at a location. USGS only assesses recently
        burned areas, and none currently covers Glendale, so this usually returns not_in_zone;
        that does not mean there is no debris-flow risk."""
        snapshot, loc = await located(location)
        return _ok(hazards.debris_flow(snapshot, loc))

    # -- resources ---------------------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY_OPEN)
    @_handles_errors
    async def nearest_resources(
        location: LocationArg,
        kinds: Annotated[
            list[ResourceKind] | None,
            Field(description="Which kinds to include; default all"),
        ] = None,
        limit: Annotated[
            int, Field(description=f"Results per kind, 1-{resources.MAX_LIMIT}")
        ] = resources.DEFAULT_LIMIT,
    ) -> NearestResources:
        """Nearest community resources to a location: fire stations, police station,
        hospitals, schools, libraries, parks and Beeline bus stops, with name, address and
        distance. Distances are straight-line meters, not travel distance or time, and only
        resources inside Glendale are included (neighboring cities' facilities may be closer).
        For other filters (e.g. only high schools) use query_dataset."""
        snapshot, loc = await located(location)
        try:
            result = resources.nearest_resources(snapshot, loc, kinds, limit)
        except ValueError as exc:
            raise ActionableError(str(exc)) from None
        return _ok(result)

    # -- MCP resources -----------------------------------------------------------------------

    @mcp.resource(
        "glendale-gis://about",
        name="about",
        title="About this server",
        description=(
            "Start here: what this server does and doesn't do, what you can ask and which tool "
            "answers it, how locations and results work, and where the data comes from."
        ),
        mime_type="text/markdown",
    )
    async def about() -> str:
        return await guide_markdown("about")

    @mcp.resource(
        "glendale-gis://datasets",
        name="datasets",
        title="Available datasets",
        description=(
            "Every dataset the server can query: hazard zones (CAL FIRE, CGS, FEMA, DWR, USGS), "
            "community resources and reference layers from the City of Glendale, with sources, "
            "feature counts and dates."
        ),
        mime_type="text/markdown",
    )
    async def datasets_resource() -> str:
        return await guide_markdown("datasets")

    @mcp.resource(
        "glendale-gis://datasets/{dataset_id}",
        name="dataset",
        title="One dataset",
        description=(
            "A dataset's description, disclaimer, freshness, coverage, and every field with its "
            "meaning and coded values. dataset_id is an id from glendale-gis://datasets."
        ),
        mime_type="text/markdown",
    )
    async def dataset_resource(dataset_id: str) -> str:
        try:
            snapshot, _, engine = state.require_snapshot()
            described = await datasets.describe_dataset(snapshot, engine, dataset_id)
        except ActionableError as exc:
            steps = "".join(f"- {s}\n" for s in exc.suggestions)
            return f"# {dataset_id}\n\n{exc.error}\n\n{steps}"
        return datasets.dataset_markdown(described)

    @mcp.resource(
        "glendale-gis://docs/real-time-sources",
        name="real-time-sources",
        title="Real-time emergency information: where to find it",
        description=(
            "This server has no real-time data. Official sources for active fires, evacuation "
            "zones and orders, weather warnings, earthquakes and outages, for residents and for "
            "developers, with rules for using live data safely."
        ),
        mime_type="text/markdown",
    )
    async def real_time_sources() -> str:
        return await guide_markdown("real_time_sources")

    @mcp.resource(
        "glendale-gis://docs/snapshot-data",
        name="snapshot-data",
        title="Reading the snapshot data",
        description=(
            "How the data is structured (GeoJSON, fields, coded values, dates), what each layer "
            "contains, how it was clipped, and how to read each hazard correctly."
        ),
        mime_type="text/markdown",
    )
    async def snapshot_data() -> str:
        return await guide_markdown("snapshot_data")

    @mcp.resource(
        "glendale-gis://snapshot/manifest",
        name="snapshot-manifest",
        title="Snapshot manifest",
        description=(
            "The loaded snapshot's manifest: for each layer, its source URL, when it was fetched, "
            "when the source last changed, feature counts, fields and the area it covers."
        ),
        mime_type="application/json",
    )
    def snapshot_manifest() -> str:
        if state.snapshot is None:
            return json.dumps(
                {"error": f"The data snapshot isn't available: {state.snapshot_error}"}
            )
        return json.dumps(state.snapshot.manifest, indent=1)

    return mcp


def run(settings: Settings, *, http: bool = False) -> None:
    """Serve over stdio, or over streamable HTTP with the hosted protections."""
    state = build_state(settings)
    server = create_server(settings, state)
    if not http:
        server.run("stdio")
        return

    import uvicorn

    from glendale_gis.http import MCP_PATH, build_app

    app = build_app(settings, server, state)
    log.info(
        "Serving MCP on http://%s:%s%s (auth: %s)",
        settings.http_host,
        settings.http_port,
        MCP_PATH,
        f"{len(settings.api_keys)} key(s)" if settings.api_keys else "none",
    )
    uvicorn.run(app, host=settings.http_host, port=settings.http_port, access_log=False)
