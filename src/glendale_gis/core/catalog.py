"""Registry of every dataset the server exposes.

The catalog is the single source of truth for what can be queried: the ArcGIS client's host and
path allowlist is derived from it. Field meanings come from `Plans/hazard-sources.md` and
`Plans/city-sources.md`; meanings not stated by an official source are marked ``inferred``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

Category = Literal["hazard", "resource", "reference"]
Access = Literal["snapshot", "live"]
Buffer = Literal["city", "hazard"]
Geometry = Literal["point", "polygon", "polyline"]

GLENDALE_COMMON = "https://gismap.glendaleca.gov/arcgis/rest/services/Common"
ARCGIS_ITEM_URL = "https://www.arcgis.com/sharing/rest/content/items/{item_id}"

GLENDALE_DISCLAIMER = (
    "City of Glendale GIS data is provided for general information and is not a substitute "
    "for legal descriptions or actual surveys."
)
REGULATORY_MAP_DISCLAIMER = (
    "Regulatory hazard zone map, not a site-specific assessment. Being outside a mapped zone "
    "does not mean there is no hazard."
)


@dataclass(frozen=True)
class FieldDoc:
    name: str
    description: str
    values: Mapping[str, str] = field(default_factory=dict)
    inferred: bool = False  # True when the meaning isn't stated by an official source


@dataclass(frozen=True)
class Dataset:
    id: str
    title: str
    category: Category
    source_agency: str
    layer_url: str
    geometry: Geometry
    access: Access = "snapshot"
    buffer: Buffer = "city"
    description: str = ""
    fields: tuple[FieldDoc, ...] = ()
    id_field: str = "OBJECTID"
    class_field: str | None = None  # nearest-zone results are grouped by this field
    # Classes the source defines as outside any zone (e.g. CAL FIRE "Unzoned, Non Wildland").
    # Being inside one of these polygons does not make a location "in_zone".
    unzoned_classes: tuple[str, ...] = ()
    name_field: str | None = None  # resources: display name
    address_fields: tuple[str, ...] = ()  # resources: joined with spaces into one address
    disclaimer: str = ""
    docs: tuple[str, ...] = ()  # official documentation URLs
    item_id: str | None = None  # ArcGIS Online item used to re-resolve a moving service URL
    allowed_prefix: str | None = None  # wider URL prefix allowed when the service URL can move

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)


@dataclass(frozen=True)
class Service:
    """A non-layer ArcGIS endpoint the server may call, with the operations it may use."""

    id: str
    url: str
    operations: frozenset[str]


# --------------------------------------------------------------------------------------------
# Hazards
# --------------------------------------------------------------------------------------------

_CGS_ZONE_FIELDS = (
    FieldDoc("QUAD_NAME", "USGS 7.5-minute quadrangle the zone map covers."),
    FieldDoc("RELEASED", "Date the zone map was released."),
    FieldDoc("REVISED", "Whether the map has been revised.", {"Y": "Yes", "N": "No"}),
    FieldDoc("PREV_DATES", "Earlier release dates, semicolon-separated."),
    FieldDoc("GEOPDFLINK", "Link to the official zone map (GeoPDF)."),
    FieldDoc("REPORTLINK", "Link to the seismic hazard zone report."),
)

HAZARDS: tuple[Dataset, ...] = (
    Dataset(
        id="calfire_fhsz_lra",
        title="Fire Hazard Severity Zones (Local Responsibility Area, 2025)",
        category="hazard",
        source_agency="CAL FIRE",
        layer_url="https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/FHSALRA25_v1_All/FeatureServer/0",
        geometry="polygon",
        buffer="hazard",
        description=(
            "Wildfire hazard severity zones mapped by CAL FIRE for areas where local agencies "
            "provide fire protection. All of Glendale is a Local Responsibility Area, so every "
            "location in the city has a class. Map dated March 24, 2025; zones take effect when "
            "the city adopts them by ordinance. The zones rate the long-term physical hazard of "
            "the landscape (fuels, terrain, weather), not the risk to a particular building. "
            "NonWildland means unzoned, not free of wildfire risk: embers and "
            "structure-to-structure spread can reach unzoned areas. Not an evacuation map."
        ),
        fields=(
            FieldDoc(
                "SRA",
                "The type of entity responsible for fire protection.",
                {"LRA": "Local Responsibility Area"},
            ),
            FieldDoc(
                "FHSZ",
                "Hazard severity code. -3 (unzoned) does not mean there is no wildfire risk.",
                {
                    "-3": "Unzoned, Non Wildland",
                    "1": "Moderate Fire Hazard Severity Zone",
                    "2": "High Fire Hazard Severity Zone",
                    "3": "Very High Fire Hazard Severity Zone",
                },
            ),
            FieldDoc(
                "FHSZ_Description",
                "Hazard severity name. NonWildland (unzoned) does not mean there is no wildfire "
                "risk.",
                {
                    "NonWildland": "Unzoned, Non Wildland",
                    "Moderate": "Moderate Fire Hazard Severity Zone",
                    "High": "High Fire Hazard Severity Zone",
                    "Very High": "Very High Fire Hazard Severity Zone",
                },
            ),
        ),
        class_field="FHSZ_Description",
        unzoned_classes=("NonWildland",),
        disclaimer=REGULATORY_MAP_DISCLAIMER,
        docs=(
            "https://www.arcgis.com/sharing/rest/content/items/018035e18cdc4778afcbe06185c01426/info/metadata/metadata.xml",
        ),
    ),
    Dataset(
        id="cgs_fault_zones",
        title="Alquist-Priolo Earthquake Fault Zones",
        category="hazard",
        source_agency="California Geological Survey",
        layer_url="https://services2.arcgis.com/zr3KAIbsRSUyARHG/arcgis/rest/services/CGS_Alquist_Priolo_Fault_Zones/FeatureServer/0",
        geometry="polygon",
        buffer="hazard",
        description=(
            "Regulatory zones around active faults where surface fault rupture is possible. "
            "Being inside a polygon is the hazard signal; attributes only describe the map."
        ),
        fields=(
            FieldDoc("QUAD_NAME", "USGS 7.5-minute quadrangle the zone map covers."),
            FieldDoc("MAP_RELEASED", "Date the fault map was released."),
            FieldDoc("MAP_REVISED", "Whether the fault map was revised.", {"Y": "Yes", "N": "No"}),
            FieldDoc("ZN_RELEASED", "Date the zone boundaries were released."),
            FieldDoc("ZN_REVISED", "Whether the zones were revised.", {"Y": "Yes", "N": "No"}),
            FieldDoc("GEOPDFLINK", "Link to the official zone map (GeoPDF)."),
            FieldDoc("REPORTLINK", "Link to the fault evaluation report."),
        ),
        disclaimer=REGULATORY_MAP_DISCLAIMER,
    ),
    Dataset(
        id="cgs_liquefaction_zones",
        title="Seismic Hazard Zones: Liquefaction",
        category="hazard",
        source_agency="California Geological Survey",
        layer_url="https://services2.arcgis.com/zr3KAIbsRSUyARHG/arcgis/rest/services/CGS_Liquefaction_Zones/FeatureServer/0",
        geometry="polygon",
        buffer="hazard",
        description=(
            "Zones of required investigation where earthquake shaking may cause liquefaction. "
            "Being inside a polygon is the hazard signal; attributes only describe the map."
        ),
        fields=_CGS_ZONE_FIELDS,
        disclaimer=REGULATORY_MAP_DISCLAIMER,
    ),
    Dataset(
        id="cgs_landslide_zones",
        title="Seismic Hazard Zones: Earthquake-Induced Landslide",
        category="hazard",
        source_agency="California Geological Survey",
        layer_url="https://services2.arcgis.com/zr3KAIbsRSUyARHG/arcgis/rest/services/CGS_Landslide_Zones/FeatureServer/0",
        geometry="polygon",
        buffer="hazard",
        description=(
            "Zones of required investigation where earthquake shaking may trigger landslides. "
            "Being inside a polygon is the hazard signal; attributes only describe the map."
        ),
        fields=_CGS_ZONE_FIELDS,
        disclaimer=REGULATORY_MAP_DISCLAIMER,
    ),
    Dataset(
        id="fema_flood_zones",
        title="FEMA National Flood Hazard Layer: Flood Hazard Zones",
        category="hazard",
        source_agency="FEMA",
        layer_url="https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28",
        geometry="polygon",
        buffer="hazard",
        description="Effective flood insurance rate map zones for Los Angeles County (06037C).",
        fields=(
            FieldDoc(
                "FLD_ZONE",
                "Flood zone designation (44 CFR 64.3).",
                {
                    "A": "Area of special flood hazard without water surface elevations determined",
                    "AE": "Area of special flood hazard with water surface elevations determined",
                    "AO": (
                        "Area of special flood hazards having shallow water depths and/or "
                        "unpredictable flow paths between 1 and 3 ft"
                    ),
                    "D": "Area of undetermined but possible flood hazards (not studied, not safe)",
                    "X": "Moderate (shaded) or minimal (unshaded) flood hazard; see ZONE_SUBTY",
                },
            ),
            FieldDoc(
                "ZONE_SUBTY",
                "Zone subtype.",
                {
                    "0.2 PCT ANNUAL CHANCE FLOOD HAZARD": (
                        "0.2%-annual-chance (500-year) flood hazard; shaded Zone X"
                    ),
                    "AREA OF MINIMAL FLOOD HAZARD": "Unshaded Zone X",
                    "1 PCT ANNUAL CHANCE FLOOD HAZARD CONTAINED IN CHANNEL": (
                        "1%-annual-chance flooding contained within a channel"
                    ),
                },
            ),
            FieldDoc(
                "SFHA_TF",
                "Whether the area is in a Special Flood Hazard Area (true for A and V zones).",
                {"T": "True", "F": "False", "U": "Unknown"},
            ),
            FieldDoc("STATIC_BFE", "Constant base flood elevation; -9999 means not applicable."),
            FieldDoc("DEPTH", "Flood depth for Zone AO areas; -9999 means not applicable."),
            FieldDoc("LEN_UNIT", "Unit for elevations and depths (normally feet)."),
            FieldDoc(
                "STUDY_TYP",
                "Study type.",
                {"NP": "Unshaded-X zones / not populated"},
            ),
            FieldDoc("DFIRM_ID", "Study identifier; 06037C is the Los Angeles County study."),
            FieldDoc("SOURCE_CIT", "Source citation abbreviation from the FIRM metadata."),
        ),
        class_field="FLD_ZONE",
        disclaimer=REGULATORY_MAP_DISCLAIMER,
        docs=(
            "https://www.fema.gov/sites/default/files/documents/fema_firm-database-technical-reference_112022.pdf",
            "https://www.fema.gov/sites/default/files/documents/fema_domain-tables-technical-reference.pdf",
            "https://www.law.cornell.edu/cfr/text/44/64.3",
        ),
    ),
    Dataset(
        id="dwr_dam_inundation",
        title="Dam Inundation Areas (DSOD-approved)",
        category="hazard",
        source_agency="California DWR, Division of Safety of Dams",
        layer_url="https://services.arcgis.com/aa38u6OgfNoCkTJ6/arcgis/rest/services/Approved_InundationBoundaries_As_of_Oct01_2025/FeatureServer/100",
        geometry="polygon",
        buffer="hazard",
        description=(
            "Areas that could flood if a dam or one of its structures failed, from inundation "
            "maps approved by DSOD. One dam can have several features (scenarios or failed "
            "structures). Federally owned dams are not included."
        ),
        fields=(
            FieldDoc("NID", "National Inventory of Dams identifier."),
            FieldDoc("StateID", "DSOD dam number."),
            FieldDoc("DamName", "Dam name."),
            FieldDoc(
                "FailedStr", "Structure assumed to fail (e.g. MainDam, SaddleDam1, Spillway1)."
            ),
            FieldDoc("Scenario", "Failure scenario identifier (Scenario1-Scenario6)."),
            FieldDoc(
                "LoadingScn",
                "Loading condition for the failure scenario.",
                {"Sunny Day": "Failure without a storm", "Storm Induced": "Failure during a storm"},
                inferred=True,
            ),
            FieldDoc("PubDate", "Date the inundation map was published."),
            FieldDoc(
                "HazardCl",
                "Dam hazard classification (consequences of failure, not likelihood).",
                {
                    "Extremely High": "Extremely High",
                    "High": "High",
                    "Significant": "Significant",
                    "Low": "Low",
                },
            ),
        ),
        id_field="FID",
        disclaimer=(
            "Inundation maps are for emergency planning and typically show flooding deeper than "
            "one foot; actual evacuation zones are set by local emergency managers."
        ),
        item_id="5354d98898194a4ab7b96eb6c85eecae",
        allowed_prefix="https://services.arcgis.com/aa38u6OgfNoCkTJ6/arcgis/rest/services/",
    ),
    Dataset(
        id="usgs_debris_flow",
        title="Post-Fire Debris-Flow Hazard Assessments (basins)",
        category="hazard",
        source_agency="USGS",
        layer_url="https://earthquake.usgs.gov/arcgis/rest/services/ls/pwfdf/MapServer/4",
        geometry="polygon",
        buffer="hazard",
        description=(
            "Modeled debris-flow likelihood, volume and combined hazard for drainage basins in "
            "recently burned areas. No current assessment covers Glendale; that does not mean "
            "there is no debris-flow risk."
        ),
        fields=(
            FieldDoc("fire_id", "Assessment fire identifier (e.g. eat2025). Casing varies."),
            FieldDoc("BP_Legend", "Debris-flow likelihood class for the design storm (percent)."),
            FieldDoc("BV_Legend", "Potential sediment volume class, in cubic meters."),
            FieldDoc(
                "BCH_Legend",
                "Combined hazard class (Cannon et al. 2010 scheme).",
                {"Low": "Low", "Moderate": "Moderate", "High": "High"},
            ),
            FieldDoc("assessment_date", "Date the assessment was run."),
            FieldDoc("start_date", "Start date of the fire."),
            FieldDoc("version", "Assessment version. Casing varies."),
        ),
        class_field="BCH_Legend",
        disclaimer=(
            "Assessments represent conditions immediately after the fire and do not predict "
            "downstream impacts, runout paths or inundation extent."
        ),
        docs=(
            "https://www.usgs.gov/programs/landslide-hazards/science/scientific-background",
            "https://ghsc.code-pages.usgs.gov/lhp/ocelote/data-spec/archive/1.1.0/data-fields.html",
        ),
    ),
)

# --------------------------------------------------------------------------------------------
# City of Glendale
# --------------------------------------------------------------------------------------------

CITY: tuple[Dataset, ...] = (
    Dataset(
        id="city_boundary",
        title="Glendale City Boundary",
        category="reference",
        source_agency="City of Glendale",
        layer_url=f"{GLENDALE_COMMON}/Glendale_City_Boundary/MapServer/0",
        geometry="polygon",
        fields=(FieldDoc("CITYNAME", "City name."),),
        disclaimer=GLENDALE_DISCLAIMER,
    ),
    Dataset(
        id="fire_stations",
        title="Fire Stations",
        category="resource",
        source_agency="City of Glendale",
        layer_url=f"{GLENDALE_COMMON}/FireStations/FeatureServer/0",
        geometry="point",  # stored as single-point multipoints
        fields=(
            FieldDoc("NAME", "Station name."),
            FieldDoc("ADDRESS", "Street address."),
            FieldDoc("CITY", "City."),
            FieldDoc("sta_no", "Station number (has a leading space in the source)."),
        ),
        name_field="NAME",
        address_fields=("ADDRESS",),
        disclaimer=GLENDALE_DISCLAIMER,
    ),
    Dataset(
        id="fire_station_districts",
        title="Fire Station Districts",
        category="reference",
        source_agency="City of Glendale",
        layer_url=f"{GLENDALE_COMMON}/FireStationDistricts/FeatureServer/0",
        geometry="polygon",
        fields=(
            FieldDoc(
                "Fire_Distr",
                "Fire station district (21-29, plus 24P whose meaning is undocumented).",
            ),
        ),
        disclaimer=GLENDALE_DISCLAIMER,
    ),
    Dataset(
        id="police_stations",
        title="Police Station",
        category="resource",
        source_agency="City of Glendale",
        layer_url=f"{GLENDALE_COMMON}/GlendalePoliceStation/FeatureServer/0",
        geometry="polygon",
        fields=(FieldDoc("Name", "Station name."),),
        name_field="Name",
        disclaimer=GLENDALE_DISCLAIMER,
    ),
    Dataset(
        id="hospitals",
        title="Hospitals",
        category="resource",
        source_agency="City of Glendale",
        layer_url=f"{GLENDALE_COMMON}/GlendaleHospitals/FeatureServer/0",
        geometry="point",
        fields=(
            FieldDoc("NAME", "Hospital name."),
            FieldDoc("ST_NUM", "Street number."),
            FieldDoc("ST_DIR", "Street direction."),
            FieldDoc("ST_NAME", "Street name."),
            FieldDoc("ST_TYPE", "Street type."),
        ),
        name_field="NAME",
        address_fields=("ST_NUM", "ST_DIR", "ST_NAME", "ST_TYPE"),
        disclaimer=GLENDALE_DISCLAIMER,
    ),
    Dataset(
        id="schools",
        title="Schools",
        category="resource",
        source_agency="City of Glendale",
        layer_url=f"{GLENDALE_COMMON}/GlendaleSchools/FeatureServer/0",
        geometry="point",
        fields=(
            FieldDoc("SCHOOL", "School name."),
            FieldDoc("ADDRESS", "Street address."),
            FieldDoc(
                "School_typ",
                "School type.",
                {
                    "Elementary": "Elementary",
                    "Middle School": "Middle School",
                    "High School": "High School",
                    "Community College": "Community College",
                },
            ),
        ),
        name_field="SCHOOL",
        address_fields=("ADDRESS",),
        disclaimer=GLENDALE_DISCLAIMER,
    ),
    Dataset(
        id="libraries",
        title="Libraries",
        category="resource",
        source_agency="City of Glendale",
        layer_url=f"{GLENDALE_COMMON}/Libraries/FeatureServer/0",
        geometry="point",
        fields=(
            FieldDoc("NAME", "Library name."),
            FieldDoc("ST_NUM", "Street number."),
            FieldDoc("ST_DIR", "Street direction."),
            FieldDoc("ST_NAME", "Street name."),
            FieldDoc("ST_TYPE", "Street type."),
        ),
        name_field="NAME",
        address_fields=("ST_NUM", "ST_DIR", "ST_NAME", "ST_TYPE"),
        disclaimer=GLENDALE_DISCLAIMER,
    ),
    Dataset(
        id="parks",
        title="Parks",
        category="resource",
        source_agency="City of Glendale",
        layer_url=f"{GLENDALE_COMMON}/Parks/FeatureServer/0",
        geometry="polygon",
        fields=(
            FieldDoc("NAME_ALF", "Park name."),
            FieldDoc("NAMEA_ALF", "Park address."),
        ),
        name_field="NAME_ALF",
        address_fields=("NAMEA_ALF",),
        disclaimer=GLENDALE_DISCLAIMER,
    ),
    Dataset(
        id="bus_stops",
        title="Beeline Bus Stops",
        category="resource",
        source_agency="City of Glendale",
        layer_url=f"{GLENDALE_COMMON}/GlendaleBeeline_BusStops/FeatureServer/0",
        geometry="point",
        fields=(
            FieldDoc("Stop_Numbe", "Stop number (stored as a float)."),
            FieldDoc("Route", "Comma-separated Beeline route numbers served (e.g. '3,7')."),
            FieldDoc(
                "On_Street",
                "Street the stop is on, prefixed with travel direction.",
                {"NB": "Northbound", "SB": "Southbound", "EB": "Eastbound", "WB": "Westbound"},
                inferred=True,
            ),
            FieldDoc(
                "At_Street",
                "Cross street, prefixed with stop position.",
                {
                    "NS": "Near side (before the intersection)",
                    "FS": "Far side (after the intersection)",
                    "Mid": "Mid-block",
                },
                inferred=True,
            ),
        ),
        name_field="On_Street",
        address_fields=("On_Street", "At_Street"),
        disclaimer=GLENDALE_DISCLAIMER,
    ),
    Dataset(
        id="zip_codes",
        title="ZIP Codes",
        category="reference",
        source_agency="City of Glendale",
        layer_url=f"{GLENDALE_COMMON}/GlendaleZIPCodes/FeatureServer/0",
        geometry="polygon",
        description="Includes neighboring ZIP codes that only partly overlap the city.",
        fields=(FieldDoc("ZIPCODE", "ZIP code."),),
        disclaimer=GLENDALE_DISCLAIMER,
    ),
    Dataset(
        id="neighborhood_zones",
        title="Neighborhood Zones",
        category="reference",
        source_agency="City of Glendale",
        layer_url=f"{GLENDALE_COMMON}/NeighborhoodZones/FeatureServer/1",
        geometry="polygon",
        fields=(FieldDoc("NAME", "Neighborhood name."),),
        disclaimer=GLENDALE_DISCLAIMER,
    ),
    Dataset(
        id="zoning",
        title="Zoning",
        category="reference",
        source_agency="City of Glendale",
        layer_url=f"{GLENDALE_COMMON}/Zoning/FeatureServer/2",
        geometry="polygon",
        description=(
            "Zoning districts (Glendale Municipal Code Title 30). Shows what is allowed, not "
            "what is built."
        ),
        fields=(
            FieldDoc("ZONENUM", "Numeric zone code (coded-value domain dZoneCode)."),
            FieldDoc(
                "ZONE_DISTR",
                "Zone district. R-3050/R-2250/R-1650/R-1250 are multifamily zones named for "
                "the minimum lot area per dwelling unit in square feet; ROS, R1R and R1 are "
                "single-family zones. Suffixes: HD historic district, H horse, P parking, PS "
                "parking structure, PPD precise plan of design, PRD planned residential "
                "development, I-IV FAR or height district.",
            ),
            FieldDoc("ZONE_DESC", "Zone district description."),
            FieldDoc("GENPLAN", "General plan land use code."),
            FieldDoc("GPLANDESC", "General plan land use description."),
            FieldDoc(
                "Type",
                "Broad zone category; values match the dZonePlanTyp domain. Inconsistent, so "
                "don't classify with it.",
                {
                    "100": "Residential open space",
                    "200": "Restricted residential",
                    "300": "Residential",
                    "400": "Moderate to high density residential",
                    "500": "Commercial",
                    "600": "South Brand Boulevard specific plan",
                    "700": "Industrial / transportation",
                    "800": "Other",
                    "900": "Recreation area",
                },
                inferred=True,
            ),
        ),
        disclaimer=GLENDALE_DISCLAIMER,
        docs=("https://ecode360.com/43352035", "https://ecode360.com/43352065"),
    ),
    Dataset(
        id="streets",
        title="Streets",
        category="reference",
        source_agency="City of Glendale (from LA County CAMS)",
        layer_url=f"{GLENDALE_COMMON}/Streets/FeatureServer/0",
        geometry="polyline",
        fields=(
            FieldDoc("FullName", "Full street name."),
            FieldDoc("Type", "Road type (Primary, Secondary, Minor, Freeway, Ramp, Alley, ...)."),
            FieldDoc("Surface", "Surface (Paved, Dirt, Unknown)."),
            FieldDoc("Status", "Access (Unrestricted, Restricted, Unknown)."),
            FieldDoc("DrivingDir", "Two Way or One Way With Arc Direction."),
            FieldDoc("From_L", "Left-side starting address number."),
            FieldDoc("To_L", "Left-side ending address number."),
            FieldDoc("From_R", "Right-side starting address number."),
            FieldDoc("To_R", "Right-side ending address number."),
            FieldDoc("Zip_L", "Left-side ZIP code."),
            FieldDoc("Zip_R", "Right-side ZIP code."),
            FieldDoc("LCity_L", "Left-side city."),
            FieldDoc("LCity_R", "Right-side city."),
        ),
        disclaimer=GLENDALE_DISCLAIMER,
    ),
    Dataset(
        id="parcels",
        title="Parcels",
        category="reference",
        source_agency="City of Glendale",
        layer_url=f"{GLENDALE_COMMON}/Zoning/FeatureServer/1",
        geometry="polygon",
        access="live",
        description="54,300 parcels; queried live from the city server, not in the snapshot.",
        disclaimer=GLENDALE_DISCLAIMER,
    ),
)

DATASETS: Mapping[str, Dataset] = MappingProxyType({d.id: d for d in HAZARDS + CITY})

GEOCODER = Service(
    id="city_geocoder",
    url=f"{GLENDALE_COMMON}/CAD_SiteAddress_Street/GeocodeServer",
    operations=frozenset({"findAddressCandidates", "reverseGeocode", "suggest"}),
)

# Layer operations the client may call on catalog layers ("" is the layer metadata itself).
LAYER_OPERATIONS = frozenset({"query"})


def get_dataset(dataset_id: str) -> Dataset:
    try:
        return DATASETS[dataset_id]
    except KeyError:
        known = ", ".join(sorted(DATASETS))
        raise KeyError(f"Unknown dataset {dataset_id!r}. Known datasets: {known}") from None


def datasets(
    *, category: Category | None = None, access: Access | None = None
) -> tuple[Dataset, ...]:
    return tuple(
        d
        for d in DATASETS.values()
        if (category is None or d.category == category) and (access is None or d.access == access)
    )
