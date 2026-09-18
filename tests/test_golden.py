"""Golden checks against the real snapshot at six reference points.

Skipped unless a snapshot has been built (``python scripts/build_snapshot.py``) at ``snapshot/``
or ``GLENDALE_GIS_SNAPSHOT_PATH``. Expected values were confirmed with live point queries against
each source on September 18, 2026; a rebuild after the sources change may legitimately change
them.
"""

import os
from pathlib import Path

import pytest

from glendale_gis.core import hazards, resources
from glendale_gis.core.snapshot import Snapshot

SNAPSHOT = Path(
    os.environ.get("GLENDALE_GIS_SNAPSHOT_PATH") or Path(__file__).parent.parent / "snapshot"
)

pytestmark = pytest.mark.skipif(
    not (SNAPSHOT / "manifest.json").exists(), reason="no snapshot built"
)


@pytest.fixture(scope="module")
def snap():
    return Snapshot.load(SNAPSHOT)


def classes(result):
    return sorted({m.attributes[result.class_field] for m in result.matches})


# name, lat, lon, in_city, wildfire class, flood zone
POINTS = [
    ("Northern foothills", 34.2144, -118.2540, True, "Very High", "D"),
    ("Unincorporated La Crescenta", 34.2330, -118.2350, False, "Very High", "X"),
    ("Verdugo hills", 34.1880, -118.2560, True, "Very High", "D"),
    ("Chevy Chase Canyon", 34.1560, -118.2150, True, "Very High", "D"),
    ("Downtown (Brand Blvd)", 34.1460, -118.2550, True, "NonWildland", "X"),
    ("LA River channel", 34.11583, -118.26711, False, None, "A"),
]


def test_snapshot_loads_every_layer(snap):
    assert snap.errors == {}


@pytest.mark.parametrize("name, lat, lon, in_city, wildfire, flood", POINTS)
def test_reference_points(snap, name, lat, lon, in_city, wildfire, flood):
    loc = hazards.locate(snap, lat, lon)
    assert loc.in_city is in_city
    r = hazards.hazards_at_location(snap, loc)
    assert classes(r.flood) == [flood]
    if wildfire:
        assert classes(r.wildfire) == [wildfire]
        assert r.wildfire.status == ("not_in_zone" if wildfire == "NonWildland" else "in_zone")
    for result in (r.wildfire, r.flood, r.fault, r.liquefaction, r.landslide, r.dam_inundation):
        assert result.status != "unavailable", (name, result.dataset, result.reason)
    assert r.debris_flow.status == "not_in_zone"


def test_downtown_details(snap):
    r = hazards.hazards_at_location(snap, hazards.locate(snap, 34.1460, -118.2550))
    assert r.fault.status == "not_in_zone"
    assert r.fault.nearest.distance_m == pytest.approx(2996, abs=5)
    assert r.dam_inundation.nearest.distance_m == pytest.approx(906, abs=5)
    assert r.wildfire.nearest_by_class["Very High"].distance_m == pytest.approx(1851, abs=5)


def test_northern_foothills_are_in_a_landslide_zone(snap):
    r = hazards.seismic_zones(snap, hazards.locate(snap, 34.2144, -118.2540))
    assert r.landslide.status == "in_zone"
    assert [m.ref.object_id for m in r.landslide.matches] == [23673]


def test_chevy_chase_is_near_a_landslide_zone(snap):
    r = hazards.seismic_zones(snap, hazards.locate(snap, 34.1560, -118.2150))
    assert r.landslide.nearest.distance_m == pytest.approx(14, abs=2)
    assert r.landslide.nearest.ref.global_id


def test_nearest_fire_station_downtown(snap):
    loc = hazards.locate(snap, 34.1460, -118.2550)
    r = resources.nearest_resources(snap, loc, ["fire_stations"], limit=1)
    station = r.groups[0].results[0]
    assert station.name == "Fire Station 21"
    assert station.distance_m == pytest.approx(649, abs=5)
