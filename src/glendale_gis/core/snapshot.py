"""Load the snapshot and answer point-in-zone and nearest-feature queries offline.

Geometries are held in local meters (see ``core.geo``), so distances come straight from shapely
and containment gives the same answer as in lon/lat. Every layer file is checked against the
SHA-256 in ``manifest.json``; a layer that is missing or fails to load is reported as unavailable
rather than taking the whole snapshot down.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.errors import ShapelyError
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry

from glendale_gis.core import geo
from glendale_gis.core.catalog import DATASETS, Dataset, datasets
from glendale_gis.core.models import Feature, Meta, Ref

MANIFEST_NAME = "manifest.json"
SUPPORTED_MANIFEST_VERSION = 1
BOUNDARY_ID = "city_boundary"
NO_CLASS = "(no value)"


class SnapshotError(Exception):
    """The snapshot as a whole can't be used (no manifest, no city boundary)."""


class LayerUnavailable(Exception):
    """One layer can't be used; the message says why."""


def catalog_meta(ds: Dataset) -> Meta:
    """``_meta`` for a dataset with no data behind it (e.g. a layer missing from the snapshot)."""
    return Meta(source=ds.source_agency, url=ds.layer_url, cached=False, as_of=None, stale=False)


def local_point(lat: float, lon: float) -> Point:
    return geo.to_local(Point(lon, lat))


# --------------------------------------------------------------------------------------------
# Layer
# --------------------------------------------------------------------------------------------


@dataclass
class Layer:
    dataset: Dataset
    entry: Mapping[str, Any]  # this layer's manifest entry
    properties: list[dict[str, Any]]
    geoms: np.ndarray  # local meters
    tree: shapely.STRtree = field(init=False)
    stale: bool = False  # an older snapshot is in use because the current one couldn't load
    _class_trees: dict[str, tuple[np.ndarray, shapely.STRtree]] = field(
        init=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        self.tree = shapely.STRtree(self.geoms)
        if self.dataset.class_field:
            groups: dict[str, list[int]] = {}
            for i in range(len(self.properties)):
                groups.setdefault(self.class_of(i), []).append(i)
            for cls, idx in groups.items():
                indices = np.asarray(idx)
                self._class_trees[cls] = (indices, shapely.STRtree(self.geoms[indices]))

    def __len__(self) -> int:
        return len(self.properties)

    # -- metadata ----------------------------------------------------------------------------

    @property
    def layer_url(self) -> str:
        return str(self.entry["layer_url"])

    @property
    def clip_meters(self) -> float | None:
        clip = self.entry.get("clip")
        return float(clip["meters"]) if clip else None

    def meta(self) -> Meta:
        return Meta(
            source=self.dataset.source_agency,
            url=self.layer_url,
            cached=True,
            as_of=self.entry.get("fetched_at"),
            stale=self.stale,
            source_last_edit=self.entry.get("source_last_edit"),
        )

    # -- features ----------------------------------------------------------------------------

    def ref(self, i: int) -> Ref:
        props = self.properties[i]
        gid_field = self.entry.get("global_id_field")
        return Ref(
            dataset=self.dataset.id,
            object_id=props.get(self.entry.get("id_field") or self.dataset.id_field),
            global_id=props.get(gid_field) if gid_field else None,
            layer_url=self.layer_url,
        )

    def feature(self, i: int) -> Feature:
        return Feature(attributes=dict(self.properties[i]), ref=self.ref(i))

    def class_of(self, i: int) -> str:
        value = self.properties[i].get(self.dataset.class_field or "")
        return NO_CLASS if value is None else str(value)

    def _order_key(self, i: int) -> tuple[int, Any]:
        oid = self.ref(i).object_id
        return (0, oid) if oid is not None else (1, i)

    # -- queries (points in local meters) ----------------------------------------------------

    def containing(self, point: Point) -> list[int]:
        """Indices of features that contain or touch the point, in object-ID order."""
        hits = self.tree.query(point, predicate="intersects").tolist()
        return sorted(hits, key=self._order_key)

    def nearest(self, point: Point) -> tuple[int, float] | None:
        """The nearest feature and its distance in meters; ties go to the lowest object ID."""
        return self._nearest_in(self.tree, None, point)

    def nearest_by_class(self, point: Point) -> dict[str, tuple[int, float]]:
        """The nearest feature of each class, for layers with a class field."""
        result = {}
        for cls, (indices, tree) in self._class_trees.items():
            found = self._nearest_in(tree, indices, point)
            if found is not None:
                result[cls] = found
        return result

    def distances(self, point: Point) -> np.ndarray:
        return shapely.distance(self.geoms, point)

    def _nearest_in(
        self, tree: shapely.STRtree, indices: np.ndarray | None, point: Point
    ) -> tuple[int, float] | None:
        if len(tree) == 0:
            return None
        found, dist = tree.query_nearest(point, return_distance=True, all_matches=True)
        candidates = [int(indices[i]) if indices is not None else int(i) for i in found]
        best = min(candidates, key=self._order_key)
        return best, float(dist.min())


# --------------------------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------------------------


class Snapshot:
    """All snapshot layers, loaded into memory. Build with ``Snapshot.load(path)``."""

    def __init__(
        self,
        path: Path,
        manifest: Mapping[str, Any],
        layers: Mapping[str, Layer],
        errors: Mapping[str, str],
        stale: bool = False,
    ) -> None:
        self.path = path
        self.stale = stale
        self.manifest = manifest
        self._layers = dict(layers)
        self.errors = dict(errors)
        if BOUNDARY_ID not in self._layers:
            reason = self.errors.get(BOUNDARY_ID, "missing")
            raise SnapshotError(f"The snapshot has no usable city boundary ({reason})")
        self.boundary = shapely.union_all(self._layers[BOUNDARY_ID].geoms)
        shapely.prepare(self.boundary)
        self._coverage: dict[float, BaseGeometry] = {}

    @classmethod
    def load(cls, path: Path | str, *, stale: bool = False) -> Snapshot:
        path = Path(path)
        manifest_path = path / MANIFEST_NAME
        if not manifest_path.exists():
            raise SnapshotError(f"No snapshot at {path} (manifest.json not found)")
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError) as exc:
            raise SnapshotError(f"Unreadable snapshot manifest: {exc}") from None
        if manifest.get("version") != SUPPORTED_MANIFEST_VERSION:
            raise SnapshotError(
                f"Unsupported snapshot version {manifest.get('version')!r}; "
                f"expected {SUPPORTED_MANIFEST_VERSION}"
            )

        entries = manifest.get("layers") or {}
        layers: dict[str, Layer] = {}
        errors: dict[str, str] = {}
        for ds in datasets(access="snapshot"):
            if ds.id not in entries:
                errors[ds.id] = "This layer is not in the snapshot."
                continue
            try:
                layers[ds.id] = _load_layer(path, ds, entries[ds.id], stale)
            except (OSError, ValueError, KeyError, TypeError, ShapelyError) as exc:
                errors[ds.id] = f"This layer could not be loaded: {exc}"
        return cls(path, manifest, layers, errors, stale)

    @property
    def built_at(self) -> str | None:
        return self.manifest.get("built_at")

    def has_layer(self, dataset_id: str) -> bool:
        return dataset_id in self._layers

    def layer(self, dataset_id: str) -> Layer:
        if dataset_id in self._layers:
            return self._layers[dataset_id]
        if dataset_id not in DATASETS:
            raise KeyError(f"Unknown dataset {dataset_id!r}")
        raise LayerUnavailable(self.errors.get(dataset_id, "This layer is not in the snapshot."))

    def in_city(self, point: Point) -> bool:
        return bool(self.boundary.intersects(point))

    def coverage(self, meters: float) -> BaseGeometry:
        """The area a layer clipped to the city plus ``meters`` covers (local meters)."""
        if meters not in self._coverage:
            area = self.boundary.buffer(meters) if meters else self.boundary
            shapely.prepare(area)
            self._coverage[meters] = area
        return self._coverage[meters]


def _load_layer(path: Path, ds: Dataset, entry: Mapping[str, Any], stale: bool) -> Layer:
    data = (path / entry["file"]).read_bytes()
    expected = entry.get("sha256")
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("file does not match the checksum in the manifest")
    collection = json.loads(data)
    properties: list[dict[str, Any]] = []
    geoms: list[BaseGeometry] = []
    for feature in collection.get("features") or []:
        if not feature.get("geometry"):
            continue
        geoms.append(geo.to_local(shape(feature["geometry"])))
        properties.append(feature.get("properties") or {})
    return Layer(
        dataset=ds,
        entry=entry,
        properties=properties,
        geoms=np.array(geoms, dtype=object),
        stale=stale,
    )
