"""A small SQLite key-value cache for live results, so stdio servers that restart often don't
refetch, and a failed fetch can fall back to the last good answer (marked stale).

Keys are hashed before they are stored, so cached addresses aren't kept in plain text. If the
cache file can't be opened (read-only disk, bad path), the cache falls back to memory rather
than failing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    stored_at REAL NOT NULL,
    PRIMARY KEY (namespace, key)
)
"""


@dataclass(frozen=True)
class Entry:
    value: Any
    stored_at: float  # Unix time
    expired: bool


class Cache:
    def __init__(self, path: Path | None, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = self._open(path)

    @staticmethod
    def _open(path: Path | None) -> sqlite3.Connection:
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(path, check_same_thread=False)
                conn.execute(SCHEMA)
                conn.commit()
                return conn
            except (OSError, sqlite3.Error) as exc:
                log.warning("Cache file unavailable (%s); using an in-memory cache", exc)
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.execute(SCHEMA)
        return conn

    @staticmethod
    def _hash(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def get(self, namespace: str, key: str, ttl_s: float) -> Entry | None:
        """The cached value, or None. Expired entries are still returned, marked ``expired``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value, stored_at FROM cache WHERE namespace = ? AND key = ?",
                (namespace, self._hash(key)),
            ).fetchone()
        if row is None:
            return None
        value, stored_at = row
        return Entry(json.loads(value), stored_at, self._clock() - stored_at > ttl_s)

    def set(self, namespace: str, key: str, value: Any) -> float:
        """Store a JSON-serializable value; returns the time it was stored."""
        now = self._clock()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (namespace, key, value, stored_at) "
                "VALUES (?, ?, ?, ?)",
                (namespace, self._hash(key), json.dumps(value), now),
            )
            self._conn.commit()
        return now

    def close(self) -> None:
        with self._lock:
            self._conn.close()


@dataclass(frozen=True)
class Fetched:
    value: Any
    fetched_at: float  # Unix time the value came from the source
    cached: bool  # served from the cache
    stale: bool  # the cache was expired and a fresh fetch failed


async def get_or_fetch(
    cache: Cache,
    namespace: str,
    key: str,
    ttl_s: float,
    fetch: Callable[[], Any],
    *,
    retryable: tuple[type[BaseException], ...] = (Exception,),
) -> Fetched:
    """Serve a fresh cache entry, else fetch and store; if the fetch fails, serve stale data.

    ``fetch`` is an async callable returning a JSON-serializable value. Its error is re-raised
    when there's nothing cached to fall back to.
    """
    entry = cache.get(namespace, key, ttl_s)
    if entry is not None and not entry.expired:
        return Fetched(entry.value, entry.stored_at, cached=True, stale=False)
    try:
        value = await fetch()
    except retryable:
        if entry is None:
            raise
        return Fetched(entry.value, entry.stored_at, cached=True, stale=True)
    stored_at = cache.set(namespace, key, value)
    return Fetched(value, stored_at, cached=False, stale=False)
