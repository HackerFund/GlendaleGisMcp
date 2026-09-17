# City of Glendale Sources — Phase 0 Findings

*Checked September 16, 2026. Scripts used a `GlendaleGisMcp-discovery/0.1 (ryan@hacker.fund)` User-Agent with about 0.3 s between requests.*

All layers are on `https://gismap.glendaleca.gov/arcgis/rest/services/Common/` (ArcGIS Server 10.91).

## Layer inventory

| Dataset | Layer path | Geometry | Features | Key fields |
|---|---|---|---|---|
| City boundary | `Glendale_City_Boundary/MapServer/0` | Polygon | 1 | `CITYNAME` |
| Fire stations | `FireStations/FeatureServer/0` | **Multipoint** | 9 | `NAME`, `ADDRESS`, `CITY`, `sta_no` |
| Fire station districts | `FireStationDistricts/FeatureServer/0` | Polygon | 10 | `Fire_Distr` |
| Police station | `GlendalePoliceStation/FeatureServer/0` | **Polygon** | 1 | `Name` |
| Hospitals | `GlendaleHospitals/FeatureServer/0` | Point | 3 | `NAME`, `ST_NUM`, `ST_DIR`, `ST_NAME`, `ST_TYPE` |
| Schools | `GlendaleSchools/FeatureServer/0` | Point | 27 | `SCHOOL`, `ADDRESS`, `School_typ` |
| Libraries | `Libraries/FeatureServer/0` | Point | 8 | `NAME`, `ST_NUM`, `ST_DIR`, `ST_NAME`, `ST_TYPE` |
| Parks | `Parks/FeatureServer/0` | **Polygon** | 43 | `NAME_ALF` (name), `NAMEA_ALF` (address) |
| Bus stops (Beeline) | `GlendaleBeeline_BusStops/FeatureServer/0` | Point | 338 | `Stop_Numbe`, `Route`, `On_Street`, `At_Street` |
| ZIP codes | `GlendaleZIPCodes/FeatureServer/0` | Polygon | 24 | `ZIPCODE` |
| Neighborhood zones | `NeighborhoodZones/FeatureServer/1` (**layer 1**, not 0) | Polygon | 37 | `NAME` |
| Zoning | `Zoning/FeatureServer/2` | Polygon | 2,426 | `ZONENUM`, `ZONE_DISTR`, `ZONE_DESC`, `GENPLAN`, `GPLANDESC`, `Type`, `LOCATION` |
| Streets | `Streets/FeatureServer/0` | Polyline | 8,070 | `FullName`, `Type`, `Surface`, `Status`, `DrivingDir`, address ranges, ZIP and city per side |
| Parcels (live only) | `Zoning/FeatureServer/1` | Polygon | 54,300 | not inspected |

All checked layers: `maxRecordCount` 2000, **no `editingInfo.lastEditDate`**, **no `GlobalID` field**. The `ref.global_id` in tool results will be null for city data, and freshness needs a fallback (feature count or content hash at snapshot time).

---

## Check 1 — City geocoder

**Service:** `Common/CAD_SiteAddress_Street/GeocodeServer`

### Service properties

| Property | Value |
|---|---|
| Capabilities | `Geocode`, `ReverseGeocode`, `Suggest` |
| Single-line input field | `SingleLine` |
| Multi-field inputs | `Address`, `Address2`, `Address3`, `Neighborhood`, `City`, `Subregion`, `Region`, `Postal`, `PostalExt`, `CountryCode` |
| Native spatial reference | **2229** (CA State Plane Zone V, US feet) |
| `MinimumCandidateScore` | 70 |
| `MinimumMatchScore` | 75 |
| Max batch size | 1000 |
| Countries | US |
| Locator version | 11.0 |

Candidate fields include `Score`, `Status`, `Match_addr`, `LongLabel`, `ShortLabel`, `Addr_type`, `AddNum`, `StName`, `StAddr`, `City`, `Subregion`, `Region`, `Postal`, `UnitType`, `UnitName`, `X`, `Y`, `DisplayX`, `DisplayY` and extent fields. Example (clean match):

```json
{"Status": "M", "Score": 100, "Match_addr": "613 E BROADWAY, GLENDALE, CA, 91206",
 "Addr_type": "PointAddress", "AddNum": "613", "StName": "E BROADWAY", "City": "GLENDALE",
 "Subregion": "LOS ANGELES COUNTY", "Region": "CA", "Postal": "91206",
 "X": 6486557.15, "Y": 1875831.32, "DisplayX": 6486557.15, "DisplayY": 1875831.32}
```

### Test results (`findAddressCandidates`, `outSR=4326`, `maxLocations=5`)

Response time was about 0.5 s for every request.

| Input | Candidates | Top result | Notes |
|---|---|---|---|
| `613 E Broadway, Glendale, CA 91206` | 2 | 100, PointAddress, 91206 | 2nd: 98.6 StreetAddress with a **different ZIP (91205)**, ~40 m away |
| `613 E Broadway` | 2 | 100 PointAddress **and** 100 StreetAddress | Tie at 100 without city/ZIP |
| `613 e broadway glendale` | 2 | same as above | Case-insensitive |
| `613 E Brodway` (misspelled) | 3 | 95.36 PointAddress (tie with StreetAddress) | 3rd: 81.98 **613 W Broadway** (wrong side of town) |
| `613 Broadway` (no direction) | 4 | 91.18 PointAddress E Broadway, **tied** with 613 W Broadway | Status `T` on tied candidates |
| `Brand Blvd` (street only) | 4 | 82.08 StreetName, Status `T` | One candidate per ZIP segment (91202 / 91203 / 91204 / 91207) |
| `1100 N Brand Blvd Apt 4` | 1 | 100 PointAddress | **Unit silently dropped**; `UnitType`/`UnitName` empty |
| `1100 N Brand Blvd #4` | 1 | 100 PointAddress | Same |
| `99999 E Broadway` (bad number) | 1 | 71.43 StreetName | Falls back to the street's midpoint |
| `Brand Blvd & Broadway` | 2 | 91.04 StreetInt | Intersections work |
| `275 E Olive Ave, Burbank, CA` | 3 | **96.19 StreetAddress, City BURBANK** | **Outside Glendale but matched** |
| `100 N Garfield Ave, Pasadena, CA` | 0 | — | |
| `3900 La Crescenta Ave, Glendale CA` | 3 | 97.14 StreetAddress, ZIP 91020 | Inside city |
| `Glendale Galleria` | 1 | 95.89 **StreetName "GLENDLE GALLERIA"** | No place/POI search; matched a misspelled street record |
| `asdf qwerty` | 0 | — | |
| `2800 Foothill Blvd, La Crescenta, CA 91214` | ≥1 | **100 PointAddress, City LA CRESCENTA** | **Outside the city boundary (unincorporated LA County)** |
| `4500 Dunsmore Ave, La Crescenta, CA` | ≥1 | 93.71 StreetAddress, City GLENDALE | Inside city |
| `2300 Honolulu Ave, Montrose, CA 91020` | ≥1 | 96.67 StreetAddress, City GLENDALE | Inside city |
| `1000 W Glenoaks Blvd, Glendale` | ≥1 | 100 PointAddress | Inside city |
| `3300 Community Ave, La Crescenta` | 2 | 97.18 StreetAddress (inside city) | 2nd: 94.81 StreetName, City LA CRESCENTA, **outside** the city |

"Inside/outside city" was tested with point-in-polygon against the full-resolution city boundary (2,769 vertices), fetched with `outSR=4326`.

### Other operations

- **`suggest`** (`text=613 E Broa`) returned 2 suggestions (`613 E BROADWAY, GLENDALE, CA, 91206` and `613 W BROADWAY, GLENDALE, 91204`), each with a `magicKey`.
- **`reverseGeocode`** needs `location` as JSON with a spatial reference, e.g. `{"x":-118.2553,"y":34.1461,"spatialReference":{"wkid":4326}}`. Plain `x,y` fails with *"Unable to find address for the specified location"*. With JSON it returned `101 S BRAND BLVD, GLENDALE, CA, 91210` (PointAddress).

### Findings

1. **Match types:** `PointAddress` (a site-address point, the most precise), `StreetAddress` (interpolated along a street segment), `StreetName` (street midpoint, no house number), `StreetInt` (intersection). Categories also list `Subaddress`, but units never matched.
2. **Scores:** 70 is the lowest returned, 75 the service's "match" threshold. Real matches were 91–100; street-only and bad-number fallbacks were 71–82.
3. **`Status`:** `M` = matched, `T` = tied with another candidate.
4. **The locator covers more than Glendale.** It matched Burbank and unincorporated La Crescenta addresses at up to 100. **The `City` field is not reliable for deciding "in Glendale"** — use point-in-polygon against the city boundary.
5. **Mailing names ≠ city limits.** Montrose and parts of La Crescenta are inside Glendale; other La Crescenta addresses are unincorporated county.
6. **Duplicates:** the same address often comes back as both `PointAddress` and `StreetAddress`, sometimes with different ZIPs. Prefer `PointAddress`, then the highest score.
7. **Units are ignored** (no error, no unit fields filled).
8. **No place-name search.**
9. **`X`, `Y`, `DisplayX`, `DisplayY` and the extent fields stay in native feet (2229) even with `outSR=4326`.** Only `candidate.location` is reprojected. Always read coordinates from `location`.

### Proposed `geocode_address` rules (for Phase 5)

- Drop candidates outside the city boundary (with a small tolerance) and say so. If all are outside, return the "outside Glendale" error.
- Collapse PointAddress/StreetAddress duplicates within about 50 m, keeping PointAddress.
- **Confident match** = one remaining candidate with score ≥ 90 and `Addr_type` of `PointAddress` or `StreetAddress`, or a PointAddress at least 5 points above the next candidate. Otherwise return candidates.
- `StreetName` results are never a confident match (no house number).
- If the input contained a unit, include a note that it was ignored.
- These thresholds are a starting point; tune them in Phase 5 with more addresses.

---

## Check 5 — Fire station geometry

All 9 features are `Multipoint` with **exactly one point each**, and all are inside the city boundary. Treat each as a single point.

| OBJECTID | NAME | ADDRESS | sta_no |
|---|---|---|---|
| 1 | Fire Station 21 | 421 Oak Street | `" 21"` |
| 2 | Fire Station 22 | 1201 S. Glendale Ave. | `" 22"` |
| 3 | Fire Station 23 | 3303 E. Chevy Chase Dr. | `" 23"` |
| 4 | Fire Station 24 | 1734 Canada Blvd. | `" 24"` |
| 5 | Fire Station 25 | 353 N. Chevy Chase Dr. | `" 25"` |
| 6 | Fire Station 26 | 1145 N. Brand Blvd. | `" 26"` |
| 7 | Fire Station 27 | 1127 Western Ave. | `" 27"` |
| 8 | Fire Station 28 | 4410 New York Ave. | `" 28"` |
| 9 | Fire Station 29 | 2465 Honolulu Ave. | `" 29"` |

- `sta_no` has a **leading space**; trim it.
- **Other resource layers aren't points either:** the police station (1 feature) and parks (43) are polygons. For `nearest_resources`, measure distance to the polygon edge (0 when inside) and also return a representative point.
- Hospitals and libraries split addresses into `ST_NUM` / `ST_DIR` / `ST_NAME` / `ST_TYPE`; schools and fire stations have a single `ADDRESS`; parks keep the address in `NAMEA_ALF`. Normalize to one `address` field in results.
- Hospitals' OBJECTIDs are 1, 4, 5 (gaps from deleted rows); don't assume sequential IDs.
- Fire station districts include `24P` besides `21`–`29`. **Its meaning isn't documented** in the service or any source found; there is no station 24P. It's a small polygon (area ~4.0M vs ~93.7M layer units for district 24) in the northeast part of district 24's extent (lon -118.220 to -118.209, lat 34.191–34.196). Report the value as-is; don't assume it means district 24.

---

## Check 6 — Coded values (city layers)

### Zoning (`Zoning/FeatureServer/2`)

- **`ZONENUM` has a coded-value domain** (`dZoneCode`, "Zoning District Abv") with 159 codes; the data uses 73 of them. Domain names match `ZONE_DISTR` exactly, so `ZONE_DISTR` can be used directly.
- `ZONE_DESC` spells out the district (e.g. `R 3050` → `MODERATE DENSITY RESIDENTIAL`). Spelling varies slightly (`HISTORIC DIST` / `HISTORIC DISTRICT`).
- **`Type` has no domain of its own.** Values: 100, 200, 300, 400, 500, 600, 700, 800, 900 and null. They match the **`dZonePlanTyp` coded-value domain attached to the `GENPLAN` field** in the same layer, even though `GENPLAN` itself mostly holds zone-level codes (410, 530, …). Applying that domain to `Type` is an **inference from matching values**; no metadata says so.

| `Type` | `dZonePlanTyp` name | Zones seen with this `Type` |
|---|---|---|
| 100 | RESIDENTIAL OPEN SPACE | ROS II, ROS III |
| 200 | RESTRICTED RESIDENTIAL | R1R I–III (+HD), one ROS III |
| 300 | RESIDENTIAL | R1 I–III (+H, P, PRD, HD) |
| 400 | MODERATE TO HIGH DENSITY RESIDENTIAL | R 3050, R 2250, R 1650, R 1250 (+PPD, H, P, PS) |
| 500 | COMMERCIAL | C1–C3, CR, CPD, MS, CE, CEM, CH, CA, DSP/*, TOD I/II |
| 600 | SOUTH BRAND BOULEVARD SPECIFIC PLAN | CE (1 feature) |
| 700 | INDUSTRIAL / TRANSPORTATION | IND, T, IMU, SFMU, IMU R, TOD I |
| 800 | OTHER | SR, SR PPD |
| 900 | RECREATION AREA | DSP/TD (2 features) |

  Some rows don't fit (ROS III under 200, CE under 600, DSP/TD under 900, TOD I under both 500 and 700, nulls), so **don't use `Type` for classification**; use `ZONE_DISTR` instead.

- **Official zone definitions** from Glendale Municipal Code Title 30 (Zoning):
  - The official code site (ecode360) blocked automated access, and glendaleca.gov returned 403. The text below was read from search snippets of the ecode360 pages and from Zoneomics' copy of Title 30 (<https://www.zoneomics.com/code/glendale-CA>), a third-party site. Confirm on the official pages before quoting in user-facing docs.
  - **Residential (GMC §30.10.010, Ch. 30.10 <https://ecode360.com/43352035>; Ch. 30.11 <https://ecode360.com/43352065>):** "ROS Residential Open Space Zone, R1R Restricted Residential Zone, R1 Residential Zone, R-3050 Moderate Density Residential Zone, R-2250 Medium Density Residential Zone, R-1650 Medium-High Density Residential Zone, R-1250 High Density Residential Zone."
  - **The number is the minimum lot area per dwelling unit (sq ft)** — confirmed: "The R-3050 zone is intended primarily as a zone for moderate density residential development with a minimum of 3,050 square feet of lot area per dwelling unit." Same pattern for 2,250 / 1,650 / 1,250.
  - **FAR Districts I / II / III** (ROS, R1R, R1; Table 30.11-B) are floor-area-ratio schedules: "District I: 0.30 for the 1st 10,000 sq. ft. of lot area and 0.10 for the portion of lot area thereafter. District II: 0.40 … District III: 0.45 … and 0.10 thereafter." How a lot is assigned to a district wasn't found.
  - **Overlay zones** (§30.10.010): "PRD Planned Residential Development Overlay Zone, H Horse Overlay Zone, P Parking Overlay Zone, PS Parking Structure Overlay Zone, PPD Precise Plan of Design Overlay Zone, HD Historic District Overlay Zone."
    - PRD: Ch. 30.20, <https://ecode360.com/43352519>
    - H: Ch. 30.21, <https://ecode360.com/43352623>
    - P: Ch. 30.22, <https://ecode360.com/43352640>, "for commercial and industrial parking areas as an interim use in residential zones"
    - PS: <https://ecode360.com/43352651>
    - ASOZ = Advertising Signage Overlay Zone, Ch. 30.26, <https://ecode360.com/43352814>
  - **Commercial:** C1 Neighborhood Commercial, C2 Community Commercial, C3 Commercial Service, CR Commercial Retail, CPD Commercial Planned Development, CH Commercial Hillside, CA Commercial Auto.
  - **Industrial:** IND Industrial, T Transportation.
  - **Mixed use:** IMU Industrial/Commercial Mixed Use, IMU-R Industrial/Commercial-Residential Mixed Use, SFMU Commercial/Residential Mixed Use, DSP Downtown Specific Plan, TOD Transit Oriented Development District I / II.
  - **Special:** SR Special Recreation, CE Commercial Equestrian, CEM Cemetery, MS Medical Service.
  - **Height districts** (Table 30.12-B, mapped on the "1986 Zoning and Height District Map", §30.10.050): C2 District I 35 ft; II 45 ft / 3 stories. C3 District I 50 ft / 3 stories; II 65 ft / 4 stories; III 90 ft / 6 stories; IV 35 ft. The C2 vs C3 row split is read from a copy whose table layout was unclear, but it matches the GIS codes (C2 I–II, C3 I–IV).
  - **Not confirmed:**
    - The meaning of MS I / II / III.
    - The official pairing of DSP subarea codes (`GAT`, `OC`, `CC`, `EB`, `AT`, `MO`, `TD`, `BC`, `AE`, `GAL`, `TCSP`) with names. `ZONE_DESC` in the data already spells them out (e.g. `DSP / GATEWAY`), so the data doesn't depend on this.
    - The `PSD` / `DD` codes in the `ZONENUM` domain.

- **Housing-type hints for the challenge:**
  - `ROS`, `R1R`, `R1`: single-family residential zones.
  - `R-3050` / `R-2250` / `R-1650` / `R-1250`: multifamily at increasing density (minimum lot area per unit).
  - `SFMU`, `IMU-R`, `TOD`, `DSP`: mixed use or downtown zones where residential may be allowed.
  - Zoning is what's *allowed*, not what's built.
- `GPLANDESC` (general plan) values: VERY LOW DENSITY/OPEN SPACE, "Very Low Density Residential/Open Space" (inconsistent casing), LOW DENSITY, LOW DENSITY RESIDENTIAL PARKING, MODERATE DENSITY, MEDIUM DENSITY, MEDIUM HIGH DENSITY, HIGH DENSITY, NEIGHBORHOOD, COMMUNITY/SERVICES, REGIONAL, DOWNTOWN SPECIFIC PLAN, TOWN CENTER SPECIFIC PLAN, MIXED USE, INDUSTRIAL, PUBLIC/SEMI-PUBLIC, SPECIAL RECREATION, CEMETERY.
- `LOCATION` was null in all of the first 2,000 features.

### Bus stops

- `On_Street` starts with a travel direction: `NB` 99, `SB` 96, `WB` 71, `EB` 70 (plus 2 odd values, "Burbank …" and "Glendale …").
- `At_Street` starts with stop position relative to the cross street: `FS` 177, `NS` 154, plus `Ns`, `Mid`, `opp.`, `Circle` and blanks.
  - **NS = near side, FS = far side.** No Glendale Beeline source defines these; the layer has no description. The standard transit-industry meaning, from TransLink (Vancouver's transit agency): "farside (FS), nearside (NS), and mid-block bus stops. Nearside is the side of an intersection before you cross and farside is the side after. Mid-block bus stops are located in between intersections." (<https://buzzer.translink.ca/2014/10/translink-101-what-does-farside-and-nearside-bus-stop-mean/>)
  - `Mid` = mid-block (same source). `opp.` = opposite is not confirmed by any official source.
- `Route` is a comma-separated string of Beeline route numbers (`3`, `7`, `3,7`, `1,2,11,12`, …). Routes seen: 1, 2, 3, 4, 5, 6, 7, 11, 12. Split on commas.
- `Stop_Numbe` is a float (e.g. `354.0`); convert to int.

### Streets

- Source: LA County CAMS streets, modified by the city (per the layer description).
- `Type`: Minor, Secondary, Primary, Freeway, Ramp, Private Road, Unpaved Road, Railroad, Driveway, Alley, Trail, Planned Road, Unknown. (Railroad and Trail features are in the "streets" layer.)
- `Surface`: Paved, Dirt, Unknown. `Status`: Unrestricted, Restricted, Unknown. `DrivingDir`: Two Way, One Way With Arc Direction, Unknown. `Elevation`: Surface, Unknown.
- **The layer extends past city limits:** `LCity_L` includes Glendale, Los Angeles, Burbank, La Canada Flintridge, Pasadena, Unincorporated and null. The snapshot's boundary clip will handle it.

### Other city layers

- **School types:** Elementary 18, High School 5, Middle School 3, Community College 1.
- **Fire districts:** 21, 22, 23, 24, 24P, 25, 26, 27, 28, 29.
- **Neighborhood zones (37):** Adams Hill, Brockmont, Chevy Chase, Citrus Grove, City Center, College Hills, Crescenta Highlands, El Miradero, Emerald Isle, Fremont Park, Glenoaks Canyon, Glenwood, Grand Central, Grandview, Greenbriar, Mariposa, Montecito Park, Montrose, Moorpark, Oakmont, Pacific-Edison, Pelanconi, Rancho San Rafael, Riverside Rancho, Rossmoyne, San Gabriel Mountains, San Rafael, Scholl Canyon, Somerset, Sparr Heights, Tropico, Verdugo Mountains, Verdugo Viejo, Verdugo Woodlands, Vineyard, Whiting Woods, Woodbury.
- **ZIP codes (24)** include neighboring ZIPs that only partly overlap the city: 90027, 90039, 90041, 90065, 91011, 91020, 91042, 91046, 91103, 91105, 91201–91208, 91210, 91214, 91352, 91501, 91502, 91506. Being in a listed ZIP doesn't mean being in Glendale.

---

## Cross-cutting gotcha: silent truncation at `maxRecordCount`

A plain query on zoning (2,426 features) returned exactly **2,000 features with `exceededTransferLimit: true`** and no error. The same happened with streets and statewide FHSZ in exploratory queries. **Any query that doesn't page will silently return partial data.** The ArcGIS client must check `exceededTransferLimit` and page with `resultOffset` + `orderByFields=OBJECTID` until it's false. `returnDistinctValues=true` and `returnCountOnly=true` returned complete results.
