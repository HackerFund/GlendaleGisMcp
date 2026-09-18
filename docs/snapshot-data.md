# Reading the Snapshot Data

The snapshot is an offline copy of 20 hazard and city map layers for Glendale, California. It is stored as 20 GeoJSON files plus a `manifest.json`, about 35 MB in total. This guide explains the format and how to read it correctly.

## Contents

- [What the snapshot is](#what-the-snapshot-is)
- [GeoJSON in five minutes](#geojson-in-five-minutes)
- [Geometry types](#geometry-types)
- [Properties: the attributes](#properties-the-attributes)
- [The manifest](#the-manifest)
- [The layers](#the-layers)
- [Clipping, rounding and what's left out](#clipping-rounding-and-whats-left-out)
- [Using the data](#using-the-data)
- [Reading hazards correctly](#reading-hazards-correctly)

## What the snapshot is

The MCP server answers most questions from the snapshot, so it doesn't have to call city, state and federal map servers on every request.

- **Hazard layers** come from CAL FIRE, the California Geological Survey (CGS), FEMA, the Division of Safety of Dams (DWR) and USGS.
- **City layers** come from the City of Glendale's GIS server: fire and police stations, hospitals, schools, parks, bus stops, zoning, streets and more.
- **Location:** `python scripts/build_snapshot.py` writes it to `snapshot/` in the repository (gitignored). Installed copies will be cached in the user cache folder, for example `~/Library/Caches/glendale-gis-mcp` on macOS.
- **Freshness:** each layer records when it was fetched and, where the source publishes it, when the source last changed. See [The manifest](#the-manifest).

The data is passed through as published. The snapshot doesn't score, rank or interpret risk; that is left to the teams building on it.

**The snapshot has no real-time data:** no active fires, evacuation orders, warnings or earthquakes. For those, see [Real-Time Emergency Information](real-time-sources.md).

## GeoJSON in five minutes

GeoJSON is plain JSON for map data, defined by [RFC 7946](https://datatracker.ietf.org/doc/html/rfc7946). A file holds a list of **features**. Each feature pairs a shape (its **geometry**) with a set of attributes (its **properties**).

Here is one real feature from `bus_stops.geojson`, formatted for reading:

```json
{
  "type": "Feature",
  "id": 1,
  "geometry": {
    "type": "Point",
    "coordinates": [-118.297595, 34.176464]
  },
  "properties": {
    "OBJECTID": 1,
    "Stop_Numbe": 354.0,
    "Route": "7",
    "On_Street": "NB Alameda",
    "At_Street": "NS Glenoaks"
  }
}
```

| Part | What it means |
| --- | --- |
| `FeatureCollection` | The whole file: `{"type": "FeatureCollection", "features": [...]}` |
| `Feature` | One map object: a stop, a park, a flood zone |
| `id` | The source's object ID for this feature |
| `geometry` | The shape: a `type` plus `coordinates` |
| `properties` | The attributes, as named by the source |

**Coordinates are longitude first, then latitude.** In `[-118.297595, 34.176464]`, -118.3 is longitude (west) and 34.18 is latitude (north). This is the most common mistake with GeoJSON, because many tools and people write latitude first. If a tool rejects a coordinate or puts Glendale far from California, the order is probably swapped.

Coordinates use WGS 84 (EPSG:4326), the same system as GPS and web maps, in decimal degrees.

The files are written as one compact line to save space. To read one, format it (for example `python -m json.tool snapshot/hospitals.geojson`) or load it with a library.

## Geometry types

The snapshot uses three kinds of shape: points, lines and polygons. Each can also come in a "Multi" form, meaning one feature made of several separate pieces.

| Type | Coordinates look like | Layers |
| --- | --- | --- |
| `Point` | `[lon, lat]` | Bus stops, fire stations, hospitals, libraries, schools |
| `LineString` / `MultiLineString` | a list of points / a list of those lists | Streets |
| `Polygon` | a list of rings; each ring is a closed list of points | City boundary, fire station districts, neighborhood zones, police station |
| `Polygon` / `MultiPolygon` | a list of polygons | All hazard zones, parks, ZIP codes, zoning |

How to read polygons:

- **The first ring is the outline; any later rings are holes.** A flood zone with a hole means the area inside the hole is not in that zone.
- **Rings are closed:** the last point repeats the first.
- **A MultiPolygon can have many pieces.** The dam inundation areas are the extreme case, with up to several thousand small pieces per feature, because they were traced from flood-model grids.

Two layers that describe places people go are polygons, not points: the police station and parks. Measure distance to their edge or their center, depending on your question. Fire stations are stored as one-point multipoints at the source; the snapshot turns each into a single `Point`.

## Properties: the attributes

Properties keep the source's own field names and values, unchanged. Nothing is renamed, cleaned or converted, so names like `Stop_Numbe` (cut to 10 characters by an old file format) stay as they are.

Every feature has the source's object ID (`OBJECTID`, or `FID` for dam inundation). Hazard layers from CGS, FEMA and DWR also have a `GlobalID`. City layers and CAL FIRE's layer don't publish one.

Values to watch for:

| What you see | What it means | Example |
| --- | --- | --- |
| Dates as large integers | Milliseconds since January 1, 1970 (UTC), the ArcGIS convention. Divide by 1,000 for Unix seconds. | `PubDate: 1564128000000` is July 26, 2019 |
| Codes | Short codes whose meanings are in the catalog | FEMA `FLD_ZONE: "AE"`; CAL FIRE `FHSZ: 3` means Very High |
| `-9999` | FEMA's "no value" marker, not a real number | `STATIC_BFE: -9999` |
| `null` | No value in the source | |
| Leading spaces | Kept as published | Fire station `sta_no: " 21"` |
| Lists in one string | Split on commas yourself | Bus stop `Route: "1,2,11,12"` |
| Whole numbers stored as decimals | Convert if you need an integer | Bus stop `Stop_Numbe: 354.0` |

The date fields are `PubDate` (dam inundation) and `assessment_date` and `start_date` (debris flow). Date-like fields in the CGS layers, such as `RELEASED`, are plain text.

For what each field means, use the `describe_dataset` tool or read `src/glendale_gis/core/catalog.py`. Meanings not stated by an official source are marked `inferred` there.

## The manifest

`manifest.json` describes the build and every layer in it. Read it first: it tells you where each layer came from, how fresh it is, and how it was cut.

Top-level keys:

| Key | Meaning |
| --- | --- |
| `version` | Manifest format version (currently 1) |
| `built_at` | When the snapshot was built (UTC) |
| `generator` | The package and version that built it |
| `crs` | Coordinate system of every file: `EPSG:4326` |
| `coordinate_decimals` | Decimal places kept in coordinates: 6 |
| `total_bytes` | Size of all layer files together |
| `layers` | One entry per dataset, keyed by dataset ID |

Each entry in `layers`:

| Key | Meaning | Example (`bus_stops`) |
| --- | --- | --- |
| `title`, `source_agency`, `category` | What the layer is and who publishes it. `category` is `hazard`, `resource` or `reference`. | Beeline Bus Stops, City of Glendale, `resource` |
| `layer_url` | The live ArcGIS layer the data came from | `https://gismap.glendaleca.gov/.../GlendaleBeeline_BusStops/FeatureServer/0` |
| `fetched_at` | When the build downloaded it (UTC) | `2026-09-18T19:45:05+00:00` |
| `source_last_edit` | When the source says it last changed; `null` if it doesn't say | `null` (city layers don't publish this) |
| `feature_count` | Features in the file | 286 |
| `source_feature_count` | Features the source returned for the area, before clipping | 331 |
| `geometry` | `point`, `polyline` or `polygon` | `point` |
| `id_field`, `global_id_field` | Which properties hold the object ID and GlobalID | `OBJECTID`, `null` |
| `fields` | Each kept field's name, ArcGIS type and display name (alias) | `{"name": "Stop_Numbe", "type": "esriFieldTypeDouble", "alias": "Stop Number"}` |
| `clip` | Which buffer the layer was cut to, and its width | `{"buffer": "city", "meters": 100.0}` |
| `file`, `bytes`, `sha256` | The file name, its size and its checksum | `bus_stops.geojson`, 58,881 |

To fetch the full, current record for any feature, query its `layer_url` for its object ID, for example `<layer_url>/query?objectIds=1&outFields=*&outSR=4326&f=json`.

## The layers

Counts and sizes are from the build of September 18, 2026. `manifest.json` has the current numbers.

**Hazards** (cut to the city plus 2 km):

| File | Source | Shape | Features | Size | Key fields |
| --- | --- | --- | --- | --- | --- |
| `calfire_fhsz_lra` | CAL FIRE | Polygon | 13 | 289 KB | `FHSZ`, `FHSZ_Description` |
| `cgs_fault_zones` | CGS | Polygon | 5 | 11 KB | `QUAD_NAME`, `ZN_RELEASED` |
| `cgs_liquefaction_zones` | CGS | Polygon | 27 | 168 KB | `QUAD_NAME`, `RELEASED` |
| `cgs_landslide_zones` | CGS | Polygon | 680 | 1.9 MB | `QUAD_NAME`, `RELEASED` |
| `fema_flood_zones` | FEMA | Polygon | 47 | 387 KB | `FLD_ZONE`, `ZONE_SUBTY`, `SFHA_TF` |
| `dwr_dam_inundation` | DWR | Polygon | 19 | 23.6 MB | `DamName`, `Scenario`, `HazardCl` |
| `usgs_debris_flow` | USGS | Polygon | 0 | — | `BCH_Legend` |

**City of Glendale** (cut to the city plus 100 m):

| File | Shape | Features | Size | Key fields |
| --- | --- | --- | --- | --- |
| `city_boundary` | Polygon | 1 | 66 KB | `CITYNAME` (not cut) |
| `fire_stations` | Point | 9 | 2 KB | `NAME`, `ADDRESS`, `sta_no` |
| `fire_station_districts` | Polygon | 10 | 23 KB | `Fire_Distr` |
| `police_stations` | Polygon | 1 | 1 KB | `Name` |
| `hospitals` | Point | 3 | 1 KB | `NAME`, `ST_NUM`, `ST_NAME` |
| `schools` | Point | 27 | 6 KB | `SCHOOL`, `School_typ` |
| `libraries` | Point | 8 | 2 KB | `NAME`, `ST_NUM`, `ST_NAME` |
| `parks` | Polygon | 43 | 185 KB | `NAME_ALF` (name), `NAMEA_ALF` (address) |
| `bus_stops` | Point | 286 | 59 KB | `Stop_Numbe`, `Route`, `On_Street` |
| `zip_codes` | Polygon | 24 | 383 KB | `ZIPCODE` |
| `neighborhood_zones` | Polygon | 37 | 46 KB | `NAME` |
| `zoning` | Polygon | 2,426 | 4.7 MB | `ZONE_DISTR`, `ZONE_DESC`, `GENPLAN` |
| `streets` | Line | 6,868 | 3.1 MB | `FullName`, `Type` |

Parcels (54,300 features) are not in the snapshot; the server queries them live.

## Clipping, rounding and what's left out

Each layer is cut to an area around the city, so the snapshot stays small.

- **Hazard layers extend 2 km past the city limit.** The extra area keeps nearest-zone distances right for addresses near the edge.
- **City layers extend 100 m past the city limit.**
- The widths are set by the `GLENDALE_GIS_HAZARD_BUFFER_M` and `GLENDALE_GIS_CITY_BUFFER_M` environment variables, and each layer's `clip` entry records the width used.
- **Features crossing the edge are cut to it.** A clipped zone's shape and area are not the full source feature. To get the whole feature, fetch it from `layer_url` by object ID.
- **Features entirely outside are dropped.** The current build drops 1,202 street segments and 52 of the 338 Beeline bus stops, which lie outside the city.
- **Outside the clipped area, the snapshot has no data.** A point more than 2 km outside Glendale isn't "not in a zone"; it simply isn't covered.
- **Coordinates are rounded to 6 decimal places**, about 0.1 m, far finer than any source's accuracy.
- **Only documented fields are kept.** The live layers may have more; `outFields=*` on a live query returns them all.

## Using the data

**Python with shapely.** Find the flood zone at a point:

```python
import json
from shapely.geometry import shape, Point

with open("snapshot/fema_flood_zones.geojson") as f:
    zones = json.load(f)["features"]

here = Point(-118.2550, 34.1460)  # longitude, latitude (downtown Glendale)
for zone in zones:
    if shape(zone["geometry"]).contains(here):
        print(zone["properties"]["FLD_ZONE"], zone["properties"]["ZONE_SUBTY"])
# X AREA OF MINIMAL FLOOD HAZARD
```

**Distances need meters, not degrees.** shapely's `.distance()` on longitude/latitude returns degrees, and a degree of longitude is shorter than a degree of latitude. Use the package's helper, which is accurate to within 0.5% across the city:

```python
from glendale_gis.core.geo import distance_m

with open("snapshot/fire_stations.geojson") as f:
    stations = json.load(f)["features"]

nearest = min(stations, key=lambda s: distance_m(here, shape(s["geometry"])))
print(nearest["properties"]["NAME"], round(distance_m(here, shape(nearest["geometry"]))), "m")
# Fire Station 21 649 m
```

These are straight-line distances, not travel distances.

**Check file integrity** against the manifest:

```python
import hashlib

manifest = json.load(open("snapshot/manifest.json"))
for layer_id, entry in manifest["layers"].items():
    data = open(f"snapshot/{entry['file']}", "rb").read()
    assert hashlib.sha256(data).hexdigest() == entry["sha256"], layer_id
```

**JavaScript with Turf.js:**

```js
import booleanPointInPolygon from "@turf/boolean-point-in-polygon";

const zones = await (await fetch("snapshot/fema_flood_zones.geojson")).json();
const here = [-118.255, 34.146]; // [lon, lat]
const matches = zones.features.filter((z) => booleanPointInPolygon(here, z));
```

**Command line with jq:**

```sh
jq '.features | length' snapshot/zoning.geojson          # 2426
jq '.features[0].properties' snapshot/hospitals.geojson  # first hospital's attributes
```

**On a map:** drag any file into [QGIS](https://qgis.org) (free desktop GIS) or [geojson.io](https://geojson.io) (in the browser). geojson.io handles the small layers well; the 23.6 MB dam inundation file is better in QGIS.

## Reading hazards correctly

These are regulatory maps, not site-specific assessments. The data says where an agency has mapped a zone. It doesn't say how likely damage is at a particular address.

- **Outside a mapped zone doesn't mean no hazard.** Every hazard layer only covers what its agency has studied and mapped.
- **Wildfire (CAL FIRE):** every location in Glendale has a class: Very High, High, Moderate or NonWildland (unzoned). About two-thirds of the city is Very High. The zones rate the long-term physical hazard of the landscape (fuels, terrain, weather), not the risk to a particular building. **NonWildland means unzoned, not safe from wildfire:** embers and house-to-house spread can reach unzoned areas. These 2025 zones take effect when the city adopts them by ordinance, and they are not evacuation zones.
- **Fault, liquefaction and landslide (CGS):** being inside a polygon is the hazard signal. The attributes only describe the map (quadrangle, release date, links to the report); they don't grade severity.
- **Flood (FEMA):** Zone D means the flood hazard was **not studied**, not that the area is safe. Zone X covers both 0.2%-annual-chance (500-year) areas and minimal-hazard areas; `ZONE_SUBTY` tells them apart. `SFHA_TF: "T"` marks a Special Flood Hazard Area.
- **Dam inundation (DWR):** one dam can have several features, one per failure scenario or failed structure, so return all of them. `HazardCl` rates the consequences of a failure, not its likelihood. Federally owned dams aren't included. Real evacuation zones are set by local emergency managers.
- **Debris flow (USGS):** the layer is empty for Glendale because USGS only assesses recently burned areas. Empty doesn't mean no debris-flow risk.
- **City data:** the City of Glendale's GIS data is for general information and is not a substitute for legal descriptions or surveys.
- **Resources near a location** only include places inside Glendale (plus 100 m), and distances are straight-line.
