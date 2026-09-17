# AGENTS.md

Guidance for coding agents working in this repository.

## Project

An MCP server that gives hackathon teams, and their agents, easy access to the geospatial data behind **Glendale, California's emergency preparedness challenge**: helping residents understand the hazards where they live and how to prepare.

**This server provides data access, not a finished solution.** It finds, queries and normalizes authoritative data. It does not interpret risk, rank hazards, give preparedness advice or build resident-facing features. Teams do that with the data.

Background:
- `Plans/glendale-gis-mcp-notes.md` — original research: MCP design practices, Glendale's ArcGIS inventory, caching and rate-limit reasoning. Written before the scope narrowed, so its tool ideas (street sweeping, etc.) are not all in scope.
- `Plans/hazard-sources.md` — verified state and federal hazard endpoints, with per-layer metadata and gotchas.
- `Plans/implementation-plan.md` — phased order of work, with "done when" criteria and open questions.

## Status

Early stage — no application code yet. Hazard endpoints and city layer counts have been checked. Next: Phase 0 of `Plans/implementation-plan.md`. Update this section as phases complete.

## Environment

- Develop on Python 3.13, virtualenv at `.venv` (`python3.13 -m venv .venv`). Activate with `source .venv/bin/activate`. Don't use an older system Python; the package needs 3.10 or later.
- The package targets **Python 3.10+** (`requires-python = ">=3.10"`, the MCP SDK minimum), because teams will have mixed versions. Don't use 3.11+ only features (e.g. `tomllib`, `ExceptionGroup`, `typing.Self`).
- Build, test, and lint commands are not set up yet — add them here when they are.

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
- Live emergency alerts or evacuation orders
- Other Glendale layers: water pressure zones, historic districts and parcels, street sweeping, pavement condition, truck routes, capital projects, hauler locator
- Glendale, **AZ** (`glendaleaz-cog-gis.hub.arcgis.com`) — a different city
- Docker

## Tools

**Generic access**
- `list_datasets` — the catalog: id, source agency, category, description, snapshot or live
- `describe_dataset` — fields, meanings of coded values, record count, freshness
- `query_dataset` — attribute and spatial queries with capped limits and optional geometry. Filters are **structured** (`field`, `op`, `value`), never raw SQL, so the same query runs on the local snapshot and translates safely to a `where` clause for live datasets.
- `geocode_address` — the city geocoder; returns possible matches with scores

**Hazards at a location**
- `wildfire_zone`, `flood_zone`, `seismic_zones` (fault, liquefaction, landslide), `dam_inundation`, `debris_flow`
- `hazards_at_location` — all of the above in one call

**Resources near a location**
- `nearest_resources(location, kinds, limit)` — fire stations, police, hospitals, schools, libraries, parks, bus stops. Distances are straight-line meters, not travel distance, and only cover resources inside Glendale; docstrings must say both.

### Shared behavior

- **`location` is an address or a latitude/longitude**, the same way in every tool. Coordinates skip geocoding.
- Responses include the matched address, coordinates and geocoder score.
- **Ambiguous addresses** return candidates instead of guessing. **Addresses outside Glendale** return a clear error.
- Every hazard result has a `status` of `in_zone`, `not_in_zone` or `unavailable` (with a reason). Never let a missing source look like "not in zone".
- **Nearest zone:** every hazard result includes the nearest zone with `distance_m` (0 when inside) and a `ref` identifying the feature. For classed layers (wildfire, flood), return the nearest zone for each class.
- `ref` = `{dataset, object_id, global_id, layer_url}`, so teams can fetch the full live record. Resource results carry the same `ref`.

```json
{
  "status": "not_in_zone",
  "nearest": {
    "distance_m": 240,
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
- Supports `--only <dataset,...>`, `--dry-run`, and `--publish`.

### Distribution

- The snapshot is published as a **GitHub Release asset**, not committed to the repo.
- The repo commits only `snapshot.lock.json`: version, download URL, SHA-256, size. `--publish` uploads the asset and rewrites this file.
- On first run the package downloads the snapshot to the user cache directory, verifies the checksum, and reuses it afterwards.
- If the download fails or there's no network, fall back to live queries and mark results `stale` or `unavailable`. Never crash.

## Tech stack

- Official `mcp` SDK (FastMCP), `httpx`, `shapely` 2 (STRtree for spatial lookups), `pydantic`, `platformdirs` (cache location). SQLite (stdlib) for the disk cache.
- Dev: `pytest`, `pytest-asyncio`, `respx` (httpx mocking), `ruff`.
- **No `pyproj`.** Distances use a local equirectangular projection centered on Glendale (error well under 1% across the city). Revisit only if accuracy needs change.
- All tunable values (buffers, throttle limits, cache dir, User-Agent contact, host/port, API key, snapshot path) live in `core/config.py`, read from environment variables with defaults.
- **No GDAL, GeoPandas, PostGIS or Docker.** Every dependency must install from wheels on macOS, Windows and Linux.
- Install for teams: `uvx --from git+<repo> glendale-gis-mcp` (primary), `pip install git+<repo>` (fallback).
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
    arcgis.py               # httpx client: allowlist, throttle, backoff, User-Agent
    geocode.py              # city geocoder + cache
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
- Hosted protections: per-client rate limiting, request size and result limits, optional API key (off by default), CORS settings, `/health` endpoint.
- Outbound throttling is shared across all users of an instance; the geocoder cache protects the city server.
- **Never log addresses or coordinates.**

## Design rules

- **Return the data faithfully.** Pass attributes through with their source; add field descriptions, not conclusions. Explain easy-to-misread values in docstrings and `describe_dataset` (e.g. FEMA Zone D means "not studied", not "safe"; CGS zone layers signal hazard by being inside the polygon, not by any attribute).
- **Curated catalog, flexible access.** Only catalog datasets are exposed, but queries on them are flexible because teams will ask questions we can't predict.
- **Docstrings are the interface.** Say what each tool is for and when to use a different one.
- **Typed outputs** with Pydantic models, and `readOnlyHint` annotations on every tool (all tools are read-only).
- **Errors are instructions.** Return a clear message with suggestions for what to try next, never a stack trace.
- **`_meta` on every response:** `source` (agency), `url`, `cached`, `as_of` (snapshot date or fetch time), `stale`.
- **Carry source disclaimers.** Glendale's GIS data is not a substitute for legal descriptions or surveys; hazard zone maps are regulatory maps, not site-specific assessments.

## Security

- **Allowlist hosts and service paths** for all queries. Never let a tool parameter supply a URL. Allowed hosts:
  - `gismap.glendaleca.gov`, `gisapps.glendaleca.gov`
  - `services1.arcgis.com`, `services2.arcgis.com`, `services.arcgis.com` — only the catalog's service paths
  - `hazards.fema.gov`
  - `earthquake.usgs.gov`
- Snapshot downloads come only from this repo's GitHub Releases, and must match the SHA-256 in `snapshot.lock.json`.
- **Exclude `SampleWorldCities`** (Esri demo service at the Glendale server root).
- Cap `limit` on the server side, and leave geometry out of responses unless the caller asks for it.
- Validate identifiers and `where`-clause inputs; do not interpolate raw caller strings into queries.

## ArcGIS gotchas

- Coordinate systems differ by source: 3857 (city), 3310 (CAL FIRE, CGS, DWR), 4269 (FEMA). Always pass `inSR=4326` and `outSR=4326`.
- `maxRecordCount` differs per service (1000–5000 seen). Read it from layer metadata; never hardcode it.
- Hazard polygons can be huge (FHSZ zones are dissolved citywide shapes). For live queries, use point queries with `returnGeometry=false`.
- CGS zone services on `gis.conservation.ca.gov` require a token; use the public ArcGIS Online copies.
- The DWR dam inundation service name contains a snapshot date and will likely change. Resolve it at snapshot build time; don't hardcode it permanently.
- For the city server, prefer FeatureServer over MapServer when both exist, but don't assume they are configured the same way.
- Not yet checked: what the city geocoder returns (point type, score range, unit numbers).

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
