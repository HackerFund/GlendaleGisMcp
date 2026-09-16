# AGENTS.md

Guidance for coding agents working in this repository.

## Project

An MCP server that exposes **Glendale, California** municipal GIS data (self-hosted ArcGIS Server) to LLM agents through a small set of outcome-shaped tools.

Background research, the service inventory, and design reasoning live in `Plans/glendale-gis-mcp-notes.md`. Read it before making design decisions; this file only covers the rules.

## Status

Early stage — no application code yet. Next steps are tracked in §10 of the notes.

## Environment

- Python 3.13, virtualenv at `.venv` (`python3.13 -m venv .venv`).
- Activate with `source .venv/bin/activate`. Don't use an older system Python; the MCP SDK needs 3.10 or later.
- Build, test, and lint commands are not set up yet — add them here when they are.

## Data sources

| Host | Role |
|---|---|
| `https://gismap.glendaleca.gov/arcgis/rest/services` | Primary |
| `https://gisapps.glendaleca.gov/arcgis/rest/services` | Secondary, best-effort — use a short timeout |

Glendale, **AZ** (`glendaleaz-cog-gis.hub.arcgis.com`) is a different city. Do not mix its data in.

## Design rules

- **Curated tools, not a passthrough.** Name tools for what the user wants (`get_zoning`, `find_parcels`, `nearby_parks`), keep the total around 5–15, and use a static layer manifest instead of a `search_datasets` tool.
- **Docstrings are the interface.** Say when to use each tool and when not to. If an agent picks the wrong tool, fix the docstring first.
- **Typed outputs** with Pydantic models, and `readOnlyHint` annotations on every tool (all tools are read-only).
- **Errors are instructions.** Return a clear message with suggestions for what to try next, never a stack trace.
- **`_meta` on every response:** `source`, `url`, `cached`, `as_of`, `stale`.
- **Parcel and zoning responses must include the city's disclaimer** that the data is not a substitute for legal descriptions or surveys.

## Security

- **Allowlist hosts.** Never let a tool parameter supply an arbitrary URL; only the two Glendale hosts above are allowed.
- **Exclude `SampleWorldCities`** (Esri demo service deployed at the server root) from every manifest and crawl.
- Cap `limit` on the server side, and leave geometry out of responses unless the caller asks for it.
- Validate identifiers and `where`-clause inputs; do not interpolate raw caller strings into queries.

## ArcGIS gotchas

- Data is Web Mercator (wkid 102100/3857). Pass `outSR=4326` or reproject before returning coordinates.
- `maxRecordCount` differs per service (1000 / 2000 / 5000 seen). Read it from layer metadata; never hardcode it.
- Prefer FeatureServer over MapServer when both exist, but don't assume they are configured the same way.
- Use the city geocoder (`Common/CAD_SiteAddress_Street/GeocodeServer`) for address lookups, not an external service.

## Being a good neighbor

The city publishes no rate limits, so throttle on our side:

- 1–2 concurrent requests, single-digit requests per second.
- Exponential backoff on 429, 503, and timeouts.
- Send a descriptive `User-Agent` with the project name and a contact.
- Cache on disk (stdio servers restart often), with lifetimes based on how often data changes. Copy small layers into local storage instead of querying them live. When a fetch fails, serve stale data and mark it `stale: true`.
- Tests must not hit the live city servers by default. Use recorded fixtures.

## Conventions

- Keep the context window in mind: summarize, paginate, and state what was left out rather than returning large GeoJSON payloads.
- Keep behavior deterministic and predictable.
