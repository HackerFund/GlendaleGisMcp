import pytest
from shapely.geometry import Point

import fixture_snapshot as fx
from glendale_gis.core import geo, hazards
from glendale_gis.core.snapshot import Snapshot


@pytest.fixture(scope="module")
def snap(tmp_path_factory):
    return Snapshot.load(fx.write(tmp_path_factory.mktemp("snap")))


def at(snap, lat, lon):
    return hazards.locate(snap, lat, lon)


def meters(a, b):
    """Distance in meters between two (lon, lat) pairs."""
    return geo.distance_m(Point(a), Point(b))


# -- wildfire (classed, with an unzoned class) -----------------------------------------------


def test_wildfire_in_zone(snap):
    r = hazards.wildfire_zone(snap, at(snap, 34.16, -118.26))
    assert r.status == "in_zone"
    assert [m.ref.object_id for m in r.matches] == [1]
    assert r.matches[0].attributes["FHSZ_Description"] == "Very High"
    assert r.nearest_by_class["Very High"].distance_m == 0
    expected = meters((-118.26, 34.16), (-118.25, 34.16))
    assert r.nearest_by_class["NonWildland"].distance_m == pytest.approx(expected, abs=1)
    assert r.nearest is None
    assert r.location.in_city
    assert r.class_field == "FHSZ_Description"


def test_wildfire_nonwildland_is_not_in_a_zone(snap):
    r = hazards.wildfire_zone(snap, at(snap, 34.16, -118.24))
    assert r.status == "not_in_zone"
    # The unzoned polygon is still reported, and a note says what it means.
    assert [m.attributes["FHSZ_Description"] for m in r.matches] == ["NonWildland"]
    assert any(
        "NonWildland" in n and "does not mean there is no wildfire risk" in n for n in r.notes
    )
    assert r.nearest_by_class["Very High"].distance_m > 0


# -- flood -----------------------------------------------------------------------------------


def test_flood_zone_d_gets_a_note(snap):
    r = hazards.flood_zone(snap, at(snap, 34.172, -118.238))
    assert r.status == "in_zone"
    assert [m.attributes["FLD_ZONE"] for m in r.matches] == ["D"]
    assert any("not been studied" in n for n in r.notes)


def test_flood_nearest_per_class_and_global_ids(snap):
    r = hazards.flood_zone(snap, at(snap, 34.16, -118.26))
    assert set(r.nearest_by_class) == {"X", "AE", "D"}
    assert r.nearest_by_class["X"].distance_m == 0
    assert r.nearest_by_class["AE"].ref.global_id == "{00000011}"
    assert r.notes == []


def test_point_on_a_zone_edge_counts_as_inside(snap):
    r = hazards.flood_zone(snap, at(snap, 34.1525, -118.245))
    assert "AE" in [m.attributes["FLD_ZONE"] for m in r.matches]


# -- unclassed layers ------------------------------------------------------------------------


def test_seismic_zones(snap):
    r = hazards.seismic_zones(snap, at(snap, 34.16, -118.255))
    assert r.fault.status == "not_in_zone"
    expected = meters((-118.255, 34.16), (-118.255, 34.175))
    assert r.fault.nearest.distance_m == pytest.approx(expected, abs=1)
    assert r.fault.nearest.exact
    assert r.fault.nearest_by_class is None
    # An empty layer is "not in zone" with no nearest feature, not unavailable.
    assert r.liquefaction.status == "not_in_zone"
    assert r.liquefaction.nearest is None
    assert any("hazard signal" in n for n in r.fault.notes)
    # The landslide layer is missing from the fixture snapshot.
    assert r.landslide.status == "unavailable"
    assert "not in the snapshot" in r.landslide.reason
    assert r.landslide.meta.cached is False


def test_dam_returns_every_scenario_in_id_order(snap):
    r = hazards.dam_inundation(snap, at(snap, 34.145, -118.245))
    assert r.status == "in_zone"
    assert [m.attributes["Scenario"] for m in r.matches] == ["S1", "S2"]
    assert r.nearest.ref.object_id == 1
    assert any("consequences" in n for n in r.notes)


def test_empty_debris_flow_layer(snap):
    r = hazards.debris_flow(snap, at(snap, 34.16, -118.25))
    assert r.status == "not_in_zone"
    assert r.nearest_by_class == {}
    assert any("does not mean there is no debris-flow risk" in n for n in r.notes)


# -- coverage ---------------------------------------------------------------------------------


def test_outside_coverage_is_unavailable_not_not_in_zone(snap):
    r = hazards.flood_zone(snap, at(snap, 34.16, -118.10))
    assert r.status == "unavailable"
    assert r.matches == []
    assert "outside the area this layer covers (Glendale plus 2000 m)" in r.reason
    assert r.meta is not None
    assert not r.location.in_city


def test_nearest_beyond_the_coverage_edge_is_not_exact(snap):
    # 1.5 km east of the city: the fault zone is ~2 km away, but the coverage edge is only
    # ~0.5 km away, so a closer zone outside the snapshot can't be ruled out.
    r = hazards.seismic_zones(snap, at(snap, 34.16, -118.2137))
    assert r.fault.status == "not_in_zone"
    assert r.fault.nearest.exact is False


# -- combined and serialized ------------------------------------------------------------------


def test_hazards_at_location(snap):
    r = hazards.hazards_at_location(snap, at(snap, 34.145, -118.245))
    assert r.location.lat == 34.145
    assert r.dam_inundation.status == "in_zone"
    assert r.wildfire.status == "not_in_zone"
    assert r.landslide.status == "unavailable"
    # Nested results don't repeat the location.
    assert r.flood.location is None


def test_meta_serializes_as_underscore_meta(snap):
    data = hazards.flood_zone(snap, at(snap, 34.16, -118.26)).model_dump(by_alias=True)
    assert data["_meta"] == {
        "source": "FEMA",
        "url": snap.layer("fema_flood_zones").layer_url,
        "cached": True,
        "as_of": "2026-09-18T19:45:05+00:00",
        "stale": False,
        "source_last_edit": "2025-11-19T17:40:00+00:00",
    }
    assert data["disclaimer"].startswith("Regulatory hazard zone map")
