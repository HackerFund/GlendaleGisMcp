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

You need Python 3.10 or later. On first run the server downloads the data snapshot (about 6 MB) from this repository's releases, checks its SHA-256, and caches it. Install it one of two ways, then connect your assistant below.

### Option A: uv (recommended)

[uv](https://docs.astral.sh/uv/) runs the server straight from GitHub, with nothing to install permanently:

```sh
uvx --from git+https://github.com/HackerFund/GlendaleGisMcp glendale-gis-mcp --fetch-snapshot
```

That downloads and verifies the data, then exits, so your assistant doesn't wait for it later. The command to give your assistant is:

```
uvx --from git+https://github.com/HackerFund/GlendaleGisMcp glendale-gis-mcp
```

### Option B: pip

```sh
python3 -m venv ~/glendale-gis-mcp
source ~/glendale-gis-mcp/bin/activate        # Windows: %USERPROFILE%\glendale-gis-mcp\Scripts\activate
pip install git+https://github.com/HackerFund/GlendaleGisMcp
glendale-gis-mcp --fetch-snapshot              # download and verify the data
```

The command to give your assistant is the full path `~/glendale-gis-mcp/bin/glendale-gis-mcp` (Windows: `%USERPROFILE%\glendale-gis-mcp\Scripts\glendale-gis-mcp.exe`). To update later, force a reinstall, since the version number doesn't change between commits:

```sh
pip install --force-reinstall --no-deps git+https://github.com/HackerFund/GlendaleGisMcp
```

### Option C: clone this repository

Run the code from a checkout, which is also what you want if you plan to change it:

```sh
git clone https://github.com/HackerFund/GlendaleGisMcp.git
cd GlendaleGisMcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                               # add ".[dev]" for the tests
glendale-gis-mcp --fetch-snapshot              # download and verify the data
```

The command to give your assistant is the absolute path printed by `echo $PWD/.venv/bin/glendale-gis-mcp`.

To pull updates later: `git pull`, then `pip install -e .` again if the dependencies changed. To build your own snapshot instead of using the published one (about 1.5 minutes, and it queries the live agency servers):

```sh
python scripts/build_snapshot.py
export GLENDALE_GIS_SNAPSHOT_PATH=$PWD/snapshot
```

## Connect your AI assistant


The examples use the uv command. With pip or a clone, swap it for the full path to `glendale-gis-mcp` and drop the `--from …` arguments.

### Claude Code

```sh
claude mcp add glendale-gis -- uvx --from git+https://github.com/HackerFund/GlendaleGisMcp glendale-gis-mcp
```

Check it with `claude mcp list`. (With pip: `claude mcp add glendale-gis -- ~/glendale-gis-mcp/bin/glendale-gis-mcp`.)

### Claude Desktop

Open Settings → Developer → Edit Config, add the entry below, then **quit Claude Desktop with ⌘Q and reopen it**. Use the full path to `uvx` (`which uvx`), because Claude Desktop doesn't see your shell's `PATH`:

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

With pip, use `"command": "/Users/you/glendale-gis-mcp/bin/glendale-gis-mcp"` and no `args`.

uv caches the code it downloaded, so a restart alone may keep an older commit. To always check GitHub, add `"--refresh"` as the first item in `args`. To pin a version for the event, put a tag or commit on the URL: `git+https://github.com/HackerFund/GlendaleGisMcp@<tag>`.

### Gemini CLI

Add this to `~/.gemini/settings.json` (or `.gemini/settings.json` in a project), then restart the CLI:

```json
{
  "mcpServers": {
    "glendale-gis": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/HackerFund/GlendaleGisMcp", "glendale-gis-mcp"]
    }
  }
}
```

The Gemini CLI also has `gemini mcp add <name> <command> [args...]`; put `--` before the command so its own flags aren't confused with uv's. See [Google's MCP documentation](https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html) for the current syntax. Use `/mcp` inside the CLI to list connected servers.

### ChatGPT

**ChatGPT can't run this server today.** Its custom connectors only reach *remote* MCP servers over HTTPS, so a local install like the above isn't an option, and its connectors authenticate with OAuth or no authentication, [per OpenAI's documentation](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt). Our hosted server uses a shared key sent as an HTTP header, which ChatGPT doesn't send.

Custom connectors also need Developer Mode (Settings → Apps → Advanced) and a paid plan.

If you want to use ChatGPT, the options are:
- Use Claude, the Gemini CLI or another MCP client for the data, and ChatGPT for the rest of your build.
- Ask the organizers for an open (no-key) endpoint for your team.
- Call the `core` package directly from your own Python code (see [Development](#development)) and pass the results to ChatGPT.

### Other clients

Cursor, VS Code, Zed, Windsurf and most other MCP clients take the same command and arguments; check their docs for where the config lives. To poke at the server by hand:

```sh
npx @modelcontextprotocol/inspector uvx --from git+https://github.com/HackerFund/GlendaleGisMcp glendale-gis-mcp
```

### Hosted server (Google Cloud Run)

Teams can use the hosted server instead of installing anything. It speaks streamable HTTP and needs the shared hackathon key on every request. The organizers hand out the key through the event channel.

- **MCP endpoint:** `https://glendale-gis-mcp-1053589358088.us-west2.run.app/mcp`
- **Key:** `Authorization: Bearer <key>` header (never in the URL)
- **Health check (no key needed):** `curl https://glendale-gis-mcp-1053589358088.us-west2.run.app/health`
- **Rate limit:** 120 requests a minute per key. Over that you get `429` with a `Retry-After` header.

A missing or wrong key returns `401`. If the hosted server is down, the local options above give you the same tools and data. Deployment steps are in [docs/deploy.md](docs/deploy.md).

**Claude Code:**

```sh
claude mcp add --transport http glendale-gis https://glendale-gis-mcp-1053589358088.us-west2.run.app/mcp \
  --header "Authorization: Bearer <key>"
```

**Gemini CLI** (`~/.gemini/settings.json`):

```json
{
  "mcpServers": {
    "glendale-gis": {
      "httpUrl": "https://glendale-gis-mcp-1053589358088.us-west2.run.app/mcp",
      "headers": { "Authorization": "Bearer <key>" }
    }
  }
}
```

Check [Google's MCP documentation](https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html) for the current field names.

**Claude Desktop and ChatGPT** can't send a custom header to a remote MCP server, so they can't use the hosted endpoint with a key. Use a local install for those.

Keep the key out of anything you publish: no screenshots, no commits, no Devpost submissions.

### Try a question

Ask, for example: "Is 613 E Broadway in Glendale in a flood zone?" or "What's the nearest fire station to 1000 W Glenoaks Blvd?" Ask "What can the Glendale GIS server do?" for a tour.

## Development

Clone it as in [Option C](#option-c-clone-this-repository), install with `pip install -e ".[dev]"`, then:

```sh
pytest                  # 339 tests; none of them touch the network
ruff check . && ruff format .
```

`AGENTS.md` holds the project's rules and design decisions, and `Plans/` has the research notes and the phased plan. The `core` package has no MCP imports, so you can use it as a plain Python library:

```python
from glendale_gis.core.snapshot import Snapshot
from glendale_gis.core import hazards

snap = Snapshot.load("snapshot")                    # or the downloaded copy in your cache
result = hazards.hazards_at_location(snap, hazards.locate(snap, 34.1466, -118.2483))
print(result.wildfire.status, result.flood.matches[0].attributes["FLD_ZONE"])
```

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
