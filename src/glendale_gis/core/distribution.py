"""Find, download and verify the published snapshot.

The snapshot is a GitHub Release asset of this repository. ``snapshot.lock.json`` (committed, and
shipped inside the package) names the version, URL, SHA-256 and size. On first run the package
downloads the asset into the cache directory, checks it against the lock, unpacks it and reuses it
afterwards. If that fails, an older verified copy is used and results are marked stale; with no
copy at all the server still starts and explains what's missing. It never crashes.

Downloads come only from this repository's releases; redirects are allowed only to GitHub's
release-asset hosts, and the checksum is verified before anything is unpacked.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx

from glendale_gis.core.config import Settings

log = logging.getLogger(__name__)

REPO = "HackerFund/GlendaleGisMcp"
RELEASE_URL_PREFIX = f"https://github.com/{REPO}/releases/download/"
DOWNLOAD_HOSTS = frozenset(
    {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
)
LOCK_NAME = "snapshot.lock.json"
REPO_LOCK = Path(__file__).resolve().parents[3] / LOCK_NAME
VERIFIED_MARKER = ".verified"
MAX_UNPACKED_BYTES = 500_000_000
MAX_REDIRECTS = 5
KEEP_VERSIONS = 2
_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DistributionError(Exception):
    """The snapshot couldn't be obtained; the message says why."""


@dataclass(frozen=True)
class Lock:
    version: str
    url: str
    sha256: str
    size: int

    @classmethod
    def from_dict(cls, data: dict) -> Lock:
        try:
            lock = cls(
                version=str(data["version"]),
                url=str(data["url"]),
                sha256=str(data["sha256"]).lower(),
                size=int(data["size"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DistributionError(f"{LOCK_NAME} is incomplete: {exc}") from None
        if not _VERSION_RE.match(lock.version):
            raise DistributionError(f"{LOCK_NAME} has an invalid version {lock.version!r}")
        if not lock.url.startswith(RELEASE_URL_PREFIX):
            raise DistributionError(f"{LOCK_NAME} URL is not a release of {REPO}")
        if not _SHA256_RE.match(lock.sha256):
            raise DistributionError(f"{LOCK_NAME} has an invalid sha256")
        if lock.size <= 0:
            raise DistributionError(f"{LOCK_NAME} has an invalid size")
        return lock


@dataclass(frozen=True)
class Resolved:
    """Where the snapshot is, how it was found, and whether it is out of date."""

    path: Path | None
    source: str  # configured, cache, downloaded, previous or none
    stale: bool = False
    error: str | None = None


def read_lock() -> Lock | None:
    """The lock shipped with the package, or the repository's copy in a source checkout."""
    packaged = resources.files("glendale_gis") / LOCK_NAME
    for candidate in (packaged, REPO_LOCK):
        if candidate.is_file():
            try:
                return Lock.from_dict(json.loads(candidate.read_text(encoding="utf-8")))
            except ValueError as exc:
                raise DistributionError(f"{LOCK_NAME} is not valid JSON: {exc}") from None
    return None


def ensure_snapshot(
    settings: Settings, *, lock: Lock | None = None, http: httpx.Client | None = None
) -> Resolved:
    """Return a usable snapshot directory, downloading it if needed. Never raises."""
    if settings.snapshot_path is not None:
        return Resolved(settings.snapshot_path, "configured")
    root = settings.cache_dir / "snapshots"
    try:
        lock = lock or read_lock()
    except DistributionError as exc:
        return _fallback(root, str(exc))
    if lock is None:
        return _fallback(
            root,
            f"No {LOCK_NAME} found, so there's no published snapshot to download. Build one "
            "with scripts/build_snapshot.py and set GLENDALE_GIS_SNAPSHOT_PATH.",
        )

    target = root / lock.version
    if _is_verified(target, lock.sha256):
        return Resolved(target, "cache")
    try:
        _download_and_unpack(lock, target, settings, http)
    except (DistributionError, httpx.HTTPError, OSError, zipfile.BadZipFile) as exc:
        return _fallback(root, f"Couldn't download snapshot {lock.version}: {exc}")
    _prune(root, keep=target)
    log.info("Downloaded snapshot %s", lock.version)
    return Resolved(target, "downloaded")


# --------------------------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------------------------


def _is_verified(path: Path, sha256: str) -> bool:
    marker = path / VERIFIED_MARKER
    return (
        marker.is_file()
        and marker.read_text().strip() == sha256
        and (path / "manifest.json").is_file()
    )


def _verified_versions(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found = [p for p in root.iterdir() if p.is_dir() and (p / VERIFIED_MARKER).is_file()]
    return sorted(found, key=lambda p: (p / VERIFIED_MARKER).stat().st_mtime, reverse=True)


def _fallback(root: Path, reason: str) -> Resolved:
    previous = _verified_versions(root)
    if previous:
        log.warning("%s Using the older snapshot %s.", reason, previous[0].name)
        return Resolved(previous[0], "previous", stale=True, error=reason)
    log.error("%s", reason)
    return Resolved(None, "none", error=reason)


def _check_download_url(url: str) -> None:
    parts = urlsplit(url)
    allowed = parts.scheme == "https" and parts.hostname in DOWNLOAD_HOSTS
    if not allowed or parts.port not in (None, 443):
        raise DistributionError(f"refusing to download from {parts.hostname!r}")


def _download_and_unpack(
    lock: Lock, target: Path, settings: Settings, http: httpx.Client | None
) -> None:
    root = target.parent
    root.mkdir(parents=True, exist_ok=True)
    owns_http = http is None
    http = http or httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=False)
    try:
        with tempfile.TemporaryDirectory(dir=root, prefix=".download-") as tmp:
            archive = Path(tmp) / "snapshot.zip"
            _download(http, lock, archive, settings.user_agent)
            unpacked = Path(tmp) / "unpacked"
            _safe_unzip(archive, unpacked)
            if not (unpacked / "manifest.json").is_file():
                raise DistributionError("the archive has no manifest.json")
            (unpacked / VERIFIED_MARKER).write_text(lock.sha256)
            if target.exists():
                shutil.rmtree(target)
            unpacked.rename(target)
    finally:
        if owns_http:
            http.close()


def _download(http: httpx.Client, lock: Lock, dest: Path, user_agent: str) -> None:
    url = lock.url
    for _ in range(MAX_REDIRECTS + 1):
        _check_download_url(url)
        with http.stream("GET", url, headers={"User-Agent": user_agent}) as response:
            if response.is_redirect:
                url = str(response.url.join(response.headers.get("location", "")))
                continue
            response.raise_for_status()
            digest = hashlib.sha256()
            size = 0
            with dest.open("wb") as out:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > lock.size:
                        raise DistributionError("the download is larger than the lock says")
                    digest.update(chunk)
                    out.write(chunk)
        if size != lock.size:
            raise DistributionError(f"expected {lock.size} bytes, got {size}")
        if digest.hexdigest() != lock.sha256:
            raise DistributionError("the checksum doesn't match snapshot.lock.json")
        return
    raise DistributionError("too many redirects")


def _safe_unzip(archive: Path, dest: Path) -> None:
    """Unpack, refusing absolute paths, '..', links and oversized archives."""
    dest.mkdir()
    total = 0
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in info.filename:
                raise DistributionError(f"unsafe path in archive: {info.filename!r}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise DistributionError(f"symbolic link in archive: {info.filename!r}")
            total += info.file_size
            if total > MAX_UNPACKED_BYTES:
                raise DistributionError("the archive unpacks to more than 500 MB")
        zf.extractall(dest)


def _prune(root: Path, keep: Path) -> None:
    """Keep the current snapshot and the most recent older one, as a fallback."""
    others = [p for p in _verified_versions(root) if p != keep]
    for old in others[KEEP_VERSIONS - 1 :]:
        shutil.rmtree(old, ignore_errors=True)
