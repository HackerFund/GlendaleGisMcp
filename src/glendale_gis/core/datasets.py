"""The catalog as callers see it: ``list_datasets`` and ``describe_dataset``."""

from __future__ import annotations

from glendale_gis.core.catalog import DATASETS
from glendale_gis.core.models import (
    ActionableError,
    DatasetDescription,
    DatasetList,
    DatasetSummary,
    FieldInfo,
)
from glendale_gis.core.query import QueryEngine
from glendale_gis.core.snapshot import LayerUnavailable, Snapshot, catalog_meta

ID_FIELD_DOCS = {
    "esriFieldTypeOID": "Object ID: identifies the feature in the source layer (ref.object_id).",
    "esriFieldTypeGlobalID": "Global ID: a unique ID that stays the same across edits "
    "(ref.global_id).",
}

REALTIME_NOTE = (
    "No dataset here is real-time. For active fires, evacuations and alerts, use official "
    'sources: call read_guide("real_time_sources").'
)


def list_datasets(snapshot: Snapshot) -> DatasetList:
    summaries = []
    for ds in DATASETS.values():
        count = None
        available = True
        if ds.access == "snapshot":
            try:
                count = len(snapshot.layer(ds.id))
            except LayerUnavailable:
                available = False
        summaries.append(
            DatasetSummary(
                id=ds.id,
                title=ds.title,
                category=ds.category,
                source_agency=ds.source_agency,
                access=ds.access,
                geometry=ds.geometry,
                feature_count=count,
                available=available,
                description=_first_sentence(ds.description) or ds.title,
            )
        )
    return DatasetList(
        datasets=summaries,
        snapshot_built_at=snapshot.built_at,
        notes=[
            "Use describe_dataset for fields and coded values, and query_dataset to search one.",
            REALTIME_NOTE,
        ],
    )


async def describe_dataset(
    snapshot: Snapshot, engine: QueryEngine, dataset_id: str
) -> DatasetDescription:
    ds = DATASETS.get(dataset_id)
    if ds is None:
        raise ActionableError(
            f"Unknown dataset {dataset_id!r}.", ["Call list_datasets to see the dataset ids."]
        )
    docs = {f.name: f for f in ds.fields}
    entry: dict = {}
    count = None
    available, reason = True, None
    meta = catalog_meta(ds)

    if ds.access == "snapshot":
        try:
            layer = snapshot.layer(ds.id)
            entry = dict(layer.entry)
            count = len(layer)
            meta = layer.meta()
        except LayerUnavailable as exc:
            available, reason = False, str(exc)
        source_fields = entry.get("fields") or [{"name": n} for n in (ds.id_field, *docs)]
    else:
        try:
            source_fields, meta = await engine.schema(ds)
        except ActionableError as exc:
            available, reason = False, exc.error
            source_fields = [{"name": n} for n in (ds.id_field, *docs)]

    fields = []
    for f in source_fields:
        doc = docs.get(f["name"])
        fields.append(
            FieldInfo(
                name=f["name"],
                type=f.get("type"),
                alias=f.get("alias"),
                description=doc.description if doc else ID_FIELD_DOCS.get(f.get("type") or ""),
                values=dict(doc.values) if doc and doc.values else None,
                inferred=doc.inferred if doc else False,
            )
        )

    return DatasetDescription(
        id=ds.id,
        title=ds.title,
        category=ds.category,
        source_agency=ds.source_agency,
        access=ds.access,
        geometry=ds.geometry,
        description=ds.description or ds.title,
        layer_url=entry.get("layer_url") or ds.layer_url,
        fields=fields,
        feature_count=count,
        fetched_at=entry.get("fetched_at"),
        source_last_edit=entry.get("source_last_edit"),
        clip=entry.get("clip"),
        class_field=ds.class_field,
        unzoned_classes=list(ds.unzoned_classes),
        id_field=entry.get("id_field") or ds.id_field,
        disclaimer=ds.disclaimer,
        docs=list(ds.docs),
        available=available,
        unavailable_reason=reason,
        _meta=meta,
    )


def _first_sentence(text: str) -> str:
    return text.split(". ")[0].rstrip(".") + "." if text else ""


# --------------------------------------------------------------------------------------------
# Markdown, for MCP resources
# --------------------------------------------------------------------------------------------

GROUPS = (
    ("hazard", "snapshot", "Hazard zones"),
    ("resource", "snapshot", "Community resources (City of Glendale)"),
    ("reference", "snapshot", "Reference layers (City of Glendale)"),
    (None, "live", "Queried live"),
)


def catalog_markdown(snapshot: Snapshot) -> str:
    """Every dataset as Markdown tables, grouped by kind."""
    listing = list_datasets(snapshot)
    built = f" The offline snapshot was built {snapshot.built_at}." if snapshot.built_at else ""
    lines = [
        "# Datasets",
        "",
        f"{len(listing.datasets)} datasets for Glendale, California.{built} Hazard layers cover "
        "the city plus 2 km; city layers cover the city plus 100 m. None is real-time.",
        "",
        "Call `describe_dataset` (or read `glendale-gis://datasets/{id}`) for a dataset's fields "
        "and coded values, and search one with `query_dataset`.",
    ]
    for category, access, heading in GROUPS:
        rows = [
            d
            for d in listing.datasets
            if d.access == access and (category is None or d.category == category)
        ]
        if not rows:
            continue
        lines += [
            "",
            f"## {heading}",
            "",
            "| id | What it is | Source | Features | Source last changed |",
            "| --- | --- | --- | --- | --- |",
        ]
        for d in rows:
            entry = _entry(snapshot, d.id)
            count = (
                "live"
                if d.access == "live"
                else (d.feature_count if d.available else "unavailable")
            )
            last_edit = (entry.get("source_last_edit") or "")[:10] or "not published"
            what = f"**{d.title}.** {d.description}" if d.description != d.title else d.title
            lines.append(f"| `{d.id}` | {what} | {d.source_agency} | {count} | {last_edit} |")
    lines += [
        "",
        "Features are the counts in the snapshot after clipping. A layer with 0 features "
        "(debris flow) means no mapped zone near Glendale, not that there is no hazard.",
    ]
    return "\n".join(lines) + "\n"


def dataset_markdown(d: DatasetDescription) -> str:
    """One dataset's description as Markdown."""
    lines = [
        f"# {d.title}",
        "",
        f"`{d.id}` · {d.source_agency} · {d.category} · "
        + ("live from the source" if d.access == "live" else "offline snapshot"),
        "",
        d.description,
    ]
    if not d.available:
        lines += ["", f"**Unavailable:** {d.unavailable_reason}"]
    if d.disclaimer:
        lines += ["", f"> {d.disclaimer}"]

    facts = [f"- Source layer: {d.layer_url}", f"- Geometry: {d.geometry}"]
    if d.feature_count is not None:
        facts.append(f"- Features in the snapshot: {d.feature_count}")
    if d.fetched_at:
        facts.append(f"- Fetched: {d.fetched_at}")
    facts.append(f"- Source last changed: {d.source_last_edit or 'not published by the source'}")
    if d.clip:
        facts.append(f"- Covers: Glendale plus {d.clip['meters']:g} m")
    facts.append(f"- Object ID field (used in `ref`): `{d.id_field}`")
    if d.class_field:
        facts.append(
            f"- Classes come from `{d.class_field}`; results give the nearest zone of each"
        )
    if d.unzoned_classes:
        names = ", ".join(d.unzoned_classes)
        facts.append(f"- Unzoned classes (don't count as in a zone): {names}")
    lines += ["", "## Facts", "", *facts]

    lines += [
        "",
        "## Fields",
        "",
        "| Field | Type | Meaning | Values |",
        "| --- | --- | --- | --- |",
    ]
    for f in d.fields:
        meaning = (f.description or "").replace("|", "\\|")
        if f.inferred:
            meaning += " *(inferred)*"
        values = "; ".join(f"`{k}` = {v}" for k, v in (f.values or {}).items())
        field_type = (f.type or "").removeprefix("esriFieldType")
        lines.append(f"| `{f.name}` | {field_type} | {meaning} | {values.replace('|', '/')} |")

    if d.docs:
        lines += ["", "## Official documentation", "", *[f"- {url}" for url in d.docs]]
    return "\n".join(lines) + "\n"


def _entry(snapshot: Snapshot, dataset_id: str) -> dict:
    try:
        return dict(snapshot.layer(dataset_id).entry)
    except (LayerUnavailable, KeyError):
        return {}
