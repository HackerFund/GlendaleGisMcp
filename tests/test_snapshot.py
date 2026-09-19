import json

import pytest

import fixture_snapshot as fx
from glendale_gis.core.snapshot import (
    LayerUnavailable,
    Snapshot,
    SnapshotError,
    local_point,
)


@pytest.fixture
def snap(tmp_path):
    return Snapshot.load(fx.write(tmp_path / "snap"))


def test_loads_layers_and_reports_missing_ones(snap):
    assert snap.has_layer("calfire_fhsz_lra")
    assert len(snap.layer("fire_stations")) == 3
    assert snap.built_at == "2026-09-18T19:45:16+00:00"
    # Layers the fixture doesn't include are unavailable, with a reason.
    assert "hospitals" in snap.errors
    with pytest.raises(LayerUnavailable, match="not in the snapshot"):
        snap.layer("hospitals")


def test_unknown_dataset_is_a_key_error(snap):
    with pytest.raises(KeyError):
        snap.layer("nope")


def test_checksum_mismatch_makes_only_that_layer_unavailable(tmp_path):
    root = fx.write(tmp_path / "snap")
    path = root / "parks.geojson"
    path.write_text(path.read_text().replace("CENTRAL", "CENTRAX"))
    snap = Snapshot.load(root)
    with pytest.raises(LayerUnavailable, match="checksum"):
        snap.layer("parks")
    assert snap.has_layer("fire_stations")


def test_corrupt_layer_is_unavailable(tmp_path):
    root = fx.write(tmp_path / "snap")
    manifest = json.loads((root / "manifest.json").read_text())
    (root / "parks.geojson").write_text("{not json")
    manifest["layers"]["parks"]["sha256"] = None  # skip the checksum to reach the parser
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(LayerUnavailable, match="could not be loaded"):
        Snapshot.load(root).layer("parks")


def test_missing_manifest(tmp_path):
    with pytest.raises(SnapshotError, match=r"manifest\.json not found"):
        Snapshot.load(tmp_path)


def test_unsupported_version(tmp_path):
    root = fx.write(tmp_path / "snap")
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["version"] = 99
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(SnapshotError, match="Unsupported snapshot version 99"):
        Snapshot.load(root)


def test_city_boundary_is_required(tmp_path):
    layers = {k: v for k, v in fx.LAYERS.items() if k != "city_boundary"}
    with pytest.raises(SnapshotError, match="city boundary"):
        Snapshot.load(fx.write(tmp_path / "snap", layers))


def test_in_city(snap):
    assert snap.in_city(local_point(34.16, -118.25))
    assert not snap.in_city(local_point(34.16, -118.20))


def test_coverage_is_the_boundary_plus_the_buffer(snap):
    area = snap.coverage(2000)
    assert area.covers(local_point(34.16, -118.225))  # ~450 m east of the city
    assert not area.covers(local_point(34.16, -118.20))  # ~2.8 km east


def test_nearest_ties_go_to_the_lowest_object_id(snap):
    dam = snap.layer("dwr_dam_inundation")
    i, distance = dam.nearest(local_point(34.145, -118.245))
    assert distance == 0
    assert dam.ref(i).object_id == 1


def test_refs_carry_global_ids_when_the_layer_has_them(snap):
    flood = snap.layer("fema_flood_zones")
    ref = flood.ref(0)
    assert ref.global_id == "{00000010}"
    assert ref.layer_url == flood.layer_url
    assert snap.layer("fire_stations").ref(0).global_id is None
