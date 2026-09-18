import sqlite3

import pytest

from glendale_gis.core.cache import Cache, get_or_fetch


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


def test_get_and_set(tmp_path, clock):
    cache = Cache(tmp_path / "c.sqlite3", clock=clock)
    assert cache.get("ns", "k", ttl_s=60) is None
    cache.set("ns", "k", {"a": [1, 2]})
    entry = cache.get("ns", "k", ttl_s=60)
    assert entry.value == {"a": [1, 2]}
    assert entry.stored_at == clock.now
    assert not entry.expired
    clock.now += 61
    assert cache.get("ns", "k", ttl_s=60).expired
    assert cache.get("other", "k", ttl_s=60) is None


def test_keys_are_hashed_on_disk(tmp_path):
    path = tmp_path / "c.sqlite3"
    cache = Cache(path)
    cache.set("geocode", "613 e broadway", ["x"])
    cache.close()
    rows = sqlite3.connect(path).execute("SELECT key FROM cache").fetchall()
    assert rows and all("broadway" not in key for (key,) in rows)


def test_persists_across_instances(tmp_path):
    Cache(tmp_path / "c.sqlite3").set("ns", "k", 1)
    assert Cache(tmp_path / "c.sqlite3").get("ns", "k", ttl_s=60).value == 1


def test_unusable_path_falls_back_to_memory(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    cache = Cache(blocker / "sub" / "c.sqlite3")
    cache.set("ns", "k", 1)
    assert cache.get("ns", "k", ttl_s=60).value == 1


async def test_get_or_fetch(clock):
    cache = Cache(None, clock=clock)
    calls = []

    async def fetch():
        calls.append(1)
        return {"n": len(calls)}

    first = await get_or_fetch(cache, "ns", "k", 60, fetch)
    assert (first.value, first.cached, first.stale) == ({"n": 1}, False, False)
    second = await get_or_fetch(cache, "ns", "k", 60, fetch)
    assert (second.value, second.cached) == ({"n": 1}, True)
    clock.now += 61
    third = await get_or_fetch(cache, "ns", "k", 60, fetch)
    assert (third.value, third.cached) == ({"n": 2}, False)
    assert len(calls) == 2


async def test_failed_fetch_serves_stale_data(clock):
    cache = Cache(None, clock=clock)
    cache.set("ns", "k", "old")
    clock.now += 61

    async def broken():
        raise ConnectionError("down")

    result = await get_or_fetch(cache, "ns", "k", 60, broken, retryable=(ConnectionError,))
    assert (result.value, result.cached, result.stale) == ("old", True, True)
    assert result.fetched_at == clock.now - 61


async def test_failed_fetch_with_nothing_cached_raises():
    async def broken():
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        await get_or_fetch(Cache(None), "ns", "k", 60, broken, retryable=(ConnectionError,))


async def test_unexpected_errors_are_not_swallowed(clock):
    cache = Cache(None, clock=clock)
    cache.set("ns", "k", "old")
    clock.now += 61

    async def buggy():
        raise KeyError("bug")

    with pytest.raises(KeyError):
        await get_or_fetch(cache, "ns", "k", 60, buggy, retryable=(ConnectionError,))
