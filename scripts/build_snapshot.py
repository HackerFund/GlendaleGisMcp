"""Build the offline snapshot of catalog layers.

    python scripts/build_snapshot.py [--only ID,...] [--dry-run] [--out DIR]

Fetches the city boundary, then every snapshot dataset in the catalog. City layers are clipped to
the boundary plus ``GLENDALE_GIS_CITY_BUFFER_M`` and hazard layers to the boundary plus
``GLENDALE_GIS_HAZARD_BUFFER_M`` (the wider buffer keeps nearest-zone distances right near the
city limit). Writes one GeoJSON file per layer and ``manifest.json``. Nothing in the output
directory changes unless the whole build succeeds.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import shapely
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from glendale_gis import __version__
from glendale_gis.core import geo
from glendale_gis.core.arcgis import ArcGISClient, ArcGISError, check_allowed
from glendale_gis.core.catalog import DATASETS, Dataset, datasets
from glendale_gis.core.config import ConfigError, Settings

log = logging.getLogger("build_snapshot")

MANIFEST_VERSION = 1
MANIFEST_NAME = "manifest.json"
BOUNDARY_ID = "city_boundary"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "snapshot"
BYTES_PER_MB = 1_000_000

# Layers allowed to be empty. No current USGS post-fire assessment covers Glendale.
EMPTY_OK = frozenset({"usgs_debris_flow"})

_DIMENSION = {"point": 0, "polyline": 1, "polygon": 2}


class BuildError(Exception):
    """A layer could not be built; the message says why."""


@dataclasses.dataclass
class LayerResult:
    dataset: Dataset
    layer_url: str
    source_count: int  # features the source returned for the buffered envelope
    features: list[dict[str, Any]]
    fields: list[dict[str, Any]]
    id_field: str
    global_id_field: str | None
    source_last_edit: str | None
    fetched_at: str
    seconds: float


# --------------------------------------------------------------------------------------------
# Source metadata
# --------------------------------------------------------------------------------------------


async def resolve_layer_url(client: ArcGISClient, ds: Dataset) -> str:
    """Return the current layer URL, re-resolving services whose URL moves between releases."""
    if not ds.item_id:
        return ds.layer_url
    try:
        service = await client.resolve_item_url(ds.item_id)
    except ArcGISError as exc:
        log.warning("%s: item lookup failed (%s); using the catalog URL", ds.id, exc)
        return ds.layer_url
    layer = ds.layer_url.rsplit("/", 1)[1]
    has_layer = re.search(r"/(FeatureServer|MapServer)/\d+$", service)
    url = check_allowed(service if has_layer else f"{service}/{layer}")
    if url != ds.layer_url:
        log.warning("%s: service has moved to %s; update the catalog", ds.id, url)
    return url


def select_fields(ds: Dataset, meta: Mapping[str, Any]) -> tuple[str, str | None, list[dict]]:
    """Return the ID field, the GlobalID field (if any) and the fields to keep, in order.

    Fails if a catalog field is missing from the source, so schema drift is caught at build time.
    """
    source = {f["name"]: f for f in meta.get("fields") or []}
    id_field = ds.id_field
    if id_field not in source:
        raise BuildError(f"ID field {id_field!r} is not in the source schema")
    global_id = next(
        (n for n, f in source.items() if f.get("type") == "esriFieldTypeGlobalID"), None
    )
    missing = [n for n in ds.field_names if n not in source]
    if missing:
        raise BuildError(f"catalog fields missing from the source: {', '.join(missing)}")

    names = [id_field]
    if global_id and global_id not in names:
        names.append(global_id)
    names += [n for n in ds.field_names if n not in names]
    kept = [
        {"name": n, "type": source[n].get("type"), "alias": source[n].get("alias") or n}
        for n in names
    ]
    return id_field, global_id, kept


def last_edit_date(meta: Mapping[str, Any]) -> str | None:
    millis = (meta.get("editingInfo") or {}).get("lastEditDate")
    return _iso_from_millis(millis) if millis else None


def envelope(area: BaseGeometry) -> dict[str, Any]:
    xmin, ymin, xmax, ymax = area.bounds
    return {
        "xmin": xmin,
        "ymin": ymin,
        "xmax": xmax,
        "ymax": ymax,
        "spatialReference": {"wkid": 4326},
    }


# --------------------------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------------------------


def clip(
    geom: BaseGeometry | None, area: BaseGeometry | None, dimension: int
) -> BaseGeometry | None:
    """Clip ``geom`` to ``area`` (already prepared), keeping only parts of ``dimension``.

    Returns ``None`` if nothing is left. Features fully inside the area are returned unchanged.
    """
    if geom is None or geom.is_empty:
        return None
    if area is None or area.contains(geom):
        return geom
    if not area.intersects(geom):
        return None
    return _keep_dimension(geom.intersection(area), dimension)


def _keep_dimension(geom: BaseGeometry, dimension: int) -> BaseGeometry | None:
    parts = [p for p in _flatten(geom) if not p.is_empty and shapely.get_dimensions(p) == dimension]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    if dimension == 2:
        return shapely.union_all(parts)
    if dimension == 1:
        return shapely.multilinestrings(parts)
    return shapely.multipoints(parts)


def _flatten(geom: BaseGeometry) -> Iterable[BaseGeometry]:
    for part in shapely.get_parts(geom):
        if part.geom_type.startswith("Multi") or part.geom_type == "GeometryCollection":
            yield from _flatten(part)
        else:
            yield part


def to_feature(
    esri_feature: Mapping[str, Any],
    ds: Dataset,
    fields: Sequence[Mapping[str, Any]],
    id_field: str,
    area: BaseGeometry | None,
) -> dict[str, Any] | None:
    """Convert one Esri feature to a clipped, rounded GeoJSON feature, or ``None`` if outside."""
    attrs = esri_feature.get("attributes") or {}
    geom = clip(geo.from_esri(esri_feature.get("geometry")), area, _DIMENSION[ds.geometry])
    if geom is None:
        return None
    geom = geo.round_coords(geom)
    if geom.is_empty:
        return None  # a sliver that vanished when rounded
    return {
        "type": "Feature",
        "id": attrs.get(id_field),
        "geometry": mapping(geom),
        "properties": {f["name"]: attrs.get(f["name"]) for f in fields},
    }


# --------------------------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------------------------


async def fetch_boundary(client: ArcGISClient) -> BaseGeometry:
    ds = DATASETS[BOUNDARY_ID]
    features = await client.query(ds.layer_url, out_fields=ds.id_field)
    shapes = [geo.from_esri(f.get("geometry")) for f in features]
    shapes = [s for s in shapes if s is not None and not s.is_empty]
    if not shapes:
        raise BuildError("the city boundary layer returned no geometry")
    return shapely.union_all(shapes)


def clip_areas(boundary: BaseGeometry, settings: Settings) -> dict[str, BaseGeometry]:
    areas = {
        "city": geo.buffer_m(boundary, settings.city_buffer_m),
        "hazard": geo.buffer_m(boundary, settings.hazard_buffer_m),
    }
    for area in areas.values():
        shapely.prepare(area)
    return areas


async def build_layer(
    client: ArcGISClient, ds: Dataset, area: BaseGeometry, *, clip_to_area: bool = True
) -> LayerResult:
    started = time.monotonic()
    layer_url = await resolve_layer_url(client, ds)
    meta = await client.layer_metadata(layer_url)
    id_field, global_id, fields = select_fields(ds, meta)
    fetched_at = _now_iso()
    raw = await client.query(
        layer_url,
        out_fields=[f["name"] for f in fields],
        geometry=envelope(area),
        geometry_type="esriGeometryEnvelope",
        metadata=meta,
        extra_params={"geometryPrecision": geo.COORD_DECIMALS + 2, "returnTrueCurves": False},
    )
    features = []
    for f in raw:
        feature = to_feature(f, ds, fields, id_field, area if clip_to_area else None)
        if feature is not None:
            features.append(feature)
    features.sort(key=lambda f: f["id"])

    ids = [f["id"] for f in features]
    if len(set(ids)) != len(ids):
        raise BuildError("duplicate object IDs in the source response")
    if not features and ds.id not in EMPTY_OK:
        raise BuildError(f"no features inside the clip area ({len(raw)} returned for the envelope)")
    return LayerResult(
        dataset=ds,
        layer_url=layer_url,
        source_count=len(raw),
        features=features,
        fields=fields,
        id_field=id_field,
        global_id_field=global_id,
        source_last_edit=last_edit_date(meta),
        fetched_at=fetched_at,
        seconds=time.monotonic() - started,
    )


async def count_layer(client: ArcGISClient, ds: Dataset, area: BaseGeometry) -> dict[str, Any]:
    """Dry run: metadata and envelope count only, no geometry."""
    layer_url = await resolve_layer_url(client, ds)
    meta = await client.layer_metadata(layer_url)
    select_fields(ds, meta)  # checks the schema
    count = await client.count(
        layer_url, geometry=envelope(area), geometry_type="esriGeometryEnvelope"
    )
    return {
        "id": ds.id,
        "count": count,
        "max_record_count": meta.get("maxRecordCount"),
        "last_edit": last_edit_date(meta),
    }


def snapshot_datasets(only: Iterable[str] | None) -> list[Dataset]:
    candidates = datasets(access="snapshot")
    if only is None:
        return list(candidates)
    wanted = list(dict.fromkeys(only))
    known = {d.id: d for d in candidates}
    unknown = [i for i in wanted if i not in known]
    if unknown:
        raise ConfigError(
            f"not snapshot datasets: {', '.join(unknown)}. Choose from: {', '.join(sorted(known))}"
        )
    return [known[i] for i in wanted]


async def build(
    settings: Settings,
    out_dir: Path,
    *,
    only: Iterable[str] | None = None,
    dry_run: bool = False,
    client: ArcGISClient | None = None,
) -> int:
    """Build the snapshot into ``out_dir``. Returns a process exit code."""
    selected = snapshot_datasets(only)
    owns_client = client is None
    client = client or ArcGISClient(settings)
    try:
        log.info("Fetching the city boundary")
        boundary = await fetch_boundary(client)
        areas = clip_areas(boundary, settings)

        if dry_run:
            rows = []
            for ds in selected:
                log.info("Counting %s", ds.id)
                rows.append(await count_layer(client, ds, areas[ds.buffer]))
            print(_dry_run_table(rows, settings))
            return 0

        results: list[LayerResult] = []
        failures: dict[str, str] = {}
        for ds in selected:
            log.info("Building %s", ds.id)
            try:
                results.append(
                    await build_layer(
                        client, ds, areas[ds.buffer], clip_to_area=ds.id != BOUNDARY_ID
                    )
                )
            except (ArcGISError, BuildError, geo.GeometryError) as exc:
                failures[ds.id] = str(exc)
                log.error("%s: %s", ds.id, exc)
    finally:
        if owns_client:
            await client.aclose()

    if failures:
        print(_failure_report(failures), file=sys.stderr)
        return 1
    return write_snapshot(out_dir, results, settings)


# --------------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------------


def write_snapshot(out_dir: Path, results: Sequence[LayerResult], settings: Settings) -> int:
    """Stage files, check the size budget, then move them into ``out_dir``.

    Layers not rebuilt this run (``--only``) keep their existing manifest entries and files.
    """
    out_dir = out_dir.resolve()
    staging = out_dir.with_name(out_dir.name + ".staging")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        manifest = _load_manifest(out_dir)
        layers: dict[str, Any] = dict(manifest.get("layers") or {})
        for r in results:
            data = _dump_collection(r.features)
            name = f"{r.dataset.id}.geojson"
            (staging / name).write_bytes(data)
            layers[r.dataset.id] = _manifest_entry(r, name, data, settings)

        total = sum(entry["bytes"] for entry in layers.values())
        print(_size_table(layers, results, total, settings))
        if total > settings.max_snapshot_mb * BYTES_PER_MB:
            print(
                f"Snapshot is {total / BYTES_PER_MB:.1f} MB, over the "
                f"{settings.max_snapshot_mb:g} MB budget. Nothing was written.",
                file=sys.stderr,
            )
            return 1

        manifest = {
            "version": MANIFEST_VERSION,
            "built_at": _now_iso(),
            "generator": f"glendale-gis-mcp {__version__}",
            "crs": "EPSG:4326",
            "coordinate_decimals": geo.COORD_DECIMALS,
            "total_bytes": total,
            "layers": dict(sorted(layers.items())),
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        for r in results:
            name = f"{r.dataset.id}.geojson"
            os.replace(staging / name, out_dir / name)
        # The manifest goes last, so it never points at files that aren't there yet.
        (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print(f"Wrote {len(results)} layer(s) to {out_dir}")
    return 0


def _manifest_entry(r: LayerResult, name: str, data: bytes, settings: Settings) -> dict[str, Any]:
    ds = r.dataset
    meters = settings.city_buffer_m if ds.buffer == "city" else settings.hazard_buffer_m
    return {
        "title": ds.title,
        "source_agency": ds.source_agency,
        "category": ds.category,
        "layer_url": r.layer_url,
        "fetched_at": r.fetched_at,
        "source_last_edit": r.source_last_edit,
        "feature_count": len(r.features),
        "source_feature_count": r.source_count,
        "geometry": ds.geometry,
        "id_field": r.id_field,
        "global_id_field": r.global_id_field,
        "fields": r.fields,
        "clip": None if ds.id == BOUNDARY_ID else {"buffer": ds.buffer, "meters": meters},
        "file": name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _load_manifest(out_dir: Path) -> dict[str, Any]:
    path = out_dir / MANIFEST_NAME
    if not path.exists():
        return {}
    manifest = json.loads(path.read_text())
    # Drop entries whose file has gone missing, so the manifest never lies.
    layers = manifest.get("layers") or {}
    manifest["layers"] = {k: v for k, v in layers.items() if (out_dir / v["file"]).exists()}
    return manifest


def _dump_collection(features: Sequence[Mapping[str, Any]]) -> bytes:
    collection = {"type": "FeatureCollection", "features": list(features)}
    return json.dumps(collection, separators=(",", ":"), ensure_ascii=False).encode()


def _size_table(
    layers: Mapping[str, Any], results: Sequence[LayerResult], total: int, settings: Settings
) -> str:
    seconds = {r.dataset.id: r.seconds for r in results}
    lines = [f"{'dataset':<26}{'source':>8}{'kept':>8}{'KB':>10}{'time':>8}"]
    for layer_id, entry in sorted(layers.items(), key=lambda kv: -kv[1]["bytes"]):
        took = f"{seconds[layer_id]:.1f}s" if layer_id in seconds else "kept"
        lines.append(
            f"{layer_id:<26}{entry['source_feature_count']:>8}{entry['feature_count']:>8}"
            f"{entry['bytes'] / 1000:>10.1f}{took:>8}"
        )
    lines.append(
        f"{'total':<26}{'':>16}{total / 1000:>10.1f}   "
        f"({total / BYTES_PER_MB:.1f} of {settings.max_snapshot_mb:g} MB)"
    )
    return "\n".join(lines)


def _dry_run_table(rows: Sequence[Mapping[str, Any]], settings: Settings) -> str:
    lines = [
        f"Buffers: city {settings.city_buffer_m:g} m, hazard {settings.hazard_buffer_m:g} m. "
        "Counts are for the buffered envelope, before clipping.",
        f"{'dataset':<26}{'count':>8}{'maxRec':>8}  last edit",
    ]
    for r in rows:
        lines.append(
            f"{r['id']:<26}{r['count']:>8}{r['max_record_count'] or '':>8}  {r['last_edit'] or '-'}"
        )
    return "\n".join(lines)


def _failure_report(failures: Mapping[str, str]) -> str:
    lines = ["Build failed; nothing was written."]
    lines += [f"  {layer_id}: {message}" for layer_id, message in failures.items()]
    return "\n".join(lines)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _iso_from_millis(millis: float) -> str:
    return datetime.fromtimestamp(millis / 1000, timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_snapshot.py",
        description=(
            "Build the offline snapshot of Glendale hazard and city layers. Buffers and the "
            "size budget come from GLENDALE_GIS_* environment variables."
        ),
    )
    parser.add_argument(
        "--only",
        type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
        metavar="ID,...",
        help="rebuild only these datasets; other layers in --out are kept",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="fetch metadata and counts only; write nothing"
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help=f"output directory (default: {DEFAULT_OUT})"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log every request")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = Settings.from_env()
        snapshot_datasets(args.only)  # validate ids before any request
    except ConfigError as exc:
        print(f"build_snapshot: {exc}", file=sys.stderr)
        return 2
    try:
        return asyncio.run(build(settings, args.out, only=args.only, dry_run=args.dry_run))
    except (ArcGISError, BuildError, geo.GeometryError) as exc:
        print(f"build_snapshot: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
