"""Write-once raw-response disk cache.

So recalibration never re-hits the API: every fetched payload is written to
data/raw/<source>/<YYYY-MM>/<key>.json.gz and read back on subsequent runs.
Atomic writes (tmp + os.replace) avoid partial files. `overwrite=True` is
used only to replace a provisional payload with its confirmed version.

Blobs are gzipped. The ENTSO-E/ENTSOG payloads that dominate this cache are
JSON and compress roughly 24x; uncompressed they had grown to 9.1 GB on the
VPS and were a material part of what filled its disk on 2026-07-07. Entries
written before that change are plain `.json` and stay readable, so an existing
cache keeps working with or without the one-off migration
(`backend/scripts/compress_raw_cache.py`).

No DB, no network — pure filesystem, so it's unit-testable with tmp_path.
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import date
from pathlib import Path

# Repo-root relative; overridable in tests by monkeypatching DATA_ROOT.
DATA_ROOT = Path("data/raw")


def _bucket(source: str, key: str, dt: date) -> Path:
    """data/raw/<source>/<YYYY-MM>/<key> — month-bucketed to keep dirs small."""
    safe_key = key.replace("/", "_").replace(" ", "_")
    return DATA_ROOT / source / f"{dt:%Y-%m}" / safe_key


def cache_path(source: str, key: str, dt: date) -> Path:
    """Canonical (compressed) location of a cached payload."""
    base = _bucket(source, key, dt)
    return base.with_name(f"{base.name}.json.gz")


def legacy_path(source: str, key: str, dt: date) -> Path:
    """Pre-compression location. Still read, never written."""
    base = _bucket(source, key, dt)
    return base.with_name(f"{base.name}.json")


def read_cached(source: str, key: str, dt: date) -> dict | None:
    """Return the cached payload, or None on miss / unreadable file.

    The compressed blob wins: after a migration or an overwrite it is the
    authoritative copy, and a stale `.json` sibling may still linger.
    """
    gz = cache_path(source, key, dt)
    if gz.exists():
        try:
            with gzip.open(gz, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, EOFError, json.JSONDecodeError):
            return None

    legacy = legacy_path(source, key, dt)
    if legacy.exists():
        try:
            with legacy.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    return None


def write_cached(source: str, key: str, dt: date, payload: dict, *, overwrite: bool = False) -> Path:
    """Atomically write payload, gzipped. Skips an existing entry unless overwrite=True."""
    path = cache_path(source, key, dt)
    legacy = legacy_path(source, key, dt)

    if not overwrite and (path.exists() or legacy.exists()):
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    # mtime=0 keeps the blob byte-identical across rewrites of the same payload.
    with tmp.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(json.dumps(payload).encode("utf-8"))
    os.replace(tmp, path)  # atomic on the same filesystem

    # An overwrite supersedes the pre-compression copy; leaving it would let a
    # stale payload resurface if the compressed blob is ever removed.
    if legacy.exists():
        legacy.unlink()

    return path


async def fetch_or_cache(source: str, key: str, dt: date, fetch_coro, *, overwrite: bool = False) -> dict:
    """Read-through cache: return the cached payload, else await `fetch_coro`
    (a zero-arg coroutine), persist it, and return it.

    overwrite=True forces a re-fetch and replaces the cached blob — used when
    re-ingesting a provisional day that may now be confirmed.
    """
    if not overwrite:
        hit = read_cached(source, key, dt)
        if hit is not None:
            return hit
    payload = await fetch_coro()
    write_cached(source, key, dt, payload, overwrite=overwrite)
    return payload
