"""Tests for the write-once raw-response disk cache."""

from __future__ import annotations

from datetime import date

import pytest

from backend.gas import raw_cache


@pytest.fixture
def cache_root(tmp_path, monkeypatch):
    monkeypatch.setattr(raw_cache, "DATA_ROOT", tmp_path)
    return tmp_path


def test_cache_path_is_month_bucketed(cache_root):
    p = raw_cache.cache_path("entsog", "flows_2026-06-01", date(2026, 6, 1))
    assert p == cache_root / "entsog" / "2026-06" / "flows_2026-06-01.json"


def test_write_then_read_roundtrip(cache_root):
    dt = date(2026, 6, 1)
    raw_cache.write_cached("agsi", "agsi_2026-06-01", dt, {"data": [{"full": "43.1"}]})
    got = raw_cache.read_cached("agsi", "agsi_2026-06-01", dt)
    assert got == {"data": [{"full": "43.1"}]}


def test_read_miss_returns_none(cache_root):
    assert raw_cache.read_cached("agsi", "nope", date(2026, 6, 1)) is None


def test_write_is_write_once_by_default(cache_root):
    dt = date(2026, 6, 1)
    raw_cache.write_cached("alsi", "k", dt, {"v": 1})
    raw_cache.write_cached("alsi", "k", dt, {"v": 2})  # ignored
    assert raw_cache.read_cached("alsi", "k", dt) == {"v": 1}


def test_overwrite_replaces_provisional(cache_root):
    dt = date(2026, 6, 1)
    raw_cache.write_cached("alsi", "k", dt, {"v": 1})
    raw_cache.write_cached("alsi", "k", dt, {"v": 2}, overwrite=True)
    assert raw_cache.read_cached("alsi", "k", dt) == {"v": 2}


def test_no_tmp_file_left_behind(cache_root):
    dt = date(2026, 6, 1)
    raw_cache.write_cached("entsog", "k", dt, {"v": 1})
    leftovers = list(cache_root.rglob("*.tmp"))
    assert leftovers == []


async def test_fetch_or_cache_only_fetches_on_miss(cache_root):
    dt = date(2026, 6, 1)
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return {"fetched": calls["n"]}

    first = await raw_cache.fetch_or_cache("entsog", "k", dt, fetch)
    second = await raw_cache.fetch_or_cache("entsog", "k", dt, fetch)
    assert first == {"fetched": 1}
    assert second == {"fetched": 1}  # served from cache, fetch not called again
    assert calls["n"] == 1


async def test_fetch_or_cache_overwrite_refetches(cache_root):
    dt = date(2026, 6, 1)
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return {"fetched": calls["n"]}

    await raw_cache.fetch_or_cache("entsog", "k", dt, fetch)
    again = await raw_cache.fetch_or_cache("entsog", "k", dt, fetch, overwrite=True)
    assert again == {"fetched": 2}
    assert calls["n"] == 2
