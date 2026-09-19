# Implementation Plan

*Written September 16, 2026. Rules and design constraints live in `AGENTS.md`; this file is the order of work.*

Each phase ends with something runnable and tested. Phases 0–5 give a working local server; 6–8 make it distributable and hosted.

---

## Phase 0 — Close the remaining unknowns ✅ Done (September 16, 2026)

Short scripted checks. Results: checks 2–4 and hazard coded values in `Plans/hazard-sources.md`; checks 1, 5 and city coded values in `Plans/city-sources.md`.

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

## Phase 1 — Project skeleton ✅ Done (September 16, 2026)

- `pyproject.toml` (hatchling build), `src/glendale_gis/` layout, `requires-python = ">=3.10"`.
- Runtime dependencies: `mcp`, `httpx`, `shapely>=2`, `pydantic>=2`, `platformdirs`.
- Dev dependencies: `pytest`, `pytest-asyncio`, `respx` (mocks httpx), `ruff`.
- Console scripts: `glendale-gis-mcp` (server).
- `core/config.py`: settings read from environment variables with defaults: city buffer (100 m), hazard buffer (2 km), cache dir, User-Agent contact (default `ryan@hacker.fund`), throttle limits, HTTP host/port, API key, snapshot path override.
- CI-free for now; a `Makefile` or documented commands for test and lint.
- Add the test and lint commands to `AGENTS.md`.

**Done when:** `uv pip install -e ".[dev]"` works on 3.10 and 3.13, `pytest` runs (empty), `glendale-gis-mcp --help` prints.

**Result:**
- Installed with `pip install -e ".[dev]"` on Python 3.13; 14 tests pass; `ruff check` and `ruff format --check` are clean.
- `glendale-gis-mcp --help` and `--version` work, and a real MCP client completed a stdio handshake (server `glendale-gis 0.1.0`, protocol `2025-11-25`, no tools yet).
- **Python 3.10 was not run** at first because it wasn't installed on the dev machine. *Update (September 18, 2026): all 323 tests pass on Python 3.10.21, run with uv.* Checked instead: every runtime dependency resolves to wheels for Python 3.10 on macOS arm64, Windows x64 and Linux x86_64, and ruff targets `py310`. Run the tests on a real 3.10 before release (e.g. `uv run --python 3.10 pytest` or CI).
- No Makefile; commands are documented in `AGENTS.md`.
- `mcp` 2.x renamed `FastMCP` to `MCPServer`, so the server uses `mcp.server.mcpserver.MCPServer`.
- **Open question for Phase 2:** `mcp` 2.x depends on `httpx2` (pydantic's fork of httpx), so installing plain `httpx` adds a second HTTP client. `respx` only mocks plain `httpx`. Decide whether the ArcGIS client uses `httpx` + `respx` (as planned) or `httpx2` with a different mocking approach.

---

## Phase 2 — Catalog and ArcGIS client ✅ Done (September 16, 2026)

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
| `city_boundary` | `Common/Glendale_City_Boundary/MapServer/0` | snapshot |
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

**Result:**
- `core/catalog.py`: 21 datasets (7 hazard, 14 city including live-only parcels) with field docs, coded values (inferred ones flagged), class fields for nearest-zone grouping, resource name/address fields, disclaimers and doc links; plus the geocoder service definition.
- `core/arcgis.py`: allowlist derived from the catalog, per-host concurrency + minimum spacing, exponential backoff with jitter (honors `Retry-After`) on 429/5xx/timeouts including ArcGIS error bodies sent with HTTP 200, GET switching to POST for long requests, no redirects, `resultOffset` paging keyed on `exceededTransferLimit` with an object-ID chunking fallback, `max_features`, and ArcGIS Online item URL resolution.
- 66 tests pass (52 new); ruff clean.
- **Live check (not part of the test suite):** all 21 catalog layers load, every documented field exists in the live schema, object-ID fields match (`FID` for dam inundation), and all layers support pagination. The dam item resolves to the catalog URL. A paged zoning query returned all 2,426 features with unique IDs in 1.2 s. The geocoder call through the client worked.
- **Allowlist trade-off:** because the dam service URL changes between releases, any layer under the DWR ArcGIS Online org is allowed (read-only operations only).

---

## Phase 3 — Snapshot builder ✅ Done (September 18, 2026)

`scripts/build_snapshot.py`:

1. Fetch the city boundary; build the city and hazard buffers from config.
2. For each snapshot dataset: query by the buffered envelope, page, clip to the right buffer with shapely, keep `OBJECTID`/`GlobalID` and catalog fields, round coordinates to 6 decimals.
3. Write `snapshot/<id>.geojson` and `manifest.json` (source, URL, fetched at, source `lastEditDate`, feature count, fields, bytes, buffer used).
4. Print a size table; fail if the total is over 100 MB or a layer expected to have features is empty.
5. Flags: `--only`, `--dry-run` (metadata and counts only), `--out`.

**Distance math decision:** shapely has no reprojection. Instead of adding `pyproj`, convert lon/lat to local meters with an equirectangular projection centered on Glendale (error well under 1% across a 20 km area). This keeps dependencies wheel-only and small. Revisit if accuracy matters more.

**Done when:** a full build succeeds, sizes are recorded in `Plans/`, and the snapshot is known to be under budget.

**Result:**
- `scripts/build_snapshot.py` builds 20 layers in about 80 seconds. It writes GeoJSON files and `manifest.json` to a staging directory and only moves them into `snapshot/` (gitignored) if every layer succeeds and the total is under budget. Each manifest entry has the source URL, fetch time, source last-edit date, feature counts before and after clipping, ID and GlobalID fields, kept fields with types, clip buffer, bytes and SHA-256. `--only` rebuilds some layers and keeps the rest.
- `core/geo.py`: the equirectangular projection (origin -118.245, 34.193, the center of the city boundary's extent), buffers and distances in meters, coordinate rounding, and Esri JSON to shapely conversion. Phase 4 reuses all of it. Distances are within 0.5% of great-circle distance across the city (tested).
- **Client fix:** with a spatial filter, offset paging on the city server returned duplicate rows for streets (8,031 rows for 8,026 features). The builder's duplicate-ID check caught it. `ArcGISClient.query` now pages spatial queries by object ID.
- **Validation (live, not in the test suite):** all geometries are valid and object IDs are unique. GlobalIDs are preserved for CGS, FEMA and DWR; CAL FIRE's layer has none. In 91 point checks (8 named places across the city against all 7 hazard layers, plus 35 points sampled inside zones or 25 m outside zone edges), point-in-polygon on the snapshot matched a live point query against the source, with no mismatches.
- 112 tests pass (46 new); ruff clean.

**Snapshot sizes** (city buffer 100 m, hazard buffer 2 km, built September 18, 2026):

| Layer | In envelope | Kept after clip | KB |
|---|---|---|---|
| `dwr_dam_inundation` | 26 | 19 | 23,618 |
| `zoning` | 2,426 | 2,426 | 4,707 |
| `streets` | 8,026 | 6,868 | 3,137 |
| `cgs_landslide_zones` | 1,203 | 680 | 1,902 |
| `fema_flood_zones` | 75 | 47 | 387 |
| `zip_codes` | 24 | 24 | 383 |
| `calfire_fhsz_lra` | 18 | 13 | 289 |
| `parks` | 43 | 43 | 185 |
| `cgs_liquefaction_zones` | 54 | 27 | 168 |
| `city_boundary` | 1 | 1 (not clipped) | 66 |
| `bus_stops` | 331 | 286 | 59 |
| `neighborhood_zones` | 37 | 37 | 46 |
| `fire_station_districts` | 10 | 10 | 23 |
| `cgs_fault_zones` | 6 | 5 | 11 |
| `schools`, `fire_stations`, `libraries`, `police_stations`, `hospitals` | 27, 9, 8, 1, 3 | all | 11 total |
| `usgs_debris_flow` | 1 | 0 | 0 |
| **Total** | | | **34,991 (35.0 of 100 MB)** |

Notes:
- **Dam inundation is two-thirds of the snapshot.** The polygons are raster-derived (about 2.3 million source vertices). The size fits the budget, so the geometry is kept as published. If size or load time becomes a problem, simplify it to about 1 m, which is well below the maps' accuracy.
- **Bus stops:** 52 of the 338 Beeline stops are more than 100 m outside the city and are dropped. **Streets:** 1,202 segments outside the city buffer are dropped.
- **Debris flow:** one USGS basin now falls inside the query envelope but outside the 2 km clip area (Phase 0 found none). The layer is allowed to be empty.
- Build time is mostly CPU for dam inundation geometry (about 50 s).

---

## Phase 4 — Core lookups (offline) ✅ Done (September 18, 2026)

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

**Result:**
- `core/snapshot.py` loads all 20 layers in about 0.8 s. Geometries are held in local meters, so distances come straight from shapely. Each file is checked against its SHA-256 in the manifest. A missing or corrupt layer is marked unavailable, with a reason, instead of stopping the server. Point-in-zone and nearest-feature queries use STRtrees, including one per class, and take under 2 ms on the largest layer. Ties go to the lowest object ID, so results are deterministic.
- `core/hazards.py`: `locate` (coordinates only; the geocoder comes in Phase 5), `wildfire_zone`, `flood_zone`, `seismic_zones`, `dam_inundation`, `debris_flow`, `hazards_at_location`.
- `core/resources.py`: `nearest_resources` over the 7 resource layers. The limit is clamped to 1–25 (default 3). Unknown kinds raise a clear error.
- `core/models.py`: `Location`, `ResolvedLocation`, `Ref`, `Meta` (serialized as `_meta`), `Feature`, `Nearest`, `HazardResult`, `SeismicZones`, `HazardsAtLocation`, `Resource`, `ResourceGroup`, `NearestResources`, `ToolError`.
- **Decisions made here:**
  - **Coverage:** a point outside the area the snapshot covers for a layer is `unavailable`, not `not_in_zone`. That area is the city plus 2 km for hazard layers.
  - **`exact` flag:** a nearest distance farther than the edge of that area has `exact: false`, because a closer zone outside the snapshot can't be ruled out.
  - **Unzoned classes:** CAL FIRE `NonWildland` is defined by the source as "Unzoned", so it doesn't count toward `in_zone`. The feature is still returned in `matches`, with a note. It is recorded as `unzoned_classes` in the catalog. Without this rule, every Glendale location would be `in_zone` for wildfire. Every FEMA flood zone, including X, counts as a zone; `FLD_ZONE`, `ZONE_SUBTY` and `SFHA_TF` tell them apart.
  - **Notes** are added only where a value is easy to misread: CGS zones, FEMA Zone D, several dam scenarios at one point, and an empty debris-flow result.
- **Tests:** 55 new, 167 in total. Most use a hand-made fixture snapshot with known answers. There are also golden checks against the real snapshot at 6 reference points: northern foothills, unincorporated La Crescenta, Verdugo hills, Chevy Chase Canyon, downtown, and the LA River channel. They are skipped when no snapshot is built. The new foothill point was confirmed with live queries on all 6 layers checked. The plan's original La Crescenta point turned out to be outside the city, in unincorporated La Crescenta. It is kept as a test of a covered point outside Glendale.

---

## Phase 5 — Geocoder, generic tools and MCP server (stdio) ✅ Done except the client checks (September 18, 2026)

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
- `MCPServer` (mcp 2.x) tools wrapping `core`, with docstrings written for agents, `readOnlyHint` on every tool, and errors that suggest next steps.
- Hazard tool docstrings say the data is mapped hazard, not current conditions, and point to `docs/real-time-sources.md` for live alerts, fires and evacuations. Link to it by its GitHub URL once the repo is public: agents installed with `uvx` can't open a repo-relative path.
- Entry point runs stdio by default.

**Done when:** the server works in MCP Inspector and one real client (Claude Desktop or Claude Code), and an agent picks the right tool for a set of sample questions ("is 123 Brand Blvd in a flood zone?", "nearest fire station to …", "what zoning is at …").

**Result:**
- **11 tools:** `list_datasets`, `describe_dataset`, `query_dataset`, `geocode_address`, `hazards_at_location`, `wildfire_zone`, `flood_zone`, `seismic_zones`, `dam_inundation`, `debris_flow`, `nearest_resources`.
  - All are marked read-only, and all have output schemas.
  - Every tool that takes a location accepts `{"address"}` or `{"lat", "lon"}`.
  - Results are returned as compact JSON text plus structured content, with `_meta` on every response.
  - Errors the caller can fix are `is_error` results with suggestions, and with candidates when an address is ambiguous.
- **`core/geocode.py`** applies the Phase 0 rules:
  - It checks each candidate against the city boundary, with 25 m tolerance.
  - It collapses PointAddress/StreetAddress duplicates within 50 m.
  - A confident match is a single candidate scoring 90 or more, or a PointAddress at least 5 points ahead of the next.
  - StreetName matches are never confident.
  - Unit numbers get a note saying they were ignored.
  - **Change from Phase 0:** intersections (StreetInt) can be confident, since they're precise points.
  - Results are cached for 30 days (`GLENDALE_GIS_GEOCODE_CACHE_TTL_S`).
  - Tested live against all the Phase 0 addresses, with the expected outcome for each.
- **`core/cache.py`:** a SQLite cache in the cache directory. Keys are hashed, so addresses aren't stored in plain text. If a fetch fails, it serves stale data marked `stale: true`. If the file can't be opened, it falls back to memory.
- **`core/query.py`:** structured filters (`eq ne lt lte gt gte in contains starts_with is_null not_null`), `near` (a radius, or 0 for "contains the point"), `bbox`, `fields`, paging with `next_offset`, and optional geometry.
  - Text comparisons ignore case and surrounding spaces.
  - For live parcels, filters become a `where` clause: field names are checked against the live schema, strings are quoted and escaped, numbers are validated, and LIKE wildcards are refused. Injection attempts are tested.
  - Live results are cached for a day (`GLENDALE_GIS_LIVE_CACHE_TTL_S`).
  - Geometries over 5,000 vertices are left out, with a pointer to `ref.layer_url`.
- **Parcels:** kept, live only. Verified live: 613 E Broadway is City Hall, APN 5642-012-904, use type "Government", zoned "DSP / Civic Center".
- **Privacy fix:** `httpx` logged request URLs, including addresses, at INFO. It's now kept at WARNING. The test for this was confirmed to fail without the fix.
- **Real client:** every tool was called through a stdio MCP client against the real snapshot and the live geocoder.
- **Resources (added after review):** `glendale-gis://about` (what the server does, every tool, how results work), `glendale-gis://datasets` and the template `glendale-gis://datasets/{dataset_id}` (generated from the catalog), plus `glendale-gis://docs/real-time-sources`, `glendale-gis://docs/snapshot-data` and `glendale-gis://snapshot/manifest`. The docs are packaged into the wheel; verified by installing a built wheel outside the repo. The instructions and tool descriptions now point to these URIs instead of a repo path. No prompts yet.
- **`read_guide` tool (added after testing in Claude Desktop):** Desktop's log showed it called `resources/list` but never `resources/read`, and never listed templates. Resources are controlled by the client, so the model couldn't reach the guides. `read_guide(topic)` returns the same Markdown, and the instructions and docstrings now point to it.
- **Tests:** 120 new, 297 in total.
- **Still to do for "done":**
  - Try it in MCP Inspector and in Claude Desktop or Claude Code.
  - Check that an agent picks the right tool for the sample questions.
  - `hazards_at_location` returns about 11.7 KB (about 3,000 tokens). Most of that is the nearest zone for each flood class; trim it if agents struggle with the size.

---

## Phase 6 — Snapshot distribution ✅ Done (September 18, 2026)

- **Requires a GitHub repo** (see open questions).
- `build_snapshot.py --publish`: zip the snapshot, compute SHA-256, create a GitHub Release (`gh release create snapshot-YYYYMMDD`), upload the asset, rewrite `snapshot.lock.json`.
- Runtime: if no local snapshot, download the asset from the lock file URL into the `platformdirs` cache dir, verify SHA-256, unzip, load. Config can point to a local snapshot path for development.
- No network or bad checksum: log it, run with live fallbacks, and mark results `stale` or `unavailable`.

**Done when:** `uvx --from git+https://github.com/HackerFund/GlendaleGisMcp glendale-gis-mcp` on a clean machine downloads the snapshot and answers a hazard query.

**Result:**
- **Published** [`snapshot-20260918`](https://github.com/HackerFund/GlendaleGisMcp/releases/tag/snapshot-20260918): 5.9 MB zipped (35 MB unpacked). `snapshot.lock.json` is committed and shipped inside the wheel.
- **`build_snapshot.py --publish`:**
  - Zips only the manifest and the files it lists, deterministically (fixed timestamps, sorted order).
  - Refuses to overwrite an existing release; use `--tag` for a second one the same day.
  - Release notes list each layer with its source, feature count and last-edit date.
  - Rewrites the lock only after the upload succeeds.
- **`core/distribution.py` at startup:**
  - `GLENDALE_GIS_SNAPSHOT_PATH` wins. Otherwise the server uses a verified cached copy, or downloads the release named in the lock.
  - Downloads only from this repo's releases; redirects are allowed only to GitHub's asset hosts.
  - Checks size and SHA-256, and refuses absolute paths, `..`, symlinks and archives over 500 MB.
  - Unpacks into a temporary folder and renames it into place.
  - Keeps the current snapshot plus one older one.
- **`glendale-gis-mcp --fetch-snapshot`** downloads and verifies the snapshot, then exits. Use it to pre-warm the cache, and in Phase 7 for the Cloud Run build.
- **Live check:** a download into an empty cache took 1.1 s through GitHub's redirect. The unpacked files are byte-for-byte identical to the local build, and the second run used the cache.
- **Change from the plan: there's no live per-point fallback.** If the download fails, the server uses the older cached snapshot, and results say `_meta.stale: true`. With no copy at all, it starts anyway, and tools explain that the snapshot is unavailable. A live fallback would only return zone membership, not nearest zones; it would need network access that just failed; and it would add load on the agency servers. So it isn't worth it before the event.
- **Tests:** 26 new, 323 in total.
- **Done-when check passed (September 18, 2026), from the pushed repo with empty caches:**
  - `uvx --from git+https://github.com/HackerFund/GlendaleGisMcp glendale-gis-mcp --fetch-snapshot` downloaded and verified the snapshot in 4.3 s. The server then ran under `uvx` over stdio and answered `flood_zone` for 613 E Broadway (zone X) and `read_guide`.
  - The README's pip route worked too: venv, `pip install git+https://…`, `--fetch-snapshot`, and the forced-reinstall update. `nearest_resources` for 1000 W Glenoaks gave Fire Station 27, 1,436 m away.

---

## Phase 7 — HTTP transport and Cloud Run

- `--http` flag: stateless streamable HTTP, host/port from config.
- `/health` endpoint (reports snapshot version and load state).
- Per-client rate limiting, request size and result limits, CORS settings, `Origin` check.
- Shared bearer secret required when binding to a non-localhost host (see AGENTS.md → Hosting → Access control).
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

1. ~~**GitHub repo:** owner, name and visibility.~~ Answered: [HackerFund/GlendaleGisMcp](https://github.com/HackerFund/GlendaleGisMcp), public. Needed for `uvx --from git+…` and Release assets (Phase 6). There is no remote yet.
2. ~~**Hackathon date:** sets how much of Phases 7–8 must be done and when.~~ Answered: **Jewel City Hacks 5, Saturday, September 26, 2026**, 8 AM–8 PM at Glendale Community College ([event](https://hacker.fund/jewelcityhacks), [Luma](https://luma.com/jewelcityhacks5)). The theme is "Think Globally, Build Locally". Phases 6–8 should be done, and the hosted server up with min instances 1, before then.
3. **GCP project and region** for Cloud Run.
4. ~~**API key for the hosted instance:** open to everyone, or key shared with registered teams?~~ Decided: one shared hackathon secret, required when deployed.
5. **Parcels:** keep as live-only or cut entirely (decide by Phase 5).
