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
7. **Hosted ArcGIS Online services** (`services*.arcgis.com`) can return 429 under heavy use. FEMA was fast during Phase 0 (see below), but keep generous timeouts.
8. **The "features in city" counts above used a simplified city boundary** (116 vertices, `maxAllowableOffset` ≈ 50 m). The full boundary has 2,769 vertices. Counts could differ slightly at the edges.

---

# Phase 0 Findings — Hazard Sources

*Checked September 16, 2026. Scripts used a `GlendaleGisMcp-discovery/0.1 (ryan@hacker.fund)` User-Agent with about 0.3 s between requests.*

"+2 km envelope" below means the city's bounding box expanded by 2 km: `-118.32958,34.10069,-118.15975,34.28532` (lon/lat). It's a rectangle, so it covers more area than a true 2 km buffer around the city polygon.

## Check 2 — USGS post-fire debris flow

**Service:** `https://earthquake.usgs.gov/arcgis/rest/services/ls/pwfdf/MapServer`. `maxRecordCount` 2000, JSON / GeoJSON / PBF, no `editingInfo` on any layer.

| Layer | Name | Geometry | Count (all US) |
|---|---|---|---|
| 0 | Locations | Point | 736 fires |
| 9 | Fire Perimeters | Polygon | 292 |
| 2 | Basin Probability Estimates | Polygon | 81,887 |
| 4 | Basin Combined Hazard | Polygon | 81,887 |
| 6 | Basin Volume | Polygon | 81,887 |
| 1 | Segment Probability Estimates | Polyline | 786,736 |
| 5, 7 | Segment Combined Hazard / Volume | Polyline | not counted |

**Layers 2, 4 and 6 contain identical rows** (checked by OBJECTID); they differ only in map symbology. Use one basin layer, e.g. layer 4.

### Fields

**Layer 0 (Locations):** `fire`, `Fire_ID` (e.g. `eat2025`), `lat`, `lon`, `location`, `size`, `status` ("Most Recent Version: 1.0"), `disclaimer`, `Rainfall` (e.g. "24mm"), `start_date`, `assessment_date`, `download_url` (ScienceBase shapefile), `ScienceBaseURL`.

**Layer 9 (Perimeters):** `fire_id`, `Version`, `assessment_date`, `start_date`.

**Basin layers (2/4/6):**

| Field | Values | Meaning |
|---|---|---|
| `BP_Legend` | `0-20%`, `20-40%`, `40-60%`, `60-80%`, `80-100%`, `NULL` | Debris-flow likelihood for the basin |
| `BV_Legend` | `<1,000`, `1,000-10,000`, `10,000-100,000`, `>100,000` (some values also written with spaces, e.g. `1,000 - 10,000`) | Potential sediment volume in cubic meters (confirmed; see Official documentation below) |
| `BCH_Legend` | `Low`, `Moderate`, `High`, `NULL` | Combined hazard class |
| `fire_id`, `assessment_date`, `version`, `start_date` | | Which assessment the basin belongs to |

**Segment layer (1):** `SP_Legend`, `SV_Legend`, `SCH_Legend` with the same value sets (volume legend without the spaced variants). Some values are real nulls, some the string `"NULL"`.

**Other value sets:** `Rainfall` ranges from `16mm` to `60mm` (most likely design-storm peak 15-minute intensity in mm/h; see below). `status` values are "Most Recent Version: 0.1 / 1.0 / 1.1 / 1.2 / 2.0". `size` for the Eaton fire is 57.6, which matches its area in km² (unit not stated in the service).

### Coverage near Glendale

- **Locations (layer 0) goes back to 2013. Perimeters and basins (layers 9, 2/4/6) only go back to 2020-08-13.** Older assessments keep their location point but lose their basins. Example: the 2017 La Tuna fire in the Verdugo Mountains has a location point and 0 perimeters / 0 basins.
- **Fire location points within about 25 km:** Sand 2016, La Tuna 2017, Creek 2017, Skirball 2017, Saddle Ridge 2019, Tick 2019, Getty 2019, Eaton 2025, Hurst 2025.
- **In the +2 km envelope:** one Eaton 2025 perimeter and one Eaton basin (`BCH_Legend` Moderate). None intersect the city itself. Whether it falls inside a true 2 km polygon buffer will show up in the Phase 3 build; if it does, `debris_flow` will report it as the nearest assessment.

### Official documentation

The MapServer's own description fields are empty, and **no USGS source defines the service's exact field names** (`BP_Legend`, `Rainfall`, `status`, …). The definitions below come from USGS's post-fire debris-flow (PWFDF) data spec and program pages. Mapping them onto the service fields is noted where it's inferred.

Sources:
- **[SPEC]** PWFDF data spec 1.1.0: <https://ghsc.code-pages.usgs.gov/lhp/ocelote/data-spec/archive/1.1.0/metadata.html> and <https://ghsc.code-pages.usgs.gov/lhp/ocelote/data-spec/archive/1.1.0/data-fields.html>
- **[SCI]** Scientific background: <https://www.usgs.gov/programs/landslide-hazards/science/scientific-background>
- **[FAQ]** Hazard assessment FAQ: <https://www.usgs.gov/programs/landslide-hazards/hazard-assessment-faq-frequently-asked-questions>
- **[DATA]** Assessment data page: <https://www.usgs.gov/programs/landslide-hazards/science/postfire-debris-flow-hazard-assessment-data>
- **[TOOL]** Tool page: <https://www.usgs.gov/tools/emergency-assessment-post-fire-debris-flow-hazards>
- **[PFDF]** USGS pfdf software, Cannon et al. (2010) model guide: <https://ghsc.code-pages.usgs.gov/lhp/pfdf/guide/models/c10.html>

| Service field | What the documentation says | Status |
|---|---|---|
| `BV_Legend` / `SV_Legend` | Potential sediment volume in **cubic meters**. Classes "0–1,000 m³; 1,000–10,000 m³; 10,000–100,000 m³; and greater than 100,000 m³" [SCI] | **Confirmed** (m³) |
| `BP_Legend` / `SP_Legend` | Debris-flow likelihood (0–1) "for a design storm with a peak 15-minute rainfall intensity of iii mm/hour" [SPEC] | Meaning confirmed; the five 20% bins aren't listed in any source |
| `BCH_Legend` / `SCH_Legend` | "Combined hazard class", values 1–3 [SPEC], using "the debris-flow combined hazard classification scheme of Cannon and others (2010)". Probability classes are ranked 1–5 and volume classes 1–4, and the ranks are added "with 9 being the highest combined hazard" [SCI]. The pfdf software's defaults split the sum into 3 classes at 3 and 6 [PFDF]. | Method confirmed; exact cutoffs used by the service and the 1/2/3 → Low/Moderate/High mapping **not confirmed** |
| `Rainfall` ("24mm") | Design storms are "peak 15-minute rainfall intensities ranging from 16-40 mm/h in 4 mm increments"; "a design storm with a peak 15-minute intensity of 24 mm/h is equivalent to the accumulation of 6 mm of rain in 15 minutes" [FAQ] | **Most likely 24 mm/h intensity with "/h" dropped, not 24 mm of rain. Not confirmed.** Don't label it as a rainfall amount. |
| `size` | The spec's `area_km2` is "The area of the fire perimeter in square kilometers" [SPEC]; Eaton's 57.6 matches | Likely km², **not confirmed** for this field |
| `start_date` / `assessment_date` | "the starting date of the fire event" / "the date the assessment was run" [SPEC] | Likely match, not confirmed |
| `status` | The spec's version object has a "version string for the assessment" and a note giving "the reason for the assessment version" [SPEC] | Likely match, not confirmed |
| `fire`, `Fire_ID`, `lat`, `lon`, `location`, `download_url`, `ScienceBaseURL` | No official definitions | Self-explanatory from values |

**Retention and archives:**
- The feature service "only contains a subset of the data for a given assessment" [DATA].
- Full assessments are on ScienceBase: a PWFDF Collection for 2025 onward (<https://www.sciencebase.gov/catalog/item/6818f950d4be0208bc3e0165>), plus a "2024 assessment archive" and a "2013 – 2023 assessment archive" [DATA].
- **No official statement explains the observed 2020-08-13 cutoff** for perimeters and basins.
- The Eaton 2025 v1.0 data release is <https://doi.org/10.5066/P14EWYME>.

**Intended use and limitations** (useful for the `debris_flow` disclaimer):
- "Results are generally representative of the conditions immediately after the fire" (Eaton data catalog page).
- "Burned areas are generally most susceptible to debris flows the first year following fire, but the level of hazard changes over time as vegetation regrows and the soil recovers"; "debris-flow activity in recently burned areas typically occurs within 2 yr of wildfire" [FAQ]. No firm validity period is given.
- "The models do not predict downstream impacts, potential debris-flow runout paths, and the areal extent of debris-flow or flood inundation." [TOOL] So a home downstream of an assessed basin isn't covered by the basin polygon.
- Values are "raw model outputs, so the number of digits following a decimal point does not represent the number of significant figures" (data.gov 2024 assessments page).
- **No official definition of "no assessment" was found.** USGS has an Assessment Requirements page (<https://www.usgs.gov/programs/landslide-hazards/hazard-assessment-requirements>) that wasn't reviewed.

### Gotchas

- **Field name casing differs between the schema and the returned attributes:** the schema says `fire_id` / `Version` but query results return `Fire_ID` / `version` on some layers. Read attributes case-insensitively.
- Legend values are strings with inconsistent spacing; normalize before grouping.
- "No assessment" is the normal state for Glendale and does not mean no debris-flow risk.

## Check 3 — FEMA paging and speed

Layer 28 (`Flood Hazard Zones`):

- `maxRecordCount` 2000. `advancedQueryCapabilities.supportsPagination` = true; `resultOffset` / `resultRecordCount` with `orderByFields=OBJECTID` works.
- Also supports statistics, `returnDistinctValues`, `orderBy`, query extent. No `editingInfo`, no `editFieldsInfo`, no coded-value domains.
- **Timing (single run, afternoon Sept 16):** metadata 0.8 s; count 0.66 s; 76 features without geometry 0.68 s; **76 features with full geometry in `outSR=4326`: 1.4 s, 3.1 MB JSON, 72,626 vertices**; the same with `maxAllowableOffset` ≈ 1 m: 1.3 s, 1.1 MB.
- `outSR=4326` works and returns `spatialReference` 4326.
- **Features in the +2 km envelope: 76.** No splitting into smaller areas or paging needed at this size, but the builder should still page generically.

## Check 4 — Finding the dam inundation layer

- **ArcGIS Online item ID `5354d98898194a4ab7b96eb6c85eecae`** ("Dam Inundation Areas Oct 1, 2025"). Owner `nathan.vanemmerik@water.ca.gov_dwr`, created 2026-02-24, modified 2026-06-19, public. Tags: `DIA`, `Dam Inundation Areas`, `DSOD`.
- Resolve the service URL at build time from `https://www.arcgis.com/sharing/rest/content/items/<id>?f=json` → `url`, then layer `100`. The item ID survives service overwrites; the URL name doesn't.
- **Fallback when the item disappears:** search `owner:nathan.vanemmerik@water.ca.gov_dwr "Inundation"` and pick the newest `Feature Service` tagged `DIA` / `DSOD`. The same owner also has a matching Service Definition item and a "DSCR (S)DAC Mapping Tool" web map and experience.
- **No organization-owned or undated DWR copy exists.** DWR's `gis_data_admin@DWR` account has `i17_California_Jurisdictional_Dams` (dam locations, not inundation) and `i21_Inundation_Extent` (Tulare Lake 2023 satellite flooding, not dam failure).
- Other copies found (Cal OES `DSOD_Approved_Inundation_Boundaries_View` from 2022, Marin County, City of Wildomar, `COF_EOC_PROD` statewide from 2020) are stale or third-party. Don't use them.

### Layer 100 fields

`FID`, `NID` (National Inventory of Dams ID, e.g. `CA00094`), `StateID` (DSOD dam number, e.g. `6.041`), `DamName`, `FailedStr`, `Scenario`, `LoadingScn`, `PubDate` (date the map was published), `HazardCl`, `GlobalID`. No coded-value domains, no layer description. Statewide count: 1,202.

| Field | Distinct values (statewide) |
|---|---|
| `Scenario` | `Scenario1` … `Scenario6` |
| `LoadingScn` | `Sunny Day`, `Storm Induced` |
| `HazardCl` | `Extremely High`, `High`, `Significant`, `Low` |
| `FailedStr` | Failed structure: `MainDam` / `Main Dam`, `SaddleDam`, `SaddleDam1`–`13`, `SaddleDamB`–`S`, `Spillway`, `Spillway1`–`2`, `Outlet1`–`2`, `North Dike`, `IntakeCanalGates`, … (inconsistent spelling) |

`HazardCl` is the dam's regulatory hazard classification (consequences if it fails), not the likelihood of failure. Per DSOD's item description, inundation boundaries usually show flooding deeper than 1 ft, some details may be redacted, **federally owned dams are not included**, and maps are updated at least every 10 years (Water Code §6161).

### In the +2 km envelope: 26 features from 20 dams

All `LoadingScn` = Sunny Day. Eagle Rock (2 structures), Reservoir No 5 (Scenarios 1–3), Devils Gate, Encino (MainDam, SaddleDam1), Blanchard Debris Basin (Scenarios 1–2), Reservoir No 4 (Scenarios 1–2), Big Tujunga No. 1 (MainDam, Spillway1), Stough Debris Basin (Scenarios 1–2), Glenoaks 968 Reservoir, La Tuna Debris Basin, Silver Lake, Lower Sunset Debris Basin, East Glorietta, Brand Park, Mulholland, Chevy Chase 1290, Brand Debris Basin, Diederich Res. `PubDate` ranges from 2019-04-30 to 2024-04-22.

**One dam can have several features** (different scenarios or failed structures). `dam_inundation` should return all matching features, not just one per dam.

## Check 6 — Coded values (hazard layers)

None of the hazard layers define coded-value domains. Meanings come from value pairs in the data.

### CAL FIRE FHSZ (LRA 2025)

Statewide count 9,752. Distinct `(SRA, FHSZ, FHSZ_Description)`:

| `FHSZ` | `FHSZ_Description` |
|---|---|
| -3 | NonWildland |
| 1 | Moderate |
| 2 | High |
| 3 | Very High |

`SRA` is always `LRA` in this layer. In the +2 km envelope: 6 Moderate, 5 NonWildland, 5 High, 2 Very High features.

**Official definitions** (CAL FIRE item metadata, item `018035e18cdc4778afcbe06185c01426`, owner `prefire.calfire`: <https://www.arcgis.com/sharing/rest/content/items/018035e18cdc4778afcbe06185c01426/info/metadata/metadata.xml>). The same definitions are given for `FHSZ` and `FHSZ_Description`:

| `FHSZ` | Official definition |
|---|---|
| -3 | "Unzoned, Non Wildland" |
| 1 | "Moderate Fire Hazard Severity Zone" |
| 2 | "High Fire Hazard Severity Zone" |
| 3 | "Very High Fire Hazard Severity Zone" |

- `SRA`: "The type of entity responsible for fire protection." Only value: "LRA = Local Responsibility Area". Zones are classified under Government Code §§51175–51189. Gov. Code §51177(e) defines a local agency as "a city, county, city and county, or district responsible for fire protection".
- **Not found in official sources:** any further explanation of "Non Wildland" beyond "Unzoned, Non Wildland", and a CAL FIRE definition of State Responsibility Area (the SRA 2023 item cites PRC §§4201–4204 and 14 CCR §2201 without defining it). The CAL FIRE OSFM FHSZ web page returned 403 to automated requests.
- **Map date:** the item snippet says "map dated March 24, 2025, and representing all phases of the LRA rollout". Phase items are dated January 22, February 24, March 10 and March 24, 2025.
- **Effective date: there is no single statewide date.** LRA zones take effect when each city or county adopts a local ordinance. Glendale's adoption date wasn't researched. For comparison, SRA 2023 maps were "adopted on April 1, 2024".

### FEMA NFHL (layer 28)

In the +2 km envelope (76 features), all `DFIRM_ID` = `06037C` (LA County FIRM) and all `STUDY_TYP` = `NP`:

| `FLD_ZONE` | `ZONE_SUBTY` | `SFHA_TF` | Features |
|---|---|---|---|
| X | 0.2 PCT ANNUAL CHANCE FLOOD HAZARD | F | 25 |
| X | AREA OF MINIMAL FLOOD HAZARD | F | 7 |
| D | (null) | F | 21 |
| A | 1 PCT ANNUAL CHANCE FLOOD HAZARD CONTAINED IN CHANNEL | T | 7 |
| A | (null) | T | 5 |
| AO | (null) | T | 8 |
| AE | (null) | T | 3 |

- `SOURCE_CIT` values: `06037C_FIRM1`, `06037C_BASE8`, `06037C_LOMC44`, `06037C_STUDY1`, `06037C_STUDY2`, `NP`.
- Earlier in-city results were X, D and A only; the AE and AO zones are in the buffer outside the city.

**Official definitions.** Sources:
- **[DT]** FEMA *Domain Tables Technical Reference* (Dec 2020): <https://www.fema.gov/sites/default/files/documents/fema_domain-tables-technical-reference.pdf>. A Nov 2024 edition may exist and wasn't checked.
- **[FT]** FEMA *FIRM Database Technical Reference* (Nov 2022): <https://www.fema.gov/sites/default/files/documents/fema_firm-database-technical-reference_112022.pdf>
- **[CFR]** 44 CFR §64.3, zone definitions (read via Cornell LII copy): <https://www.law.cornell.edu/cfr/text/44/64.3>

| Field / value | Official meaning |
|---|---|
| `FLD_ZONE` **A** | "Area of special flood hazard without water surface elevations determined" [CFR] |
| **AE** (A1–30) | "Area of special flood hazard with water surface elevations determined" [CFR] |
| **AO** | "Area of special flood hazards having shallow water depths and/or unpredictable flow paths between (1) and (3) ft" [CFR] |
| **D** | "Area of undetermined but possible, flood hazards" [CFR] |
| **X shaded** (formerly B) | "Areas of moderate flood hazards or areas of future-conditions flood hazard" [CFR] |
| **X unshaded** (formerly C) | "Area of minimal hazards" [CFR] |
| `ZONE_SUBTY` 0.2 PCT ANNUAL CHANCE FLOOD HAZARD | 0.2%-annual-chance (500-year) flood hazard, "typically symbolized as shaded Zone X" [DT §2.82] |
| AREA OF MINIMAL FLOOD HAZARD | The term "is used by… CFR 64.3 to define an unshaded Zone X" [DT §2.82] |
| 1 PCT ANNUAL CHANCE FLOOD HAZARD CONTAINED IN CHANNEL | 1%-annual-chance flooding within a channelized structure, used "only… if the flooding associated with these channelized structures… is large enough to show as an area" [DT §2.82]. Allowed under zones A and AE [FT Table 14]. "PCT" is used because "%" is a reserved character [FT]. |
| `SFHA_TF` | "If the area is within a SFHA this field would be true. This field will be true for any area coded as an A or V flood zone area. It should be false for any X or D flood areas." Values T / F / U (unknown). [FT p.46] |
| `STUDY_TYP` **NP** | "NP; unshaded-X zones" [DT §2.67]. Separately, [FT §7.3] uses text "NP" for data intentionally not populated. Other `STUDY_TYP` values describe BFE and floodway status (e.g. "SFHA without BFE", "SFHA with BFE and floodway", "Shaded Zone X with depths less than 1'"). |
| **-9999** in numeric fields | The null value for numeric fields, meaning the field does not apply [FT §7.3]. Different from **-8888** (intentionally not populated). |
| `DEPTH` | "the depth for Zone AO areas… shown beneath the zone label on the FIRM… only populated if a depth is shown on the FIRM" [FT p.46] |
| `LEN_UNIT` | "the measurement system used for the BFEs and/or depths. Normally this would be feet." Domain values FT / CM, though the service returns `Feet` [FT p.46] |
| `STATIC_BFE` | Constant base flood elevation across the polygon, "normally… in lakes or coastal zones" [FT] |
| `VELOCITY` | "Normally… applicable to alluvial fan areas (certain Zone AO areas)" [FT] |
| `DFIRM_ID` | "Study Identifier": state + county FIPS + "C" for countywide studies (`06037C` = Los Angeles County, CA) [FT p.45] |
| `FLD_AR_ID` | "Primary key for table lookup. Assigned by table creator." [FT] |
| `SOURCE_CIT` | "Abbreviation used in the metadata file when describing the source information", matching the L_Source_Cit table [FT] |

**Notes:**
- Zone D's meaning ("undetermined but possible") confirms it must not be presented as "no flood hazard".
- FEMA docs spell the subtype "0.2-PERCENT-ANNUAL-CHANCE"; the service returns "0.2 PCT ANNUAL CHANCE". Use the service's spelling when matching values.
- The docs say `SFHA_TF` is T for A zones; the data matched that (A, AE, AO = T; X, D = F).

### CGS zones (Alquist-Priolo, liquefaction, landslide)

- No domains. `REVISED` / `MAP_REVISED` / `ZN_REVISED` are `Y` / `N`. `COMMENTS` is null in every feature near Glendale.
- **Alquist-Priolo, +2 km envelope (6 features):** Los Angeles (zone 2017-06-15, revised Y), Sunland (1979-01-01), **Sunland again (1976-01-01)**, Pasadena (2025-11-20), Hollywood (2014-11-06), Burbank (1979-01-01). The Sunland quad has two features with different release dates; check in Phase 3 whether they overlap or are separate zones.
- **Liquefaction, +2 km envelope:** 54 features. Quads Pasadena (34), Los Angeles (7), Burbank (6), Hollywood (5), Condor Peak (1, released 2003-04-17), Sunland (1). All others released 1999-03-25.
- **Landslide, +2 km envelope:** 1,202 features. Pasadena 521, Burbank 252, Sunland 139, Hollywood 137, Los Angeles 133, Condor Peak 20.

## Still not checked

- Hazard results at real geocoded addresses (Phase 4 golden tests will cover this).
- Other possibly useful sources: LA County evacuation zones, extreme heat, air quality. The CGS tsunami layer isn't relevant inland.
