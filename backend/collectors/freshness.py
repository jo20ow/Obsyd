"""Shared collector-freshness spec — one source of truth for /api/health/collectors
and the daily collector watchdog, so they can never drift.

Two probe kinds:
  * ``is_date_string=False`` — compare an INGESTION timestamp column
    (fetched_at / created_at / timestamp) to now. "Did the collector write recently?"
  * ``is_date_string=True`` — compare max(DELIVERY-DATE string "YYYY-MM-DD") to today.
    Product-critical sources (ENTSO-E, Energy-Charts, gas, yfinance) are re-written
    every night with overwrite=True, so an ingestion-timestamp probe looks fresh even
    when the data is days stale — only the data's own date reveals a frozen frontier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from backend.models.energy import (
    EnergyPrice,
    PowerEpisode,
    PowerFlow,
    PowerGrid,
    PowerHourly,
    PowerOutage,
    PowerPriceDaily,
    SeriesDim,
)
from backend.models.gas import GasBalance, GasStorageCountry
from backend.models.prices import EIAPrice, FREDSeries
from backend.models.sentiment import GDELTVolume
from backend.models.vessels import VesselPosition
from backend.power.zones import POWER_ZONES


@dataclass(frozen=True)
class FreshnessSpec:
    key: str
    model: type
    column: str                 # attribute name on model to take max() of
    max_age: timedelta
    is_date_string: bool = False # True → column is "YYYY-MM-DD" delivery-date string
    filter_col: str | None = None
    filter_val: str | None = None
    #: Set for canonical-store series: probe max(power_hourly.ts_utc) for this
    #: series key across ALL zones ("is the collector alive at all"), instead
    #: of a model column. model/column are ignored then.
    hourly_series: str | None = None


# Windows are matched to each source's publication cadence (day-ahead is daily but
# frontier-based; realised grid lags ~1d; gas confirms 1-2d late; yfinance ~3 trading days).
SPECS: list[FreshnessSpec] = [
    # Ingestion-timestamp probes (legacy 4).
    FreshnessSpec("eia", EIAPrice, "fetched_at", timedelta(days=14)),
    FreshnessSpec("fred", FREDSeries, "fetched_at", timedelta(days=7)),
    FreshnessSpec("ais", VesselPosition, "timestamp", timedelta(hours=2)),
    FreshnessSpec("gdelt", GDELTVolume, "created_at", timedelta(hours=24)),
    # Delivery-date probes (product-critical — the ones that were unmonitored).
    FreshnessSpec("power_flows", PowerFlow, "date", timedelta(days=3), is_date_string=True),
    FreshnessSpec("gas_balance", GasBalance, "date", timedelta(days=3), is_date_string=True),
    # The per-country layer rides the SAME payload as the EU aggregate, so if it goes stale
    # while gas_balance does not, the country walk broke — not the feed. That is worth being
    # able to tell apart, which is why it gets its own probe rather than sharing one.
    FreshnessSpec("gas_storage_country", GasStorageCountry, "date", timedelta(days=3),
                  is_date_string=True),
    FreshnessSpec("ttf", EnergyPrice, "date", timedelta(days=4),
                  is_date_string=True, filter_col="symbol", filter_val="TTF"),
    FreshnessSpec("copper", EnergyPrice, "date", timedelta(days=4),
                  is_date_string=True, filter_col="symbol", filter_val="COPPER"),
]

# Canonical-store series added by the 2026-07 depth roadmap — a stalled
# collector here would have gone unnoticed exactly like the July incident.
# Windows: QH prices publish daily; imbalance confirms late; A72 is weekly
# with ~2 weeks publication lag (mirrors HYDRO_STALE_DAYS); A71 is nightly.
SPECS += [
    FreshnessSpec("price_qh", PowerPriceDaily, "", timedelta(days=2),
                  hourly_series="price.dayahead.qh"),
    FreshnessSpec("imbalance_qh", PowerPriceDaily, "", timedelta(days=4),
                  hourly_series="imbalance.price.qh"),
    FreshnessSpec("generation_forecast", PowerPriceDaily, "", timedelta(days=2),
                  hourly_series="generation.forecast"),
    FreshnessSpec("hydro_reservoir", PowerPriceDaily, "", timedelta(days=16),
                  hourly_series="hydro.reservoir"),
    # Outage messages land continuously across 37 zones; a silent day means
    # the collector is dead, not that Europe stopped breaking.
    FreshnessSpec("power_outages", PowerOutage, "created_at", timedelta(days=2)),
    # Hourly cross-border flows (Block 2.4). flow.FR is the probe because a
    # French border (DE_LU-FR sorts DE_LU-first) exists in every enabled setup;
    # the daily grain keeps its own power_flows spec above.
    FreshnessSpec("flows_hourly", PowerPriceDaily, "", timedelta(days=3),
                  hourly_series="flow.FR"),
    # Scheduled exchanges (A09). sched.FR mirrors the flow.FR probe: DE_LU-FR sorts DE_LU
    # first, so the series exists in every enabled setup that has France.
    FreshnessSpec("scheduled_exchanges", PowerPriceDaily, "", timedelta(days=3),
                  hourly_series="sched.FR"),
    # Day-ahead market net position (A25). One probe across all zones: "is the collector alive".
    FreshnessSpec("net_position", PowerPriceDaily, "", timedelta(days=3),
                  hourly_series="netpos.dayahead"),
    # Episodes are DERIVED, not ingested — so the probe asks whether the nightly recompute ran,
    # not whether a feed arrived. A silent episode engine looks exactly like a quiet Europe.
    FreshnessSpec("episodes", PowerEpisode, "updated_at", timedelta(days=2)),
    # /api/power/live (near-real-time TODAY). The intraday scheduler writes
    # load.actual every ~30 min and ENTSO-E's own publication lag is ~1-2h, so 6h
    # would be the honest window for THIS probe alone — but test_outage_history.py
    # pins outage_snapshot (below) as the tightest window on the desk on purpose
    # (it is the one series that cannot be backfilled at all), so this stays
    # capped at 1 day rather than undercutting that invariant. The live route's
    # own `lag_minutes` field carries the real-time precision this coarser
    # health-check window can't.
    FreshnessSpec("live_load", PowerPriceDaily, "", timedelta(days=1),
                  hourly_series="load.actual"),
    # The outage snapshot is the ONE series that cannot be backfilled: A77 takes an
    # unavailability down once it is over, so an hour the recorder missed is gone for
    # good. It must therefore be the tightest window on the desk — a day of silence is
    # a day of history destroyed, not a late delivery to catch up on.
    FreshnessSpec("outage_snapshot", PowerPriceDaily, "", timedelta(days=1),
                  hourly_series="outage.offline"),
    # Activated balancing energy (aFRR/mFRR, A83 volumes/A84 prices — backend/power/
    # entsoe_balancing.py). aFRR price is the probe — ONE reference series across ALL
    # zones ("is the collector alive at all"), the same pattern flows_hourly/
    # scheduled_exchanges/net_position use above, NOT a per-zone coverage claim: the live
    # spike found aFRR/mFRR coverage varies by zone and even by window (FR's own 3-day
    # spike window carried mFRR/FCR/RR prices but no aFRR at all). 2 days respects the
    # pinned invariant that outage_snapshot (1 day, above) stays the tightest hourly spec
    # on the desk (see test_outage_history.py).
    FreshnessSpec("balancing_energy", PowerPriceDaily, "", timedelta(days=2),
                  hourly_series="balancing.afrr.price.up"),
]

# Per-enabled-zone day-ahead + grid freshness (was DE_LU-hardcoded — every enabled
# zone is now monitored). Keys are suffixed with the zone, e.g. "power_dayahead:FR".
for _z in POWER_ZONES:
    SPECS.append(FreshnessSpec(f"power_dayahead:{_z}", PowerPriceDaily, "date", timedelta(days=2),
                               is_date_string=True, filter_col="zone", filter_val=_z))
    SPECS.append(FreshnessSpec(f"power_grid:{_z}", PowerGrid, "date", timedelta(days=3),
                               is_date_string=True, filter_col="zone", filter_val=_z))


def freshness_meta(as_of: str | None, today: _date | None, max_age_days: int) -> dict:
    """as_of/age_days/stale triple for one endpoint/component. Inert without
    `today`. Shared by the power and gas route layers so every panel caption
    derives data age the same way this health module does."""
    age_days: int | None = None
    stale = False
    if today is not None and as_of is not None:
        try:
            age_days = (today - _date.fromisoformat(as_of[:10])).days
            stale = age_days > max_age_days
        except ValueError:
            age_days = None
    return {"as_of": as_of, "age_days": age_days, "stale": stale}


def _spec_max(db, spec: FreshnessSpec):
    if spec.hourly_series is not None:
        sid = db.query(SeriesDim.id).filter(SeriesDim.key == spec.hourly_series).scalar()
        if sid is None:
            return None
        return (
            db.query(func.max(PowerHourly.ts_utc))
            .filter(PowerHourly.series_id == sid)
            .scalar()
        )
    q = db.query(func.max(getattr(spec.model, spec.column)))
    if spec.filter_col is not None:
        q = q.filter(getattr(spec.model, spec.filter_col) == spec.filter_val)
    return q.scalar()


def evaluate_freshness(db, *, now: datetime | None = None) -> dict[str, dict]:
    """Return {key: {"fresh": bool, "last_seen": str|None, "max_age_days": float}} for every spec."""
    if now is None:
        now = datetime.now(timezone.utc)
    today = now.date()
    # The ingestion columns are naive UTC (datetime.utcnow); compare naively to avoid
    # aware/naive subtraction errors.
    now_naive = now.replace(tzinfo=None)

    out: dict[str, dict] = {}
    for spec in SPECS:
        latest = _spec_max(db, spec)
        fresh = False
        last_seen = None
        if latest is not None:
            if spec.hourly_series is not None:
                # epoch seconds from power_hourly
                latest_dt = datetime.fromtimestamp(latest, tz=timezone.utc)
                last_seen = latest_dt.isoformat()
                fresh = (now - latest_dt) <= spec.max_age
            elif spec.is_date_string:
                last_seen = str(latest)
                try:
                    d = _date.fromisoformat(last_seen[:10])
                    fresh = (today - d) <= spec.max_age
                except ValueError:
                    fresh = False
            else:
                last_seen = latest.isoformat()
                fresh = (now_naive - latest) <= spec.max_age
        out[spec.key] = {
            "fresh": fresh,
            "last_seen": last_seen,
            "max_age_days": spec.max_age.total_seconds() / 86400,
        }
    return out
