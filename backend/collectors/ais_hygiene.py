"""
AIS Data Hygiene — plausibility filters for vessel position data.

Filters out implausible AIS data before storage:
  - Invalid coordinates (out of range or 0/0)
  - Invalid MMSI (not 9 digits, test/SAR ranges)
  - Excessive speed (tankers >25 kn, any vessel >50 kn)
  - Stale timestamps (>24h old)
  - Duplicate positions (same MMSI within cooldown window)

Task 7.3 + 7.10 of Phase 7 Signal Intelligence Overhaul.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# --- Plausibility thresholds ---

MAX_TANKER_SOG = 25.0    # knots — VLCC max ~16 kn, Suezmax ~15, safety margin
MAX_ANY_SOG = 50.0       # knots — no commercial vessel goes faster
MAX_POSITION_AGE_H = 24  # hours — reject stale AIS reports
DEDUP_SECONDS = 120      # don't store same MMSI more often than this

# MMSI ranges to exclude (ITU-R M.585)
# 970xxxxxx = SAR aircraft
# 972xxxxxx = AIS SART
# 974xxxxxx = EPIRB-AIS
# 99xxxxxxx = aids to navigation
# 111xxxxxx = SAR relay
EXCLUDED_MMSI_PREFIXES = ("970", "972", "974", "99", "111")

# Dedup cache: mmsi -> last stored timestamp
_last_stored: dict[str, datetime] = {}
_MAX_CACHE_SIZE = 50000


def validate_position(
    mmsi: str,
    lat: float,
    lon: float,
    sog: float,
    ship_type: int,
    timestamp: datetime | None = None,
) -> tuple[bool, str]:
    """Validate a single AIS position report.

    Returns (is_valid, reason).
    """
    # MMSI validation
    if not mmsi or len(mmsi) != 9 or not mmsi.isdigit():
        return False, "invalid_mmsi"

    if any(mmsi.startswith(p) for p in EXCLUDED_MMSI_PREFIXES):
        return False, "excluded_mmsi_range"

    # Coordinate validation
    if lat == 0.0 and lon == 0.0:
        return False, "null_island"

    if not (-90.0 <= lat <= 90.0):
        return False, "lat_out_of_range"

    if not (-180.0 <= lon <= 180.0):
        return False, "lon_out_of_range"

    # Speed validation
    if sog < 0:
        return False, "negative_sog"

    is_tanker = 80 <= ship_type <= 89
    if is_tanker and sog > MAX_TANKER_SOG:
        return False, "tanker_speed_implausible"

    if sog > MAX_ANY_SOG:
        return False, "speed_implausible"

    # Timestamp validation
    if timestamp:
        now = datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        age = now - timestamp
        if age > timedelta(hours=MAX_POSITION_AGE_H):
            return False, "stale_timestamp"
        if age < timedelta(seconds=-60):
            return False, "future_timestamp"

    return True, "ok"


def should_store(mmsi: str, timestamp: datetime | None = None) -> bool:
    """Check if we should store this position (dedup within cooldown window)."""
    global _last_stored

    now = timestamp or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    last = _last_stored.get(mmsi)
    if last and (now - last).total_seconds() < DEDUP_SECONDS:
        return False

    _last_stored[mmsi] = now

    # Prune cache if it gets too large
    if len(_last_stored) > _MAX_CACHE_SIZE:
        cutoff = now - timedelta(seconds=DEDUP_SECONDS * 2)
        _last_stored = {k: v for k, v in _last_stored.items() if v > cutoff}

    return True


# --- Aggregate stats for monitoring ---

_stats = {
    "total": 0,
    "passed": 0,
    "rejected": 0,
    "deduped": 0,
    "reasons": {},
}


def filter_and_count(
    mmsi: str,
    lat: float,
    lon: float,
    sog: float,
    ship_type: int,
    timestamp: datetime | None = None,
) -> bool:
    """Validate + dedup in one call. Updates internal stats. Returns True if should store."""
    _stats["total"] += 1

    valid, reason = validate_position(mmsi, lat, lon, sog, ship_type, timestamp)
    if not valid:
        _stats["rejected"] += 1
        _stats["reasons"][reason] = _stats["reasons"].get(reason, 0) + 1
        return False

    if not should_store(mmsi, timestamp):
        _stats["deduped"] += 1
        return False

    _stats["passed"] += 1
    return True


def get_stats() -> dict:
    return dict(_stats)


def reset_stats():
    _stats.update({"total": 0, "passed": 0, "rejected": 0, "deduped": 0, "reasons": {}})
