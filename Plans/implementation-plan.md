# Implementation Plan

*Written September 16, 2026. Rules and design constraints live in `AGENTS.md`; this file is the order of work.*

Each phase ends with something runnable and tested. Phases 0–5 give a working local server; 6–8 make it distributable and hosted.

---

## Phase 0 — Close the remaining unknowns

Short scripted checks, results appended to `Plans/hazard-sources.md` (or a new `Plans/city-sources.md`).

| Check | Why it matters |
|---|---|
| **City geocoder:** point type (rooftop / site vs street interpolation), score range, candidate count, unit numbers ("Apt 4"), misspellings, and what an address outside Glendale returns | Shapes `geocode_address`, ambiguity handling and the out-of-city error |
| **USGS debris-flow schema** (layers 2, 4, 6, 9), using a recent fire elsewhere as a sample | No Glendale features exist to inspect |
| **FEMA paging:** does layer 28 support `resultOffset`, and how slow is it? | Snapshot build reliability |
| **Dam inundation lookup:** find the item by searching ArcGIS Online (title + DWR owner) instead of a fixed URL | The URL includes a snapshot date and will change |
| **FireStations geometry** is `Multipoint`; confirm each feature is really one station | Nearest-resource distances |
| **Coded values:** domains and codes for zoning `Type`, FHSZ `FHSZ` (e.g. -3), FEMA `STUDY_TYP` | Field docs in the catalog |

**Done when:** each check has a written answer.

---

## Phase 1 — Project skeleton

- `pyproject.toml` (hatchling build), `src/glendale_gis/` layout, `requires-python = ">=3.10"`.
- Runtime dependencies: `mcp`, `httpx`, `shapely>=2`, `pydantic>=2`, `platformdirs`.
- Dev dependencies: `pytest`, `pytest-asyncio`, `respx` (mocks httpx), `ruff`.
- Console scripts: `glendale-gis-mcp` (server).
- `core/config.py`: settings read from environment variables with defaults: city buffer (100 m), hazard buffer (2 km), cache dir, User-Agent contact (default `ryan@hacker.fund`), throttle limits, HTTP host/port, API key, snapshot path override.
- CI-free for now; a `Makefile` or documented commands for test and lint.
- Add the test and lint commands to `AGENTS.md`.

**Done when:** `uv pip install -e ".[dev]"` works on 3.10 and 3.13, `pytest` runs (empty), `glendale-gis-mcp --help` prints.

---

## Phase 2 — Catalog and ArcGIS client

### `core/catalog.py`

One entry per dataset: `id`, `title`, `category` (hazard / resource / reference), `source_agency`, `layer_url`, `access` (snapshot / live), `buffer` (city / hazard), fields to keep, field descriptions, coded value meanings, disclaimer.

| id | Layer | Access |
|---|---|---|
| `calfire_fhsz_lra` | CAL FIRE FHSZ LRA 2025 | snapshot |
| `cgs_fault_zones` | CGS Alquist-Priolo | snapshot |
| `cgs_liquefaction_zones` | CGS liquefaction | snapshot |
| `cgs_landslide_zones` | CGS landslide | snapshot |
| `fema_flood_zones` | FEMA NFHL layer 28 | snapshot |
| `dwr_dam_inundation` | DSOD inundation areas | snapshot |
| `usgs_debris_flow` | USGS post-fire basins | snapshot |
| `city_boundary` | `Common/Glendale_City_Boundary` | snapshot |
| `fire_stations`, `fire_station_districts`, `police_stations`, `hospitals`, `schools`, `libraries`, `parks`, `bus_stops` | `Common/*` | snapshot |
| `zip_codes`, `neighborhood_zones`, `zoning`, `streets` | `Common/*` | snapshot |
| `parcels` | `Common/Zoning/FeatureServer/1` | live (may be cut) |

### `core/arcgis.py`

- The host and service-path allowlist is derived from the catalog. Anything else is rejected before a request is made.
- Per-host concurrency limit (default 2) and a minimum delay between requests.
- Exponential backoff with jitter on 429, 503 and timeouts; generous FEMA timeout.
- Descriptive User-Agent from config.
- Helpers: `layer_metadata`, `query` (with automatic paging by `maxRecordCount`), `count`.
- Always sends `inSR`/`outSR=4326`.

**Done when:** unit tests with `respx` cover allowlist rejection, paging, and retry/backoff.

---

## Phase 3 — Snapshot builder

`scripts/build_snapshot.py`:

1. Fetch the city boundary; build the city and hazard buffers from config.
2. For each snapshot dataset: query by the buffered envelope, page, clip to the right buffer with shapely, keep `OBJECTID`/`GlobalID` and catalog fields, round coordinates to 6 decimals.
3. Write `snapshot/<id>.geojson` and `manifest.json` (source, URL, fetched at, source `lastEditDate`, feature count, fields, bytes, buffer used).
4. Print a size table; fail if the total is over 100 MB or a layer expected to have features is empty.
5. Flags: `--only`, `--dry-run` (metadata and counts only), `--out`.

**Distance math decision:** shapely has no reprojection. Instead of adding `pyproj`, convert lon/lat to local meters with an equirectangular projection centered on Glendale (error well under 1% across a 20 km area). This keeps dependencies wheel-only and small. Revisit if accuracy matters more.

**Done when:** a full build succeeds, sizes are recorded in `Plans/`, and the snapshot is known to be under budget.

---

## Phase 4 — Core lookups (offline)

### `core/snapshot.py`
- Load the manifest and GeoJSON; build a shapely `STRtree` per layer at startup.
- `features_at_point(dataset, point)`
- `nearest(dataset, point, group_by=None)` — distance in meters, nearest per class when `group_by` is set.

### `core/hazards.py`
One function per tool: `wildfire_zone`, `flood_zone`, `seismic_zones`, `dam_inundation`, `debris_flow`, `hazards_at_location`. Each returns `status` (`in_zone` / `not_in_zone` / `unavailable`), matched attributes, `nearest` with `ref`, and `_meta`.

### `core/resources.py`
`nearest_resources(point, kinds, limit)` with distance and `ref`.

### `core/models.py`
Pydantic models: `Location` (address or lat/lon), `Ref`, `Nearest`, `HazardResult`, `ResourceResult`, `Meta`, error model with suggestions.

**Tests:** a tiny hand-made fixture snapshot (a few polygons and points) with known answers, plus golden checks against the real snapshot for 5 reference points (foothill, Verdugo hills, Chevy Chase Canyon, downtown, near a flood channel).

**Done when:** all hazard and resource lookups work from coordinates with no network.

---

## Phase 5 — Geocoder, generic tools and MCP server (stdio)

### `core/geocode.py`
- Calls the city geocoder, returns candidates with scores.
- Rejects results outside the city boundary.
- A single confident match continues; otherwise candidates come back to the caller.
- SQLite cache keyed by normalized address; long TTL.
- Never logs addresses.

### Generic access
- `list_datasets`, `describe_dataset` from the catalog and manifest.
- `query_dataset` with **structured filters** (`field`, `op`, `value`) instead of raw SQL. The same filters run against the snapshot locally and are translated into a safe `where` clause for live datasets (parcels). This avoids SQL injection and the need to parse SQL for local data. Includes bbox or point filters, capped `limit`, `offset`, optional geometry.

### `server.py`
- FastMCP tools wrapping `core`, with docstrings written for agents, `readOnlyHint` on every tool, and errors that suggest next steps.
- Entry point runs stdio by default.

**Done when:** the server works in MCP Inspector and one real client (Claude Desktop or Claude Code), and an agent picks the right tool for a set of sample questions ("is 123 Brand Blvd in a flood zone?", "nearest fire station to …", "what zoning is at …").

---

## Phase 6 — Snapshot distribution

- **Requires a GitHub repo** (see open questions).
- `build_snapshot.py --publish`: zip the snapshot, compute SHA-256, create a GitHub Release (`gh release create snapshot-YYYYMMDD`), upload the asset, rewrite `snapshot.lock.json`.
- Runtime: if no local snapshot, download the asset from the lock file URL into the `platformdirs` cache dir, verify SHA-256, unzip, load. Config can point to a local snapshot path for development.
- No network or bad checksum: log it, run with live fallbacks, and mark results `stale` or `unavailable`.

**Done when:** `uvx --from git+<repo> glendale-gis-mcp` on a clean machine downloads the snapshot and answers a hazard query.

---

## Phase 7 — HTTP transport and Cloud Run

- `--http` flag: stateless streamable HTTP, host/port from config.
- `/health` endpoint (reports snapshot version and load state).
- Per-client rate limiting, request size and result limits, optional API key (off by default), CORS settings.
- Shared outbound throttle across all requests.
- Logging without addresses or coordinates.
- Deploy with `gcloud run deploy --source .`; snapshot downloaded at startup (or bundled at build time if cold start is too slow); min instances 1 during the event.
- Short deploy notes in `Plans/` or the README.

**Done when:** a remote MCP client connects to the Cloud Run URL and runs every tool; a load test with a few hundred requests shows the throttle holding.

---

## Phase 8 — Docs for hackers

- `README.md`: what the server is and isn't, install with `uvx` and pip, config snippets for Claude Desktop, Claude Code, Cursor and VS Code, hosted URL, the dataset list with sources and dates, disclaimers.
- Using `core` as a plain Python library.
- How to rebuild the snapshot.

**Done when:** someone new follows the README to a working query in under 10 minutes.

---

## Open questions

1. **GitHub repo:** owner, name and visibility. Needed for `uvx --from git+…` and Release assets (Phase 6). There is no remote yet.
2. **Hackathon date:** sets how much of Phases 7–8 must be done and when.
3. **GCP project and region** for Cloud Run.
4. **API key for the hosted instance:** open to everyone, or key shared with registered teams?
5. **Parcels:** keep as live-only or cut entirely (decide by Phase 5).
