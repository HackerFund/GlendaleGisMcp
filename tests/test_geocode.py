import dataclasses

import httpx
import pytest
import respx

import fixture_snapshot as fx
from glendale_gis.core.arcgis import ArcGISClient
from glendale_gis.core.cache import Cache
from glendale_gis.core.catalog import GEOCODER
from glendale_gis.core.config import Settings
from glendale_gis.core.geocode import Geocoder
from glendale_gis.core.models import ActionableError, Location
from glendale_gis.core.snapshot import Snapshot

URL = f"{GEOCODER.url}/findAddressCandidates"
INSIDE = (-118.25, 34.16)  # middle of the fixture city
OUTSIDE = (-118.20, 34.16)  # ~2.8 km east of it


@pytest.fixture(scope="module")
def snap(tmp_path_factory):
    return Snapshot.load(fx.write(tmp_path_factory.mktemp("snap")))


@pytest.fixture
def settings():
    return dataclasses.replace(Settings.from_env({}), min_request_interval_s=0.0, max_retries=0)


@pytest.fixture
async def geocoder(snap, settings):
    async with ArcGISClient(settings) as client:
        yield Geocoder(client, Cache(None), snap, settings)


def cand(address, score, match_type, lon, lat):
    return {
        "address": address,
        "location": {"x": lon, "y": lat},
        "score": score,
        # X/Y stay in state-plane feet in the real service; they must be ignored.
        "attributes": {
            "Match_addr": address,
            "Addr_type": match_type,
            "Score": score,
            "X": 6486557.15,
            "Y": 1875831.32,
        },
    }


def respond(*candidates):
    return respx.get(URL).mock(
        return_value=httpx.Response(200, json={"candidates": list(candidates)})
    )


@respx.mock
async def test_confident_match(geocoder):
    route = respond(cand("613 E BROADWAY, GLENDALE, CA, 91206", 100, "PointAddress", *INSIDE))
    r = await geocoder.geocode("  613   E Broadway ")
    assert r.status == "matched"
    assert r.location.matched_address == "613 E BROADWAY, GLENDALE, CA, 91206"
    assert (r.location.lon, r.location.lat) == INSIDE  # from location, not X/Y
    assert r.location.source == "geocoder"
    assert r.location.score == 100
    assert r.location.in_city
    assert r.meta.source == "City of Glendale"
    assert r.meta.cached is False
    params = route.calls[0].request.url.params
    assert params["SingleLine"] == "613 E Broadway"
    assert params["outSR"] == "4326"


@respx.mock
async def test_point_and_street_duplicates_collapse_to_the_point(geocoder):
    respond(
        cand("613 E BROADWAY, 91206", 98.6, "StreetAddress", INSIDE[0] + 0.0004, INSIDE[1]),
        cand("613 E BROADWAY, 91205", 100, "PointAddress", *INSIDE),
    )
    r = await geocoder.geocode("613 E Broadway")
    assert r.status == "matched"
    assert [c.match_type for c in r.candidates] == ["PointAddress"]


@respx.mock
async def test_tie_is_ambiguous(geocoder):
    respond(
        cand("613 E BROADWAY", 91.18, "PointAddress", *INSIDE),
        cand("613 W BROADWAY", 91.18, "PointAddress", INSIDE[0] - 0.015, INSIDE[1]),
    )
    r = await geocoder.geocode("613 Broadway")
    assert r.status == "ambiguous"
    assert r.location is None
    assert len(r.candidates) == 2
    with pytest.raises(ActionableError, match="more than one place") as exc:
        await geocoder.resolve(Location(address="613 Broadway"))
    assert len(exc.value.candidates) == 2


@respx.mock
async def test_clear_point_address_lead_wins(geocoder):
    respond(
        cand("613 E BROADWAY", 95.36, "PointAddress", *INSIDE),
        cand("613 W BROADWAY", 81.98, "PointAddress", INSIDE[0] - 0.015, INSIDE[1]),
    )
    assert (await geocoder.geocode("613 E Brodway")).status == "matched"


@respx.mock
async def test_street_only_match_is_never_confident(geocoder):
    respond(cand("E BROADWAY, GLENDALE", 99, "StreetName", *INSIDE))
    r = await geocoder.geocode("99999 E Broadway")
    assert r.status == "ambiguous"
    assert any("Only the street matched" in n for n in r.notes)
    with pytest.raises(ActionableError, match="Only the street matched"):
        await geocoder.resolve(Location(address="99999 E Broadway"))


@respx.mock
async def test_intersection_can_be_confident(geocoder):
    respond(cand("S BRAND BLVD & BROADWAY", 91.04, "StreetInt", *INSIDE))
    assert (await geocoder.geocode("Brand Blvd & Broadway")).status == "matched"


@respx.mock
async def test_outside_the_city_is_an_error_with_candidates(geocoder):
    respond(cand("275 E OLIVE AVE, BURBANK", 96.19, "StreetAddress", *OUTSIDE))
    with pytest.raises(ActionableError, match="outside the Glendale city limits") as exc:
        await geocoder.geocode("275 E Olive Ave, Burbank")
    assert exc.value.candidates[0]["in_city"] is False
    assert any("lat/lon" in s for s in exc.value.suggestions)


@respx.mock
async def test_outside_candidates_are_dropped_with_a_note(geocoder):
    respond(
        cand("3300 COMMUNITY AVE, GLENDALE", 97.18, "StreetAddress", *INSIDE),
        cand("COMMUNITY AVE, LA CRESCENTA", 94.81, "StreetName", *OUTSIDE),
    )
    r = await geocoder.geocode("3300 Community Ave, La Crescenta")
    assert r.status == "matched"
    assert len(r.candidates) == 1
    assert any("1 match(es) outside" in n for n in r.notes)


@respx.mock
async def test_address_on_the_city_line_counts(geocoder):
    on_edge = (fx.CITY[2] + 0.0001, 34.16)  # ~9 m outside the fixture boundary
    respond(cand("1 EDGE ST", 100, "PointAddress", *on_edge))
    r = await geocoder.geocode("1 Edge St")
    assert r.status == "matched"
    assert r.location.in_city is False  # reported honestly


@pytest.mark.parametrize(
    "text", ["1100 N Brand Blvd Apt 4", "1100 N Brand Blvd #4", "12 Main St Unit B"]
)
@respx.mock
async def test_unit_numbers_get_a_note(geocoder, text):
    respond(cand("1100 N BRAND BLVD", 100, "PointAddress", *INSIDE))
    r = await geocoder.geocode(text)
    assert any("unit or apartment number was ignored" in n for n in r.notes)


@respx.mock
async def test_no_match(geocoder):
    respond()
    with pytest.raises(ActionableError, match="No address in Glendale matched") as exc:
        await geocoder.geocode("asdf qwerty")
    assert any("Place names" in s for s in exc.value.suggestions)


@respx.mock
async def test_results_are_cached(geocoder):
    route = respond(cand("613 E BROADWAY", 100, "PointAddress", *INSIDE))
    await geocoder.geocode("613 E Broadway")
    again = await geocoder.geocode("613 e  BROADWAY")  # same after normalizing
    assert again.meta.cached is True
    assert route.call_count == 1


@respx.mock
async def test_geocoder_down(geocoder):
    respx.get(URL).mock(return_value=httpx.Response(503))
    with pytest.raises(ActionableError, match="isn't responding") as exc:
        await geocoder.geocode("613 E Broadway")
    assert any("lat/lon" in s for s in exc.value.suggestions)


async def test_geocoder_down_serves_stale_cache(snap, settings):
    clock = [1_000_000.0]
    cache = Cache(None, clock=lambda: clock[0])
    async with ArcGISClient(settings) as client:
        geocoder = Geocoder(client, cache, snap, settings)
        with respx.mock:
            respond(cand("613 E BROADWAY", 100, "PointAddress", *INSIDE))
            await geocoder.geocode("613 E Broadway")
        clock[0] += settings.geocode_cache_ttl_s + 1
        with respx.mock:
            respx.get(URL).mock(return_value=httpx.Response(503))
            r = await geocoder.geocode("613 E Broadway")
    assert r.status == "matched"
    assert r.meta.stale is True


async def test_coordinates_skip_the_geocoder(geocoder):
    with respx.mock:  # any request would fail: no routes
        loc = await geocoder.resolve(Location(lat=34.16, lon=-118.25))
    assert loc.source == "coordinates"
    assert loc.in_city
