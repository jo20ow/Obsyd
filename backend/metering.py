"""In-memory usage metering with an aggregate flush — Phase 2.

The audit doctrine this implements: the app runs ONE uvicorn worker, so an
in-process counter is authoritative; SQLite behind WAL serves reads fast but
serializes writes, so the read path must NEVER write per request. Counters
accumulate here under a lock and the scheduler flushes them as a handful of
upserts every few minutes (api_usage_flush). Worst case on a hard restart:
the unflushed tail of one interval is lost — acceptable for billing-grade
daily aggregates and stated here rather than papered over.

Identity = ApiKey.id. Anonymous traffic is not metered (the Caddy access log
already covers it); metering exists to answer "what did THIS key consume",
which is the quota and billing axis.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.models.api_key import ApiKey, ApiUsageDaily

logger = logging.getLogger(__name__)

_lock = threading.Lock()
#: (day "YYYY-MM-DD", key_id) → [requests, points]
_counters: dict[tuple[str, int], list[int]] = {}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record(key_id: int, *, requests: int = 1, points: int = 0) -> None:
    """Count usage for a key. Cheap and lock-tight — called on the hot path."""
    key = (_today(), key_id)
    with _lock:
        slot = _counters.get(key)
        if slot is None:
            _counters[key] = [requests, points]
        else:
            slot[0] += requests
            slot[1] += points


def usage_today(key_id: int) -> tuple[int, int]:
    """(requests, points) accumulated IN MEMORY for today — the quota check's
    fast half; add the flushed DB row for the full day."""
    with _lock:
        slot = _counters.get((_today(), key_id))
        return (slot[0], slot[1]) if slot else (0, 0)


def flush(db: Session, *, now: datetime | None = None) -> int:
    """Upsert all accumulated counters into api_usage_daily and stamp
    last_used_at on the keys seen. Returns rows flushed. Swapping the dict
    under the lock keeps the write outside it — recording never blocks on
    SQLite."""
    with _lock:
        if not _counters:
            return 0
        drained, _counters_new = dict(_counters), _counters.clear()
    now = now or datetime.now(timezone.utc)
    for (day, key_id), (requests, points) in drained.items():
        stmt = sqlite_insert(ApiUsageDaily).values(
            day=day, key_id=key_id, requests=requests, points=points
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["day", "key_id"],
            set_={
                "requests": ApiUsageDaily.requests + stmt.excluded.requests,
                "points": ApiUsageDaily.points + stmt.excluded.points,
            },
        )
        db.execute(stmt)
    seen_keys = {key_id for _, key_id in drained}
    db.query(ApiKey).filter(ApiKey.id.in_(seen_keys)).update(
        {"last_used_at": now.replace(tzinfo=None)}, synchronize_session=False
    )
    db.commit()
    logger.info("metering flush: %d (day,key) rows", len(drained))
    return len(drained)


def _reset() -> None:
    """Test isolation."""
    with _lock:
        _counters.clear()
