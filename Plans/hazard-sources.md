# State and Federal Hazard Sources — Endpoint Check

*Checked September 16, 2026, against the City of Glendale boundary (`gismap.glendaleca.gov/.../Common/Zoning/MapServer/0`).*

All sources below are public (no token), use the ArcGIS REST API, support point and polygon `query`, and return JSON and GeoJSON.

## Verified sources

| Hazard | Owner | Layer URL | Features in city | maxRec | Native SR | lastEditDate |
|---|---|---|---|---|---|---|
| Wildfire (FHSZ, LRA 2025) | CAL FIRE | `https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/FHSALRA25_v1_All/FeatureServer/0` | 7 | 2000 | 3310 | 2026-02-02 |
| Fault rupture (Alquist-Priolo) | CA Dept of Conservation / CGS | `https://services2.arcgis.com/zr3KAIbsRSUyARHG/arcgis/rest/services/CGS_Alquist_Priolo_Fault_Zones/FeatureServer/0` | 3 | 2000 | 3310 | 2025-11-19 |
| Liquefaction zones | CGS | `https://services2.arcgis.com/zr3KAIbsRSUyARHG/arcgis/rest/services/CGS_Liquefaction_Zones/FeatureServer/0` | 8 | 2000 | 3310 | 2025-11-19 |
| Earthquake-induced landslide zones | CGS | `https://services2.arcgis.com/zr3KAIbsRSUyARHG/arcgis/rest/services/CGS_Landslide_Zones/FeatureServer/0` | 343 | 2000 | 3310 | 2025-11-19 |
| Flood (NFHL flood hazard zones) | FEMA | `https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28` | 15 | 2000 | 4269 | none |
| Dam inundation | DWR / Division of Safety of Dams | `https://services.arcgis.com/aa38u6OgfNoCkTJ6/arcgis/rest/services/Approved_InundationBoundaries_As_of_Oct01_2025/FeatureServer/100` | 12 | 2000 | 3310 | 2026-03-19 |
| Post-fire debris flow | USGS | `https://earthquake.usgs.gov/arcgis/rest/services/ls/pwfdf/MapServer` (layers 2, 4, 6, 9) | 0 | — | — | — |

Freshness: every hosted ArcGIS Online layer exposes `editingInfo.lastEditDate`. FEMA's layer does not.

## What the data says inside Glendale

- **Wildfire:** all of Glendale is a Local Responsibility Area (LRA). The layer covers the whole city, with classes Very High, High, Moderate and NonWildland, so every address gets a value. Test points in La Crescenta, the Verdugo hills and Chevy Chase Canyon returned Very High; downtown returned NonWildland.
- **Faults:** 3 fault-zone polygons from the Burbank, Pasadena (zone revised 2025-11-20) and Sunland quads.
- **Liquefaction:** 8 polygons (Pasadena and Burbank quads). No SHZ "unevaluated areas" intersect the city.
- **Landslide:** 343 polygons, the most detailed hazard layer in the city.
- **Flood:** FEMA zones in the city are X (minimal and 0.2% annual chance), D (undetermined, 8 features) and one A zone "contained in channel". Zone D means the hazard was not studied, not that it's absent.
- **Dam inundation:** 12 areas from 11 facilities, including city reservoirs and LA County debris basins (Brand Park, Diederich Res, Chevy Chase 1290, Glenoaks 968, East Glorietta, Stough / Blanchard / Lower Sunset / Brand debris basins) and Encino. Hazard classes are High or Extremely High, and there are several failure scenarios for some dams.
- **Debris flow:** no USGS post-fire assessment currently covers Glendale. The service only holds assessments for recent fires, so "no assessment" does not mean no debris-flow risk.

## Gotchas

1. **CGS zone attributes are metadata, not severity.** Attributes are only quad name, release dates and links to the PDF map and report. Being inside the polygon is the signal.
2. **Use ArcGIS Online copies of the CGS zones.** The matching services on `gis.conservation.ca.gov/.../CGS_Earthquake_Hazard_Zones/SHP_*_Zones` return `499 Token Required`. Only `SHP_Fault_Traces` is public there.
3. **Skip the old FHSZ service** at `services.gis.ca.gov/arcgis/rest/services/Environment/Fire_Severity_Zones` (SRA 2007 and LRA 2011). The CAL FIRE SRA layer `FHSZSRA_23_3` returns nothing in Glendale because the city has no SRA land.
4. **FHSZ polygons are dissolved and very large.** Use point queries with `returnGeometry=false`; never pull whole geometries.
5. **The dam inundation URL contains a snapshot date** (`As_of_Oct01_2025`), and it lives in an individual DWR staff account, not an organization account. Expect it to be replaced when DSOD publishes a new release. Look it up at build time rather than hardcoding it forever.
6. **Coordinate systems differ:** 3310 (CA Albers), 4269 (NAD83) and the city's 3857. Always pass `inSR=4326` and `outSR=4326`.
7. **Hosted ArcGIS Online services** (`services*.arcgis.com`) can return 429 under heavy use. FEMA's server is known to be slow; use generous timeouts.

## Not yet checked

- Point queries used hand-picked coordinates, not geocoded addresses.
- USGS debris-flow layer schema (no features in the city to sample).
- Other sources that might matter for the challenge: LA County evacuation zones, the CGS tsunami layer (not relevant inland), extreme heat, and air quality.
