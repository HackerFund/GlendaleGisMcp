"""Geometry helpers: local meters around Glendale, and Esri JSON to shapely.

Distances and buffers use an equirectangular projection centered on Glendale
(``x = R·Δlon·cos(lat0)``, ``y = R·Δlat``), so no ``pyproj`` is needed. Error is well under 1%
across the city and its hazard buffer. The projection is an affine transform of lon/lat, so
intersection and containment give the same answer in either space; only distances and buffers
need to go through it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import shapely
from shapely.geometry import (
    LinearRing,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

# Center of the city boundary's extent.
ORIGIN_LON = -118.245
ORIGIN_LAT = 34.193
EARTH_RADIUS_M = 6_371_008.8  # mean radius

_M_PER_DEG_Y = math.radians(1) * EARTH_RADIUS_M
_M_PER_DEG_X = _M_PER_DEG_Y * math.cos(math.radians(ORIGIN_LAT))

COORD_DECIMALS = 6  # about 0.1 m


class GeometryError(ValueError):
    """An Esri geometry could not be converted."""


# --------------------------------------------------------------------------------------------
# Local meters
# --------------------------------------------------------------------------------------------


def to_local(geom: BaseGeometry) -> BaseGeometry:
    """Lon/lat (EPSG:4326) to local meters."""
    return shapely.transform(
        geom,
        lambda c: (c - (ORIGIN_LON, ORIGIN_LAT)) * (_M_PER_DEG_X, _M_PER_DEG_Y),
    )


def to_lonlat(geom: BaseGeometry) -> BaseGeometry:
    """Local meters back to lon/lat."""
    return shapely.transform(
        geom,
        lambda c: c / (_M_PER_DEG_X, _M_PER_DEG_Y) + (ORIGIN_LON, ORIGIN_LAT),
    )


def buffer_m(geom: BaseGeometry, meters: float) -> BaseGeometry:
    """Buffer a lon/lat geometry by a distance in meters."""
    if meters == 0:
        return geom
    return to_lonlat(to_local(geom).buffer(meters))


def distance_m(a: BaseGeometry, b: BaseGeometry) -> float:
    """Straight-line distance in meters between two lon/lat geometries (0 if they touch)."""
    return float(to_local(a).distance(to_local(b)))


def round_coords(geom: BaseGeometry, decimals: int = COORD_DECIMALS) -> BaseGeometry:
    """Snap to a ``10**-decimals`` grid, keeping the geometry valid, with clean decimal values."""
    snapped = shapely.set_precision(geom, 10.0**-decimals)
    return shapely.transform(snapped, lambda c: c.round(decimals))


# --------------------------------------------------------------------------------------------
# Esri JSON
# --------------------------------------------------------------------------------------------


def from_esri(geometry: Mapping[str, Any] | None) -> BaseGeometry | None:
    """Convert an Esri JSON geometry (already in lon/lat) to shapely; ``None`` if absent.

    Polygons follow Esri's ring convention: clockwise rings are shells and counterclockwise
    rings are holes. The result is made valid, since Esri and GEOS validity rules differ.
    Single-point multipoints (e.g. Glendale fire stations) become points.
    """
    if not geometry:
        return None
    if "curveRings" in geometry or "curvePaths" in geometry:
        raise GeometryError("True curves are not supported; query with returnTrueCurves=false")
    if "rings" in geometry:
        return _polygon_from_rings(geometry["rings"])
    if "paths" in geometry:
        paths = [p for p in geometry["paths"] if len(p) >= 2]
        if not paths:
            return None
        if len(paths) == 1:
            return LineString(_xy(paths[0]))
        return MultiLineString([_xy(p) for p in paths])
    if "points" in geometry:
        points = [_xy([p])[0] for p in geometry["points"]]
        if not points:
            return None
        return Point(points[0]) if len(points) == 1 else MultiPoint(points)
    if "x" in geometry and "y" in geometry:
        if geometry["x"] is None or geometry["y"] is None:
            return None  # Esri's empty point
        return Point(float(geometry["x"]), float(geometry["y"]))
    raise GeometryError(f"Unrecognized Esri geometry with keys {sorted(geometry)}")


def _xy(coords: Sequence[Sequence[float]]) -> list[tuple[float, float]]:
    # Drop Z and M values if present.
    return [(float(c[0]), float(c[1])) for c in coords]


def _polygon_from_rings(rings: Sequence[Sequence[Sequence[float]]]) -> BaseGeometry | None:
    closed = [LinearRing(_xy(r)) for r in rings if len(r) >= 4]
    if not closed:
        return None
    # Esri shells are clockwise; counterclockwise rings are holes.
    ccw = shapely.is_ccw(closed)
    shell_rings = [r for r, is_ccw in zip(closed, ccw, strict=True) if not is_ccw]
    hole_rings = [r for r, is_ccw in zip(closed, ccw, strict=True) if is_ccw]
    if not shell_rings:  # malformed: only counterclockwise rings; treat them as shells
        shell_rings, hole_rings = hole_rings, []

    shells = shapely.polygons(shell_rings)
    holes_of: dict[int, list[LinearRing]] = {}
    if hole_rings:
        # Give each hole to the smallest shell that covers the whole ring (the innermost one),
        # using a spatial index: raster-derived layers have thousands of rings per feature.
        # Testing a single interior point is not enough, since it can land on an island
        # inside the hole. Holes that no shell covers (slightly malformed rings) fall back to
        # a point test.
        tree = shapely.STRtree(shells)
        areas = shapely.area(shells)
        best: dict[int, int] = {}

        def assign(hole_idx: Sequence[int], shell_idx: Sequence[int]) -> None:
            for h, s in zip(hole_idx, shell_idx, strict=True):
                if h not in best or areas[s] < areas[best[h]]:
                    best[h] = s

        assign(*(a.tolist() for a in tree.query(hole_rings, predicate="covered_by")))
        orphans = [h for h in range(len(hole_rings)) if h not in best]
        if orphans:
            points = shapely.point_on_surface(shapely.polygons([hole_rings[h] for h in orphans]))
            found, shell_idx = tree.query(points, predicate="within")
            assign([orphans[i] for i in found.tolist()], shell_idx.tolist())
        for h, s in best.items():
            holes_of.setdefault(s, []).append(hole_rings[h])
        # Holes with no shell are dropped: Esri would not draw them either.

    parts = shapely.make_valid(
        [Polygon(shell.exterior, holes_of.get(i, [])) for i, shell in enumerate(shells)]
    )
    polygons = [p for part in parts for p in _polygon_parts(part)]
    if not polygons:
        return None
    if len(polygons) == 1:
        return polygons[0]
    multi = MultiPolygon(polygons)
    # Merge shells only if they overlap or share edges (common in raster-derived layers).
    return multi if multi.is_valid else shapely.union_all(polygons)  # valid by construction


def _polygon_parts(geom: BaseGeometry) -> list[Polygon]:
    if isinstance(geom, Polygon):
        return [] if geom.is_empty else [geom]
    if hasattr(geom, "geoms"):
        return [p for g in geom.geoms for p in _polygon_parts(g)]
    return []  # points or lines left over from make_valid
