# Glendale GIS MCP Server

An [MCP](https://modelcontextprotocol.io) server that gives hackathon teams, and their AI agents, easy access to the hazard maps and city GIS data for **Glendale, California**: which mapped hazards apply at an address, what community resources are nearby, and the City of Glendale layers behind them.

It was built for **Jewel City Hacks 5**, for teams working on emergency preparedness: helping residents understand the hazards where they live and how to prepare.

## Jewel City Hacks 5

| | |
| --- | --- |
| **When** | Saturday, September 26, 2026, 8:00 AM – 8:00 PM |
| **Where** | Glendale Community College, Math Discovery Center, 1500 N Verdugo Rd, Glendale, CA 91208 |
| **Theme** | "Think Globally, Build Locally": create technology that makes cities smarter, more connected, and more inclusive |
| **Hosts** | [Hacker Fund](https://hacker.fund/jewelcityhacks) and Glendale Tech, with the City of Glendale Economic Development Division and the GCC Computer Science Club |
| **Teams** | 1–4 people; students, professionals and residents welcome. High school students must be 14 or older, and anyone under 18 needs parental consent. |
| **Register** | [luma.com/jewelcityhacks5](https://luma.com/jewelcityhacks5) |

**Schedule:** check-in 8:00 AM · project submission closes 6:00 PM · demo expo 6:15 PM (5-minute presentations) · closing ceremony and winners 7:15 PM · check-out by 8:00 PM.

**Judging:** technical execution 40%, social/local impact 20%, presentation 15%, feasibility 15%, creativity 10%. Prizes: Grand Champion, Best Hardware Hack, Best Software Hack, and Top 5 Hackathon Hackers.

Event details are from [hacker.fund/jewelcityhacks](https://hacker.fund/jewelcityhacks) and the [Luma page](https://luma.com/jewelcityhacks5) as of September 18, 2026. Check there for changes. Questions about the event: team [at] hacker.fund.

## What this server gives you

It **finds, queries and normalizes** authoritative data. It doesn't interpret risk, rank hazards or give advice; that's what teams build.

| Ask | Tool |
| --- | --- |
| Which hazards apply at an address or point? | `hazards_at_location`, or `wildfire_zone`, `flood_zone`, `seismic_zones`, `dam_inundation`, `debris_flow` |
| Nearest fire stations, police, hospitals, schools, libraries, parks, bus stops? | `nearest_resources` |
| Zoning or parcel at a location, schools of a type, bus stops on a route, any layer by attribute or area? | `query_dataset` |
| What data is there, and what do the fields mean? | `list_datasets`, `describe_dataset` |
| Is this a valid Glendale address? | `geocode_address` |
| What can this server do? Where's live emergency information? | `read_guide` |

Every tool takes a location as a street address (`{"address": "613 E Broadway"}`) or coordinates (`{"lat": 34.1466, "lon": -118.2483}`). Results cite their source, and each feature carries a `ref` for fetching the full live record.

**Data** (21 datasets):

- **Hazards:** CAL FIRE Fire Hazard Severity Zones (2025), California Geological Survey fault rupture, liquefaction and landslide zones, FEMA flood zones, DWR dam inundation areas, and USGS post-fire debris-flow assessments.
- **City of Glendale:** city boundary, fire stations and districts, police station, hospitals, schools, libraries, parks, Beeline bus stops, ZIP codes, neighborhood zones, zoning, streets, and parcels (queried live).

Most data comes from an offline snapshot of the public ArcGIS services (about 35 MB), so queries are fast and don't load the city's servers.

**Not included:** real-time information (active fires, evacuation orders, weather warnings, earthquakes). See [Real-Time Emergency Information](docs/real-time-sources.md) for the official sources and feeds to use instead.

## Try it

You need Python 3.10 or later. On first run the server downloads the data snapshot (about 6 MB) from this repository's releases, checks its SHA-256, and caches it. Pick one of the two ways to install it.

### Option A: uv (recommended)

[uv](https://docs.astral.sh/uv/) runs the server without a permanent install:

```sh
uvx --from git+https://github.com/HackerFund/GlendaleGisMcp glendale-gis-mcp --fetch-snapshot
```

That downloads and verifies the snapshot, then exits, so your client doesn't wait for it later.

**Claude Desktop:** in `claude_desktop_config.json` (Settings → Developer → Edit Config), then quit and reopen Claude Desktop. Use the full path to `uvx` (find it with `which uvx`), since Claude Desktop doesn't see your shell's `PATH`:

```json
{
  "mcpServers": {
    "glendale-gis": {
      "command": "/full/path/to/uvx",
      "args": ["--from", "git+https://github.com/HackerFund/GlendaleGisMcp", "glendale-gis-mcp"]
    }
  }
}
```

**Claude Code:**

```sh
claude mcp add glendale-gis -- uvx --from git+https://github.com/HackerFund/GlendaleGisMcp glendale-gis-mcp
```

### Option B: pip

Install into a virtual environment of its own:

```sh
python3 -m venv ~/glendale-gis-mcp
source ~/glendale-gis-mcp/bin/activate        # Windows: %USERPROFILE%\glendale-gis-mcp\Scripts\activate
pip install git+https://github.com/HackerFund/GlendaleGisMcp
glendale-gis-mcp --fetch-snapshot              # download and verify the data
```

The server command is then `~/glendale-gis-mcp/bin/glendale-gis-mcp` (Windows: `%USERPROFILE%\glendale-gis-mcp\Scripts\glendale-gis-mcp.exe`). Clients need its full path; `echo ~/glendale-gis-mcp/bin/glendale-gis-mcp` shows it.

**Claude Desktop:**

```json
{
  "mcpServers": {
    "glendale-gis": {
      "command": "/Users/you/glendale-gis-mcp/bin/glendale-gis-mcp"
    }
  }
}
```

**Claude Code:**

```sh
claude mcp add glendale-gis -- ~/glendale-gis-mcp/bin/glendale-gis-mcp
```

To update later, force a reinstall (the version number doesn't change between commits, so a plain upgrade may skip it):

```sh
pip install --force-reinstall --no-deps git+https://github.com/HackerFund/GlendaleGisMcp
```

### Try a question

Ask, for example: "Is 613 E Broadway in Glendale in a flood zone?" or "What's the nearest fire station to 1000 W Glenoaks Blvd?" Ask "What can the Glendale GIS server do?" for a tour.

### From source (to change the code)


```sh
git clone https://github.com/HackerFund/GlendaleGisMcp.git && cd GlendaleGisMcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The published snapshot is used by default. To build your own (about 1.5 minutes; it queries the live agency servers), run `python scripts/build_snapshot.py` and point the server at it with `GLENDALE_GIS_SNAPSHOT_PATH=$PWD/snapshot`.

## Docs

- [Reading the Snapshot Data](docs/snapshot-data.md): GeoJSON, fields, each layer, and how to read each hazard
- [Real-Time Emergency Information](docs/real-time-sources.md): official alerts and live data feeds
- Inside a connected client, call `read_guide("about")` for the full tour.

## Please read

- The hazard layers are **regulatory maps, not site-specific assessments**. Being outside a mapped zone doesn't mean there's no hazard. For example, CAL FIRE "NonWildland" means unzoned, not safe from wildfire.
- City of Glendale GIS data is for general information and is not a substitute for legal descriptions or surveys.
- This server has no real-time data. In an emergency, follow official alerts.

## License

Copyright (C) 2026 Ryan Gates

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See [LICENSE](LICENSE) for details.

The data comes from public agency services and keeps their terms and disclaimers.
