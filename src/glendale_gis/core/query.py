"""Structured queries over catalog datasets: attribute filters, spatial filters and paging.

Filters are ``{field, op, value}`` objects, never raw SQL. Snapshot datasets are filtered in
memory. Live datasets (parcels) get a ``where`` clause built here: field names are checked
against the layer's schema, strings are quoted and escaped, numbers must be numbers, and LIKE
wildcards are refused, so no caller text reaches the query unchecked.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

import shapely
from shapely.geometry import box, mapping

from glendale_gis.core import geo
from glendale_gis.core.arcgis import ArcGISClient, ArcGISError
from glendale_gis.core.cache import Cache, get_or_fetch
from glendale_gis.core.catalog import DATASETS, Dataset
from glendale_gis.core.config import Settings
from glendale_gis.core.models import (
    ActionableError,
    Filter,
    Meta,
    Near,
    QueryFeature,
    QueryResult,
    Ref,
)
from glendale_gis.core.snapshot import Layer, LayerUnavailable, Snapshot, local_point

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_OFFSET = 100_000
MAX_FILTERS = 10
MAX_IN_VALUES = 100
MAX_GEOMETRY_VERTICES = 5_000
LIVE_NAMESPACE = "live-query"
META_NAMESPACE = "layer-metadata"

NUMERIC_TYPES = frozenset(
    {
        "esriFieldTypeOID",
        "esriFieldTypeInteger",
        "esriFieldTypeSmallInteger",
        "esriFieldTypeBigInteger",
        "esriFieldTypeDouble",
        "esriFieldTypeSingle",
    }
)
SQL_OPS = {"eq": "=", "ne": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}


class QueryEngine:
    def __init__(
        self, snapshot: Snapshot, client: ArcGISClient, cache: Cache, settings: Settings
    ) -> None:
        self._snapshot = snapshot
        self._client = client
        self._cache = cache
        self._ttl_s = settings.live_cache_ttl_s

    async def query(
        self,
        dataset_id: str,
        *,
        filters: Sequence[Filter] = (),
        near: Near | None = None,
        bbox: Sequence[float] | None = None,
        fields: Sequence[str] | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        include_geometry: bool = False,
    ) -> QueryResult:
        ds = DATASETS.get(dataset_id)
        if ds is None:
            raise ActionableError(
                f"Unknown dataset {dataset_id!r}.", ["Call list_datasets to see the dataset ids."]
            )
        if len(filters) > MAX_FILTERS:
            raise ActionableError(f"Too many filters (at most {MAX_FILTERS}).")
        if near is not None and bbox is not None:
            raise ActionableError("Use either 'near' or 'bbox', not both.")
        if bbox is not None:
            _check_bbox(bbox)
        limit = max(1, min(int(limit), MAX_LIMIT))
        offset = max(0, min(int(offset), MAX_OFFSET))

        if ds.access == "live":
            return await self._live(
                ds, filters, near, bbox, fields, limit, offset, include_geometry
            )
        return self._local(ds, filters, near, bbox, fields, limit, offset, include_geometry)

    async def schema(self, ds: Dataset) -> tuple[list[dict[str, Any]], Meta]:
        """The live layer's fields, from its (cached) metadata."""
        meta_body, meta = await self._fetch_cached(
            ds, META_NAMESPACE, ds.layer_url, lambda: self._client.layer_metadata(ds.layer_url)
        )
        return list(meta_body.get("fields") or []), meta

    # -- snapshot ----------------------------------------------------------------------------

    def _local(
        self,
        ds: Dataset,
        filters: Sequence[Filter],
        near: Near | None,
        bbox: Sequence[float] | None,
        fields: Sequence[str] | None,
        limit: int,
        offset: int,
        include_geometry: bool,
    ) -> QueryResult:
        try:
            layer = self._snapshot.layer(ds.id)
        except LayerUnavailable as exc:
            raise ActionableError(
                f"Dataset {ds.id} is unavailable: {exc}",
                ["Try another dataset, or rebuild the snapshot."],
            ) from None
        types = _local_field_types(layer)
        _check_fields(ds, [f.field for f in filters] + list(fields or []), types)
        for f in filters:
            _check_filter(f, types)

        indices = list(range(len(layer)))
        distances = None
        if near is not None:
            distances = layer.distances(local_point(near.lat, near.lon))
            indices = [i for i in indices if distances[i] <= near.radius_m]
        elif bbox is not None:
            area = geo.to_local(box(*bbox))
            indices = layer.tree.query(area, predicate="intersects").tolist()
        indices = [i for i in indices if all(_matches(layer.properties[i], f) for f in filters)]

        def order(i: int) -> tuple:
            oid = layer.ref(i).object_id or 0
            return (float(distances[i]), oid) if distances is not None else (oid,)

        indices.sort(key=order)
        page = indices[offset : offset + limit]
        features = []
        for i in page:
            geometry = None
            omitted = None
            if include_geometry:
                geometry, omitted = _geometry(layer.geoms[i])
            features.append(
                QueryFeature(
                    attributes=_select(layer.properties[i], fields, layer.entry.get("id_field")),
                    ref=layer.ref(i),
                    distance_m=round(float(distances[i])) if distances is not None else None,
                    geometry=geometry,
                    geometry_omitted=omitted,
                )
            )
        return _result(ds, len(indices), offset, features, layer.meta(), _notes(ds, near))

    # -- live --------------------------------------------------------------------------------

    async def _live(
        self,
        ds: Dataset,
        filters: Sequence[Filter],
        near: Near | None,
        bbox: Sequence[float] | None,
        fields: Sequence[str] | None,
        limit: int,
        offset: int,
        include_geometry: bool,
    ) -> QueryResult:
        schema, _ = await self.schema(ds)
        types = {f["name"]: f.get("type", "") for f in schema}
        _check_fields(ds, [f.field for f in filters] + list(fields or []), types)
        id_field = next((n for n, t in types.items() if t == "esriFieldTypeOID"), ds.id_field)
        gid_field = next((n for n, t in types.items() if t == "esriFieldTypeGlobalID"), None)

        params: dict[str, Any] = {"where": where_clause(filters, types)}
        if near is not None:
            params.update(
                geometry={"x": near.lon, "y": near.lat, "spatialReference": {"wkid": 4326}},
                geometryType="esriGeometryPoint",
                spatialRel="esriSpatialRelIntersects",
                inSR=4326,
            )
            if near.radius_m:
                params.update(distance=near.radius_m, units="esriSRUnit_Meter")
        elif bbox is not None:
            params.update(
                geometry=",".join(str(v) for v in bbox),
                geometryType="esriGeometryEnvelope",
                spatialRel="esriSpatialRelIntersects",
                inSR=4326,
            )
        out_fields = sorted({id_field, *(fields or [])}) if fields else ["*"]
        page_params = {
            **params,
            "outFields": ",".join(out_fields),
            "returnGeometry": include_geometry or near is not None,
            "outSR": 4326,
            "orderByFields": id_field,
            "resultOffset": offset,
            "resultRecordCount": limit,
        }
        url = f"{ds.layer_url}/query"
        key = json.dumps(page_params, sort_keys=True, default=str)

        async def fetch() -> dict[str, Any]:
            count = await self._client.request_json(url, {**params, "returnCountOnly": True})
            page = await self._client.request_json(url, page_params)
            return {"count": int(count.get("count") or 0), "features": page.get("features") or []}

        body, meta = await self._fetch_cached(ds, LIVE_NAMESPACE, key, fetch)
        point = local_point(near.lat, near.lon) if near is not None else None
        features = []
        for f in body["features"]:
            attrs = f.get("attributes") or {}
            shape = geo.from_esri(f.get("geometry")) if f.get("geometry") else None
            geometry = omitted = None
            if include_geometry and shape is not None:
                geometry, omitted = _geometry(geo.to_local(shape))
            distance = None
            if point is not None and shape is not None:
                distance = round(float(geo.to_local(shape).distance(point)))
            features.append(
                QueryFeature(
                    attributes=_select(attrs, fields, id_field),
                    ref=Ref(
                        dataset=ds.id,
                        object_id=attrs.get(id_field),
                        global_id=attrs.get(gid_field) if gid_field else None,
                        layer_url=ds.layer_url,
                    ),
                    distance_m=distance,
                    geometry=geometry,
                    geometry_omitted=omitted,
                )
            )
        notes = _notes(ds, near)
        if near is not None:
            notes.append("Live results are ordered by object ID, not by distance.")
        return _result(ds, body["count"], offset, features, meta, notes)

    async def _fetch_cached(self, ds: Dataset, namespace: str, key: str, fetch) -> tuple[Any, Meta]:
        try:
            fetched = await get_or_fetch(
                self._cache, namespace, key, self._ttl_s, fetch, retryable=(ArcGISError,)
            )
        except ArcGISError:
            raise ActionableError(
                f"The {ds.source_agency} server for {ds.id} isn't responding right now.",
                ["Try again in a minute."],
            ) from None
        meta = Meta(
            source=ds.source_agency,
            url=ds.layer_url,
            cached=fetched.cached,
            as_of=datetime.fromtimestamp(fetched.fetched_at, timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            stale=fetched.stale,
        )
        return fetched.value, meta


# --------------------------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------------------------


def where_clause(filters: Sequence[Filter], types: Mapping[str, str]) -> str:
    """Translate validated filters into an ArcGIS ``where`` clause."""
    if not filters:
        return "1=1"
    clauses = []
    for f in filters:
        _check_filter(f, types)
        numeric = types.get(f.field) in NUMERIC_TYPES
        column = f.field if numeric else f"UPPER({f.field})"
        if f.op == "is_null":
            clauses.append(f"{f.field} IS NULL")
        elif f.op == "not_null":
            clauses.append(f"{f.field} IS NOT NULL")
        elif f.op == "in":
            values = ", ".join(_sql_literal(v, numeric) for v in f.value)  # type: ignore[union-attr]
            clauses.append(f"{column} IN ({values})")
        elif f.op == "contains":
            clauses.append(f"{column} LIKE '%{_sql_text(f.value)}%'")
        elif f.op == "starts_with":
            clauses.append(f"{column} LIKE '{_sql_text(f.value)}%'")
        else:
            clauses.append(f"{column} {SQL_OPS[f.op]} {_sql_literal(f.value, numeric)}")
    return " AND ".join(clauses)


def _sql_literal(value: Any, numeric: bool) -> str:
    if numeric:
        return repr(_number(value))
    return f"'{_sql_text(value)}'"


def _sql_text(value: Any) -> str:
    text = str(value).strip().upper()
    if "%" in text or "_" in text:
        raise ActionableError("Text values can't contain '%' or '_'.")
    return text.replace("'", "''")


def _number(value: Any) -> int | float:
    if isinstance(value, bool):
        raise ActionableError("Expected a number, got true/false.")
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(str(value).strip())
    except ValueError:
        raise ActionableError(f"Expected a number, got {value!r}.") from None
    return int(number) if number.is_integer() else number


def _check_filter(f: Filter, types: Mapping[str, str]) -> None:
    numeric = types.get(f.field) in NUMERIC_TYPES
    if types.get(f.field) == "esriFieldTypeDate":
        raise ActionableError(
            f"Filtering on date field {f.field} isn't supported.",
            ["Filter on another field, then read the dates from the results."],
        )
    if f.op in ("is_null", "not_null"):
        if f.value is not None:
            raise ActionableError(f"'{f.op}' takes no value.")
        return
    if f.value is None:
        raise ActionableError(f"'{f.op}' needs a value.", ["Use 'is_null' to find empty values."])
    if f.op == "in":
        if not isinstance(f.value, list) or not f.value or len(f.value) > MAX_IN_VALUES:
            raise ActionableError(f"'in' needs a list of 1 to {MAX_IN_VALUES} values.")
        if numeric:
            for v in f.value:
                _number(v)
        return
    if isinstance(f.value, list):
        raise ActionableError(f"'{f.op}' takes one value, not a list. Use 'in' for a list.")
    if f.op in ("contains", "starts_with"):
        if numeric:
            raise ActionableError(f"'{f.op}' only works on text fields; {f.field} is numeric.")
        return
    if numeric:
        _number(f.value)


def _matches(props: Mapping[str, Any], f: Filter) -> bool:
    actual = props.get(f.field)
    if f.op == "is_null":
        return actual is None or (isinstance(actual, str) and not actual.strip())
    if f.op == "not_null":
        return not (actual is None or (isinstance(actual, str) and not actual.strip()))
    if actual is None:
        return False
    if f.op == "in":
        return any(_equal(actual, v) for v in f.value)  # type: ignore[union-attr]
    if f.op == "eq":
        return _equal(actual, f.value)
    if f.op == "ne":
        return not _equal(actual, f.value)
    if f.op == "contains":
        return _text(f.value) in _text(actual)
    if f.op == "starts_with":
        return _text(actual).startswith(_text(f.value))
    left, right = _comparable(actual, f.value)
    if left is None:
        return False
    return {
        "lt": left < right,
        "lte": left <= right,
        "gt": left > right,
        "gte": left >= right,
    }[f.op]


def _text(value: Any) -> str:
    return str(value).strip().casefold()


def _equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, (int, float)) and not isinstance(actual, bool):
        try:
            return float(actual) == float(_number(expected))
        except ActionableError:
            return False
    return _text(actual) == _text(expected)


def _comparable(actual: Any, expected: Any) -> tuple[Any, Any]:
    if isinstance(actual, (int, float)) and not isinstance(actual, bool):
        try:
            return float(actual), float(_number(expected))
        except ActionableError:
            return None, None
    return _text(actual), _text(expected)


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _local_field_types(layer: Layer) -> dict[str, str]:
    fields = layer.entry.get("fields") or []
    if fields:
        return {f["name"]: f.get("type", "") for f in fields}
    # Older manifests without field types: fall back to the catalog.
    names = [layer.dataset.id_field, *layer.dataset.field_names]
    return {n: "" for n in names}


def _check_fields(ds: Dataset, names: Sequence[str], types: Mapping[str, str]) -> None:
    unknown = [n for n in names if n not in types]
    if unknown:
        raise ActionableError(
            f"Unknown field(s) for {ds.id}: {', '.join(unknown)}. Field names are case-sensitive.",
            [f"Fields: {', '.join(types)}.", "Call describe_dataset for what each field means."],
        )


def _check_bbox(bbox: Sequence[float]) -> None:
    if len(bbox) != 4:
        raise ActionableError("bbox needs 4 numbers: [min_lon, min_lat, max_lon, max_lat].")
    min_lon, min_lat, max_lon, max_lat = bbox
    if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
        raise ActionableError(
            "bbox must be [min_lon, min_lat, max_lon, max_lat] with min < max, in degrees."
        )


def _select(
    attrs: Mapping[str, Any], fields: Sequence[str] | None, id_field: str | None
) -> dict[str, Any]:
    if not fields:
        return dict(attrs)
    keep = [id_field, *fields] if id_field else list(fields)
    return {k: attrs.get(k) for k in dict.fromkeys(keep) if k is not None}


def _geometry(local_geom: Any) -> tuple[dict[str, Any] | None, str | None]:
    count = int(shapely.get_num_coordinates(local_geom))
    if count > MAX_GEOMETRY_VERTICES:
        return None, (
            f"Geometry has {count:,} vertices (limit {MAX_GEOMETRY_VERTICES:,}). Fetch it from "
            "ref.layer_url by object ID if you need it."
        )
    return mapping(geo.round_coords(geo.to_lonlat(local_geom))), None


def _notes(ds: Dataset, near: Near | None) -> list[str]:
    notes = []
    if ds.disclaimer:
        notes.append(ds.disclaimer)
    if near is not None:
        notes.append("Distances are straight-line meters to the nearest edge (0 when inside).")
    return notes


def _result(
    ds: Dataset,
    total: int,
    offset: int,
    features: list[QueryFeature],
    meta: Meta,
    notes: list[str],
) -> QueryResult:
    end = offset + len(features)
    return QueryResult(
        dataset=ds.id,
        total=total,
        offset=offset,
        returned=len(features),
        next_offset=end if end < total else None,
        features=features,
        notes=notes,
        _meta=meta,
    )
