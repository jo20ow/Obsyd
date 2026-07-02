"""Economic release calendar — "what moves markets this week", free via FRED.

FRED's releases/dates API is free (needs the FRED key we already have) and publishes
scheduled FUTURE release dates. We curate the majors (jobs, CPI, GDP, PCE, …) and
return the upcoming schedule. Honest scope: this is the release *schedule* + names —
NOT consensus/forecast (survey estimates are licensed, not free) and not the actual
values (those live in the FRED series / indicators). Fail-soft: any error → [].
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

_URL = "https://api.stlouisfed.org/fred/releases/dates"

# Curated major US macro releases: (case-insensitive substring of the FRED release
# name, display label). Substring match is robust to FRED's exact naming.
CURATED: list[tuple[str, str]] = [
    ("employment situation", "Jobs report — payrolls & unemployment"),
    ("consumer price index", "CPI — consumer inflation"),
    ("producer price index", "PPI — producer inflation"),
    ("personal income and outlays", "PCE — income, outlays & core inflation"),
    ("gross domestic product", "GDP"),
    ("employment cost index", "Employment Cost Index"),
    ("retail", "Retail sales"),
    ("industrial production", "Industrial production"),
    ("university of michigan", "Consumer sentiment (UMich)"),
    ("job openings", "JOLTS — job openings"),
]

_cache: dict[str, tuple[float, list]] = {}
_TTL = 6 * 3600  # the schedule changes slowly


def _label_for(name: str) -> str | None:
    low = (name or "").lower()
    for sub, label in CURATED:
        if sub in low:
            return label
    return None


def parse_calendar(release_dates: list[dict], today: str) -> list[dict]:
    """Filter FRED release_dates to curated majors on/after `today`, sorted ascending, deduped."""
    seen = set()
    out = []
    for rd in release_dates or []:
        date = rd.get("date")
        name = rd.get("release_name", "")
        if not date or date < today:
            continue
        label = _label_for(name)
        if label is None:
            continue
        key = (date, rd.get("release_id"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"date": date, "release": name, "label": label})
    out.sort(key=lambda x: x["date"])
    return out


async def _fetch_release_dates(key: str, start: str, end: str) -> list[dict]:
    params = {
        "api_key": key,
        "file_type": "json",
        "include_release_dates_with_no_data": "true",
        "sort_order": "asc",
        "realtime_start": start,
        "realtime_end": end,
        "limit": 1000,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(_URL, params=params)
        resp.raise_for_status()
        return resp.json().get("release_dates", [])


async def get_calendar(days_ahead: int = 21) -> list[dict]:
    """Upcoming curated macro releases for the next `days_ahead` days (cached, fail-soft)."""
    key = settings.fred_api_key
    if not key:
        return []
    key = key.get_secret_value() if hasattr(key, "get_secret_value") else key

    now = time.monotonic()
    cache_key = f"cal:{days_ahead}"
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < _TTL:
        return cached[1]

    today = datetime.now(timezone.utc).date()
    try:
        raw = await _fetch_release_dates(key, today.isoformat(), (today + timedelta(days=days_ahead)).isoformat())
    except Exception as exc:  # noqa: BLE001 — calendar must never crash the route
        logger.warning("econ calendar: FRED releases/dates fetch failed: %s", exc)
        return []
    data = parse_calendar(raw, today.isoformat())
    _cache[cache_key] = (now, data)
    return data
