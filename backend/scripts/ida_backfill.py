"""Backfill intraday-auction prices (backend/power/entsoe_ida.py) from each
publishing zone's documented floor. Tiny by nature: TP submission is
voluntary and today Spain-only from 2026-01 (see
docs/findings/2026-09-13-ida-intraday-coverage.md).

Usage (on the VPS, as the obsyd user):
    python -m backend.scripts.ida_backfill
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime

from backend.database import SessionLocal
from backend.power.entsoe_ida import IDA_HISTORY_FLOOR, ingest_ida

logging.basicConfig(level=logging.INFO, format="%(message)s")


def _months(start: date, stop: date) -> list[date]:
    out, d = [], start.replace(day=1)
    while d <= stop:
        out.append(d)
        d = (d.replace(day=28) + __import__("datetime").timedelta(days=5)).replace(day=1)
    return out


def main() -> None:
    months = _months(date.fromisoformat(IDA_HISTORY_FLOOR), datetime.now(UTC).date())
    db = SessionLocal()
    try:
        asyncio.run(ingest_ida(db, months))
    finally:
        db.close()


if __name__ == "__main__":
    main()
