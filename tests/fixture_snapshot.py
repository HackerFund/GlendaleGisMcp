"""A tiny hand-made snapshot with known answers, written in the builder's format.

The "city" is a box of about 3.7 km x 4.4 km. Hazard layers are clipped to it plus 2 km and city
layers plus 100 m, as in a real build.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from shapely.geometry import box, mapping

from glendale_gis.core.catalog import DATASETS

CITY = (-118.27, 34.14, -118.23, 34.18)
HAZARD_M = 2000.0
CITY_M = 100.0


def poly(xmin, ymin, xmax, ymax):
    return mapping(box(xmin, ymin, xmax, ymax))


def point(lon, lat):
    return {"type": "Point", "coordinates": [lon, lat]}


def features(dataset_id, *rows):
    """rows: (object_id, geometry, properties)."""
    ds = DATASETS[dataset_id]
    out = []
    for oid, geometry, props in rows:
        out.append(
            {
                "type": "Feature",
                "id": oid,
                "geometry": geometry,
                "properties": {ds.id_field: oid, **props},
            }
        )
    return out


LAYERS = {
    "city_boundary": features("city_boundary", (1, poly(*CITY), {"CITYNAME": "Glendale"})),
    # West of -118.25 is Very High, east of it NonWildland.
    "calfire_fhsz_lra": features(
        "calfire_fhsz_lra",
        (1, poly(-118.30, 34.12, -118.25, 34.20), {"FHSZ": 3, "FHSZ_Description": "Very High"}),
        (2, poly(-118.25, 34.12, -118.20, 34.20), {"FHSZ": -3, "FHSZ_Description": "NonWildland"}),
    ),
    "fema_flood_zones": features(
        "fema_flood_zones",
        (10, poly(-118.30, 34.12, -118.25, 34.20), {"FLD_ZONE": "X", "SFHA_TF": "F"}),
        (11, poly(-118.245, 34.15, -118.240, 34.155), {"FLD_ZONE": "AE", "SFHA_TF": "T"}),
        (12, poly(-118.24, 34.17, -118.235, 34.175), {"FLD_ZONE": "D", "SFHA_TF": "F"}),
    ),
    "cgs_fault_zones": features(
        "cgs_fault_zones",
        (5, poly(-118.26, 34.175, -118.25, 34.185), {"QUAD_NAME": "Burbank"}),
    ),
    "cgs_liquefaction_zones": features("cgs_liquefaction_zones"),
    "dwr_dam_inundation": features(
        "dwr_dam_inundation",
        (2, poly(-118.25, 34.14, -118.24, 34.15), {"DamName": "Brand Park", "Scenario": "S2"}),
        (1, poly(-118.25, 34.14, -118.24, 34.15), {"DamName": "Brand Park", "Scenario": "S1"}),
    ),
    "usgs_debris_flow": features("usgs_debris_flow"),
    "fire_stations": features(
        "fire_stations",
        (1, point(-118.26, 34.16), {"NAME": "Fire Station 21", "ADDRESS": "421 Oak Street"}),
        (2, point(-118.24, 34.16), {"NAME": "Fire Station 22", "ADDRESS": None}),
        (3, point(-118.25, 34.16), {"NAME": " Fire  Station 23 ", "sta_no": " 23"}),
    ),
    "parks": features(
        "parks",
        (7, poly(-118.252, 34.158, -118.248, 34.162), {"NAME_ALF": "CENTRAL PARK"}),
    ),
}

# Clip buffer per layer; the boundary isn't clipped.
CLIP = {"city_boundary": None, **{k: CITY_M for k in ("fire_stations", "parks")}}


def write(root: Path, layers=None, *, global_ids=("fema_flood_zones",)) -> Path:
    """Write the fixture snapshot to ``root`` and return it."""
    layers = LAYERS if layers is None else layers
    root.mkdir(parents=True, exist_ok=True)
    entries = {}
    for layer_id, feats in layers.items():
        ds = DATASETS[layer_id]
        if layer_id in global_ids:
            for f in feats:
                f["properties"]["GlobalID"] = f"{{{f['id']:08d}}}"
        data = json.dumps({"type": "FeatureCollection", "features": feats}).encode()
        name = f"{layer_id}.geojson"
        (root / name).write_bytes(data)
        meters = CLIP.get(layer_id, HAZARD_M)
        entries[layer_id] = {
            "title": ds.title,
            "source_agency": ds.source_agency,
            "category": ds.category,
            "layer_url": ds.layer_url,
            "fetched_at": "2026-09-18T19:45:05+00:00",
            "source_last_edit": "2025-11-19T17:40:00+00:00" if ds.category == "hazard" else None,
            "feature_count": len(feats),
            "source_feature_count": len(feats),
            "geometry": ds.geometry,
            "id_field": ds.id_field,
            "global_id_field": "GlobalID" if layer_id in global_ids else None,
            "fields": [],
            "clip": None if meters is None else {"buffer": ds.buffer, "meters": meters},
            "file": name,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    manifest = {
        "version": 1,
        "built_at": "2026-09-18T19:45:16+00:00",
        "crs": "EPSG:4326",
        "coordinate_decimals": 6,
        "total_bytes": sum(e["bytes"] for e in entries.values()),
        "layers": entries,
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root
