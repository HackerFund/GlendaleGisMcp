import asyncio
import dataclasses
import hashlib
import json
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from shapely.geometry import LineString, Point, box, shape

import build_snapshot as bs
from glendale_gis.core import catalog
from glendale_gis.core.arcgis import ArcGISClient, NotAllowedError
from glendale_gis.core.config import Settings

BOUNDARY = catalog.get_dataset("city_boundary")
STATIONS = catalog.get_dataset("fire_stations")
FAULTS = catalog.get_dataset("cgs_fault_zones")
DEBRIS = catalog.get_dataset("usgs_debris_flow")
DAM = catalog.get_dataset("dwr_dam_inundation")

# A 4 km x 4.4 km stand-in for the city.
CITY = (-118.27, 34.14, -118.23, 34.18)


def cw(xmin, ymin, xmax, ymax):
    return [[xmin, ymin], [xmin, ymax], [xmax, ymax], [xmax, ymin], [xmin, ymin]]


def settings(**overrides) -> Settings:
    options = {"min_request_interval_s": 0.0, "max_retries": 0, **overrides}
    return dataclasses.replace(Settings.from_env({}), **options)


# -- fake ArcGIS server ----------------------------------------------------------------------


class FakeLayer:
    """Serves layer metadata and /query (ID lists, counts, ID chunks, offset pages)."""

    def __init__(self, ds, features, *, global_id=False, extra_meta=None, drop_fields=()):
        self.ds = ds
        self.features = features
        fields = [{"name": ds.id_field, "type": "esriFieldTypeOID", "alias": ds.id_field}]
        if global_id:
            fields.append({"name": "GlobalID", "type": "esriFieldTypeGlobalID"})
        fields += [
            {"name": n, "type": "esriFieldTypeString", "alias": n.title()}
            for n in ds.field_names
            if n not in drop_fields
        ]
        self.meta = {
            "maxRecordCount": 2000,
            "objectIdField": ds.id_field,
            "advancedQueryCapabilities": {"supportsPagination": True},
            "fields": fields,
            **(extra_meta or {}),
        }

    def mount(self, router, url=None):
        url = url or self.ds.layer_url
        router.get(url).mock(return_value=httpx.Response(200, json=self.meta))
        router.route(url=url + "/query").mock(side_effect=self.query)

    def query(self, request: httpx.Request) -> httpx.Response:
        raw = request.content.decode() if request.method == "POST" else request.url.query.decode()
        params = {k: v[0] for k, v in parse_qs(raw).items()}
        ids = [f["attributes"][self.ds.id_field] for f in self.features]
        if params.get("returnIdsOnly") == "true":
            return httpx.Response(200, json={"objectIds": ids})
        if params.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": len(ids)})
        if "objectIds" in params:
            wanted = {int(i) for i in params["objectIds"].split(",")}
            chosen = [f for f in self.features if f["attributes"][self.ds.id_field] in wanted]
            return httpx.Response(200, json={"features": chosen})
        return httpx.Response(200, json={"features": self.features})


def feature(ds, oid, geometry, **attrs):
    attributes = {ds.id_field: oid, **{n: f"{n}-{oid}" for n in ds.field_names}, **attrs}
    return {"attributes": attributes, "geometry": geometry}


def boundary_layer():
    return FakeLayer(BOUNDARY, [feature(BOUNDARY, 1, {"rings": [cw(*CITY)]})])


def station_layer():
    return FakeLayer(
        STATIONS,
        [
            feature(STATIONS, 2, {"points": [[-118.25, 34.16]]}),  # single-point multipoint
            feature(STATIONS, 1, {"points": [[-118.2412345678, 34.1512345678]]}),
            feature(STATIONS, 3, {"points": [[-118.10, 34.16]]}),  # ~12 km east: dropped
        ],
    )


def fault_layer():
    return FakeLayer(
        FAULTS,
        [
            feature(FAULTS, 10, {"rings": [cw(-118.26, 34.15, -118.25, 34.16)]}, GlobalID="{A}"),
            feature(FAULTS, 11, {"rings": [cw(-118.24, 34.15, -117.90, 34.16)]}, GlobalID="{B}"),
            feature(FAULTS, 12, {"rings": [cw(-117.60, 34.15, -117.50, 34.16)]}, GlobalID="{C}"),
        ],
        global_id=True,
        extra_meta={"editingInfo": {"lastEditDate": 1763574000000}},
    )


def mount(router, *layers):
    for layer in layers:
        layer.mount(router)


def run_build(tmp_path, only, **overrides):
    return asyncio.run(bs.build(settings(**overrides), tmp_path / "snap", only=only))


def read_manifest(out):
    return json.loads((out / "manifest.json").read_text())


def read_layer(out, layer_id):
    return json.loads((out / f"{layer_id}.geojson").read_text())


# -- end to end ------------------------------------------------------------------------------


def test_build_writes_clipped_layers_and_manifest(tmp_path, capsys):
    with respx.mock(assert_all_called=False) as router:
        mount(router, boundary_layer(), station_layer(), fault_layer())
        code = run_build(tmp_path, ["city_boundary", "fire_stations", "cgs_fault_zones"])
    assert code == 0
    out = tmp_path / "snap"
    assert not (tmp_path / "snap.staging").exists()

    stations = read_layer(out, "fire_stations")
    assert [f["id"] for f in stations["features"]] == [1, 2]  # sorted, far station dropped
    first = stations["features"][0]
    assert first["geometry"] == {"type": "Point", "coordinates": [-118.241235, 34.151235]}
    assert list(first["properties"]) == ["OBJECTID", "NAME", "ADDRESS", "CITY", "sta_no"]

    faults = read_layer(out, "cgs_fault_zones")
    assert [f["id"] for f in faults["features"]] == [10, 11]
    assert [f["properties"]["GlobalID"] for f in faults["features"]] == ["{A}", "{B}"]
    crossing = shape(faults["features"][1]["geometry"])
    assert crossing.bounds[2] < -118.20  # clipped ~2 km past the city edge, not at -117.90
    assert crossing.bounds[2] > -118.23

    manifest = read_manifest(out)
    assert manifest["version"] == bs.MANIFEST_VERSION
    assert set(manifest["layers"]) == {"city_boundary", "fire_stations", "cgs_fault_zones"}
    entry = manifest["layers"]["cgs_fault_zones"]
    assert entry["feature_count"] == 2
    assert entry["source_feature_count"] == 3
    assert entry["global_id_field"] == "GlobalID"
    assert entry["source_last_edit"] == "2025-11-19T17:40:00+00:00"
    assert entry["clip"] == {"buffer": "hazard", "meters": 2000.0}
    data = (out / "cgs_fault_zones.geojson").read_bytes()
    assert entry["bytes"] == len(data)
    assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    assert manifest["layers"]["city_boundary"]["clip"] is None
    assert manifest["layers"]["fire_stations"]["clip"] == {"buffer": "city", "meters": 100.0}
    assert manifest["total_bytes"] == sum(e["bytes"] for e in manifest["layers"].values())
    assert "total" in capsys.readouterr().out


def test_hazard_layers_use_the_wider_buffer(tmp_path):
    with respx.mock(assert_all_called=False) as router:
        mount(router, boundary_layer(), fault_layer())
        assert run_build(tmp_path, ["cgs_fault_zones"], hazard_buffer_m=500) == 0
    crossing = shape(read_layer(tmp_path / "snap", "cgs_fault_zones")["features"][1]["geometry"])
    east_edge_m = (crossing.bounds[2] - CITY[2]) * 111_195 * 0.827  # deg lon to m at 34.2°
    assert east_edge_m == pytest.approx(500, rel=0.02)


def test_only_keeps_other_layers(tmp_path):
    with respx.mock(assert_all_called=False) as router:
        mount(router, boundary_layer(), station_layer(), fault_layer())
        assert run_build(tmp_path, ["fire_stations", "cgs_fault_zones"]) == 0
        assert run_build(tmp_path, ["fire_stations"]) == 0
    out = tmp_path / "snap"
    assert set(read_manifest(out)["layers"]) == {"fire_stations", "cgs_fault_zones"}
    assert (out / "cgs_fault_zones.geojson").exists()


def test_over_budget_writes_nothing(tmp_path, capsys):
    with respx.mock(assert_all_called=False) as router:
        mount(router, boundary_layer(), station_layer())
        assert run_build(tmp_path, ["fire_stations"], max_snapshot_mb=0.0001) == 1
    assert not (tmp_path / "snap").exists()
    assert not (tmp_path / "snap.staging").exists()
    assert "over the" in capsys.readouterr().err


def test_empty_layer_fails_the_build_and_writes_nothing(tmp_path, capsys):
    empty = FakeLayer(STATIONS, [feature(STATIONS, 3, {"points": [[-118.10, 34.16]]})])
    with respx.mock(assert_all_called=False) as router:
        mount(router, boundary_layer(), empty, fault_layer())
        assert run_build(tmp_path, ["cgs_fault_zones", "fire_stations"]) == 1
    assert not (tmp_path / "snap").exists()
    err = capsys.readouterr().err
    assert "fire_stations: no features inside the clip area (1 returned" in err


def test_debris_flow_may_be_empty(tmp_path):
    with respx.mock(assert_all_called=False) as router:
        mount(router, boundary_layer(), FakeLayer(DEBRIS, []))
        assert run_build(tmp_path, ["usgs_debris_flow"]) == 0
    assert read_layer(tmp_path / "snap", "usgs_debris_flow")["features"] == []


def test_missing_catalog_field_fails(tmp_path, capsys):
    drifted = FakeLayer(STATIONS, station_layer().features, drop_fields=("sta_no",))
    with respx.mock(assert_all_called=False) as router:
        mount(router, boundary_layer(), drifted)
        assert run_build(tmp_path, ["fire_stations"]) == 1
    assert "catalog fields missing from the source: sta_no" in capsys.readouterr().err


def test_duplicate_ids_fail(tmp_path, capsys):
    dup = feature(STATIONS, 1, {"points": [[-118.25, 34.15]]})
    layer = FakeLayer(STATIONS, [dup, dup])
    with respx.mock(assert_all_called=False) as router:
        mount(router, boundary_layer(), layer)
        assert run_build(tmp_path, ["fire_stations"]) == 1
    assert "duplicate object IDs" in capsys.readouterr().err


def test_dry_run_counts_and_writes_nothing(tmp_path, capsys):
    with respx.mock(assert_all_called=False) as router:
        mount(router, boundary_layer(), fault_layer())
        code = asyncio.run(
            bs.build(settings(), tmp_path / "snap", only=["cgs_fault_zones"], dry_run=True)
        )
    assert code == 0
    assert not (tmp_path / "snap").exists()
    out = capsys.readouterr().out
    assert "cgs_fault_zones" in out
    assert "2025-11-19T17:40:00+00:00" in out


def test_unknown_or_live_dataset_is_a_usage_error(capsys):
    assert bs.main(["--only", "parcels,nope", "--out", "/nonexistent"]) == 2
    assert "not snapshot datasets: parcels, nope" in capsys.readouterr().err


# -- pieces ----------------------------------------------------------------------------------


AREA = box(0, 0, 10, 10)


def test_clip_keeps_features_inside_unchanged():
    inside = box(1, 1, 2, 2)
    assert bs.clip(inside, AREA, 2) is inside


def test_clip_drops_features_outside():
    assert bs.clip(box(20, 20, 30, 30), AREA, 2) is None
    assert bs.clip(Point(20, 20), AREA, 0) is None
    assert bs.clip(None, AREA, 2) is None


def test_clip_cuts_crossing_polygons_and_drops_lower_dimension_leftovers():
    clipped = bs.clip(box(5, 5, 15, 15), AREA, 2)
    assert clipped.equals(box(5, 5, 10, 10))
    # Touching the area along an edge only would leave a line: nothing is kept.
    assert bs.clip(box(10, 0, 20, 10), AREA, 2) is None


def test_clip_lines():
    clipped = bs.clip(LineString([(5, 5), (15, 5)]), AREA, 1)
    assert clipped.equals(LineString([(5, 5), (10, 5)]))


def test_select_fields_orders_id_global_id_then_catalog_fields():
    layer = FakeLayer(FAULTS, [], global_id=True)
    id_field, global_id, fields = bs.select_fields(FAULTS, layer.meta)
    assert id_field == "OBJECTID"
    assert global_id == "GlobalID"
    assert [f["name"] for f in fields] == ["OBJECTID", "GlobalID", *FAULTS.field_names]
    assert fields[2]["alias"] == "Quad_Name"


def test_select_fields_requires_the_id_field():
    layer = FakeLayer(FAULTS, [])
    layer.meta["fields"] = layer.meta["fields"][1:]
    with pytest.raises(bs.BuildError, match="ID field"):
        bs.select_fields(FAULTS, layer.meta)


ITEM_URL = catalog.ARCGIS_ITEM_URL.format(item_id=DAM.item_id)
DWR = "https://services.arcgis.com/aa38u6OgfNoCkTJ6/arcgis/rest/services"


@pytest.mark.parametrize(
    "item_url, expected",
    [
        (f"{DWR}/Approved_InundationBoundaries_As_of_Apr01_2026/FeatureServer", "/100"),
        (f"{DWR}/Approved_InundationBoundaries_As_of_Apr01_2026/FeatureServer/100", "/100"),
    ],
)
async def test_resolve_layer_url_follows_a_moved_service(item_url, expected):
    async with ArcGISClient(settings()) as client:
        with respx.mock:
            respx.get(ITEM_URL).mock(return_value=httpx.Response(200, json={"url": item_url}))
            url = await bs.resolve_layer_url(client, DAM)
    assert url == item_url.removesuffix("/100") + expected


async def test_resolve_layer_url_rejects_urls_outside_the_allowlist():
    async with ArcGISClient(settings()) as client:
        with respx.mock:
            evil = {"url": "https://evil.example.com/arcgis/rest/services/X/FeatureServer"}
            respx.get(ITEM_URL).mock(return_value=httpx.Response(200, json=evil))
            with pytest.raises(NotAllowedError):
                await bs.resolve_layer_url(client, DAM)


async def test_resolve_layer_url_falls_back_to_the_catalog_url():
    async with ArcGISClient(settings()) as client:
        with respx.mock:
            respx.get(ITEM_URL).mock(return_value=httpx.Response(404))
            assert await bs.resolve_layer_url(client, DAM) == DAM.layer_url


async def test_layers_without_an_item_id_are_not_resolved():
    async with ArcGISClient(settings()) as client:
        assert await bs.resolve_layer_url(client, FAULTS) == FAULTS.layer_url
