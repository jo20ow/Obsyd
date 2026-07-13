"""Why is this zone expensive today? — a ranked driver card.

The situation hero already prints three numbers side by side (price z, residual z,
spark) and a list of flags. It never says WHICH condition is doing the work, nor
how today compares to physically similar days. So the question a power analyst
asks first every single morning — "why is FR €40 over DE today?" — is answered by
scrolling six panels and eyeballing.

This decomposes today into the conditions that CO-OCCUR with the price, each
ranked by how far it sits from its own norm, and then places today against the
most physically similar days in the zone's own history.

POSTURE B, ENFORCED IN THE WORDING
----------------------------------
Every sentence is co-occurrence, never causation, never a forecast:

    "€142/MWh WHILE wind sits 2.8σ below its norm, 6.1 GW (5% of the fleet) is
     forced offline and the zone is importing 3.2 GW."

Not "because". The analog line is the same discipline — it reports what similar
days CLEARED, in the past tense, with the sample size attached:

    "The 34 days whose residual load was within 1 GW of today's cleared at €95 on
     average (p10-p90: €61-€140). Today is €142."

That is a statement about the record, not about tomorrow. There is no model here,
no LLM, and no claim that any driver caused any price.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.models.energy import PowerFlow, PowerGrid, PowerPriceDaily
from backend.power.baseline import BASELINE_DAYS
from backend.power.zones import POWER_ZONES
from backend.signals.detectors.base import trailing_zscore

#: A day is an ANALOG of today when its residual load sits within this band. Not
#: a model — a filter on the record.
ANALOG_BAND_MW = 1_000.0

#: Below this many analogs we say so instead of quoting a mean of four days.
ANALOG_MIN_N = 10

#: Drivers below this |z| are not worth a sentence — they are just Tuesday.
NOTABLE_Z = 1.0

#: An outage earns a place in the HEADLINE only above this share of the fleet (or,
#: where no A68 fleet is known, this many MW). "0.1 GW is forced offline (0% of the
#: fleet)" is noise in a sentence that is supposed to be signal — the table still
#: shows it.
HEADLINE_OUTAGE_FLEET_PCT = 1.0
HEADLINE_OUTAGE_MW = 500.0


def _fmt_gw(mw: float) -> str:
    return f"{mw / 1000:.1f} GW"


def _driver(key: str, label: str, value: float | None, unit: str,
            history: list[float], *, invert: bool = False) -> dict | None:
    """One driver with its deviation from its own trailing norm.

    `invert` marks drivers where LOW is the notable direction (wind, solar): the
    sentence then reads "below its norm" rather than "above".
    """
    if value is None:
        return None
    stat = trailing_zscore(value, history) if len(history) >= 2 else None
    if stat is None:
        # Too little history, or a baseline with zero variance. The VALUE is still
        # a fact — only the deviation is unknowable. Dropping the driver entirely
        # would hide a real number; claiming a z would invent one.
        return {
            "key": key, "label": label,
            "value": round(value, 2), "unit": unit,
            "z": None, "baseline_mean": None, "baseline_n": len(history),
            "direction": None, "notable": False, "inverted": invert,
        }
    z, mean, _std, n = stat
    return {
        "key": key,
        "label": label,
        "value": round(value, 2),
        "unit": unit,
        "z": round(z, 2),
        "baseline_mean": round(mean, 2),
        "baseline_n": n,
        "direction": "below" if z < 0 else "above",
        "notable": abs(z) >= NOTABLE_Z,
        "inverted": invert,
    }


def net_position_by_day(db: Session, zone: str, start: str) -> dict[str, float]:
    """Net physical export of `zone` per day since `start`, summed over its borders.

    Positive = the zone exported on net. ONE query for the whole window — the
    per-day version meant 30 round trips per request. Country-level flows, so
    sub-zones have none: they get no net-position driver rather than a
    fabricated zero.
    """
    rows = (
        db.query(PowerFlow.date, PowerFlow.from_zone, PowerFlow.to_zone, PowerFlow.net_mw)
        .filter(PowerFlow.date >= start,
                (PowerFlow.from_zone == zone) | (PowerFlow.to_zone == zone))
        .all()
    )
    out: dict[str, float] = {}
    for day, from_zone, _to_zone, net_mw in rows:
        out[day] = out.get(day, 0.0) + (net_mw if from_zone == zone else -net_mw)
    return out


def _analogs(db: Session, zone: str, residual_mw: float, today: str) -> dict:
    """The days whose residual load was closest to today's — and what they cleared.

    A filter on the record, not a model: same zone, residual within ANALOG_BAND_MW,
    excluding today itself.
    """
    from backend.power.borders import percentile

    rows = (
        db.query(PowerGrid.date, PowerGrid.residual_mw, PowerPriceDaily.mean_price)
        .join(PowerPriceDaily,
              (PowerPriceDaily.date == PowerGrid.date)
              & (PowerPriceDaily.zone == PowerGrid.zone))
        .filter(PowerGrid.zone == zone,
                PowerGrid.date != today,
                PowerGrid.residual_mw.isnot(None),
                PowerPriceDaily.mean_price.isnot(None))
        .all()
    )
    prices = [
        float(price) for _d, resid, price in rows
        if abs(float(resid) - residual_mw) <= ANALOG_BAND_MW
    ]
    if len(prices) < ANALOG_MIN_N:
        return {
            "n": len(prices),
            "band_mw": ANALOG_BAND_MW,
            "enough": False,
            "reason": (
                f"Only {len(prices)} day(s) in this zone's record had a residual load "
                f"within {_fmt_gw(ANALOG_BAND_MW)} of today's — too few to quote a norm."
            ),
        }
    return {
        "n": len(prices),
        "band_mw": ANALOG_BAND_MW,
        "enough": True,
        "mean_price": round(sum(prices) / len(prices), 2),
        "p10": round(percentile(prices, 0.10), 2),
        "p90": round(percentile(prices, 0.90), 2),
    }


def compute_drivers(db: Session, zone: str, *, today: _date | None = None) -> dict:
    """Today's price, the conditions co-occurring with it, ranked, plus analogs."""
    if zone not in POWER_ZONES:
        return {"available": False, "zone": zone, "reason": f"Unknown zone {zone}."}
    today = today or datetime.now(timezone.utc).date()
    start = (today - timedelta(days=BASELINE_DAYS)).isoformat()

    grid = (
        db.query(PowerGrid)
        .filter(PowerGrid.zone == zone, PowerGrid.date >= start)
        .order_by(PowerGrid.date.asc())
        .all()
    )
    prices = (
        db.query(PowerPriceDaily)
        .filter(PowerPriceDaily.zone == zone, PowerPriceDaily.date >= start)
        .order_by(PowerPriceDaily.date.asc())
        .all()
    )
    if not grid or not prices:
        return {
            "available": False, "zone": zone,
            "reason": f"No price/grid history for {POWER_ZONES[zone]['label']} yet.",
        }

    latest_grid, latest_price = grid[-1], prices[-1]
    as_of = max(latest_grid.date, latest_price.date)

    price_stat = _driver(
        "price", "Day-ahead price", latest_price.mean_price, "EUR/MWh",
        [p.mean_price for p in prices[:-1] if p.mean_price is not None],
    )

    drivers: list[dict] = []
    d = _driver("residual", "Residual load", latest_grid.residual_mw, "MW",
                [g.residual_mw for g in grid[:-1] if g.residual_mw is not None])
    if d:
        drivers.append(d)
    d = _driver("wind", "Wind generation", latest_grid.wind_mw, "MW",
                [g.wind_mw for g in grid[:-1] if g.wind_mw is not None], invert=True)
    if d:
        drivers.append(d)
    d = _driver("solar", "Solar generation", latest_grid.solar_mw, "MW",
                [g.solar_mw for g in grid[:-1] if g.solar_mw is not None], invert=True)
    if d:
        drivers.append(d)

    net_by_day = net_position_by_day(db, zone, start)
    net = net_by_day.get(latest_grid.date)
    if net is not None:
        history = [v for d_, v in sorted(net_by_day.items()) if d_ != latest_grid.date]
        d = _driver("net_position", "Net export position", net, "MW", history)
        if d:
            drivers.append(d)

    # Forced outages are a LEVEL, not a deviation — there is no hourly outage
    # history in the store yet (that is the outage-time-series work), so this
    # carries no z and says so by omitting one.
    from backend.signals.detectors.power import (
        forced_outage_mw_now,
        installed_capacity_mw,
        published_unit_capacity_mw,
    )

    forced_mw, _rows = forced_outage_mw_now(db, zone)
    installed = installed_capacity_mw(db, zone)
    published = published_unit_capacity_mw(db, zone)
    outage = None
    if forced_mw > 0:
        outage = {
            "key": "forced_outages",
            "label": "Forced outages",
            "value": round(forced_mw, 1),
            "unit": "MW",
            "z": None,
            "fleet_pct": round(100.0 * forced_mw / installed, 1) if installed else None,
            # A SECOND denominator, with its own name — never merged into fleet_pct. A71/A33
            # publishes only units above ~100 MW (DE-LU: 52 GW vs A68's 295 GW), so it is a
            # different population, not a smaller sample. But it is the SAME population the
            # outages themselves come from, and it exists for all 37 zones — including the 18
            # that have no A68 and therefore no fleet_pct at all.
            "published_fleet_pct": (
                round(100.0 * forced_mw / published, 1) if published else None
            ),
            "published_fleet_mw": round(published, 1) if published else None,
            "notable": True,
        }

    # Most-deviant first; a driver without a trustworthy baseline sinks to the end
    # rather than pretending to a rank it cannot claim.
    drivers.sort(key=lambda x: (x["z"] is None, -abs(x["z"] or 0.0)))

    analogs = (
        _analogs(db, zone, latest_grid.residual_mw, latest_grid.date)
        if latest_grid.residual_mw is not None else
        {"enough": False, "n": 0, "reason": "No residual load for this zone."}
    )

    return {
        "available": True,
        "zone": zone,
        "zone_label": POWER_ZONES[zone]["label"],
        "as_of": as_of,
        "baseline_days": BASELINE_DAYS,
        "price": price_stat,
        "drivers": drivers,
        "outage": outage,
        "market_net_position": market_net_position(db, zone, today),
        "analogs": analogs,
        "headline": _headline(zone, price_stat, drivers, outage),
        "note": (
            "Conditions that CO-OCCUR with today's price, ranked by how far each sits "
            "from its own norm. Descriptive: no driver is claimed to have caused the "
            "price, and no forecast is made."
        ),
    }


def market_net_position(db: Session, zone: str, today: _date) -> dict:
    """The zone's day-ahead MARKET net position (ENTSO-E A25/B09) — a DIFFERENT quantity
    from the `net_position` driver above, and deliberately kept apart from it.

    The driver is derived: physical flows, country-level, summed off the borders. This is the
    SDAC day-ahead allocation, from the auction itself, and it exists for the 18 sub-zones that
    have no country-level flows at all. Two numbers called "net position" on one screen, meaning
    two things, is how a desk loses an analyst — so this one is labelled, not merged.

    GR, IE_SEM and CH publish no A25: they get an explicit reason, not a silent absence.
    """
    from backend.power.entsoe_exchange import (
        NET_POSITION_SERIES,
        NET_POSITION_UNSUPPORTED,
    )
    from backend.power.hourly_store import read_hourly

    if zone in NET_POSITION_UNSUPPORTED:
        return {"available": False,
                "reason": f"{zone} publishes no day-ahead net position (ENTSO-E A25)."}

    start = int(datetime.combine(today - timedelta(days=1), datetime.min.time(),
                                 tzinfo=timezone.utc).timestamp())
    points = read_hourly(db, NET_POSITION_SERIES, zone, start_ts=start)
    if not points:
        return {"available": False,
                "reason": "No day-ahead net position for this zone yet."}

    values = [v for _t, v in points]
    mean = sum(values) / len(values)
    return {
        "available": True,
        "mean_mw": round(mean, 1),
        "min_mw": round(min(values), 1),
        "max_mw": round(max(values), 1),
        "export_hours_pct": round(100.0 * sum(1 for v in values if v > 0) / len(values), 1),
        "hours": len(values),
        "as_of": datetime.fromtimestamp(points[-1][0], tz=timezone.utc).date().isoformat(),
        "direction": "exporting" if mean > 0 else "importing",
        "note": (
            "Day-ahead market net position (SDAC allocation, ENTSO-E A25). Positive = the zone "
            "is a net exporter. A DIFFERENT quantity from the physical net flow above, which is "
            "summed from country-level metered flows."
        ),
    }


def _outage_is_headline_worthy(outage: dict) -> bool:
    """Material enough to say out loud. Below this it stays in the table, where a
    small number is context rather than clutter."""
    if outage["fleet_pct"] is not None:
        return outage["fleet_pct"] >= HEADLINE_OUTAGE_FLEET_PCT
    return outage["value"] >= HEADLINE_OUTAGE_MW


def _headline(zone: str, price: dict | None, drivers: list[dict], outage: dict | None) -> str:
    """Template text. Co-occurrence ('WHILE'), never causation ('because')."""
    label = POWER_ZONES[zone]["label"]
    if price is None or price["value"] is None:
        return f"{label} · no price today."

    head = f"{label} cleared at €{price['value']:.0f}/MWh"
    if price["z"] is not None:
        head += f" ({price['z']:+.1f}σ vs its {BASELINE_DAYS}d norm)"

    parts = []
    for d in drivers:
        if not d["notable"]:
            continue
        if d["key"] == "net_position":
            # A signed number the reader has to decode reads worse than the word
            # for what it means.
            verb = "exporting" if d["value"] >= 0 else "importing"
            parts.append(
                f"the zone is {verb} {_fmt_gw(abs(d['value']))} "
                f"({abs(d['z']):.1f}σ {d['direction']} its norm)"
            )
            continue
        val = _fmt_gw(d["value"]) if d["unit"] == "MW" else f"{d['value']:.0f} {d['unit']}"
        parts.append(f"{d['label'].lower()} is {val}, {abs(d['z']):.1f}σ {d['direction']} its norm")
    if outage and _outage_is_headline_worthy(outage):
        seg = f"{_fmt_gw(outage['value'])} is forced offline"
        if outage["fleet_pct"] is not None:
            seg += f" ({outage['fleet_pct']:.0f}% of the fleet)"
        parts.append(seg)

    if not parts:
        return head + " — nothing is far from its norm today."
    return head + " WHILE " + ", ".join(parts[:-1]) + (
        f" and {parts[-1]}" if len(parts) > 1 else parts[0]
    ) + "."
