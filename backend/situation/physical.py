"""Unified physical-energy situation — the niche-defining top-line.

Fuses the three descriptive domain engines into one glance:
  • OIL   — chokepoint transit + rerouting anomalies (AIS + IMF PortWatch)
  • GAS   — EU storage balance residual (GIE/ENTSOG) WATCH/SIGNAL flag
  • POWER — day-ahead / residual-load / spark situation (ENTSO-E), worst zone

Each domain reports a descriptive state (CALM / ELEVATED / STRESSED) measured as a
deviation vs its OWN history — never a forecast. The overall state is the worst of
the available domains. This reuses the existing per-domain logic (load_power_situation,
detect_gas_balance, detect_chokepoint/detect_rerouting) — no new data, no new model.
"""
from __future__ import annotations

from datetime import date as _date
from datetime import datetime, timezone

from sqlalchemy.orm import Session

_RANK = {"CALM": 0, "ELEVATED": 1, "STRESSED": 2}


def _state_from_severity(severity: str | None) -> str:
    """Detector severity → descriptive desk state (mirrors the power situation bands)."""
    return {"critical": "STRESSED", "warning": "ELEVATED"}.get(severity or "", "CALM")


def _worst(states) -> str:
    ranked = [s for s in states if s in _RANK]
    return max(ranked, key=lambda s: _RANK[s]) if ranked else "CALM"


def _today(today: _date | None) -> _date:
    return today or datetime.now(timezone.utc).date()


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else round((s[m - 1] + s[m]) / 2, 1)


def _summary(d7: list[float], d30: list[float]) -> dict | None:
    """Common context shape: median forward move at +7d/+30d + sample size. Generic
    across domains (oil→Brent, gas→TTF) so the frontend renders one uniform line."""
    n = max(len(d7), len(d30))
    if n == 0:
        return None
    return {
        "n": n,
        "median_7d_pct": _median(d7),
        "median_30d_pct": _median(d30),
        "note": "historical co-movement over a small sample — not a forecast",
    }


def chokepoint_price_context(chokepoint: str) -> dict | None:
    """Honest historical analog for a chokepoint transit drop: how Brent moved after
    comparable past drops. Reuses signals.historical_lookup.find_anomalies."""
    try:
        from backend.signals.historical_lookup import find_anomalies

        res = find_anomalies(chokepoint)
    except Exception:
        return None
    events = res.get("anomalies") or []
    d7 = [e["brent_change_7d_pct"] for e in events if e.get("brent_change_7d_pct") is not None]
    d30 = [e["brent_change_30d_pct"] for e in events if e.get("brent_change_30d_pct") is not None]
    return _summary(d7, d30)


def gas_balance_price_context(db: Session) -> dict | None:
    """Honest historical analog for a gas-balance SIGNAL: how TTF moved after past
    SIGNAL onsets. Same shape as the oil context. Pure local DB read."""
    from backend.models.energy import EnergyPrice
    from backend.models.gas import GasBalance
    from backend.signals.historical_lookup import _find_nearest_price

    ttf = {r.date: r.close for r in db.query(EnergyPrice).filter(EnergyPrice.symbol == "TTF").all()}
    if not ttf:
        return None
    rows = db.query(GasBalance).order_by(GasBalance.date.asc()).all()
    d7: list[float] = []
    d30: list[float] = []
    prev_signal = False
    for r in rows:
        is_signal = bool(r.flag) and r.flag.startswith("SIGNAL")
        if is_signal and not prev_signal:  # onset only — don't double-count a run
            base = _find_nearest_price(ttf, r.date, direction=-1)
            if base:
                a7 = _find_nearest_price(ttf, r.date, direction=1, offset_days=7)
                a30 = _find_nearest_price(ttf, r.date, direction=1, offset_days=30)
                if a7:
                    d7.append(round((a7 - base) / base * 100, 1))
                if a30:
                    d30.append(round((a30 - base) / base * 100, 1))
        prev_signal = is_signal
    return _summary(d7, d30)


def _oil_domain(db: Session) -> dict:
    """Oil molecules in motion: chokepoint transit + Suez→Cape rerouting anomalies."""
    base = {"domain": "oil", "label": "Oil", "tab": "overview"}
    try:
        from backend.signals.detectors.oil import detect_chokepoint, detect_rerouting

        results = [*detect_chokepoint(db), *detect_rerouting(db)]
    except Exception:
        results = []

    if not results:
        return {**base, "available": True, "state": "CALM",
                "headline": "Chokepoint transit within normal range.", "as_of": None, "stale": False}

    worst = max(results, key=lambda r: _RANK[_state_from_severity(r.severity)])
    out = {**base, "available": True, "state": _state_from_severity(worst.severity),
           "headline": worst.title, "detail": worst.detail, "as_of": worst.as_of, "stale": False}
    # "So what?" — attach the honest historical price analog for a chokepoint drop.
    if getattr(worst, "rule", "") == "chokepoint_anomaly" and worst.zone:
        ctx = chokepoint_price_context(worst.zone)
        if ctx:
            cp = (worst.title or "").split(":")[0].strip() or "chokepoint"
            out["context"] = {**ctx, "price_label": "Brent", "event_label": f"{cp} transit drops"}
    return out


def _gas_domain(db: Session, today: _date) -> dict:
    """EU gas balance residual — storage vs implied (supply − demand − exports)."""
    base = {"domain": "gas", "label": "Gas", "tab": "gas"}
    from backend.models.gas import GasBalance
    from backend.signals.detectors.base import is_stale
    from backend.signals.detectors.gas import detect_gas_balance

    row = db.query(GasBalance).order_by(GasBalance.date.desc()).first()
    if row is None:
        return {**base, "available": False, "state": "CALM",
                "headline": "No gas balance data yet.", "as_of": None, "stale": False}

    as_of = row.date
    stale = is_stale(as_of, 3, today=today)
    flagged = detect_gas_balance(db)
    if flagged:
        r = flagged[0]
        out = {**base, "available": True, "state": _state_from_severity(r.severity),
               "headline": r.title, "detail": r.detail, "as_of": as_of, "stale": stale}
        ctx = gas_balance_price_context(db)
        if ctx:
            out["context"] = {**ctx, "price_label": "TTF", "event_label": "EU gas-balance SIGNALs"}
        return out
    return {**base, "available": True, "state": "CALM",
            "headline": "EU gas balance within normal range.", "as_of": as_of, "stale": stale}


def _power_forward(db: Session, zone: str) -> dict | None:
    """Tomorrow's (D+1) residual-load forecast for a zone — the price-driving forward
    quantity: forecast load − wind − solar. None if the wind/solar forecast is absent."""
    from backend.models.energy import PowerLoadForecast

    row = (
        db.query(PowerLoadForecast)
        .filter(PowerLoadForecast.zone == zone)
        .order_by(PowerLoadForecast.date.desc())
        .first()
    )
    if row is None or row.wind_forecast_mw is None or row.solar_forecast_mw is None:
        return None
    resid = row.forecast_mw - row.wind_forecast_mw - row.solar_forecast_mw
    return {"date": row.date, "residual_mw": round(resid, 2), "load_mw": round(row.forecast_mw, 2)}


def _power_domain(db: Session) -> dict:
    """European power grid — worst of the covered bidding zones (DE-LU / FR / NL)."""
    base = {"domain": "power", "label": "Power", "tab": "energy"}
    from backend.power.zones import POWER_ZONES
    from backend.routes.power import load_power_situation

    best: dict | None = None
    for zone in POWER_ZONES:
        try:
            sit = load_power_situation(db, zone)
        except Exception:
            continue
        if not sit.get("available"):
            continue
        state = sit.get("state", "CALM")
        if best is None or _RANK.get(state, 0) > _RANK.get(best["state"], 0):
            best = {"state": state, "headline": sit.get("headline"),
                    "as_of": sit.get("as_of"), "stale": bool(sit.get("stale")), "zone": zone}

    if best is None:
        return {**base, "available": False, "state": "CALM",
                "headline": "No power data yet.", "as_of": None, "stale": False}
    forward = _power_forward(db, best["zone"])
    return {**base, "available": True, **best, "forward": forward}


def combine_domains(gas: dict, power: dict) -> dict:
    """Pure: collapse the desk's domains into one envelope. Overall = worst of the
    AVAILABLE domains (an unavailable domain never drives the headline state).

    Refocus 2026-07-03: Obsyd is the European electricity desk (electrons + their
    fuel side, gas). The oil domain moved to the sibling project — `_oil_domain` /
    `chokepoint_price_context` stay defined but unwired (extracted in Phase 2)."""
    domains = {"gas": gas, "power": power}
    available = [d for d in domains.values() if d.get("available")]
    return {
        "available": bool(available),
        "overall": _worst([d["state"] for d in available]),
        "domains": domains,
    }


def build_physical_situation(db: Session, today: _date | None = None) -> dict:
    """The European power system at a glance — electrons + their gas fuel in one line."""
    t = _today(today)
    return combine_domains(_gas_domain(db, t), _power_domain(db))
