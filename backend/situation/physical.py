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


def chokepoint_price_context(chokepoint: str) -> dict | None:
    """Honest historical analog for a chokepoint transit drop: how Brent moved after
    comparable past drops (median of +7d / +30d changes). Descriptive co-movement over
    a small sample — NOT a forecast. Reuses signals.historical_lookup.find_anomalies."""
    try:
        from backend.signals.historical_lookup import find_anomalies

        res = find_anomalies(chokepoint)
    except Exception:
        return None
    events = res.get("anomalies") or []
    d7 = [e["brent_change_7d_pct"] for e in events if e.get("brent_change_7d_pct") is not None]
    d30 = [e["brent_change_30d_pct"] for e in events if e.get("brent_change_30d_pct") is not None]
    n = max(len(d7), len(d30))
    if n == 0:
        return None
    return {
        "n": n,
        "brent_median_7d_pct": _median(d7),
        "brent_median_30d_pct": _median(d30),
        "note": "historical co-movement after comparable transit drops — small sample, not a forecast",
    }


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
            out["context"] = ctx
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
        return {**base, "available": True, "state": _state_from_severity(r.severity),
                "headline": r.title, "detail": r.detail, "as_of": as_of, "stale": stale}
    return {**base, "available": True, "state": "CALM",
            "headline": "EU gas balance within normal range.", "as_of": as_of, "stale": stale}


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
    return {**base, "available": True, **best}


def combine_domains(oil: dict, gas: dict, power: dict) -> dict:
    """Pure: collapse the three domains into one envelope. Overall = worst of the
    AVAILABLE domains (an unavailable domain never drives the headline state)."""
    domains = {"oil": oil, "gas": gas, "power": power}
    available = [d for d in domains.values() if d.get("available")]
    return {
        "available": bool(available),
        "overall": _worst([d["state"] for d in available]),
        "domains": domains,
    }


def build_physical_situation(db: Session, today: _date | None = None) -> dict:
    """The whole physical energy system at a glance — molecules + electrons in one line."""
    t = _today(today)
    return combine_domains(_oil_domain(db), _gas_domain(db, t), _power_domain(db))
