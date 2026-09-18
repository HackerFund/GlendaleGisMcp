import dataclasses
from urllib.parse import parse_qs

import httpx
import pytest
import respx

import fixture_snapshot as fx
from glendale_gis.core import query as q
from glendale_gis.core.arcgis import ArcGISClient
from glendale_gis.core.cache import Cache
from glendale_gis.core.catalog import get_dataset
from glendale_gis.core.config import Settings
from glendale_gis.core.models import ActionableError, Filter, Near
from glendale_gis.core.snapshot import Snapshot

PARCELS = get_dataset("parcels").layer_url


@pytest.fixture(scope="module")
def snap(tmp_path_factory):
    return Snapshot.load(fx.write(tmp_path_factory.mktemp("snap")))


@pytest.fixture
def settings():
    return dataclasses.replace(Settings.from_env({}), min_request_interval_s=0.0, max_retries=0)


@pytest.fixture
async def engine(snap, settings):
    async with ArcGISClient(settings) as client:
        yield q.QueryEngine(snap, client, Cache(None), settings)


def ids(result):
    return [f.ref.object_id for f in result.features]


def F(field, op="eq", value=None):
    return Filter(field=field, op=op, value=value)


# -- snapshot filters ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flt, expected",
    [
        (F("Route", "eq", "7"), [2]),
        (F("Route", "eq", " 7 "), [2]),  # surrounding spaces ignored
        (F("On_Street", "eq", "nb brand"), [1]),  # case ignored
        (F("Route", "ne", "7"), [1, 3]),
        (F("Route", "contains", "7"), [1, 2]),
        (F("On_Street", "starts_with", "sb"), [2]),
        (F("Route", "in", ["12", "7"]), [2, 3]),
        (F("Stop_Numbe", "eq", 354), [1]),
        (F("Stop_Numbe", "eq", "354"), [1]),  # numeric text is coerced
        (F("Stop_Numbe", "gt", 50), [1, 3]),
        (F("Stop_Numbe", "lte", 88), [2, 3]),
        (F("Stop_Numbe", "in", [12, 88]), [2, 3]),
        (F("On_Street", "is_null"), [3]),
        (F("On_Street", "not_null"), [1, 2]),
    ],
)
async def test_filters(engine, flt, expected):
    assert ids(await engine.query("bus_stops", filters=[flt])) == expected


async def test_filters_combine_with_and(engine):
    r = await engine.query(
        "bus_stops", filters=[F("Route", "contains", "7"), F("Stop_Numbe", "lt", 100)]
    )
    assert ids(r) == [2]


async def test_near_sorts_by_distance_and_reports_it(engine):
    r = await engine.query("bus_stops", near=Near(lat=34.1650, lon=-118.2500, radius_m=600))
    assert ids(r) == [2, 1]
    assert r.features[0].distance_m == 0
    assert r.features[1].distance_m == pytest.approx(500, abs=2)
    assert any("straight-line" in n for n in r.notes)


async def test_near_with_zero_radius_finds_containing_polygons(engine):
    r = await engine.query("parks", near=Near(lat=34.16, lon=-118.25))
    assert ids(r) == [7]
    assert r.features[0].distance_m == 0
    assert (await engine.query("parks", near=Near(lat=34.17, lon=-118.25))).total == 0


async def test_bbox(engine):
    r = await engine.query("bus_stops", bbox=[-118.251, 34.16, -118.249, 34.166])
    assert ids(r) == [1, 2]


async def test_paging(engine):
    first = await engine.query("bus_stops", limit=2)
    assert (first.total, first.returned, first.next_offset, ids(first)) == (3, 2, 2, [1, 2])
    last = await engine.query("bus_stops", limit=2, offset=2)
    assert (last.returned, last.next_offset, ids(last)) == (1, None, [3])


async def test_limit_is_clamped(engine):
    assert (await engine.query("bus_stops", limit=0)).returned == 1
    assert (await engine.query("bus_stops", limit=10_000)).returned == 3


async def test_fields_selects_attributes_and_keeps_the_id(engine):
    r = await engine.query("bus_stops", fields=["Route"], limit=1)
    assert r.features[0].attributes == {"OBJECTID": 1, "Route": "3,7"}


async def test_geometry_only_when_asked(engine, monkeypatch):
    assert (await engine.query("fire_stations", limit=1)).features[0].geometry is None
    r = await engine.query("fire_stations", limit=1, include_geometry=True)
    assert r.features[0].geometry == {"type": "Point", "coordinates": (-118.26, 34.16)}
    monkeypatch.setattr(q, "MAX_GEOMETRY_VERTICES", 3)
    r = await engine.query("parks", include_geometry=True)
    assert r.features[0].geometry is None
    assert "5 vertices" in r.features[0].geometry_omitted


async def test_results_carry_ref_meta_and_disclaimer(engine, snap):
    r = await engine.query("bus_stops", limit=1)
    assert r.features[0].ref.layer_url == snap.layer("bus_stops").layer_url
    assert r.meta.cached is True
    assert any("not a substitute for legal descriptions" in n for n in r.notes)


# -- errors ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"dataset_id": "nope"}, "Unknown dataset"),
        ({"dataset_id": "hospitals"}, "unavailable"),  # not in the fixture snapshot
        ({"filters": [F("route", "eq", "7")]}, "Unknown field(s) for bus_stops: route"),
        ({"fields": ["Nope"]}, "Unknown field(s)"),
        ({"filters": [F("Stop_Numbe", "gt", "abc")]}, "Expected a number"),
        ({"filters": [F("Stop_Numbe", "contains", "3")]}, "only works on text fields"),
        ({"filters": [F("Route", "in", "7")]}, "'in' needs a list"),
        ({"filters": [F("Route", "eq", ["7"])]}, "Use 'in' for a list"),
        ({"filters": [F("Route", "eq")]}, "needs a value"),
        ({"filters": [F("Route", "is_null", "x")]}, "takes no value"),
        ({"filters": [F("Route")] * 11}, "Too many filters"),
        ({"bbox": [1, 2, 3]}, "bbox needs 4 numbers"),
        ({"bbox": [-118.2, 34.2, -118.3, 34.1]}, "min < max"),
        ({"bbox": [0, 0, 1, 1], "near": Near(lat=34.16, lon=-118.25)}, "either 'near' or 'bbox'"),
    ],
)
async def test_errors_explain_what_to_fix(engine, kwargs, message):
    dataset = kwargs.pop("dataset_id", "bus_stops")
    with pytest.raises(ActionableError) as exc:
        await engine.query(dataset, **kwargs)
    assert message in exc.value.error


async def test_unknown_field_error_lists_the_real_fields(engine):
    with pytest.raises(ActionableError) as exc:
        await engine.query("bus_stops", filters=[F("route")])
    assert "Route" in exc.value.suggestions[0]


async def test_date_fields_cant_be_filtered(engine):
    with pytest.raises(ActionableError, match="date field PubDate"):
        await engine.query("dwr_dam_inundation", filters=[F("PubDate", "gt", 1)])


# -- live: where clause ----------------------------------------------------------------------

TYPES = {
    "OBJECTID": "esriFieldTypeOID",
    "APN": "esriFieldTypeString",
    "Units": "esriFieldTypeInteger",
}


@pytest.mark.parametrize(
    "filters, expected",
    [
        ([], "1=1"),
        ([F("APN", "eq", "5642-012-904")], "UPPER(APN) = '5642-012-904'"),
        ([F("APN", "eq", "abc")], "UPPER(APN) = 'ABC'"),
        ([F("Units", "gte", "3")], "Units >= 3"),
        ([F("Units", "lt", 2.5)], "Units < 2.5"),
        ([F("APN", "in", ["a", "b"])], "UPPER(APN) IN ('A', 'B')"),
        ([F("Units", "in", [1, 2])], "Units IN (1, 2)"),
        ([F("APN", "contains", "012")], "UPPER(APN) LIKE '%012%'"),
        ([F("APN", "starts_with", "5642")], "UPPER(APN) LIKE '5642%'"),
        ([F("APN", "is_null")], "APN IS NULL"),
        ([F("APN", "not_null"), F("Units", "eq", 1)], "APN IS NOT NULL AND Units = 1"),
        # Quotes are escaped, so a value can't break out of the string.
        ([F("APN", "eq", "x' OR '1'='1")], "UPPER(APN) = 'X'' OR ''1''=''1'"),
    ],
)
def test_where_clause(filters, expected):
    assert q.where_clause(filters, TYPES) == expected


@pytest.mark.parametrize(
    "flt, message",
    [
        (F("Units", "eq", "1; DROP TABLE x"), "Expected a number"),
        (F("Units", "eq", True), "Expected a number"),
        (F("APN", "contains", "%"), "can't contain '%' or '_'"),
        (F("APN", "eq", "a_b"), "can't contain '%' or '_'"),
    ],
)
def test_where_clause_rejects_unsafe_values(flt, message):
    with pytest.raises(ActionableError, match=message):
        q.where_clause([flt], TYPES)


# -- live: requests --------------------------------------------------------------------------

PARCEL_META = {
    "maxRecordCount": 2000,
    "fields": [
        {"name": "OBJECTID", "type": "esriFieldTypeOID"},
        {"name": "APN", "type": "esriFieldTypeString"},
        {"name": "UseType", "type": "esriFieldTypeString"},
    ],
}
PARCEL = {
    "attributes": {"OBJECTID": 28949, "APN": "5642-012-904", "UseType": "Government"},
    "geometry": {
        "rings": [
            [
                [-118.249, 34.146],
                [-118.249, 34.147],
                [-118.247, 34.147],
                [-118.247, 34.146],
                [-118.249, 34.146],
            ]
        ]
    },
}


def mock_parcels(count=1):
    respx.get(PARCELS).mock(return_value=httpx.Response(200, json=PARCEL_META))

    def respond(request):
        raw = request.content.decode() if request.method == "POST" else request.url.query.decode()
        params = {k: v[0] for k, v in parse_qs(raw).items()}
        if params.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": count})
        return httpx.Response(200, json={"features": [PARCEL]})

    return respx.route(url=PARCELS + "/query").mock(side_effect=respond)


def params_of(call):
    request = call.request
    raw = request.content.decode() if request.method == "POST" else request.url.query.decode()
    return {k: v[0] for k, v in parse_qs(raw).items()}


@respx.mock
async def test_live_query_near_a_point(engine):
    route = mock_parcels()
    r = await engine.query("parcels", near=Near(lat=34.1466, lon=-118.2482), fields=["APN"])
    assert r.total == 1
    assert r.features[0].attributes == {"OBJECTID": 28949, "APN": "5642-012-904"}
    assert r.features[0].distance_m == 0
    assert r.features[0].ref.object_id == 28949
    assert r.meta.cached is False
    assert r.meta.source == "City of Glendale"
    page = params_of(route.calls[1])
    assert page["where"] == "1=1"
    assert page["geometryType"] == "esriGeometryPoint"
    assert page["inSR"] == "4326"
    assert page["outFields"] == "APN,OBJECTID"
    assert page["resultRecordCount"] == "20"
    assert "distance" not in page


@respx.mock
async def test_live_query_with_radius_and_filters(engine):
    route = mock_parcels(count=40)
    r = await engine.query(
        "parcels",
        filters=[F("UseType", "eq", "government")],
        near=Near(lat=34.1466, lon=-118.2482, radius_m=250),
        limit=1,
    )
    assert (r.total, r.next_offset) == (40, 1)
    page = params_of(route.calls[1])
    assert page["where"] == "UPPER(UseType) = 'GOVERNMENT'"
    assert (page["distance"], page["units"]) == ("250.0", "esriSRUnit_Meter")
    assert any("ordered by object ID" in n for n in r.notes)


@respx.mock
async def test_live_results_are_cached(engine):
    route = mock_parcels()
    await engine.query("parcels", bbox=[-118.25, 34.14, -118.24, 34.15])
    again = await engine.query("parcels", bbox=[-118.25, 34.14, -118.24, 34.15])
    assert again.meta.cached is True
    assert route.call_count == 2  # one count + one page, only once


@respx.mock
async def test_live_unknown_field_is_checked_against_the_live_schema(engine):
    mock_parcels()
    with pytest.raises(ActionableError, match="Unknown field"):
        await engine.query("parcels", filters=[F("Owner", "eq", "x")])


@respx.mock
async def test_live_server_down(engine):
    respx.get(PARCELS).mock(return_value=httpx.Response(503))
    with pytest.raises(ActionableError, match="isn't responding"):
        await engine.query("parcels")
