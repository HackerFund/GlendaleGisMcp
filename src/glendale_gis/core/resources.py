"""Nearest community resources (fire stations, hospitals, schools, parks, ...) to a point.

Distances are straight-line meters, not travel distance, and only resources inside Glendale
(plus the 100 m city buffer) are included.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from glendale_gis.core.catalog import DATASETS, datasets
from glendale_gis.core.models import NearestResources, ResolvedLocation, Resource, ResourceGroup
from glendale_gis.core.snapshot import Layer, LayerUnavailable, Snapshot, catalog_meta, local_point

RESOURCE_KINDS: tuple[str, ...] = tuple(d.id for d in datasets(category="resource"))
DEFAULT_LIMIT = 3
MAX_LIMIT = 25

DISTANCE_NOTE = (
    "Distances are straight-line meters, not travel distance. Only resources inside Glendale "
    "are included."
)


def nearest_resources(
    snapshot: Snapshot,
    loc: ResolvedLocation,
    kinds: Iterable[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> NearestResources:
    """The ``limit`` nearest resources of each kind (all kinds if ``kinds`` is None).

    Raises ``ValueError`` for unknown kinds. ``limit`` is clamped to 1..``MAX_LIMIT``.
    """
    selected = _check_kinds(kinds)
    limit = max(1, min(int(limit), MAX_LIMIT))
    groups = [_group(snapshot, kind, loc, limit) for kind in selected]
    notes = [DISTANCE_NOTE]
    if not loc.in_city:
        notes.append(
            "The location is outside Glendale. Resources in neighboring cities are not included, "
            "so closer ones may exist."
        )
    return NearestResources(location=loc, groups=groups, notes=notes)


def _check_kinds(kinds: Iterable[str] | None) -> Sequence[str]:
    if kinds is None:
        return RESOURCE_KINDS
    selected = list(dict.fromkeys(kinds))
    unknown = [k for k in selected if k not in RESOURCE_KINDS]
    if unknown:
        raise ValueError(
            f"Unknown resource kind(s): {', '.join(unknown)}. "
            f"Choose from: {', '.join(RESOURCE_KINDS)}."
        )
    if not selected:
        raise ValueError(f"Give at least one kind. Choose from: {', '.join(RESOURCE_KINDS)}.")
    return selected


def _group(snapshot: Snapshot, kind: str, loc: ResolvedLocation, limit: int) -> ResourceGroup:
    ds = DATASETS[kind]
    try:
        layer = snapshot.layer(kind)
    except LayerUnavailable as exc:
        return ResourceGroup(
            kind=kind, title=ds.title, status="unavailable", reason=str(exc), _meta=catalog_meta(ds)
        )
    distances = layer.distances(local_point(loc.lat, loc.lon))
    order = sorted(range(len(layer)), key=lambda i: (distances[i], layer.ref(i).object_id or 0))
    return ResourceGroup(
        kind=kind,
        title=ds.title,
        status="ok",
        results=[_resource(layer, i, float(distances[i])) for i in order[:limit]],
        total_in_dataset=len(layer),
        _meta=layer.meta(),
    )


def _resource(layer: Layer, i: int, distance: float) -> Resource:
    ds = layer.dataset
    props = layer.properties[i]
    return Resource(
        kind=ds.id,
        name=_clean(props.get(ds.name_field)) if ds.name_field else None,
        address=" ".join(p for f in ds.address_fields if (p := _clean(props.get(f)))) or None,
        distance_m=round(distance),
        attributes=dict(props),
        ref=layer.ref(i),
    )


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None
