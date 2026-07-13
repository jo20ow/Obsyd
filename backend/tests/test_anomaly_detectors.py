"""Tests for the cross-vertical anomaly radar (anonymous Alert backbone).

Covers the detector layer + run_all_detectors + the /api/alerts vertical
exposure/grouping. This is a different subsystem from test_alert_rules.py
(which covers the Pro user rule-builder) — keep them separate.
"""

from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from backend.main import app
from backend.models.alerts import Alert
from backend.models.analytics import (
    DaysOfSupplyHistory,
    FreightProxyHistory,
    SupplyDemandBalance,
)
from backend.models.energy import PowerGenMix, PowerGrid, PowerPriceDaily
from backend.models.gas import GasBalance
from backend.models.sentiment import SentimentScore
from backend.models.vessels import FloatingStorageEvent, GeofenceEvent
from backend.signals.detectors import DETECTORS, run_all_detectors
from backend.signals.detectors.base import is_stale
from backend.signals.detectors.gas import detect_gas_balance
from backend.signals.detectors.oil import (
    detect_chokepoint,
    detect_days_of_supply,
    detect_floating_storage,
    detect_freight_divergence,
    detect_rerouting,
    detect_supply_demand_divergence,
)
from backend.signals.detectors.power import detect_dunkelflaute, detect_negative_prices
from backend.signals.detectors.sentiment import detect_sentiment_risk
from backend.signals.rules import check_flow_anomaly


@pytest.fixture
def client(db_session):
    return TestClient(app)


# ─── Gas ──────────────────────────────────────────────────────────────────────


def test_gas_balance_signal_is_critical(db_session):
    db_session.add(GasBalance(date="2026-06-24", residual_7d=-820, z_score=3.4, flag="SIGNAL:supply↑"))
    db_session.commit()
    results = detect_gas_balance(db_session)
    assert len(results) == 1
    r = results[0]
    assert r.vertical == "gas" and r.rule == "gas_balance" and r.severity == "critical"
    assert "signal" in r.title.lower()
    assert "supply↑" in r.detail


def test_gas_balance_ok_flag_suppressed(db_session):
    db_session.add(GasBalance(date="2026-06-24", residual_7d=-10, z_score=-0.2, flag="OK"))
    db_session.commit()
    assert detect_gas_balance(db_session) == []


def test_gas_balance_watch_is_warning(db_session):
    db_session.add(GasBalance(date="2026-06-24", residual_7d=400, z_score=2.3, flag="WATCH:demand↓"))
    db_session.commit()
    assert detect_gas_balance(db_session)[0].severity == "warning"


def test_gas_balance_uses_latest_day_not_stale_flag(db_session):
    # Old flagged day + a newer normal (unflagged) day → current state wins → suppress.
    db_session.add(GasBalance(date="2026-06-01", residual_7d=900, z_score=3.2, flag="SIGNAL:supply↑"))
    db_session.add(GasBalance(date="2026-06-24", residual_7d=-20, z_score=-0.17, flag=None))
    db_session.commit()
    assert detect_gas_balance(db_session) == []


# ─── Oil analytics ──────────────────────────────────────────────────────────


def test_days_of_supply_tight_warns(db_session):
    db_session.add(DaysOfSupplyHistory(date="2026-06-24", assessment="TIGHT", deviation=-4.5, commercial_days=21.0))
    db_session.commit()
    r = detect_days_of_supply(db_session)[0]
    assert r.vertical == "oil" and r.severity == "warning"


def test_days_of_supply_in_line_suppressed(db_session):
    db_session.add(DaysOfSupplyHistory(date="2026-06-24", assessment="IN_LINE", deviation=0.2, commercial_days=27.0))
    db_session.commit()
    assert detect_days_of_supply(db_session) == []


def test_supply_demand_divergence_warns(db_session):
    db_session.add(
        SupplyDemandBalance(date="2026-06-24", divergence_type="EIA_AIS_DIVERGENCE", divergence_detail="Forecast vs AIS gap.")
    )
    db_session.commit()
    r = detect_supply_demand_divergence(db_session)[0]
    assert r.severity == "warning" and "gap" in r.detail.lower()


def test_supply_demand_confirmed_suppressed(db_session):
    db_session.add(SupplyDemandBalance(date="2026-06-24", divergence_type="EIA_AIS_CONFIRMED", divergence_detail="Agree."))
    db_session.commit()
    assert detect_supply_demand_divergence(db_session) == []


def test_freight_divergence_info(db_session):
    db_session.add(FreightProxyHistory(date="2026-06-24", proxy_index=104.0, divergence_flag="FREIGHT_PROXY_DIVERGENCE"))
    db_session.commit()
    r = detect_freight_divergence(db_session)[0]
    assert r.severity == "info" and r.vertical == "oil"


# Onset-detection: a persistent state (same as the prior reading) must NOT re-fire.


def test_days_of_supply_persistence_suppressed(db_session):
    db_session.add(DaysOfSupplyHistory(date="2026-06-23", assessment="TIGHT", deviation=-4.0, commercial_days=21.0))
    db_session.add(DaysOfSupplyHistory(date="2026-06-24", assessment="TIGHT", deviation=-4.2, commercial_days=20.8))
    db_session.commit()
    assert detect_days_of_supply(db_session) == []


def test_supply_demand_persistence_suppressed(db_session):
    db_session.add(SupplyDemandBalance(date="2026-06-23", divergence_type="EIA_AIS_DIVERGENCE", divergence_detail="x"))
    db_session.add(SupplyDemandBalance(date="2026-06-24", divergence_type="EIA_AIS_DIVERGENCE", divergence_detail="y"))
    db_session.commit()
    assert detect_supply_demand_divergence(db_session) == []


def test_freight_persistence_suppressed(db_session):
    db_session.add(FreightProxyHistory(date="2026-06-23", proxy_index=104.0, divergence_flag="FREIGHT_PROXY_DIVERGENCE"))
    db_session.add(FreightProxyHistory(date="2026-06-24", proxy_index=103.0, divergence_flag="FREIGHT_PROXY_DIVERGENCE"))
    db_session.commit()
    assert detect_freight_divergence(db_session) == []


_FS_ANCHOR = date(2026, 6, 24)


def _seed_fs_daily(db, zone, counts_by_offset):
    """Create events so active_on(anchor - offset) == count for each offset (anchor = _FS_ANCHOR)."""
    for off, c in counts_by_offset.items():
        dt = datetime.combine(_FS_ANCHOR - timedelta(days=off), time(12, 0))
        for i in range(c):
            db.add(FloatingStorageEvent(
                mmsi=f"{zone}-{off}-{i}", zone=zone,
                first_seen=dt, last_seen=dt, duration_days=8.0, status="active",
            ))
    db.commit()


def test_floating_storage_structural_high_no_alert(db_session):
    # Malacca-like: structurally ~10 tankers every day, today 11 → normal vs its own history → no alert.
    counts = {0: 11}
    for o in range(1, 90):
        counts[o] = 9 + (o % 3)  # 9/10/11 → mean ~10
    _seed_fs_daily(db_session, "malacca", counts)
    assert detect_floating_storage(db_session) == []


def test_floating_storage_spike_alerts(db_session):
    # Normally ~2-3 tankers, today 22 → unusual buildup → critical.
    counts = {0: 22}
    for o in range(1, 90):
        counts[o] = 2 + (o % 2)  # 2/3 → mean ~2.5
    _seed_fs_daily(db_session, "suez", counts)
    res = detect_floating_storage(db_session)
    assert len(res) == 1 and res[0].zone == "suez" and res[0].severity == "critical"


def test_floating_storage_sparse_zone_no_alert(db_session):
    # A zone that normally holds ~0 tankers must not alert on a small absolute count.
    _seed_fs_daily(db_session, "hormuz", {0: 10, 1: 1, 2: 1})
    assert detect_floating_storage(db_session) == []


# ─── Power ────────────────────────────────────────────────────────────────────


def test_negative_prices_warns(db_session):
    anchor = date(2026, 6, 24)
    for o in range(1, 21):  # 20 prior days of low negative-hours (0/1/2)
        d = (anchor - timedelta(days=o)).isoformat()
        db_session.add(PowerPriceDaily(date=d, zone="DE_LU", mean_price=40, min_price=-5, max_price=80, negative_hours=o % 3))
    db_session.add(PowerPriceDaily(date=anchor.isoformat(), zone="DE_LU", mean_price=10, min_price=-40, max_price=60, negative_hours=12))
    db_session.commit()
    r = detect_negative_prices(db_session)[0]
    assert r.vertical == "power" and r.zone == "DE_LU" and r.severity in ("warning", "critical")


def test_negative_prices_normal_for_zone_suppressed(db_session):
    # Zone routinely has ~8 negative hours; today 8 → normal vs its own history → no alert.
    anchor = date(2026, 6, 24)
    for o in range(0, 20):
        d = (anchor - timedelta(days=o)).isoformat()
        db_session.add(PowerPriceDaily(date=d, zone="DE_LU", mean_price=20, min_price=-20, max_price=70, negative_hours=7 + (o % 3)))
    db_session.commit()
    assert detect_negative_prices(db_session) == []


def test_negative_prices_zero_suppressed(db_session):
    db_session.add(PowerPriceDaily(date="2026-06-24", zone="DE_LU", mean_price=50, min_price=10, max_price=80, negative_hours=0))
    db_session.commit()
    assert detect_negative_prices(db_session) == []


# ─── Sentiment ──────────────────────────────────────────────────────────────


def test_sentiment_high_risk_warns(db_session):
    db_session.add(SentimentScore(date="2026-06-24", risk_score=9.0, risk_factors='["Strait of Hormuz tension"]'))
    db_session.commit()
    r = detect_sentiment_risk(db_session)[0]
    assert r.vertical == "sentiment" and r.severity == "warning"
    assert "Hormuz" in r.detail


def test_sentiment_low_risk_suppressed(db_session):
    db_session.add(SentimentScore(date="2026-06-24", risk_score=4.0, risk_factors="[]"))
    db_session.commit()
    assert detect_sentiment_risk(db_session) == []


def test_sentiment_relative_jump_info(db_session):
    # Calm baseline (~3), today jumps to 7 → unusual vs own norm (but below absolute 8) → info.
    anchor = date(2026, 6, 24)
    for o in range(1, 20):
        db_session.add(SentimentScore(date=(anchor - timedelta(days=o)).isoformat(), risk_score=3.0 + (o % 2) * 0.5, risk_factors="[]"))
    db_session.add(SentimentScore(date=anchor.isoformat(), risk_score=7.0, risk_factors="[]"))
    db_session.commit()
    r = detect_sentiment_risk(db_session)[0]
    assert r.severity == "info" and r.vertical == "sentiment"


# ─── Runner (loop) ────────────────────────────────────────────────────────────


def test_run_all_detectors_power_gas_only(db_session):
    # Refocus 2026-07-03: the radar registry runs only power/gas detectors. A seeded
    # sentiment score must NOT surface (sentiment moved to the sibling project).
    fresh = date.today().isoformat()
    db_session.add(GasBalance(date=fresh, residual_7d=-800, z_score=3.5, flag="SIGNAL:supply↑"))
    db_session.add(SentimentScore(date=fresh, risk_score=9.0, risk_factors="[]"))
    db_session.commit()

    run_all_detectors(db_session)
    rules = {a.rule for a in db_session.query(Alert).all()}
    assert "gas_balance" in rules
    assert "sentiment_risk" not in rules  # no longer in the registry


def test_run_all_detectors_isolates_failures(db_session, monkeypatch):
    """One detector raising must not suppress the others."""
    def boom(db):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr("backend.signals.detectors.DETECTORS", [boom, detect_gas_balance])
    db_session.add(GasBalance(date=date.today().isoformat(), residual_7d=-800, z_score=3.5, flag="SIGNAL:supply↑"))
    db_session.commit()

    n = run_all_detectors(db_session)  # must not raise
    assert n == 1
    assert db_session.query(Alert).filter(Alert.vertical == "gas").count() == 1


def test_detector_registry_count(db_session):
    assert len(DETECTORS) == 8  # gas/negative_prices/dunkelflaute/forced_outages + imbalance_extreme/price_spike/hydro_deviation/record_break


# ─── flow_anomaly (legacy maritime check) — baseline + onset cure ─────────────


def _seed_geofence(db, zone, counts_newest_first):
    """counts_newest_first[0] is the latest day; one GeofenceEvent per descending date."""
    base = date(2026, 6, 24)
    for i, c in enumerate(counts_newest_first):
        db.add(GeofenceEvent(zone=zone, date=(base - timedelta(days=i)).isoformat(), tanker_count=c))
    db.commit()


def _flow_alert_count(db):
    return db.query(Alert).filter(Alert.rule == "flow_anomaly").count()


def test_flow_anomaly_onset_fires(db_session):
    # ~30 days of stable ~10 tankers, today spikes to 40, yesterday was normal → onset → fire.
    counts = [40] + [10 + (i % 3) for i in range(31)]
    _seed_geofence(db_session, "hormuz", counts)
    check_flow_anomaly(db_session, "hormuz", counts[0])
    assert _flow_alert_count(db_session) == 1


def test_flow_anomaly_persistence_suppressed(db_session):
    # Spike sustained two days (today AND yesterday anomalous) → persistence → no fire.
    counts = [41, 40] + [10 + (i % 3) for i in range(30)]
    _seed_geofence(db_session, "hormuz", counts)
    check_flow_anomaly(db_session, "hormuz", counts[0])
    assert _flow_alert_count(db_session) == 0


def test_flow_anomaly_normal_no_fire(db_session):
    counts = [11] + [10 + (i % 3) for i in range(31)]
    _seed_geofence(db_session, "hormuz", counts)
    check_flow_anomaly(db_session, "hormuz", counts[0])
    assert _flow_alert_count(db_session) == 0


def test_flow_anomaly_insufficient_history_no_fire(db_session):
    _seed_geofence(db_session, "hormuz", [40, 10, 10])  # < FLOW_MIN_HISTORY
    check_flow_anomaly(db_session, "hormuz", 40)
    assert _flow_alert_count(db_session) == 0


# ─── Phase B: dunkelflaute / rerouting / chokepoint ───────────────────────────


def _seed_dunkelflaute_history(db, zone="DE_LU", *, normal_share=0.40, years=3):
    """A zone with a REAL wind/solar fleet and a history to be unusual against.

    Both are load-bearing. The old detector needed neither — it asked one flat question of every
    zone — which is how the radar ended up standing at 27 simultaneous Dunkelflaute alerts, led
    by "NO5: renewables 0% of load" for a zone that is pure hydro.
    """
    from datetime import date, timedelta

    day = date(2026, 6, 24)
    for i in range(1, years * 365):
        d = day - timedelta(days=i)
        if d.month != 6:               # same-month history is what the tail is measured against
            continue
        wind = 60_000 * normal_share * 0.6
        solar = 60_000 * normal_share * 0.4
        db.add(PowerGrid(date=d.isoformat(), zone=zone, load_mw=60_000,
                         wind_mw=wind, solar_mw=solar))


def test_dunkelflaute_warns(db_session):
    """The real thing: a zone with a 40% normal renewable share dropping to 8%."""
    _seed_dunkelflaute_history(db_session)
    db_session.add(PowerGrid(date="2026-06-24", zone="DE_LU", load_mw=60000, wind_mw=3000, solar_mw=2000))  # ~8%
    # Complete generation coverage (~60 GW ≈ load) → the low renewable share is real.
    db_session.add(PowerGenMix(date="2026-06-24", zone="DE_LU", psr_type="Fossil Gas", gen_mw=40000))
    db_session.add(PowerGenMix(date="2026-06-24", zone="DE_LU", psr_type="Nuclear", gen_mw=15000))
    db_session.add(PowerGenMix(date="2026-06-24", zone="DE_LU", psr_type="Wind Onshore", gen_mw=3000))
    db_session.add(PowerGenMix(date="2026-06-24", zone="DE_LU", psr_type="Solar", gen_mw=2000))
    db_session.commit()
    r = detect_dunkelflaute(db_session)[0]
    assert r.vertical == "power" and r.rule == "dunkelflaute" and r.severity == "warning"
    assert "bottom 2%" in r.detail, "the claim is relative to the zone's own record"


def test_dunkelflaute_high_renewables_suppressed(db_session):
    _seed_dunkelflaute_history(db_session)
    db_session.add(PowerGrid(date="2026-06-24", zone="DE_LU", load_mw=60000, wind_mw=30000, solar_mw=15000))  # 75%
    db_session.commit()
    assert detect_dunkelflaute(db_session) == []


def test_a_hydro_zone_is_never_in_a_dunkelflaute(db_session):
    """THE bug, as it stood on prod: "NO5: Dunkelflaute — renewables 0% of load", every single
    day. NO5 is hydro — it has no wind and no solar, and never has. The sentence describes its
    fleet, not an event. NO1, NO5 and SK were below the flat threshold on 100% of ALL days.

    A fixture with a wind fleet cannot express this bug; the zone must have none."""
    from datetime import date, timedelta

    day = date(2026, 6, 24)
    for i in range(1, 3 * 365):
        d = day - timedelta(days=i)
        if d.month != 6:
            continue
        # Pure hydro: a 2% renewable share, every day, forever. Nothing here is an event.
        db_session.add(PowerGrid(date=d.isoformat(), zone="NO5", load_mw=10_000,
                                 wind_mw=200, solar_mw=0))
    db_session.add(PowerGrid(date="2026-06-24", zone="NO5", load_mw=10_000, wind_mw=0, solar_mw=0))
    db_session.commit()

    assert detect_dunkelflaute(db_session) == [], "0% of load is NO5's normal, not its emergency"


def test_a_zone_with_no_history_for_this_month_makes_no_claim(db_session):
    """Without a same-month record there is no tail to be in, so there is nothing to say."""
    db_session.add(PowerGrid(date="2026-06-24", zone="DE_LU", load_mw=60000,
                             wind_mw=3000, solar_mw=2000))
    db_session.commit()
    assert detect_dunkelflaute(db_session) == []


def test_rerouting_high_warns(db_session, monkeypatch):
    monkeypatch.setattr(
        "backend.signals.tonnage_proxy.compute_rerouting_index",
        lambda days=365: {"available": True, "current": {"state": "high_rerouting", "severity": "warning", "ratio_pct": 58.0, "baseline_30d": 0.30}},
    )
    r = detect_rerouting(db_session)[0]
    assert r.rule == "rerouting_high" and r.severity == "warning" and r.vertical == "oil"


def test_rerouting_normal_suppressed(db_session, monkeypatch):
    monkeypatch.setattr(
        "backend.signals.tonnage_proxy.compute_rerouting_index",
        lambda days=365: {"available": True, "current": {"state": "normal", "severity": None}},
    )
    assert detect_rerouting(db_session) == []


def test_chokepoint_maps_alert(db_session, monkeypatch):
    monkeypatch.setattr(
        "backend.signals.portwatch_alerts.check_chokepoint_anomalies",
        lambda: [{
            "chokepoint": "Strait of Hormuz", "anomaly_pct": -42.0, "direction": "drop",
            "n_total": 30, "baseline_avg": 52.0, "baseline_type": "yoy",
            "alert_level": "critical", "disruption_name": "Red Sea crisis",
        }],
    )
    r = detect_chokepoint(db_session)[0]
    assert r.rule == "chokepoint_anomaly" and r.zone == "hormuz" and r.severity == "critical"
    assert "Red Sea crisis" in r.detail


# ─── API exposure + grouping ──────────────────────────────────────────────────


def test_api_exposes_vertical_and_filters(client, db_session):
    db_session.add(Alert(rule="gas_balance", zone="EU", vertical="gas", severity="critical", title="g", detail="d"))
    db_session.add(Alert(rule="negative_prices", zone="DE_LU", vertical="power", severity="warning", title="p", detail="d"))
    db_session.commit()

    body = client.get("/api/alerts?vertical=gas").json()
    assert len(body) == 1 and body[0]["vertical"] == "gas"


def test_api_group_by_vertical_severity_sorted(client, db_session):
    db_session.add(Alert(rule="freight_divergence", zone="t", vertical="oil", severity="info", title="i", detail=""))
    db_session.add(Alert(rule="cushing_drawdown", zone="c", vertical="oil", severity="critical", title="c", detail=""))
    db_session.add(Alert(rule="gas_balance", zone="EU", vertical="gas", severity="warning", title="g", detail=""))
    db_session.commit()

    body = client.get("/api/alerts?group_by_vertical=true").json()
    assert body["total"] == 3
    assert set(body["verticals"].keys()) == {"oil", "gas"}
    # oil group: critical before info
    oil_sev = [a["severity"] for a in body["verticals"]["oil"]]
    assert oil_sev == ["critical", "info"]


def test_api_excludes_stale_alerts(client, db_session):
    # The radar feed shows only current anomalies: a re-fired alert stays fresh, a resolved
    # one ages out of the default 48h window so weeks of history don't pile up.
    db_session.add(Alert(rule="gas_balance", zone="EU", vertical="gas", severity="critical", title="fresh", detail=""))
    db_session.add(Alert(rule="flow_anomaly", zone="houston", vertical="oil", severity="warning", title="stale",
                         detail="", created_at=datetime.utcnow() - timedelta(days=3)))
    db_session.commit()

    titles = [a["title"] for a in client.get("/api/alerts").json()]
    assert "fresh" in titles and "stale" not in titles
    # An explicit wider window can still reach it.
    titles_wide = [a["title"] for a in client.get("/api/alerts?max_age_hours=168").json()]
    assert "stale" in titles_wide


# ─── Migration ────────────────────────────────────────────────────────────────


def test_alerts_vertical_column_present(db_session):
    # The column is part of the schema the app actually binds to.
    cols = {c["name"] for c in inspect(db_session.get_bind()).get_columns("alerts")}
    assert "vertical" in cols


def test_alert_defaults_to_oil(db_session):
    db_session.add(Alert(rule="cushing_drawdown", zone="cushing", severity="critical", title="t", detail="d"))
    db_session.commit()
    row = db_session.query(Alert).filter(Alert.rule == "cushing_drawdown").first()
    assert row.vertical == "oil"


# ─── Staleness gate ───────────────────────────────────────────────────────────
# A detector reads the newest persisted row and treats it as "now". If a collector
# stalls, that row goes days stale while the alert keeps looking fresh in the feed
# (each 5-min re-fire bumps created_at, so retention never expires it). The runner
# must suppress a detector result whose underlying data is older than the vertical's
# tolerance, so a frozen source goes quiet instead of asserting stale data as current.


def test_is_stale_helper():
    today = date(2026, 7, 2)
    assert is_stale("2026-07-01", 3, today=today) is False   # 1 day old, within tolerance
    assert is_stale("2026-06-24", 3, today=today) is True    # 8 days old, stale
    assert is_stale("2026-07-02", 3, today=today) is False   # today
    assert is_stale(None, 3, today=today) is True            # missing → treat as stale
    assert is_stale("not-a-date", 3, today=today) is True    # unparseable → stale


def test_run_all_detectors_suppresses_stale_data(db_session):
    # A gas SIGNAL from a month ago must NOT surface as a current anomaly.
    old = (date.today() - timedelta(days=30)).isoformat()
    db_session.add(GasBalance(date=old, residual_7d=-800, z_score=3.5, flag="SIGNAL:supply↑"))
    db_session.commit()
    run_all_detectors(db_session)
    assert db_session.query(Alert).filter(Alert.vertical == "gas").count() == 0


def test_run_all_detectors_emits_fresh_data(db_session):
    # The same SIGNAL dated today must surface.
    fresh = date.today().isoformat()
    db_session.add(GasBalance(date=fresh, residual_7d=-800, z_score=3.5, flag="SIGNAL:supply↑"))
    db_session.commit()
    run_all_detectors(db_session)
    assert db_session.query(Alert).filter(Alert.rule == "gas_balance").count() == 1


# ─── Dunkelflaute coverage guard ──────────────────────────────────────────────
# ENTSO-E A75 generation is materially incomplete for some zones (notably NL),
# which makes wind+solar look artificially tiny and fires FALSE Dunkelflaute
# alerts. Renewable share is only trustworthy when reported generation covers a
# plausible fraction of load — otherwise the detector must stay silent.


def test_dunkelflaute_suppressed_on_incomplete_coverage(db_session):
    d = date.today().isoformat()
    # Load 10 GW, wind+solar ~1% → would flag. But the generation mix only reports
    # ~4.8 GW total (<60% of load) → coverage too low to trust the share → suppress.
    db_session.add(PowerGrid(date=d, zone="NL", load_mw=10000, wind_mw=70, solar_mw=60))
    db_session.add(PowerGenMix(date=d, zone="NL", psr_type="Fossil Gas", gen_mw=3400))
    db_session.add(PowerGenMix(date=d, zone="NL", psr_type="Hard Coal", gen_mw=1360))
    db_session.commit()
    assert detect_dunkelflaute(db_session) == []


def test_dunkelflaute_suppressed_when_no_generation_mix(db_session):
    # Grid present but no generation-mix rows at all → cannot validate coverage → suppress.
    d = date.today().isoformat()
    db_session.add(PowerGrid(date=d, zone="NL", load_mw=10000, wind_mw=70, solar_mw=60))
    db_session.commit()
    assert detect_dunkelflaute(db_session) == []


# ─── forced outages (power) ───────────────────────────────────────────────────


def _outage(db, *, mrid="o1", revision=1, zone="DE_LU", business_type="A54",
            nominal=1400.0, available=0.0, status="active",
            start_days=-1, end_days=5, unit="Block A"):
    from datetime import datetime, timedelta, timezone

    from backend.models.energy import PowerOutage

    now = datetime.now(timezone.utc)
    db.add(PowerOutage(
        mrid=mrid, revision=revision, doc_type="A77", zone=zone,
        business_type=business_type, psr_type="B14", unit_name=unit,
        unit_eic="11WX", location="X", nominal_mw=nominal, available_mw=available,
        start_utc=(now + timedelta(days=start_days)).strftime("%Y-%m-%dT%H:%MZ"),
        end_utc=(now + timedelta(days=end_days)).strftime("%Y-%m-%dT%H:%MZ"),
        status=status,
    ))
    db.commit()


def test_forced_outages_above_a_gigawatt_alert(db_session):
    from backend.signals.detectors.power import detect_forced_outages

    _outage(db_session, mrid="a", nominal=900.0, available=0.0)
    _outage(db_session, mrid="b", nominal=400.0, available=100.0, unit="Block B")
    results = detect_forced_outages(db_session)

    assert len(results) == 1
    r = results[0]
    assert r.rule == "forced_outages" and r.vertical == "power" and r.zone == "DE_LU"
    assert r.severity == "warning"
    assert "1.2 GW" in r.title
    assert "Block A" in r.detail, "the largest unit is the story"


def test_forced_outages_critical_at_three_gigawatts(db_session):
    from backend.signals.detectors.power import detect_forced_outages

    for i in range(3):
        _outage(db_session, mrid=f"m{i}", nominal=1200.0, available=0.0)
    assert detect_forced_outages(db_session)[0].severity == "critical"


def test_forced_outages_ignore_planned_withdrawn_ended_and_higher_revisions(db_session):
    from backend.signals.detectors.power import detect_forced_outages

    _outage(db_session, mrid="planned", business_type="A53", nominal=5000.0)
    _outage(db_session, mrid="ended", end_days=-1, nominal=5000.0)
    _outage(db_session, mrid="future", start_days=3, nominal=5000.0)
    _outage(db_session, mrid="wd", revision=1, nominal=5000.0)
    _outage(db_session, mrid="wd", revision=2, nominal=5000.0, status="withdrawn")
    assert detect_forced_outages(db_session) == []


def _capacity(db, zone="DE_LU", total_mw=100_000.0, year=2026):
    from backend.models.energy import InstalledCapacity

    db.add(InstalledCapacity(zone=zone, year=year, psr_type="Solar", capacity_mw=total_mw * 0.6))
    db.add(InstalledCapacity(zone=zone, year=year, psr_type="Fossil Gas", capacity_mw=total_mw * 0.4))
    db.commit()


def test_forced_outages_capacity_relative_suppresses_small_share(db_session):
    """1.2 GW forced is a v1-absolute warning, but only 1.2% of a 100 GW fleet —
    with A68 coverage the capacity-relative threshold governs."""
    from backend.signals.detectors.power import detect_forced_outages

    _capacity(db_session, total_mw=100_000.0)
    _outage(db_session, mrid="a", nominal=900.0, available=0.0)
    _outage(db_session, mrid="b", nominal=400.0, available=100.0, unit="Block B")
    assert detect_forced_outages(db_session) == []


def test_forced_outages_capacity_relative_warn_and_share_in_title(db_session):
    from backend.signals.detectors.power import detect_forced_outages

    _capacity(db_session, total_mw=20_000.0)
    _outage(db_session, mrid="a", nominal=900.0, available=0.0)
    _outage(db_session, mrid="b", nominal=400.0, available=100.0, unit="Block B")
    results = detect_forced_outages(db_session)
    assert len(results) == 1
    assert results[0].severity == "warning"  # 1.2 GW = 6% of 20 GW
    assert "6% of fleet" in results[0].title


def test_forced_outages_capacity_relative_critical(db_session):
    from backend.signals.detectors.power import detect_forced_outages

    _capacity(db_session, total_mw=20_000.0)
    for i in range(2):
        _outage(db_session, mrid=f"m{i}", nominal=900.0, available=0.0)
    assert detect_forced_outages(db_session)[0].severity == "critical"  # 1.8 GW = 9%


def test_forced_outages_mw_floor_beats_share_in_tiny_zones(db_session):
    """4% of a 5 GW fleet is only 200 MW — below the 300 MW floor, one mid-size
    unit trip must not page the radar."""
    from backend.signals.detectors.power import detect_forced_outages

    _capacity(db_session, total_mw=5_000.0)
    _outage(db_session, mrid="a", nominal=200.0, available=0.0)
    assert detect_forced_outages(db_session) == []


def test_forced_outages_latest_capacity_year_wins(db_session):
    """Severity divides by the LATEST A68 year, not the sum over all years."""
    from backend.signals.detectors.power import installed_capacity_mw

    _capacity(db_session, total_mw=10_000.0, year=2025)
    _capacity(db_session, total_mw=20_000.0, year=2026)
    assert installed_capacity_mw(db_session, "DE_LU") == 20_000.0
    assert installed_capacity_mw(db_session, "FR") is None


# ─── imbalance_extreme ────────────────────────────────────────────────────────


def _seed_imbalance(db, zone="DE_LU", days=30, normal_peak=120.0, last_peak=None):
    from datetime import date, timedelta

    from backend.power.hourly_store import day_hour_ts, upsert_hourly

    today = date.today()
    points = []
    for i in range(days):
        d = (today - timedelta(days=days - 1 - i)).isoformat()
        peak = last_peak if (i == days - 1 and last_peak is not None) else normal_peak + (i % 5)
        # Two points per day: a quiet hour and the day's peak.
        points.append((day_hour_ts(d, 3), 20.0))
        points.append((day_hour_ts(d, 18), peak))
    upsert_hourly(db, "imbalance.price", zone, points, unit="EUR/MWh")


def test_imbalance_extreme_fires_on_spike_above_floor(db_session):
    from backend.signals.detectors.power import detect_imbalance_extremes

    _seed_imbalance(db_session, last_peak=650.0)
    results = detect_imbalance_extremes(db_session)
    assert len(results) == 1
    r = results[0]
    assert r.rule == "imbalance_extreme" and r.zone == "DE_LU"
    assert r.severity == "critical"  # huge z AND >= 500 € floor
    assert "650" in r.title
    assert r.max_age_days == 4


def test_imbalance_extreme_floor_suppresses_low_variance_zones(db_session):
    """z alone must not page: a quiet zone spiking 40→250 €/MWh is a big z but
    stays under the 300 € floor."""
    from backend.signals.detectors.power import detect_imbalance_extremes

    _seed_imbalance(db_session, normal_peak=40.0, last_peak=250.0)
    assert detect_imbalance_extremes(db_session) == []


def test_imbalance_extreme_needs_baseline(db_session):
    from backend.signals.detectors.power import detect_imbalance_extremes

    _seed_imbalance(db_session, days=5, last_peak=900.0)  # < MIN_BASELINE_N days
    assert detect_imbalance_extremes(db_session) == []


# ─── price_spike ──────────────────────────────────────────────────────────────


def _seed_prices(db, zone="DE_LU", days=40, base=60.0, last=None):
    from datetime import date, timedelta

    from backend.models.energy import PowerPriceDaily

    today = date.today()
    for i in range(days):
        d = (today - timedelta(days=days - 1 - i)).isoformat()
        price = last if (i == days - 1 and last is not None) else base + (i % 5)
        db.add(PowerPriceDaily(date=d, zone=zone, mean_price=price,
                               min_price=price - 30, max_price=price + 40,
                               negative_hours=0))
    db.commit()


def test_price_spike_fires_both_tails(db_session):
    from backend.signals.detectors.power import detect_price_spikes

    _seed_prices(db_session, zone="DE_LU", last=160.0)
    _seed_prices(db_session, zone="FR", last=-40.0)
    results = {r.zone: r for r in detect_price_spikes(db_session)}
    assert results["DE_LU"].severity == "critical"
    assert "unusually high" in results["DE_LU"].title
    assert "unusually low" in results["FR"].title


def test_price_spike_delta_floor_suppresses_micro_variance(db_session):
    """60±2 € then 70 € is many σ but only 10 € — not a spike anyone trades."""
    from backend.signals.detectors.power import detect_price_spikes

    _seed_prices(db_session, base=60.0, last=70.0)
    assert detect_price_spikes(db_session) == []


# ─── hydro_deviation ──────────────────────────────────────────────────────────


def _seed_hydro(db, zone="NO2", years=4, level=50e6, last=None):
    """Weekly points; one point per year lands exactly at newest − i·364d so the
    same-week band finds them."""
    import time as _time

    from backend.power.hourly_store import upsert_hourly

    newest = (int(_time.time()) // 3600) * 3600
    points = []
    for i in range(years, 0, -1):
        points.append((newest - i * 364 * 86_400, level + i * 1e6))
    points.append((newest, last if last is not None else level))
    upsert_hourly(db, "hydro.reservoir", zone, points, unit="MWh")


def test_hydro_deviation_fires_below_band(db_session):
    from backend.signals.detectors.power import detect_hydro_deviations

    _seed_hydro(db_session, last=30e6)  # far below every prior year
    results = detect_hydro_deviations(db_session)
    assert len(results) == 1
    r = results[0]
    assert r.rule == "hydro_deviation" and r.zone == "NO2"
    assert "below" in r.title
    assert r.max_age_days == 16


def test_hydro_deviation_within_band_is_silent(db_session):
    from backend.signals.detectors.power import detect_hydro_deviations

    _seed_hydro(db_session, last=52e6)  # inside the prior-year range
    assert detect_hydro_deviations(db_session) == []


def test_hydro_deviation_needs_three_band_years(db_session):
    from backend.signals.detectors.power import detect_hydro_deviations

    _seed_hydro(db_session, years=2, last=30e6)
    assert detect_hydro_deviations(db_session) == []


def test_hydro_max_age_override_survives_runner(db_session):
    """A 10-day-old weekly hydro point must survive run_all_detectors — the
    per-vertical 'power: 3' window would wrongly suppress it without the
    per-result max_age_days override."""
    from datetime import date, timedelta

    from backend.models.alerts import Alert
    from backend.signals.detectors import run_all_detectors

    _seed_hydro(db_session, last=30e6)
    run_all_detectors(db_session, today=date.today() + timedelta(days=10))
    rules = {a.rule for a in db_session.query(Alert).all()}
    assert "hydro_deviation" in rules


# ─── record_break ─────────────────────────────────────────────────────────────


def _seed_record(db, zone="DE_LU", series="price.dayahead", coverage_days=400, fresh=True):
    import time as _time

    from backend.models.energy import PowerRecord
    from backend.power.hourly_store import upsert_hourly

    now = int(_time.time())
    upsert_hourly(db, series, zone, [
        (now - coverage_days * 86_400, 50.0),
        (now - 3 * 86_400, 60.0),
    ], unit="EUR/MWh")
    ts = now - (2 * 86_400 if fresh else 30 * 86_400)
    db.add(PowerRecord(series_key=series, zone=zone, kind="max",
                       value=871.0, ts_utc=ts, unit="EUR/MWh"))
    db.commit()


def test_record_break_pings_once_per_zone(db_session):
    from backend.signals.detectors.power import detect_record_breaks

    _seed_record(db_session)
    results = detect_record_breaks(db_session)
    assert len(results) == 1
    r = results[0]
    assert r.rule == "record_break" and r.severity == "info"
    assert "871" in r.detail and "day-ahead hour" in r.detail


def test_record_break_requires_a_year_of_coverage(db_session):
    """A 'record' over 3 months of history describes our coverage, not the grid."""
    from backend.signals.detectors.power import detect_record_breaks

    _seed_record(db_session, coverage_days=100)
    assert detect_record_breaks(db_session) == []


def test_record_break_ignores_old_records(db_session):
    from backend.signals.detectors.power import detect_record_breaks

    _seed_record(db_session, fresh=False)
    assert detect_record_breaks(db_session) == []
