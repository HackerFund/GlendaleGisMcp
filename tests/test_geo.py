import math

import pytest
from shapely.geometry import LineString, MultiPoint, MultiPolygon, Point, Polygon

from glendale_gis.core import geo

DOWNTOWN = Point(-118.2550, 34.1460)
LA_CRESCENTA = Point(-118.2350, 34.2330)


def haversine_m(a: Point, b: Point) -> float:
    lat1, lat2 = math.radians(a.y), math.radians(b.y)
    dlat, dlon = lat2 - lat1, math.radians(b.x - a.x)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * geo.EARTH_RADIUS_M * math.asin(math.sqrt(h))


# -- local meters ----------------------------------------------------------------------------


def test_round_trip_through_local_meters():
    back = geo.to_lonlat(geo.to_local(LA_CRESCENTA))
    assert back.x == pytest.approx(LA_CRESCENTA.x, abs=1e-9)
    assert back.y == pytest.approx(LA_CRESCENTA.y, abs=1e-9)


def test_origin_maps_to_zero():
    origin = geo.to_local(Point(geo.ORIGIN_LON, geo.ORIGIN_LAT))
    assert (origin.x, origin.y) == pytest.approx((0.0, 0.0))


@pytest.mark.parametrize(
    "a, b",
    [
        (DOWNTOWN, LA_CRESCENTA),  # ~10 km, mostly north-south
        (Point(-118.30, 34.15), Point(-118.19, 34.15)),  # ~10 km east-west
        (Point(-118.31, 34.12), Point(-118.18, 34.27)),  # corner to corner of the city extent
    ],
)
def test_distance_is_within_half_a_percent_of_great_circle(a, b):
    expected = haversine_m(a, b)
    assert geo.distance_m(a, b) == pytest.approx(expected, rel=0.005)


def test_distance_is_zero_inside_a_polygon():
    square = DOWNTOWN.buffer(0.01)
    assert geo.distance_m(DOWNTOWN, square) == 0.0


def test_buffer_is_in_meters():
    ring = geo.buffer_m(DOWNTOWN, 100).exterior
    distances = [geo.distance_m(DOWNTOWN, Point(c)) for c in ring.coords]
    assert min(distances) == pytest.approx(100, rel=0.01)
    assert max(distances) == pytest.approx(100, rel=0.01)


def test_zero_buffer_returns_the_geometry():
    assert geo.buffer_m(DOWNTOWN, 0) is DOWNTOWN


def test_round_coords_gives_clean_six_decimal_values():
    rounded = geo.round_coords(Point(-118.123456789, 34.987654321))
    assert (rounded.x, rounded.y) == (-118.123457, 34.987654)
    assert repr(rounded.x) == "-118.123457"


# -- Esri JSON -------------------------------------------------------------------------------


def cw(xmin, ymin, xmax, ymax):
    """A clockwise ring (an Esri shell)."""
    return [[xmin, ymin], [xmin, ymax], [xmax, ymax], [xmax, ymin], [xmin, ymin]]


def ccw(xmin, ymin, xmax, ymax):
    """A counterclockwise ring (an Esri hole)."""
    return list(reversed(cw(xmin, ymin, xmax, ymax)))


def test_polygon_with_hole():
    geom = geo.from_esri({"rings": [cw(0, 0, 10, 10), ccw(2, 2, 4, 4)]})
    assert isinstance(geom, Polygon)
    assert len(geom.interiors) == 1
    assert geom.area == pytest.approx(100 - 4)


def test_island_inside_a_hole_is_kept():
    rings = [cw(0, 0, 10, 10), ccw(2, 2, 8, 8), cw(4, 4, 6, 6)]
    geom = geo.from_esri({"rings": rings})
    assert isinstance(geom, MultiPolygon)
    assert geom.area == pytest.approx(100 - 36 + 4)
    assert geom.contains(Point(5, 5))
    assert not geom.contains(Point(3, 3))


def test_hole_goes_to_the_shell_that_contains_it():
    rings = [cw(0, 0, 10, 10), cw(20, 0, 30, 10), ccw(22, 2, 24, 4)]
    geom = geo.from_esri({"rings": rings})
    assert not geom.contains(Point(23, 3))
    assert geom.contains(Point(3, 3))


def test_shells_sharing_an_edge_are_merged_into_a_valid_polygon():
    geom = geo.from_esri({"rings": [cw(0, 0, 1, 1), cw(1, 0, 2, 1)]})
    assert geom.is_valid
    assert isinstance(geom, Polygon)
    assert geom.area == pytest.approx(2)


def test_counterclockwise_only_rings_are_treated_as_shells():
    geom = geo.from_esri({"rings": [ccw(0, 0, 1, 1)]})
    assert geom.area == pytest.approx(1)


def test_self_intersecting_ring_is_made_valid():
    bowtie = [[0, 0], [0, 1], [1, 0], [1, 1], [0, 0]]
    assert geo.from_esri({"rings": [bowtie]}).is_valid


def test_single_point_multipoint_becomes_a_point():
    assert geo.from_esri({"points": [[-118.25, 34.15]]}) == Point(-118.25, 34.15)
    assert isinstance(geo.from_esri({"points": [[0, 0], [1, 1]]}), MultiPoint)


def test_polyline_and_z_values():
    geom = geo.from_esri({"paths": [[[0, 0, 5], [1, 1, 5]]], "hasZ": True})
    assert geom == LineString([(0, 0), (1, 1)])
    assert not geom.has_z


@pytest.mark.parametrize(
    "geometry", [None, {}, {"x": None, "y": None}, {"rings": []}, {"paths": [[[0, 0]]]}]
)
def test_empty_geometries_return_none(geometry):
    assert geo.from_esri(geometry) is None


def test_true_curves_are_rejected():
    with pytest.raises(geo.GeometryError, match="curves"):
        geo.from_esri({"curveRings": []})


def test_unknown_geometry_is_rejected():
    with pytest.raises(geo.GeometryError):
        geo.from_esri({"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1})
