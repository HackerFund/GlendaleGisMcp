import pytest
from pydantic import ValidationError

import fixture_snapshot as fx
from glendale_gis.core import hazards, resources
from glendale_gis.core.models import Location
from glendale_gis.core.snapshot import Snapshot


@pytest.fixture(scope="module")
def snap(tmp_path_factory):
    return Snapshot.load(fx.write(tmp_path_factory.mktemp("snap")))


def nearest(snap, lat, lon, **kwargs):
    return resources.nearest_resources(snap, hazards.locate(snap, lat, lon), **kwargs)


def group(result, kind):
    return next(g for g in result.groups if g.kind == kind)


def test_nearest_fire_stations_in_distance_order(snap):
    r = nearest(snap, 34.16, -118.2449, kinds=["fire_stations"], limit=2)
    stations = group(r, "fire_stations")
    assert stations.status == "ok"
    assert stations.total_in_dataset == 3
    assert [s.ref.object_id for s in stations.results] == [2, 3]
    assert stations.results[0].distance_m < stations.results[1].distance_m
    assert stations.meta.source == "City of Glendale"


def test_names_and_addresses_are_tidied_and_attributes_kept(snap):
    stations = group(nearest(snap, 34.16, -118.25, kinds=["fire_stations"]), "fire_stations")
    first = stations.results[0]
    assert first.name == "Fire Station 23"
    assert first.attributes["NAME"] == " Fire  Station 23 "  # unchanged
    assert first.attributes["sta_no"] == " 23"
    assert first.address is None
    assert first.distance_m == 0
    by_id = {s.ref.object_id: s for s in stations.results}
    assert by_id[1].address == "421 Oak Street"
    assert by_id[2].address is None


def test_distance_to_a_park_is_zero_inside_it(snap):
    parks = group(nearest(snap, 34.16, -118.25, kinds=["parks"]), "parks")
    assert parks.results[0].name == "CENTRAL PARK"
    assert parks.results[0].distance_m == 0


def test_all_kinds_by_default_with_missing_ones_unavailable(snap):
    r = nearest(snap, 34.16, -118.25)
    assert [g.kind for g in r.groups] == list(resources.RESOURCE_KINDS)
    hospitals = group(r, "hospitals")
    assert hospitals.status == "unavailable"
    assert "not in the snapshot" in hospitals.reason
    assert hospitals.meta.cached is False
    assert any("straight-line" in n for n in r.notes)


@pytest.mark.parametrize("limit, expected", [(0, 1), (-5, 1), (2, 2), (1000, 3)])
def test_limit_is_clamped(snap, limit, expected):
    r = nearest(snap, 34.16, -118.25, kinds=["fire_stations"], limit=limit)
    assert len(group(r, "fire_stations").results) == expected


def test_max_limit():
    assert resources.MAX_LIMIT == 25


def test_unknown_kind(snap):
    with pytest.raises(ValueError, match="Unknown resource kind.*zoning.*Choose from"):
        nearest(snap, 34.16, -118.25, kinds=["zoning"])


def test_empty_kinds(snap):
    with pytest.raises(ValueError, match="at least one kind"):
        nearest(snap, 34.16, -118.25, kinds=[])


def test_outside_city_adds_a_note(snap):
    r = nearest(snap, 34.16, -118.21, kinds=["fire_stations"])
    assert not r.location.in_city
    assert any("outside Glendale" in n for n in r.notes)


# -- Location input ---------------------------------------------------------------------------


def test_location_accepts_an_address_or_coordinates():
    assert Location(address="613 E Broadway").address == "613 E Broadway"
    assert Location(lat=34.16, lon=-118.25).lat == 34.16


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"lat": 34.16},
        {"address": "613 E Broadway", "lat": 34.16, "lon": -118.25},
        {"lat": 95, "lon": -118.25},
        {"lat": 34.16, "lon": -200},
        {"address": ""},
        {"address": "x", "extra": 1},
    ],
)
def test_location_rejects_bad_input(kwargs):
    with pytest.raises(ValidationError):
        Location(**kwargs)
