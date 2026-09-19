import dataclasses
import hashlib
import json
import os
import zipfile
from pathlib import Path

import httpx
import pytest
import respx

import build_snapshot as bs
import fixture_snapshot as fx
from glendale_gis import __main__ as cli
from glendale_gis.core import distribution as dist
from glendale_gis.core.config import Settings
from glendale_gis.core.snapshot import Snapshot

ASSET_HOST = "https://release-assets.githubusercontent.com/github-production-release-asset/1/abc"


def make_archive(tmp_path, name="snap.zip"):
    root = fx.write(tmp_path / "src-snapshot")
    archive = tmp_path / name
    bs.zip_snapshot(root, archive)
    return archive.read_bytes()


def lock_for(data, version="20260918"):
    return dist.Lock(
        version=version,
        url=f"{dist.RELEASE_URL_PREFIX}snapshot-{version}/glendale-gis-snapshot-{version}.zip",
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
    )


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(Settings.from_env({}), cache_dir=tmp_path / "cache")


def serve(lock, data):
    """GitHub answers the release URL with a redirect to its asset host, like the real one."""
    respx.get(lock.url).mock(return_value=httpx.Response(302, headers={"Location": ASSET_HOST}))
    return respx.get(ASSET_HOST).mock(return_value=httpx.Response(200, content=data))


def resolve(settings, lock):
    with httpx.Client() as http:
        return dist.ensure_snapshot(settings, lock=lock, http=http)


def test_configured_path_wins(settings, tmp_path):
    settings = dataclasses.replace(settings, snapshot_path=tmp_path / "mine")
    with respx.mock:  # no request may be made
        r = dist.ensure_snapshot(settings, lock=lock_for(b"x"))
    assert (r.path, r.source, r.stale) == (tmp_path / "mine", "configured", False)


@respx.mock
def test_downloads_verifies_and_reuses(settings, tmp_path):
    data = make_archive(tmp_path)
    lock = lock_for(data)
    asset = serve(lock, data)
    first = resolve(settings, lock)
    assert first.source == "downloaded"
    assert first.path == settings.cache_dir / "snapshots" / "20260918"
    assert (first.path / ".verified").read_text() == lock.sha256
    snapshot = Snapshot.load(first.path)
    assert snapshot.has_layer("bus_stops")
    second = resolve(settings, lock)
    assert (second.source, second.path) == ("cache", first.path)
    assert asset.call_count == 1
    assert "GlendaleGisMcp/" in asset.calls[0].request.headers["User-Agent"]


@respx.mock
def test_checksum_mismatch_is_rejected(settings, tmp_path):
    data = make_archive(tmp_path)
    lock = dataclasses.replace(lock_for(data), sha256="0" * 64)
    serve(lock, data)
    r = resolve(settings, lock)
    assert (r.path, r.source) == (None, "none")
    assert "checksum doesn't match" in r.error
    assert not (settings.cache_dir / "snapshots" / "20260918").exists()


@respx.mock
def test_oversized_download_is_rejected(settings, tmp_path):
    data = make_archive(tmp_path)
    lock = dataclasses.replace(lock_for(data), size=len(data) - 1)
    serve(lock, data)
    assert "larger than the lock says" in resolve(settings, lock).error


@respx.mock
def test_redirect_to_another_host_is_refused(settings, tmp_path):
    data = make_archive(tmp_path)
    lock = lock_for(data)
    respx.get(lock.url).mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example.com/x.zip"})
    )
    r = resolve(settings, lock)
    assert r.path is None
    assert "refusing to download from 'evil.example.com'" in r.error


@respx.mock
def test_unsafe_archive_paths_are_refused(settings, tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("../escape.txt", "gotcha")
    data = archive.read_bytes()
    lock = lock_for(data)
    serve(lock, data)
    r = resolve(settings, lock)
    assert "unsafe path in archive" in r.error
    assert not (settings.cache_dir / "escape.txt").exists()


@respx.mock
def test_archive_without_manifest_is_refused(settings, tmp_path):
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "no manifest")
    data = archive.read_bytes()
    lock = lock_for(data)
    serve(lock, data)
    assert "no manifest.json" in resolve(settings, lock).error


@respx.mock
def test_failed_download_falls_back_to_an_older_snapshot(settings, tmp_path):
    old_data = make_archive(tmp_path, "old.zip")
    old = lock_for(old_data, version="20260901")
    serve(old, old_data)
    assert resolve(settings, old).source == "downloaded"

    new = lock_for(b"new snapshot", version="20260918")
    respx.get(new.url).mock(return_value=httpx.Response(503))
    r = resolve(settings, new)
    assert (r.source, r.stale) == ("previous", True)
    assert r.path.name == "20260901"
    assert "Couldn't download snapshot 20260918" in r.error


@respx.mock
def test_only_the_current_and_one_older_snapshot_are_kept(settings, tmp_path):
    data = make_archive(tmp_path)
    for i, version in enumerate(["20260901", "20260910", "20260918"]):
        lock = lock_for(data, version=version)
        serve(lock, data)
        resolve(settings, lock)
        marker = settings.cache_dir / "snapshots" / version / ".verified"
        os.utime(marker, (1_000_000 + i, 1_000_000 + i))  # make the order unambiguous
    kept = sorted(p.name for p in (settings.cache_dir / "snapshots").iterdir())
    assert kept == ["20260910", "20260918"]


def test_no_lock_and_nothing_cached(settings, monkeypatch):
    monkeypatch.setattr(dist, "read_lock", lambda: None)
    r = dist.ensure_snapshot(settings)
    assert (r.path, r.source) == (None, "none")
    assert "No snapshot.lock.json" in r.error


@pytest.mark.parametrize(
    "change, message",
    [
        ({"url": "https://example.com/x.zip"}, "not a release of HackerFund/GlendaleGisMcp"),
        ({"sha256": "abc"}, "invalid sha256"),
        ({"size": 0}, "invalid size"),
        ({"version": "../x"}, "invalid version"),
    ],
)
def test_lock_validation(change, message):
    good = dataclasses.asdict(lock_for(b"x"))
    with pytest.raises(dist.DistributionError, match=message):
        dist.Lock.from_dict({**good, **change})


def test_stale_snapshot_marks_results_stale(tmp_path):
    snap = Snapshot.load(fx.write(tmp_path / "s"), stale=True)
    assert snap.stale
    assert snap.layer("bus_stops").meta().stale is True


def test_committed_lock_is_valid():
    lock = dist.read_lock()
    if lock is None:
        pytest.skip("no snapshot.lock.json yet")
    assert lock.url.startswith(dist.RELEASE_URL_PREFIX)


# -- CLI --------------------------------------------------------------------------------------


def test_fetch_snapshot_cli(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GLENDALE_GIS_SNAPSHOT_PATH", str(tmp_path))
    assert cli.main(["--fetch-snapshot"]) == 0
    assert "Snapshot ready (configured)" in capsys.readouterr().out


def test_fetch_snapshot_cli_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("GLENDALE_GIS_SNAPSHOT_PATH", raising=False)
    monkeypatch.setenv("GLENDALE_GIS_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(dist, "read_lock", lambda: None)
    assert cli.main(["--fetch-snapshot"]) == 1
    assert "no snapshot" in capsys.readouterr().err


# -- publishing ---------------------------------------------------------------------------------


class FakeGh:
    def __init__(self, exists=False, create_ok=True):
        self.calls = []
        self.exists = exists
        self.create_ok = create_ok
        self.uploaded = None

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:3] == ["gh", "release", "view"]:
            return _done(0 if self.exists else 1)
        if args[:3] == ["gh", "release", "create"]:
            self.uploaded = Path(args[4]).read_bytes()
            return _done(0 if self.create_ok else 1, stderr="HTTP 403")
        raise AssertionError(args)


def _done(code, stderr=""):
    import subprocess

    return subprocess.CompletedProcess([], code, "", stderr)


def test_publish_uploads_and_writes_the_lock(tmp_path, capsys):
    root = fx.write(tmp_path / "snap")
    (root / "stray.txt").write_text("not part of the snapshot")
    lock_path = tmp_path / "snapshot.lock.json"
    gh = FakeGh()
    assert bs.publish(root, lock_path=lock_path, run=gh) == 0

    create = gh.calls[1]
    assert create[:4] == ["gh", "release", "create", "snapshot-20260918"]
    assert create[create.index("--repo") + 1] == "HackerFund/GlendaleGisMcp"
    assert create[4].endswith("glendale-gis-snapshot-20260918.zip")
    notes = create[create.index("--notes") + 1]
    assert "| `bus_stops` | City of Glendale | 3 |" in notes

    lock = json.loads(lock_path.read_text())
    assert lock["version"] == "20260918"
    assert lock["tag"] == "snapshot-20260918"
    assert lock["url"] == (
        "https://github.com/HackerFund/GlendaleGisMcp/releases/download/"
        "snapshot-20260918/glendale-gis-snapshot-20260918.zip"
    )
    assert lock["sha256"] == hashlib.sha256(gh.uploaded).hexdigest()
    assert lock["size"] == len(gh.uploaded)
    dist.Lock.from_dict(lock)  # valid for installed copies

    archive = tmp_path / "uploaded.zip"
    archive.write_bytes(gh.uploaded)
    listed = zipfile.ZipFile(archive).namelist()
    assert listed[0] == "manifest.json"
    assert "stray.txt" not in listed
    assert "Commit it" in capsys.readouterr().out


def test_zip_is_deterministic(tmp_path):
    root = fx.write(tmp_path / "snap")
    bs.zip_snapshot(root, tmp_path / "a.zip")
    bs.zip_snapshot(root, tmp_path / "b.zip")
    assert (tmp_path / "a.zip").read_bytes() == (tmp_path / "b.zip").read_bytes()


def test_publish_refuses_an_existing_release(tmp_path, capsys):
    lock_path = tmp_path / "snapshot.lock.json"
    gh = FakeGh(exists=True)
    assert bs.publish(fx.write(tmp_path / "snap"), lock_path=lock_path, run=gh) == 1
    assert "--tag snapshot-20260918-2" in capsys.readouterr().err
    assert not lock_path.exists()
    assert len(gh.calls) == 1


def test_publish_failure_leaves_the_lock_alone(tmp_path, capsys):
    lock_path = tmp_path / "snapshot.lock.json"
    lock_path.write_text("original")
    gh = FakeGh(create_ok=False)
    assert bs.publish(fx.write(tmp_path / "snap"), lock_path=lock_path, run=gh) == 1
    assert lock_path.read_text() == "original"
    assert "HTTP 403" in capsys.readouterr().err


def test_publish_with_a_custom_tag(tmp_path):
    lock_path = tmp_path / "snapshot.lock.json"
    gh = FakeGh()
    root = fx.write(tmp_path / "snap")
    assert bs.publish(root, tag="snapshot-20260918-2", lock_path=lock_path, run=gh) == 0
    assert json.loads(lock_path.read_text())["version"] == "20260918-2"
    assert bs.publish(root, tag="v1", lock_path=lock_path, run=gh) == 2


def test_publish_needs_a_built_snapshot(tmp_path, capsys):
    assert bs.publish(tmp_path, run=FakeGh()) == 1
    assert "no snapshot to publish" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv", [["--no-build"], ["--tag", "snapshot-1"], ["--publish", "--dry-run"]]
)
def test_publish_flag_combinations(argv, capsys):
    assert bs.main(argv) == 2
