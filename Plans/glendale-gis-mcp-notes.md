# Building an MCP Server for Glendale, CA GIS — Research Notes

*Compiled September 16, 2026*

---

## 1. MCP Server Design Best Practices

**Core mental model:** You're not writing an API, you're writing a job description for a new hire who can't ask follow-up questions. An LLM discovers what your server can do purely from the descriptions you write.

| # | Practice | Why |
|---|---|---|
| 1 | Name tools after **outcomes**, not endpoints | `escalate_ticket` > `post_issues_v2`. Docstring = schema in FastMCP. |
| 2 | Curate ruthlessly — **5–15 tools** | Every definition lives in the context window permanently. |
| 3 | One server, **one bounded domain** | CRM server and logs server, not a Swiss Army chainsaw. |
| 4 | **Type your outputs** (Pydantic) | Return annotations generate validated output schemas. |
| 5 | **Annotate side effects** | `readOnlyHint`, `destructiveHint`, `idempotentHint` drive client permission prompts. |
| 6 | Errors as **instructions**, not stack traces | "Wrong floor, try the 3rd" — recoverable. |
| 7 | **Pre-digest large data** | Paginate, truncate, say what's missing. Plate the dish; don't hand over the pot. |
| 8 | **Never trust the caller** | Delegated permissions + chained calls = outsized blast radius. Validate, least-privilege, rate-limit. |
| 9 | Be **boring and deterministic** | Consistent behavior is easier for an LLM to model than adaptive logic. |
| 10 | Test with a **real agent** | Wrong-tool selection is a docstring bug, not a code bug. |

```python
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

mcp = FastMCP("support-desk")

class Ticket(BaseModel):
    id: str
    status: str = Field(description="open | pending | closed")

@mcp.tool(annotations={"readOnlyHint": True})
def get_ticket(ticket_id: str) -> Ticket:
    """Fetch a support ticket by ID.

    Use when the customer references a ticket number.
    Do NOT use for billing — use `refund_request` instead.
    """
```

> Recent SDK versions have been renaming `FastMCP` → `MCPServer` in `mcp.server`. Check your installed version.

---

## 2. GIS MCP Landscape

**Primary resource:** Sparkgeo's curated, health-checked registry of geospatial MCP servers. As of the September 2026 check: **57 active, 15 stale, 15 hosted; 40 open-access, 41 commercial.**

| Role | Servers |
|---|---|
| **Toolbox** (spatial math) | `gis-mcp` (92+ tools: Shapely, GeoPandas, PyProj, Rasterio, PySAL); `mcp-geo` (geocoding, routing, OSM, isochrones) |
| **Warehouse** (your data) | `mcp-postgis` (publishes results as QGIS-visible views; ships read-only role recipe + `MCP_POSTGIS_MODE=read_only`); Overture Maps MCP (GeoParquet on S3 via DuckDB, token-budgeted); Wherobots (Apache Sedona) |
| **Telescope** (earth obs.) | `stac-mcp`, `planetary-computer-mcp`, `copernicus-mcp`, `earthdata-mcp`, Earth Engine servers; `chuk-mcp-stac` (20 typed Pydantic v2 tools + COG-header size estimation before download) |
| **Remote control** (desktop) | QGIS MCP (plugin + TCP socket → PyQGIS); ArcGIS Pro MCP (C# add-in, named pipes) |
| **Concierge** (commercial) | Esri MCP for ArcGIS Location Services (beta, hosted); CARTO, Planet, SkyFi, Vexcel |

**Cautions:** ~41 of 93 tracked servers are commercial, 6 paywalled. Geospatial responses are enormous — prefer servers that summarize, paginate, or estimate.

---

## 3. Frameworks for City GIS Portals

**Key insight: city GIS portals are franchises, not independent restaurants.** Nearly all run one of four standard kitchens — ArcGIS Hub / ArcGIS REST FeatureServer, Socrata (SODA), CKAN, or OGC API Features / WFS. The unit of reuse is **one adapter per portal technology, parameterized by base URL** — not one server per city.

### Reference implementations

- **`mcp-arcgis-*` (Pipeworx)** — per-city repos (Seattle, Detroit, Charlotte, Tulsa, Pittsburgh, Albuquerque, Aurora, McKinney, Peoria AZ, San Bernardino, Fontana, LA County). Every one exposes the identical three tools:
  - `search_datasets` — keyword → name, summary, record count, Feature Service URL
  - `query_layer` — SQL-like `where`, `out_fields`, `order_by`, `limit`, `offset`
  - `layer_info` — fields/types, geometry type, record count, capabilities
- **`mcp-canada` (ReyemTech)** — best-architected multi-portal Python example, on FastMCP. `shared/arcgis_hub.py` is explicit reusable infrastructure; `shared/ogc.py` adds WFS 2.0. Also handles **bare ArcGIS Server** (GeoNB) as a distinct shape. Worth stealing: `_meta` envelope (source API, URL, cached flag, timestamp) + errors carrying code, message, and **suggestions**.
- **`colombian-open-data-mcp`** — keeps Socrata and CKAN as separate tool families because identifier formats differ (4x4 codes vs UUIDs/slugs), and identifier validation is the defence against URL injection. Pushes computation to the portal (SoQL, DataStore filters, ArcGIS statistics) so only answers travel.

### Generic hosted adapters
The **datamule** Apify actors: point-at-any OGC API Features service; classic WFS 2.0/1.1 (GetCapabilities, paged GetFeature by bbox/CQL, GeoJSON or GML); any Esri ArcGIS REST layer. Pay-per-feature — good for evaluation, poor as permanent infrastructure.

### Generation shortcuts
`FastMCP.from_openapi()` works, but FastMCP's own docs warn that LLMs perform significantly better with curated servers than auto-converted OpenAPI ones — it's for bootstrapping, not shipping. *Auto-generation gives you a phone book; you wanted a concierge.*

---

## 4. What ArcGIS Is

Esri's family of GIS software — a product line, not one program. Roughly Microsoft Office for maps.

- **ArcGIS Pro** — desktop authoring/analysis
- **ArcGIS Enterprise / ArcGIS Server** — self-hosted publishing (← Glendale CA)
- **ArcGIS Online** — Esri-hosted equivalent
- **ArcGIS Hub** — public open-data storefront with searchable catalog (← Glendale AZ)

The part that matters: the **ArcGIS REST API**. Published layers get predictable URLs and operations (`?f=json` metadata, `/query` with SQL-ish `where`). That uniformity is why one adapter works across hundreds of cities — they all bought the same product.

---

## 5. Glendale, CA — Full GIS Inventory

> Three cities share the name. **Glendale, AZ** runs ArcGIS Hub at `glendaleaz-cog-gis.hub.arcgis.com` (also `opendata.glendaleaz.com`, org code `COG-GIS`) with CSV/KML/Zip/GeoJSON/GeoTIFF/PNG downloads and GeoServices/WMS/WFS API links — the easy case. **Glendale, WI** has little published. Everything below is **Glendale, California**.

### 5.1 Servers

| Host | Role | Notes |
|---|---|---|
| `gismap.glendaleca.gov/arcgis/rest/services` | **Primary** | ArcGIS Server **10.91** |
| `gisapps.glendaleca.gov/arcgis/rest/services` | Secondary | Partially degraded — see 5.5 |

**No ArcGIS Hub. No open-data catalog. No catalog search endpoint.**

### 5.2 Folder structure (`gismap`)

Three folders: **Common**, **PW**, **Utilities**.

#### Common — 16 datasets
Most published as **both** FeatureServer and MapServer.

| Dataset | Notes |
|---|---|
| `Zoning` | See layer detail below |
| `HistoricParcels` | |
| `HistoricDistricts` | |
| `Streets` | Large — needs paging |
| `Parks` | |
| `Libraries` | Small |
| `FireStations` | Small |
| `FireStationDistricts` | |
| `GlendalePoliceStation` | Small |
| `GlendaleHospitals` | Small |
| `GlendaleSchools` | Small |
| `GlendaleBeeline_BusStops` | Transit |
| `GlendaleZIPCodes` | Small |
| `NeighborhoodZones` | Small |
| `Glendale_City_Boundary` | Single feature |
| `PressureZones` | Water utility |
| `CAD_SiteAddress_Street` | **GeocodeServer** — see 5.4 |

#### PW (Public Works) — 8 services

| Service | Notes |
|---|---|
| `Pavement_Condition_Index_2024` | |
| `StreetSweeping` | |
| `Truck_Route` | |
| `MoratoriumStreets` | Operational — changes often |
| `Holiday_Moratorium_Streets` | Seasonal |
| `Street_Service_Request_Map` | Operational — changes often |
| `PWCIPMap` | Capital improvement projects |
| `IWM_Franchise_Hauler_Locator` | **GeocodeServer** |

#### Utilities
Third folder present at root.

#### ⚠️ `SampleWorldCities`
Esri's **stock demo service, still deployed at the root**. Exclude it explicitly or an agent will eventually query it and confidently report on world capitals.

### 5.3 Service detail — `Common/Zoning/MapServer`

| Property | Value |
|---|---|
| Layer 0 | `Glendale_Boundary` |
| Layer 1 | `Parcels` |
| Layer 2 | `Zoning` |
| MaxRecordCount | **2000** |
| Query formats | JSON, geoJSON, PBF |
| MaxImageHeight / MaxImageWidth | 4096 |
| Copyright | City of Glendale |
| Supported ops | includes `Return Updates` |

### 5.4 The geocoder — sleeper asset

`Common/CAD_SiteAddress_Street/GeocodeServer` is the city's **own authoritative address locator**. Gives you address → coordinates → spatial query with no Nominatim round-trip and no boundary guessing. Unlocks the query people actually want: *"what's the zoning at 123 Brand Blvd?"*

### 5.5 `gisapps.glendaleca.gov` — secondary host

| Service | Status |
|---|---|
| `Common/HOA_Areas/MapServer` | OK — MaxRecordCount 1000, JSON + geoJSON |
| `Common/Glendale_BaseMap` | OK |
| `Common/GlendaleParks` | OK |
| `Common/Zoning/FeatureServer` | OK — MaxRecordCount **5000**, **JSON only** (no geoJSON) |
| `Common/Zoning/MapServer` | ❌ *"Application Error: Could not access any server machines"* |

**Treat `gisapps` as best-effort with a short timeout. `gismap` is primary.**

### 5.6 Gotchas

1. **Everything is Web Mercator** — wkid 102100/3857, units `esriMeters`. Reproject to WGS84 or pass `outSR=4326`, or the model reads `-13,169,659` as degrees.
2. **MaxRecordCount varies per service** — 2000 / 5000 / 1000 observed across three endpoints. Read it from `layer_info`; never hardcode.
3. **Prefer FeatureServer over MapServer** where both exist (query-oriented), but note they're not configured identically (see 5.3 vs 5.5).
4. **Exclude `SampleWorldCities`.**
5. **No published MCP server exists for either Glendale.** Pipeworx has many neighbors but no Glendale pack. Build-your-own.
6. **Legal disclaimer:** Glendale's GIS terms state the data is not a substitute for legal descriptions or actual surveys. If parcels or zoning reach anything decision-bearing, that disclaimer should ride in the tool response, not just the README.

---

## 6. Recommended Architecture

**Drop `search_datasets` entirely.** The whole city is ~25 services — you're not building a library catalog, you're writing a small restaurant's menu. Crawl folders once at *build* time, ship a static manifest, expose one clean tool per **concept**.

```python
LAYERS = {
    "zoning":   ".../Common/Zoning/MapServer/2",
    "parcels":  ".../Common/Zoning/MapServer/1",
    "boundary": ".../Common/Zoning/MapServer/0",
    "parks":    ".../Common/Parks/FeatureServer/0",
    "schools":  ".../Common/GlendaleSchools/FeatureServer/0",
    "pci_2024": ".../PW/Pavement_Condition_Index_2024/MapServer/0",
}
GEOCODER = ".../Common/CAD_SiteAddress_Street/GeocodeServer"
```

Then `get_zoning`, `find_parcels`, `nearby_parks` — outcome-shaped tools, not a generic query passthrough.

**Security:** cap `limit` server-side, never return geometry unless asked, and **allowlist the host** so a `url` parameter can't be redirected to an arbitrary endpoint. That's the injection surface in this design.

---

## 7. Rate Limits

**None published.** Glendale is self-hosted ArcGIS Server 10.91 — no quota, no `X-RateLimit` headers, no documented requests/minute. What exists is a **capacity ceiling**: instance pools, CPU, memory, backing DB. You don't get throttled, you get slow, then 503s, and the first person to notice is a city employee whose permit map stopped loading.

> *It's not a metered parking garage with a posted rate — it's your neighbor's driveway. No sign, but there's definitely a wrong amount.*

**Contrast — ArcGIS Online** *does* enforce quotas: `API calls quota exceeded (10007)! maximum allowed (10000) per Minute. Retry after 60 sec.` with HTTP 429. Handle this if you ever point the design at a Hub-hosted layer.

**Self-throttle by policy:**
- 1–2 concurrent requests; single-digit req/sec ceiling
- Exponential backoff on 429, 503, and timeouts alike
- Cache aggressively (see §9)
- Set a real `User-Agent` with tool name + contact — if you cause load, you want an email, not an IP block

---

## 8. Data Freshness — Three Mechanisms

| Rung | Mechanism | Reliability |
|---|---|---|
| 1 | `editingInfo.lastEditDate` on layer JSON (epoch ms) | Cheapest. **Caveat:** updates on data edits *and* on any layer property change — answers "did anything change?", not "did the data change?" Marked "if present" in the spec, and is **frequently absent on self-hosted MapServer layers over an enterprise geodatabase** — i.e. exactly Glendale's setup. |
| 2 | `MAX(EditDate)` via `outStatistics` | Precise. Requires edit tracking. Read the real field name from `editFieldsInfo.editDateField`. |
| 3 | `returnUpdates=true` on layer resource | Returns updated `timeExtent`. Only useful for time-aware layers. Present in Zoning's supported operations. |
| 4 | Count / content fingerprint | Fallback. Catches adds and deletes, misses attribute edits. |

```python
async def data_freshness(layer_url: str) -> dict:
    meta = await get(f"{layer_url}?f=json")
    if ed := meta.get("editingInfo", {}).get("lastEditDate"):
        return {"as_of": ms_to_iso(ed), "method": "editingInfo",
                "caveat": "also moves on schema changes"}
    if field := meta.get("editFieldsInfo", {}).get("editDateField"):
        r = await get(f"{layer_url}/query", params={
            "where": "1=1", "f": "json",
            "outStatistics": json.dumps([{
                "statisticType": "max", "onStatisticField": field,
                "outStatisticFieldName": "mx"}])})
        return {"as_of": ..., "method": "max_edit_date"}
    n = await get(f"{layer_url}/query",
                  params={"where": "1=1", "returnCountOnly": "true", "f": "json"})
    return {"as_of": None, "method": "count_fingerprint", "count": n["count"],
            "note": "no edit tracking; change detected by count drift only"}
```

**Put the result in `_meta` on every response.** An agent can reason about "as of March 2025" or an honest "unknown". It cannot reason about silent staleness.

> **TODO:** confirm whether Glendale's layers actually expose `editingInfo` — one direct call to a layer endpoint settles it.

---

## 9. Caching Strategy

**Cache by rate of change, not by resource type.**

> *Pantry, fridge, counter. Flour keeps a year, milk a week, what you're chopping doesn't get put away. Same shelf for all three and you either throw out good flour or serve spoiled milk.*

| Tier | Contents | TTL |
|---|---|---|
| **Pantry** | Service catalog, schemas, field types, MaxRecordCount, SRS | Forever; refresh weekly. Crawl at build time, ship a manifest. |
| **Pantry** | Geocoding results | Months — addresses don't move. Highest hit rate you'll have. |
| **Fridge** | Parcels, zoning, boundary, districts, park polygons | Days–weeks. Dominates bandwidth and context. |
| **Counter** | Street service requests, active moratorium streets, status fields | Minutes, or no cache. |

### Two-tier validation
Pair a **cheap freshness probe** with a **long-lived payload cache** — one small request per hour keeps a multi-megabyte parcel layer valid indefinitely.

```python
async def get_layer(key: str, max_probe_age=3600):
    entry = store.get(key)
    if entry and entry.probed_at > now() - max_probe_age:
        return entry.data                      # trust it, no network
    stamp = await data_freshness(LAYERS[key])  # cheap metadata call
    if entry and stamp == entry.stamp:
        entry.touch(); return entry.data       # revalidated, no refetch
    data = await fetch_all(LAYERS[key])        # expensive, rare
    store.put(key, data, stamp)
    return data
```

### For a city this size: replicate, don't cache
Parks, Libraries, FireStations, Schools, ZIP codes and NeighborhoodZones are dozens-to-hundreds of rows. **Pull them whole into local SQLite or DuckDB with spatial indexing**, refreshed nightly against `lastEditDate`. Only Parcels and Streets are big enough to need paging.

This inverts the architecture productively: MCP tools query local storage, the network becomes a background sync job. No rate-limit worries, no cold-start latency, and the server still works when `gisapps` returns *"could not access any server machines."*

### Four rules
1. **Normalize cache keys** — `ZONE='R1'` and `ZONE = 'R1'` must hit the same entry. Canonicalize params; never key on raw URL.
2. **Snap bboxes to a grid** — arbitrary bboxes have near-zero hit rate. Round to fixed tiles, fetch the tile, filter locally.
3. **Persist to disk** — stdio MCP servers restart constantly. An in-memory cache is always cold.
4. **Serve stale, but say so** — on fetch failure return cached data with `_meta.stale: true` + timestamp. An agent can reason about "as of last Tuesday"; it can't reason about a 503.

> **The tier nobody budgets for: your context window is the last cache layer, and the most expensive one.** A 4 MB parcel GeoJSON is cheap on disk and catastrophic to return. Cache liberally, return stingily.

---

## 10. Next Steps

- [ ] Crawl `gismap` folders → static `LAYERS` manifest (~25 services, exclude `SampleWorldCities`)
- [ ] Probe one layer endpoint for `editingInfo` presence → pick the freshness rung
- [ ] Build geocoder tool first — it unlocks address-anchored queries
- [ ] Replicate the small layers into local DuckDB/SQLite
- [ ] Wire `_meta` envelope (source, url, cached, as_of, stale) into every response
- [ ] Add the survey/legal-description disclaimer to parcel and zoning responses
- [ ] Test against a real agent session — watch for wrong-tool selection
