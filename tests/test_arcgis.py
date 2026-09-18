import dataclasses
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from glendale_gis.core import catalog
from glendale_gis.core.arcgis import ArcGISClient, ArcGISError, NotAllowedError, check_allowed
from glendale_gis.core.config import Settings

FHSZ = catalog.get_dataset("calfire_fhsz_lra").layer_url
ZONING = catalog.get_dataset("zoning").layer_url
DAM = catalog.get_dataset("dwr_dam_inundation")


class FakeTime:
    """Deterministic clock and sleep: sleeping advances the clock and is recorded."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(fake: FakeTime, **overrides) -> ArcGISClient:
    options = {"min_request_interval_s": 0.0, "max_retries": 3, **overrides}
    settings = dataclasses.replace(Settings.from_env({}), **options)
    return ArcGISClient(settings, clock=fake.clock, sleep=fake.sleep, jitter=lambda: 0.0)


def params_of(request: httpx.Request) -> dict:
    raw = request.content.decode() if request.method == "POST" else request.url.query.decode()
    return {k: v[0] for k, v in parse_qs(raw).items()}


# -- allowlist -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://gismap.glendaleca.gov/arcgis/rest/services/Common/Zoning/FeatureServer/2",
        "https://evil.example.com/arcgis/rest/services/Common/Zoning/FeatureServer/2",
        "https://gismap.glendaleca.gov/arcgis/rest/services/SampleWorldCities/MapServer/0",
        "https://gismap.glendaleca.gov/arcgis/rest/services/Common/Zoning/FeatureServer/1/../2",
        "https://gismap.glendaleca.gov/arcgis/rest/services/Common/Zoning/FeatureServer/2%2F..%2F3",
        "https://user@gismap.glendaleca.gov/arcgis/rest/services/Common/Zoning/FeatureServer/2",
        "https://gismap.glendaleca.gov:8443/arcgis/rest/services/Common/Zoning/FeatureServer/2",
        "https://gismap.glendaleca.gov/arcgis/rest/services/Common/Zoning/FeatureServer/2?where=1=1",
        "https://gismap.glendaleca.gov/arcgis/rest/services/Common/Zoning/FeatureServer/2/applyEdits",
        "https://gismap.glendaleca.gov/arcgis/rest/services/Common/Zoning/FeatureServer/2/query/x",
        "https://gismap.glendaleca.gov.evil.com/arcgis/rest/services/Common/Zoning/FeatureServer/2",
        # Geocoder allows only its read operations.
        "https://gismap.glendaleca.gov/arcgis/rest/services/Common/CAD_SiteAddress_Street/GeocodeServer/geocodeAddresses",
        # Dam prefix allows only FeatureServer/MapServer layers beneath the DWR org.
        "https://services.arcgis.com/aa38u6OgfNoCkTJ6/arcgis/rest/services/Other/FeatureServer/0/applyEdits",
        "https://services.arcgis.com/SomeOtherOrg/arcgis/rest/services/X/FeatureServer/0",
        "https://www.arcgis.com/sharing/rest/content/items/0000000000000000000000000000abcd",
    ],
)
def test_disallowed_urls_are_rejected(url):
    with pytest.raises(NotAllowedError):
        check_allowed(url)


@pytest.mark.parametrize(
    "url",
    [
        ZONING,
        ZONING + "/query",
        ZONING + "/",
        catalog.GEOCODER.url,
        catalog.GEOCODER.url + "/findAddressCandidates",
        catalog.GEOCODER.url + "/reverseGeocode",
        "https://services.arcgis.com/aa38u6OgfNoCkTJ6/arcgis/rest/services/Approved_InundationBoundaries_As_of_Oct01_2026/FeatureServer/100/query",
        f"https://www.arcgis.com/sharing/rest/content/items/{DAM.item_id}",
    ],
)
def test_allowed_urls_pass(url):
    assert check_allowed(url) == url.rstrip("/")


async def test_disallowed_url_sends_no_request():
    fake = FakeTime()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route()
        async with make_client(fake) as client:
            with pytest.raises(NotAllowedError):
                await client.request_json("https://evil.example.com/arcgis/rest/services/x")
        assert not route.called


# -- requests ----------------------------------------------------------------------------------


@respx.mock
async def test_sends_user_agent_and_f_json():
    route = respx.get(FHSZ).mock(return_value=httpx.Response(200, json={"name": "FHSZ"}))
    async with make_client(FakeTime()) as client:
        body = await client.request_json(FHSZ)
    assert body == {"name": "FHSZ"}
    request = route.calls.last.request
    assert request.headers["User-Agent"].startswith("GlendaleGisMcp/")
    assert "ryan@hacker.fund" in request.headers["User-Agent"]
    assert params_of(request)["f"] == "json"


@respx.mock
async def test_long_requests_use_post():
    route = respx.post(FHSZ + "/query").mock(return_value=httpx.Response(200, json={"count": 1}))
    ring = [[-118.3 + i * 1e-4, 34.1] for i in range(300)]
    async with make_client(FakeTime()) as client:
        n = await client.count(
            FHSZ, geometry={"rings": [ring]}, geometry_type="esriGeometryPolygon"
        )
    assert n == 1
    params = params_of(route.calls.last.request)
    assert params["returnCountOnly"] == "true"
    assert params["inSR"] == "4326"
    assert params["geometryType"] == "esriGeometryPolygon"


@respx.mock
async def test_redirects_are_not_followed():
    respx.get(FHSZ).mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example.com/"})
    )
    async with make_client(FakeTime()) as client:
        with pytest.raises(ArcGISError, match="redirect"):
            await client.request_json(FHSZ)


# -- errors and retries ------------------------------------------------------------------------


@respx.mock
async def test_retries_503_then_succeeds_with_backoff():
    route = respx.get(FHSZ).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    fake = FakeTime()
    async with make_client(fake) as client:
        assert await client.request_json(FHSZ) == {"ok": True}
    assert route.call_count == 3
    assert fake.sleeps == [1.0, 2.0]


@respx.mock
async def test_retry_after_header_is_honored():
    respx.get(FHSZ).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    fake = FakeTime()
    async with make_client(fake) as client:
        await client.request_json(FHSZ)
    assert fake.sleeps == [7.0]


@respx.mock
async def test_gives_up_after_max_retries():
    route = respx.get(FHSZ).mock(return_value=httpx.Response(503))
    fake = FakeTime()
    async with make_client(fake, max_retries=2) as client:
        with pytest.raises(ArcGISError) as exc:
            await client.request_json(FHSZ)
    assert exc.value.code == 503
    assert route.call_count == 3


@respx.mock
async def test_timeouts_are_retried():
    respx.get(FHSZ).mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json={"ok": True})]
    )
    async with make_client(FakeTime()) as client:
        assert await client.request_json(FHSZ) == {"ok": True}


@respx.mock
async def test_arcgis_error_body_with_http_200_raises_without_retry():
    route = respx.get(FHSZ).mock(
        return_value=httpx.Response(
            200,
            json={"error": {"code": 400, "message": "Invalid query", "details": ["bad where"]}},
        )
    )
    async with make_client(FakeTime()) as client:
        with pytest.raises(ArcGISError) as exc:
            await client.request_json(FHSZ)
    assert exc.value.code == 400
    assert exc.value.details == ("bad where",)
    assert route.call_count == 1


@respx.mock
async def test_arcgis_quota_error_body_is_retried():
    route = respx.get(FHSZ).mock(
        side_effect=[
            httpx.Response(200, json={"error": {"code": 429, "message": "quota exceeded"}}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    async with make_client(FakeTime()) as client:
        assert await client.request_json(FHSZ) == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_html_error_page_raises():
    respx.get(FHSZ).mock(return_value=httpx.Response(404, text="<html>404</html>"))
    async with make_client(FakeTime()) as client:
        with pytest.raises(ArcGISError) as exc:
            await client.request_json(FHSZ)
    assert exc.value.code == 404


# -- throttling --------------------------------------------------------------------------------


@respx.mock
async def test_requests_to_same_host_are_spaced_out():
    respx.get(FHSZ).mock(return_value=httpx.Response(200, json={}))
    fake = FakeTime()
    async with make_client(fake, min_request_interval_s=0.25) as client:
        for _ in range(3):
            await client.request_json(FHSZ)
    assert fake.sleeps == [0.25, 0.25]


@respx.mock
async def test_different_hosts_are_throttled_independently():
    respx.get(FHSZ).mock(return_value=httpx.Response(200, json={}))
    respx.get(ZONING).mock(return_value=httpx.Response(200, json={}))
    fake = FakeTime()
    async with make_client(fake, min_request_interval_s=0.25) as client:
        await client.request_json(FHSZ)
        await client.request_json(ZONING)
    assert fake.sleeps == []


# -- paging ------------------------------------------------------------------------------------


def feature(oid):
    return {"attributes": {"OBJECTID": oid}, "geometry": {"x": 0, "y": 0}}


PAGING_META = {
    "maxRecordCount": 2,
    "objectIdField": "OBJECTID",
    "advancedQueryCapabilities": {"supportsPagination": True},
}


@respx.mock
async def test_query_pages_until_transfer_limit_clears():
    def respond(request):
        offset = int(params_of(request)["resultOffset"])
        pages = {
            0: {"features": [feature(1), feature(2)], "exceededTransferLimit": True},
            2: {"features": [feature(3), feature(4)], "exceededTransferLimit": True},
            4: {"features": [feature(5)]},
        }
        return httpx.Response(200, json=pages[offset])

    route = respx.get(ZONING + "/query").mock(side_effect=respond)
    async with make_client(FakeTime()) as client:
        features = await client.query(ZONING, metadata=PAGING_META)
    assert [f["attributes"]["OBJECTID"] for f in features] == [1, 2, 3, 4, 5]
    assert route.call_count == 3
    params = params_of(route.calls[0].request)
    assert params["resultRecordCount"] == "2"
    assert params["orderByFields"] == "OBJECTID"
    assert params["outSR"] == "4326"
    assert params["returnGeometry"] == "true"


@respx.mock
async def test_query_stops_on_empty_page_even_if_flag_is_set():
    route = respx.get(ZONING + "/query").mock(
        side_effect=[
            httpx.Response(
                200, json={"features": [feature(1), feature(2)], "exceededTransferLimit": True}
            ),
            httpx.Response(200, json={"features": [], "exceededTransferLimit": True}),
        ]
    )
    async with make_client(FakeTime()) as client:
        features = await client.query(ZONING, metadata=PAGING_META)
    assert len(features) == 2
    assert route.call_count == 2


@respx.mock
async def test_query_respects_max_features():
    route = respx.get(ZONING + "/query").mock(
        return_value=httpx.Response(
            200, json={"features": [feature(1)], "exceededTransferLimit": True}
        )
    )
    async with make_client(FakeTime()) as client:
        features = await client.query(ZONING, metadata=PAGING_META, max_features=1)
    assert len(features) == 1
    assert route.call_count == 1
    assert params_of(route.calls[0].request)["resultRecordCount"] == "1"


@respx.mock
async def test_query_falls_back_to_object_ids_without_pagination():
    meta = {"maxRecordCount": 2, "fields": [{"name": "FID", "type": "esriFieldTypeOID"}]}

    def respond(request):
        params = params_of(request)
        if params.get("returnIdsOnly") == "true":
            assert "outFields" not in params
            return httpx.Response(200, json={"objectIdFieldName": "FID", "objectIds": [5, 3, 4]})
        ids = [int(i) for i in params["objectIds"].split(",")]
        assert "where" not in params
        return httpx.Response(200, json={"features": [{"attributes": {"FID": i}} for i in ids]})

    route = respx.get(ZONING + "/query").mock(side_effect=respond)
    async with make_client(FakeTime()) as client:
        features = await client.query(ZONING, where="Type=400", metadata=meta)
    assert [f["attributes"]["FID"] for f in features] == [3, 4, 5]
    assert route.call_count == 3


@respx.mock
async def test_spatial_query_pages_by_object_id_even_when_offsets_are_supported():
    # Offset pages with a spatial filter can overlap on ArcGIS Server (seen on Glendale streets).
    envelope = {"xmin": -118.3, "ymin": 34.1, "xmax": -118.2, "ymax": 34.3}

    def respond(request):
        params = params_of(request)
        assert "resultOffset" not in params
        if params.get("returnIdsOnly") == "true":
            assert params["geometryType"] == "esriGeometryEnvelope"
            assert params["inSR"] == "4326"
            return httpx.Response(200, json={"objectIds": [3, 1, 2]})
        assert "geometry" not in params  # the ID chunk replaces the spatial filter
        ids = [int(i) for i in params["objectIds"].split(",")]
        return httpx.Response(200, json={"features": [feature(i) for i in ids]})

    route = respx.get(ZONING + "/query").mock(side_effect=respond)
    async with make_client(FakeTime()) as client:
        features = await client.query(
            ZONING,
            geometry=envelope,
            geometry_type="esriGeometryEnvelope",
            metadata=PAGING_META,
        )
    assert [f["attributes"]["OBJECTID"] for f in features] == [1, 2, 3]
    assert route.call_count == 3  # IDs, then chunks of maxRecordCount (2)


@respx.mock
async def test_query_fetches_metadata_when_not_given():
    respx.get(ZONING).mock(return_value=httpx.Response(200, json=PAGING_META))
    respx.get(ZONING + "/query").mock(
        return_value=httpx.Response(200, json={"features": [feature(1)]})
    )
    async with make_client(FakeTime()) as client:
        assert len(await client.query(ZONING)) == 1


# -- item lookup -------------------------------------------------------------------------------


@respx.mock
async def test_resolve_item_url():
    item_url = f"https://www.arcgis.com/sharing/rest/content/items/{DAM.item_id}"
    respx.get(item_url).mock(
        return_value=httpx.Response(200, json={"url": DAM.layer_url.rsplit("/", 1)[0] + "/"})
    )
    async with make_client(FakeTime()) as client:
        url = await client.resolve_item_url(DAM.item_id)
    assert url == DAM.layer_url.rsplit("/", 1)[0]
