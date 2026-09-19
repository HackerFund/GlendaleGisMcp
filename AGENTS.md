# AGENTS.md

Guidance for coding agents working in this repository.

## Project

An MCP server that gives hackathon teams, and their agents, easy access to the geospatial data behind **Glendale, California's emergency preparedness challenge**: helping residents understand the hazards where they live and how to prepare.

**This server provides data access, not a finished solution.** It finds, queries and normalizes authoritative data. It does not interpret risk, rank hazards, give preparedness advice or build resident-facing features. Teams do that with the data.

Background:
- `Plans/glendale-gis-mcp-notes.md` — original research: MCP design practices, Glendale's ArcGIS inventory, caching and rate-limit reasoning. Written before the scope narrowed, so its tool ideas (street sweeping, etc.) are not all in scope.
- `Plans/hazard-sources.md` — verified state and federal hazard endpoints, with per-layer metadata, coded values and gotchas (includes Phase 0 findings).
- `Plans/city-sources.md` — Glendale layer inventory, geocoder behavior, coded values and gotchas (Phase 0 findings).
- `docs/real-time-sources.md` — where to find real-time emergency information (alerts, active fires, evacuation zones, earthquakes), which the server deliberately doesn't provide. Sources were checked on September 18, 2026; re-check before relying on them.
- `docs/snapshot-data.md` — user guide to the snapshot's GeoJSON files and manifest. Update it when the snapshot format or layers change.
- `Plans/implementation-plan.md` — phased order of work, with "done when" criteria and open questions.

## Status

**Phases 0–6 are complete.** Phase 0 findings are in `Plans/hazard-sources.md` and `Plans/city-sources.md`. Phase 1 set up the package skeleton, settings (`core/config.py`), CLI and an MCP server with no tools yet. Phase 2 added the dataset catalog (`core/catalog.py`) and the ArcGIS client (`core/arcgis.py`). Phase 3 added the snapshot builder (`scripts/build_snapshot.py`) and geometry helpers (`core/geo.py`). Phase 4 added offline lookups: `core/snapshot.py`, `core/hazards.py`, `core/resources.py` and `core/models.py`. Phase 5 added the geocoder (`core/geocode.py`), the disk cache (`core/cache.py`), structured queries (`core/query.py`), `core/datasets.py`, and all 11 MCP tools in `server.py`. Phase 6 added snapshot distribution: `core/distribution.py` (download, verify, cache, fallback), `build_snapshot.py --publish`, `glendale-gis-mcp --fetch-snapshot` and the committed `snapshot.lock.json`. Next: Phase 7 of `Plans/implementation-plan.md`. Update this section as phases complete.

## Environment

- Develop on Python 3.13 in a virtualenv at `.venv` (`python3.13 -m venv .venv`). Activate with `source .venv/bin/activate`. Don't use an older system Python; the package needs 3.10 or later.
- The package targets **Python 3.10+** (`requires-python = ">=3.10"`, the MCP SDK minimum), because teams will have mixed versions. Don't use 3.11+ only features (e.g. `tomllib`, `ExceptionGroup`, `typing.Self`).
- Commands (run from the repo root with `.venv` active):
  - Install for development: `pip install -e ".[dev]"`
  - Tests: `pytest`
  - Lint: `ruff check .`
  - Format: `ruff format .` (`Plans/` is excluded so research notes aren't rewritten)
  - Run the server: `glendale-gis-mcp` (stdio) or `glendale-gis-mcp --http [--host H] [--port P]`
  - Download and verify the published snapshot: `glendale-gis-mcp --fetch-snapshot`
  - Publish a new snapshot: `python scripts/build_snapshot.py --publish` (builds, uploads a GitHub Release with `gh`, rewrites `snapshot.lock.json`; then commit the lock). `--no-build` publishes the existing `snapshot/`; `--tag snapshot-YYYYMMDD-2` for a second release the same day.
  - Build the snapshot: `python scripts/build_snapshot.py [--only ID,...] [--dry-run] [--out DIR]` (hits live servers; about 1.5 minutes). Output goes to `snapshot/`, which is gitignored.
- All settings are `GLENDALE_GIS_<FIELD>` environment variables matching the fields in `core/config.py` (e.g. `GLENDALE_GIS_HAZARD_BUFFER_M`, `GLENDALE_GIS_CONTACT`, `GLENDALE_GIS_SNAPSHOT_PATH`). Unset or empty values keep defaults; invalid values raise `ConfigError`.

## Scope

### Data

**State and federal hazard layers** (endpoints and details in `Plans/hazard-sources.md`):

| Hazard | Source |
|---|---|
| Wildfire | CAL FIRE Fire Hazard Severity Zones (LRA 2025) |
| Fault rupture | CGS Alquist-Priolo Fault Zones |
| Liquefaction, earthquake-induced landslide | CGS Seismic Hazard Zones |
| Flood | FEMA National Flood Hazard Layer |
| Dam failure | DWR Division of Safety of Dams inundation areas |
| Post-fire debris flow | USGS assessments (none currently cover Glendale) |

**City of Glendale GIS** (`gismap.glendaleca.gov`, `Common` folder):

| Layer | Features | Access |
|---|---|---|
| City boundary | 1 | Snapshot |
| Fire stations, fire station districts | 9, 10 | Snapshot |
| Police station | 1 | Snapshot |
| Hospitals | 3 | Snapshot |
| Schools | 27 | Snapshot |
| Libraries | 8 | Snapshot |
| Parks | 43 | Snapshot |
| Bus stops (Beeline) | 338 | Snapshot |
| ZIP codes | 24 | Snapshot |
| Neighborhood zones | 37 | Snapshot |
| Zoning | 2,426 | Snapshot |
| Streets | 8,070 | Snapshot |
| Parcels | 54,300 | **Live only** — too large for the snapshot; may be cut entirely |
| Address geocoder (`CAD_SiteAddress_Street/GeocodeServer`) | — | **Live only**, cached |

### Out of scope

- Interpreting, scoring or ranking risk; preparedness advice or checklists
- Resident-facing UI, personalization or storing household data
- Live emergency alerts or evacuation orders, and any other real-time data. This was decided on September 18, 2026, after looking at NIFC's current fire perimeters: the data would be easy to add, but perimeters lag the fire and many fires have none, so "no perimeter nearby" reads as "safe". Instead, `docs/real-time-sources.md` lists the official sources and shows teams how to fetch perimeters themselves. The server `INSTRUCTIONS` and the tool docstrings point there. Don't add live feeds without revisiting this decision.
- Other Glendale layers: water pressure zones, historic districts and parcels, street sweeping, pavement condition, truck routes, capital projects, hauler locator
- Glendale, **AZ** (`glendaleaz-cog-gis.hub.arcgis.com`) — a different city
- Docker

## Tools

**Generic access**
- `list_datasets` — the catalog: id, source agency, category, description, snapshot or live
- `describe_dataset` — fields, meanings of coded values, record count, freshness
- `query_dataset` — attribute and spatial queries with capped limits and optional geometry. Filters are **structured** (`field`, `op`, `value`), never raw SQL, so the same query runs on the local snapshot and translates safely to a `where` clause for live datasets.
- `geocode_address` — the city geocoder; returns possible matches with scores
- `read_guide(topic)` — the guides (`about`, `datasets`, `real_time_sources`, `snapshot_data`) as Markdown; the same content as the resources below

**Hazards at a location**
- `wildfire_zone`, `flood_zone`, `seismic_zones` (fault, liquefaction, landslide), `dam_inundation`, `debris_flow`
- `hazards_at_location` — all of the above in one call

**Resources near a location**
- `nearest_resources(location, kinds, limit)` — fire stations, police, hospitals, schools, libraries, parks, bus stops. Distances are straight-line meters, not travel distance, and only cover resources inside Glendale; docstrings must say both.

**Resources** (read-only documents clients can load)
- `glendale-gis://about`: the start page. What the server does and doesn't do, which tool answers which question, a table of every tool (built from the registered tools, so it can't drift), locations, reading results, and what's not included. Built by `server.overview_markdown`.
- `glendale-gis://datasets`: every dataset grouped by kind, with description, source, feature count and last-edit date. The template `glendale-gis://datasets/{dataset_id}` gives one dataset's facts and a field table with meanings and coded values. Both are generated from the catalog and manifest (`core/datasets.py`).
- `glendale-gis://docs/real-time-sources` and `glendale-gis://docs/snapshot-data`: the Markdown guides in `docs/`, packaged into the wheel as `glendale_gis/docs` (`core/docs.py` reads the packaged copy, or `docs/` in a source checkout). Edit `docs/` only; there is one copy of each.
- `glendale-gis://snapshot/manifest`: the loaded snapshot's `manifest.json`.
- **Every guide is also a tool (`read_guide`).** Resources are controlled by the client: Claude Desktop lists them but never reads them unless a person attaches one, and it doesn't list resource templates. So the model can only reach guidance through tools. `read_guide` and the resources share one content function; `describe_dataset` covers the per-dataset pages. The server `INSTRUCTIONS` and tool docstrings tell the model to call `read_guide(...)`, never to open a repo path.
- No MCP prompts yet.

### Shared behavior

- **`location` is an address or a latitude/longitude**, the same way in every tool. Coordinates skip geocoding.
- Responses include the matched address, coordinates and geocoder score.
- **Ambiguous addresses** return candidates instead of guessing. **Addresses outside Glendale** return a clear error.
- Every hazard result has a `status` of `in_zone`, `not_in_zone` or `unavailable` (with a reason). Never let a missing source look like "not in zone". A missing layer, or a point outside the area the snapshot covers for that layer, is `unavailable`.
- **Unzoned classes don't count as in a zone.** A class the source itself defines as outside any zone (CAL FIRE `NonWildland`, "Unzoned, Non Wildland") is listed in the catalog's `unzoned_classes`. The feature still appears in `matches`, with a note. Every FEMA flood zone, including X, counts as a zone.
- **Nearest zone:** every hazard result includes the nearest zone with `distance_m` (0 when inside) and a `ref` identifying the feature. For classed layers (wildfire, flood), return the nearest zone for each class. `exact: false` marks a nearest distance that is farther than the edge of the snapshot's coverage, where a closer zone outside the snapshot can't be ruled out.
- `ref` = `{dataset, object_id, global_id, layer_url}`, so teams can fetch the full live record. Resource results carry the same `ref`.

```json
{
  "status": "not_in_zone",
  "nearest": {
    "distance_m": 240,
    "exact": true,
    "attributes": {"FHSZ_Description": "Very High", "SRA": "LRA"},
    "ref": {"dataset": "calfire_fhsz_lra", "object_id": 4, "global_id": null,
            "layer_url": "https://services1.arcgis.com/.../FeatureServer/0"}
  }
}
```

## Snapshot

Built by `scripts/build_snapshot.py`. It:
- Fetches the city boundary; clips **city layers** to the boundary + a small buffer and **hazard layers** to the boundary + a wider buffer (so nearest-zone distances are right near the city limit). Both buffer distances are **config values**, not hardcoded; defaults are 100 m (city) and 2 km (hazard).
- Queries with `outSR=4326`, pages past `maxRecordCount`, and pauses politely between requests.
- **Preserves `OBJECTID` and `GlobalID`** unchanged, since `ref` depends on them.
- Rounds coordinates to 6 decimal places and keeps only useful fields.
- Writes one GeoJSON file per layer plus `manifest.json` (source, URL, fetch time, source last-edit date, feature count, fields, byte size).
- Reports per-layer sizes and fails if the total exceeds **100 MB** or a layer is unexpectedly empty.
- Supports `--only <dataset,...>` (other layers already in `--out` are kept), `--dry-run`, `--out`, and (Phase 6) `--publish`.
- Fails the whole build, writing nothing, if a catalog field is missing from the source schema or a layer has duplicate object IDs.

### Distribution

- The snapshot is published as a **GitHub Release asset**, not committed to the repo.
- The repo commits only `snapshot.lock.json`: version, download URL, SHA-256, size. `--publish` uploads the asset and rewrites this file.
- On first run the package downloads the snapshot to the user cache directory, verifies the checksum, and reuses it afterwards.
- If the download fails or there's no network, fall back to live queries and mark results `stale` or `unavailable`. Never crash.

## Tech stack

- Official `mcp` SDK **2.x** — the high-level server class is `MCPServer` from `mcp.server.mcpserver` (it was `FastMCP` in 1.x; don't use 1.x examples), `httpx`, `shapely` 2 (STRtree for spatial lookups), `pydantic`, `platformdirs` (cache location). SQLite (stdlib) for the disk cache.
- Dev: `pytest`, `pytest-asyncio`, `respx` (httpx mocking), `ruff`.
- **No `pyproj`.** Distances use a local equirectangular projection centered on Glendale (error well under 1% across the city). Revisit only if accuracy needs change.
- All tunable values (buffers, throttle limits, cache dir, User-Agent contact, host/port, API key, snapshot path) live in `core/config.py`, read from environment variables with defaults.
- **No GDAL, GeoPandas, PostGIS or Docker.** Every dependency must install from wheels on macOS, Windows and Linux.
- Install for teams: `uvx --from git+https://github.com/HackerFund/GlendaleGisMcp glendale-gis-mcp` (primary), `pip install git+https://github.com/HackerFund/GlendaleGisMcp` (fallback). Repo: https://github.com/HackerFund/GlendaleGisMcp (public).
- Transport: stdio by default; `--http` for streamable HTTP.

### Layout

```
pyproject.toml              # entry point: glendale-gis-mcp
snapshot.lock.json          # points to the GitHub Release asset
scripts/build_snapshot.py
src/glendale_gis/
  core/                     # plain Python, no MCP imports — importable as a library
    config.py               # env-var settings with defaults
    catalog.py              # dataset registry: id, source, url, category, field docs, snapshot/live
    snapshot.py             # download + verify, load GeoJSON, STRtree, point/nearest queries
    hazards.py              # one function per hazard tool, plus hazards_at_location
    resources.py            # nearest_resources
    arcgis.py               # httpx client: allowlist, throttle, backoff, User-Agent
    geo.py                  # local-meter projection, buffers, distances, Esri JSON -> shapely
    geocode.py              # city geocoder + cache
    query.py                # structured filters: in memory for the snapshot, safe where clauses for live data
    datasets.py             # list_datasets, describe_dataset
    docs.py                 # reads the packaged user docs served as resources
    cache.py                # cache interface (SQLite locally)
    models.py               # Pydantic outputs, Location input, _meta envelope
  server.py                 # MCP tools; thin wrapper over core
  __main__.py               # glendale-gis-mcp [--http --host --port]
tests/fixtures/             # recorded ArcGIS responses
```

## Hosting

**Google Cloud Run**, deployed from source with `gcloud run deploy --source .` (buildpacks, no Dockerfile).

- **Stateless** streamable HTTP, so instances are interchangeable.
- Load the snapshot into memory at startup. Keep **min instances = 1 during the event** to avoid slow cold starts; scale to zero otherwise.
- All config from environment variables, with local-friendly defaults (port, cache location, User-Agent contact, rate limits, API key).
- Hosted protections: per-client rate limiting, request size and result limits, CORS settings, `/health` endpoint, and a capped `--max-instances` so abuse has a fixed cost ceiling.
- **Access control: one shared hackathon secret** (`GLENDALE_GIS_API_KEY`), distributed to participants through the event channel.
  - Required whenever `--http` binds to anything other than localhost: the server refuses to start without it. Not used for stdio or `--http` on `127.0.0.1`.
  - Accepted **only** as `Authorization: Bearer <key>`, never in a query string. Compare with `hmac.compare_digest`. Missing or wrong key → `401` with a short message, no detail.
  - `/health` stays unauthenticated.
  - Store it in Secret Manager and expose it to Cloud Run as an env var. Generate with `secrets.token_urlsafe(32)`. Never commit it; example configs use a `<HACKATHON_KEY>` placeholder.
  - Implement the check to accept a *set* of keys (comma-separated), so rotation with overlap or per-team keys later is a config change, not a code change.
  - Rotate or remove it after the event.
  - Plain ASGI middleware, not the SDK's OAuth path (`AuthSettings`/`TokenVerifier`): that advertises OAuth metadata and would send OAuth-capable clients into a login flow that goes nowhere. OAuth 2.1 is a later option only if teams need clients that can't send custom headers (e.g. Claude.ai/Desktop connectors); those teams use the local stdio install.
  - Check the `Origin` header on HTTP requests (DNS-rebinding protection, required by the MCP spec).
- Outbound throttling is shared across all users of an instance; the geocoder cache protects the city server.
- **Never log addresses, coordinates or the API key.** `httpx` logs every request URL at INFO, and geocoder URLs contain the address, so `server.quiet_http_logs()` keeps it at WARNING. A test fails if an address reaches the logs.

## Design rules

- **Return the data faithfully.** Pass attributes through with their source; add field descriptions, not conclusions. Explain easy-to-misread values in docstrings and `describe_dataset` (e.g. FEMA Zone D means "not studied", not "safe"; CGS zone layers signal hazard by being inside the polygon, not by any attribute).
- **Curated catalog, flexible access.** Only catalog datasets are exposed, but queries on them are flexible because teams will ask questions we can't predict.
- **Docstrings are the interface.** Say what each tool is for and when to use a different one.
- **Typed outputs** with Pydantic models, and `readOnlyHint` annotations on every tool (all tools are read-only).
- **Errors are instructions.** Return a clear message with suggestions for what to try next, never a stack trace.
- **`_meta` on every response:** `source` (agency), `url`, `cached`, `as_of` (snapshot date or fetch time), `stale`.
- **Carry source disclaimers.** Glendale's GIS data is not a substitute for legal descriptions or surveys; hazard zone maps are regulatory maps, not site-specific assessments.
- **Warn about misreadings in four places, kept in step:** the server `INSTRUCTIONS` in `server.py` (sent to every client on connect), the catalog descriptions and field docs (shown by `describe_dataset`), the result `notes` in `core/hazards.py`, and `docs/snapshot-data.md`. For example, CAL FIRE `NonWildland` means unzoned, not safe from wildfire; FEMA Zone D means not studied; an empty debris-flow result doesn't mean no risk. Keep `INSTRUCTIONS` short, since clients load it into every conversation.

## Security

- **Allowlist hosts and service paths** for all queries. Never let a tool parameter supply a URL. The allowlist is built from `core/catalog.py` (`check_allowed` in `core/arcgis.py`); to allow a new source, add it to the catalog — never loosen the check. What it permits:
  - Each catalog layer URL, plus its `/query` operation
  - The city geocoder, plus `findAddressCandidates`, `reverseGeocode` and `suggest`
  - The dam inundation ArcGIS Online item lookup, and any FeatureServer/MapServer layer (plus `/query`) under the DWR org prefix `services.arcgis.com/aa38u6OgfNoCkTJ6/arcgis/rest/services/`, because that service URL changes between releases
  - HTTPS only, default port, no credentials, no query string or fragment in the URL, no `..` or percent-encoded paths; redirects are not followed
- Snapshot downloads come only from this repo's GitHub Releases, and must match the SHA-256 in `snapshot.lock.json`.
- **Exclude `SampleWorldCities`** (Esri demo service at the Glendale server root).
- Cap `limit` on the server side, and leave geometry out of responses unless the caller asks for it.
- Validate identifiers and `where`-clause inputs; do not interpolate raw caller strings into queries.

## ArcGIS gotchas

- Coordinate systems differ by source: 3857 (city), 2229 (city geocoder), 3310 (CAL FIRE, CGS, DWR), 4269 (FEMA). Always pass `inSR=4326` and `outSR=4326`.
- `maxRecordCount` differs per service (1000–5000 seen). Read it from layer metadata; never hardcode it.
- **Queries silently truncate at `maxRecordCount`.** Results come back with `exceededTransferLimit: true` and no error. Always check it and page with `resultOffset` + `orderByFields=OBJECTID`.
- **Offset paging with a spatial filter is unreliable on ArcGIS Server:** the city streets layer returned 8,031 rows for 8,026 features (pages overlapped). For spatial queries, page by object ID instead (`returnIdsOnly`, then `objectIds` chunks). `ArcGISClient.query` does this automatically.
- **Dam inundation polygons are raster-derived:** thousands of rings per feature, about 2.3 million vertices in the buffer area, 23.6 MB of the 35 MB snapshot. Shells often share edges, so they must be unioned to be valid. An island can sit inside a hole, so assign each hole to the smallest shell that covers the whole ring, not just an interior point (`core/geo.py`).
- Hazard and city layers have **no coded-value domains** except zoning `ZONENUM` and `GENPLAN`. Field meanings are documented in `Plans/hazard-sources.md` and `Plans/city-sources.md`; mark inferred meanings as inferred in field docs.
- Sentinel and messy values: FEMA uses `-9999` for "no value"; USGS legend strings have inconsistent spacing and field-name casing; fire station `sta_no` has a leading space; bus `Route` is a comma-separated string.
- City layers have **no `GlobalID` and no `editingInfo`** — `ref.global_id` is null and freshness needs a fallback.
- Hazard polygons can be huge (FHSZ zones are dissolved citywide shapes). For live queries, use point queries with `returnGeometry=false`.
- CGS zone services on `gis.conservation.ca.gov` require a token; use the public ArcGIS Online copies.
- The DWR dam inundation service name contains a snapshot date and will likely change. Resolve the URL at build time from ArcGIS Online item ID `5354d98898194a4ab7b96eb6c85eecae` (layer 100), with an owner/tag search as fallback.
- For the city server, prefer FeatureServer over MapServer when both exist, but don't assume they are configured the same way.
- **City geocoder:** it matches addresses outside Glendale (Burbank, unincorporated La Crescenta) with scores up to 100, and its `City` field isn't reliable. **Always check the point against the city boundary.** It ignores unit numbers, has no place-name search, often returns PointAddress + StreetAddress duplicates, and its `X`/`Y`/`DisplayX`/`DisplayY` attributes stay in feet (2229) even with `outSR=4326` — read coordinates from `candidate.location`. Details and proposed matching rules in `Plans/city-sources.md`.

## Being a good neighbor

No source publishes rate limits for our use, so throttle on our side:

- 1–2 concurrent requests per host, single-digit requests per second.
- Exponential backoff on 429, 503, and timeouts. ArcGIS Online can return 429; FEMA is slow — use generous timeouts.
- Send a descriptive `User-Agent` with the project name and a contact. Default contact: `ryan@hacker.fund` (e.g. `GlendaleGisMcp/<version> (ryan@hacker.fund)`), overridable in config.
- Cache live results on disk (stdio servers restart often). When a fetch fails, serve stale data and mark it `stale: true`.
- Tests must not hit live servers by default. Use recorded fixtures.

## Conventions

- Keep the context window in mind: summarize, paginate, and state what was left out rather than returning large GeoJSON payloads.
- Keep behavior deterministic and predictable.
