"""Write-once raw-response disk cache.

So recalibration never re-hits the API: every fetched payload is written to
data/raw/<source>/<YYYY-MM>/<key>.json and read back on subsequent runs.
Atomic writes (tmp + os.replace) avoid partial files. `overwrite=True` is
used only to replace a provisional payload with its confirmed version.

No DB, no network — pure filesystem, so it's unit-testable with tmp_path.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

# Repo-root relative; overridable in tests by monkeypatching DATA_ROOT.
DATA_ROOT = Path("data/raw")


def cache_path(source: str, key: str, dt: date) -> Path:
    """data/raw/<source>/<YYYY-MM>/<key>.json — month-bucketed to keep dirs small."""
    safe_key = key.replace("/", "_").replace(" ", "_")
    return DATA_ROOT / source / f"{dt:%Y-%m}" / f"{safe_key}.json"


def read_cached(source: str, key: str, dt: date) -> dict | None:
    """Return the cached payload, or None on miss / unreadable file."""
    path = cache_path(source, key, dt)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def write_cached(source: str, key: str, dt: date, payload: dict, *, overwrite: bool = False) -> Path:
    """Atomically write payload. Skips an existing file unless overwrite=True."""
    path = cache_path(source, key, dt)
    if path.exists() and not overwrite:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)  # atomic on the same filesystem
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
