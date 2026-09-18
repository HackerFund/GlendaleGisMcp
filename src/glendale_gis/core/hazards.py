"""Hazard lookups at a point: which mapped zones contain it, and the nearest zone.

Every result has a ``status``: ``in_zone``, ``not_in_zone`` or ``unavailable`` (with a
``reason``). A layer that is missing, or a point outside the area the snapshot covers, is
``unavailable``, never ``not_in_zone``.
"""

from __future__ import annotations

from glendale_gis.core.catalog import DATASETS
from glendale_gis.core.models import (
    HazardResult,
    HazardsAtLocation,
    Nearest,
    ResolvedLocation,
    SeismicZones,
)
from glendale_gis.core.snapshot import (
    Layer,
    LayerUnavailable,
    Snapshot,
    catalog_meta,
    local_point,
)

WILDFIRE = "calfire_fhsz_lra"
FLOOD = "fema_flood_zones"
FAULT = "cgs_fault_zones"
LIQUEFACTION = "cgs_liquefaction_zones"
LANDSLIDE = "cgs_landslide_zones"
DAM = "dwr_dam_inundation"
DEBRIS = "usgs_debris_flow"

UNZONED_NOTE = (
    "The source classes this location as {classes}, which it defines as outside any mapped "
    "zone. That does not mean there is no hazard."
)
UNZONED_NOTES = {
    WILDFIRE: (
        "CAL FIRE classes this location as {classes} (unzoned): it is not in a mapped fire "
        "hazard severity zone. This does not mean there is no wildfire risk; embers and "
        "structure-to-structure spread can reach unzoned areas."
    ),
}
CGS_NOTE = (
    "Being inside the zone is the hazard signal. The attributes describe the map (quadrangle, "
    "release date, report links), not the severity."
)


def locate(snapshot: Snapshot, lat: float, lon: float) -> ResolvedLocation:
    """A location given as coordinates (no geocoding)."""
    return ResolvedLocation(
        lat=lat,
        lon=lon,
        in_city=snapshot.in_city(local_point(lat, lon)),
        source="coordinates",
    )


# --------------------------------------------------------------------------------------------
# One result
# --------------------------------------------------------------------------------------------


def hazard_result(snapshot: Snapshot, dataset_id: str, loc: ResolvedLocation) -> HazardResult:
    """Look up one hazard layer at a location."""
    ds = DATASETS[dataset_id]
    base = {
        "dataset": ds.id,
        "title": ds.title,
        "class_field": ds.class_field,
        "disclaimer": ds.disclaimer,
    }
    try:
        layer = snapshot.layer(ds.id)
    except LayerUnavailable as exc:
        return HazardResult(**base, status="unavailable", reason=str(exc), _meta=catalog_meta(ds))

    point = local_point(loc.lat, loc.lon)
    area = snapshot.coverage(layer.clip_meters or 0.0)
    if not area.covers(point):
        outside_m = round(area.distance(point))
        return HazardResult(
            **base,
            status="unavailable",
            reason=(
                f"The point is {outside_m} m outside the area this layer covers (Glendale plus "
                f"{layer.clip_meters:g} m). Choose a location in or near Glendale."
            ),
            _meta=layer.meta(),
        )

    # Distances up to the edge of the covered area are exact; past it, a zone outside the
    # snapshot could be closer.
    edge_m = float(area.boundary.distance(point))
    matches = layer.containing(point)
    zoned = [i for i in matches if layer.class_of(i) not in ds.unzoned_classes]
    result = HazardResult(
        **base,
        status="in_zone" if zoned else "not_in_zone",
        matches=[layer.feature(i) for i in matches],
        _meta=layer.meta(),
    )
    if ds.class_field:
        result.nearest_by_class = {
            cls: _nearest(layer, i, d, edge_m)
            for cls, (i, d) in sorted(layer.nearest_by_class(point).items())
        }
    else:
        found = layer.nearest(point)
        result.nearest = _nearest(layer, *found, edge_m) if found else None
    result.notes = _notes(ds.id, layer, matches)
    return result


def _nearest(layer: Layer, i: int, distance: float, edge_m: float) -> Nearest:
    feature = layer.feature(i)
    return Nearest(
        attributes=feature.attributes,
        ref=feature.ref,
        distance_m=round(distance),
        exact=distance <= edge_m,
    )


def _notes(dataset_id: str, layer: Layer, matches: list[int]) -> list[str]:
    notes = []
    unzoned = sorted({layer.class_of(i) for i in matches} & set(layer.dataset.unzoned_classes))
    if unzoned:
        notes.append(UNZONED_NOTES.get(dataset_id, UNZONED_NOTE).format(classes=", ".join(unzoned)))
    if dataset_id in (FAULT, LIQUEFACTION, LANDSLIDE):
        notes.append(CGS_NOTE)
    elif dataset_id == FLOOD:
        if any(layer.class_of(i) == "D" for i in matches):
            notes.append(
                "Zone D means the flood hazard has not been studied. It does not mean the area "
                "is safe."
            )
    elif dataset_id == DAM and len(matches) > 1:
        notes.append(
            "Several inundation areas can come from one dam (different failure scenarios or "
            "structures). HazardCl rates the consequences of a failure, not its likelihood."
        )
    elif dataset_id == DEBRIS and not matches:
        notes.append(
            "No current USGS post-fire assessment covers this location. USGS only assesses "
            "recently burned areas, so this does not mean there is no debris-flow risk."
        )
    return notes


def _standalone(result: HazardResult, loc: ResolvedLocation) -> HazardResult:
    result.location = loc
    return result


# --------------------------------------------------------------------------------------------
# One function per tool
# --------------------------------------------------------------------------------------------


def wildfire_zone(snapshot: Snapshot, loc: ResolvedLocation) -> HazardResult:
    return _standalone(hazard_result(snapshot, WILDFIRE, loc), loc)


def flood_zone(snapshot: Snapshot, loc: ResolvedLocation) -> HazardResult:
    return _standalone(hazard_result(snapshot, FLOOD, loc), loc)


def dam_inundation(snapshot: Snapshot, loc: ResolvedLocation) -> HazardResult:
    return _standalone(hazard_result(snapshot, DAM, loc), loc)


def debris_flow(snapshot: Snapshot, loc: ResolvedLocation) -> HazardResult:
    return _standalone(hazard_result(snapshot, DEBRIS, loc), loc)


def seismic_zones(snapshot: Snapshot, loc: ResolvedLocation) -> SeismicZones:
    return SeismicZones(
        location=loc,
        fault=hazard_result(snapshot, FAULT, loc),
        liquefaction=hazard_result(snapshot, LIQUEFACTION, loc),
        landslide=hazard_result(snapshot, LANDSLIDE, loc),
    )


def hazards_at_location(snapshot: Snapshot, loc: ResolvedLocation) -> HazardsAtLocation:
    return HazardsAtLocation(
        location=loc,
        wildfire=hazard_result(snapshot, WILDFIRE, loc),
        flood=hazard_result(snapshot, FLOOD, loc),
        fault=hazard_result(snapshot, FAULT, loc),
        liquefaction=hazard_result(snapshot, LIQUEFACTION, loc),
        landslide=hazard_result(snapshot, LANDSLIDE, loc),
        dam_inundation=hazard_result(snapshot, DAM, loc),
        debris_flow=hazard_result(snapshot, DEBRIS, loc),
    )
