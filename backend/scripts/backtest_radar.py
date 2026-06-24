"""Descriptive backtest of the anomaly radar over the full historical record.

Posture B: we do NOT measure prediction skill (no IC/hit-rate). We measure whether the
detectors are GOOD as a descriptive radar, on three axes:
  - PRECISION  — when they fire, is it a real deviation? (old flat-threshold vs new baseline)
  - RECALL     — do they catch the obvious real events? (ground-truth spot-checks)
  - CALIBRATION— fire frequency + severity mix over time (not too noisy, not silent)

Read-only. Replays each detector over the persisted per-day series. For detectors whose
logic changed in Phase A/B, it runs BOTH the old and new decision rule on the same data to
quantify the noise reduction (and confirm real spikes are still caught).

Run on the VPS as the obsyd user:  python -m backend.scripts.backtest_radar
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import timedelta

from backend.database import SessionLocal
from backend.models.analytics import DaysOfSupplyHistory, FreightProxyHistory, SupplyDemandBalance
from backend.models.energy import PowerGrid, PowerPriceDaily
from backend.models.gas import GasBalance
from backend.models.sentiment import SentimentScore
from backend.models.thermal import ThermalHotspot
from backend.models.vessels import FloatingStorageEvent, GeofenceEvent


def _month(d: str) -> str:
    return d[:7]


def _zscore(cur, hist, min_n=14):
    if len(hist) < min_n:
        return None
    m = statistics.fmean(hist)
    s = statistics.pstdev(hist)
    if s == 0:
        return None
    return (cur - m) / s, m


def hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


# ─────────────────────────────────────────────────────────────────────────────
def precision_old_vs_new(db):
    hr("PRECISION — old flat-threshold vs new baseline (fire-events over full history)")

    # FLOATING STORAGE: reconstruct daily active count per zone, apply both rules per day.
    rows = db.query(FloatingStorageEvent.zone, FloatingStorageEvent.first_seen, FloatingStorageEvent.last_seen).all()
    byz = defaultdict(list)
    for z, fs, ls in rows:
        byz[z or "global"].append((fs.date(), ls.date()))
    if rows:
        anchor = max(ls for _, _, ls in rows).date()
        start = min(fs for _, fs, _ in rows).date()
        span = (anchor - start).days
        old_f = new_f = 0
        per_zone = {}
        for z, evs in byz.items():
            o = n = 0
            counts = []
            for off in range(span + 1):
                d = anchor - timedelta(days=off)
                counts.append(sum(1 for fs, ls in evs if fs <= d <= ls))
            for i, c in enumerate(counts):
                if c >= 3:  # OLD flat rule
                    o += 1
                base = counts[i + 1 : i + 91]  # trailing 90d (older days)
                zs = _zscore(c, base)
                if zs and zs[1] >= 2.0 and zs[0] >= 2.0:  # NEW baseline rule
                    n += 1
            old_f += o
            new_f += n
            per_zone[z] = (o, n)
        print(f"floating_storage: OLD fired {old_f} zone-days, NEW {new_f} zone-days  (over ~{span}d)")
        for z, (o, n) in sorted(per_zone.items(), key=lambda x: -x[1][0]):
            print(f"    {z:9} old={o:4}  new={n:4}   ({'STRUCTURAL HUB → old over-fired' if o > 5 * (n + 1) else 'ok'})")

    # NEGATIVE PRICES per zone
    for zone in [z for (z,) in db.query(PowerPriceDaily.zone).distinct().all()]:
        prows = db.query(PowerPriceDaily.negative_hours).filter(PowerPriceDaily.zone == zone).order_by(PowerPriceDaily.date).all()
        series = [r[0] for r in prows]
        o = n = 0
        for i, c in enumerate(series):
            if c >= 1:  # OLD: any negative hour fired (info+)
                o += 1
            if c >= 3:
                base = series[max(0, i - 45) : i]
                zs = _zscore(c, base)
                if zs and zs[0] >= 2.0:  # NEW
                    n += 1
        print(f"negative_prices[{zone}]: OLD fired {o} days, NEW {n} days  (of {len(series)})")

    # SENTIMENT
    srows = db.query(SentimentScore.risk_score).order_by(SentimentScore.date).all()
    s = [r[0] for r in srows]
    o = n = 0
    for i, v in enumerate(s):
        if v >= 6:  # OLD absolute
            o += 1
        if v >= 8 or (_zscore(v, s[max(0, i - 30) : i]) or (0, 0))[0] >= 2.0 and v >= 6:  # NEW
            n += 1
    print(f"sentiment_risk: OLD fired {o} days, NEW {n} days  (of {len(s)})")

    # THERMAL per refinery (reconstruct daily nearby counts → old any≥1 vs new z-score)
    from backend.collectors.firms import PROXIMITY_KM, REFINERIES, _haversine_km
    for ref in REFINERIES:
        trows = db.query(ThermalHotspot.latitude, ThermalHotspot.longitude, ThermalHotspot.acq_date).filter(
            ThermalHotspot.area_name == ref["area"]
        ).all()
        byd = defaultdict(int)
        for la, lo, d in trows:
            if d and _haversine_km(ref["lat"], ref["lon"], la, lo) <= PROXIMITY_KM:
                byd[d] += 1
        if not byd:
            continue
        days = sorted(byd)
        o = n = 0
        for i, d in enumerate(days):
            c = byd[d]
            if c >= 1:  # OLD: any nearby hotspot → warning
                o += 1
            if c >= 2:
                base = [byd[x] for x in days[:i]][-45:]
                zs = _zscore(c, base)
                if zs and zs[0] >= 2.0:  # NEW
                    n += 1
        print(f"refinery_thermal[{ref['name'][:22]:22}]: OLD fired {o} days, NEW {n} days  (of {len(days)})")


# ─────────────────────────────────────────────────────────────────────────────
def calibration(db):
    hr("CALIBRATION — fire frequency + severity mix (NEW logic, per detector)")

    # gas_balance from persisted flags
    g = db.query(GasBalance.date, GasBalance.flag).filter(GasBalance.flag.isnot(None)).all()
    fires = [(d, f.split(":")[0]) for d, f in g if f and f.split(":")[0] in ("WATCH", "SIGNAL")]
    sev = Counter("critical" if lvl == "SIGNAL" else "warning" for _, lvl in fires)
    months = sorted({_month(d) for d, _ in fires})
    print(f"gas_balance: {len(fires)} fire-days  sev={dict(sev)}  across {len(months)} months")

    # days_of_supply / supply_demand / freight (persisted categorical)
    dos = db.query(DaysOfSupplyHistory.assessment).all()
    print(f"days_of_supply assessments: {dict(Counter(a for (a,) in dos if a))}")
    sd = db.query(SupplyDemandBalance.divergence_type).all()
    print(f"supply_demand divergence_type: {dict(Counter(t for (t,) in sd if t))}")
    fp = db.query(FreightProxyHistory.divergence_flag).all()
    print(f"freight divergence_flag: {dict(Counter(f for (f,) in fp if f))}")

    # dunkelflaute seasonality (renewable_share<15% per day)
    pg = db.query(PowerGrid.date, PowerGrid.load_mw, PowerGrid.wind_mw, PowerGrid.solar_mw).filter(PowerGrid.zone == "DE_LU").all()
    dunkel = [d for d, load, w, s in pg if load and load > 0 and ((w or 0) + (s or 0)) / load < 0.15]
    print(f"dunkelflaute[DE_LU]: {len(dunkel)} days of {len(pg)}  by-month={dict(Counter(_month(d) for d in dunkel))}")


# ─────────────────────────────────────────────────────────────────────────────
def recall_ground_truth(db):
    hr("RECALL — ground-truth spot-checks")

    # Rerouting during the (ongoing) Red Sea disruption: monthly Cape-share state.
    try:
        from backend.signals.tonnage_proxy import REROUTING_ELEVATED, REROUTING_HIGH, compute_rerouting_index
        data = compute_rerouting_index(days=400)
        if data.get("available"):
            hist = data.get("history", [])
            bym = defaultdict(list)
            for h in hist:
                bym[_month(h["date"])].append(h["ratio"])
            print(f"rerouting Cape-share monthly avg (HIGH>={REROUTING_HIGH:.0%}, ELEV>={REROUTING_ELEVATED:.0%}):")
            for m in sorted(bym):
                avg = statistics.fmean(bym[m])
                tag = "HIGH" if avg >= REROUTING_HIGH else "elev" if avg >= REROUTING_ELEVATED else "norm"
                print(f"    {m}: {avg:5.0%}  {tag}")
        else:
            print("rerouting: no data")
    except Exception as e:
        print(f"rerouting check failed: {e}")

    # Gas WATCH/SIGNAL dates — eyeball clustering.
    g = db.query(GasBalance.date, GasBalance.flag, GasBalance.z_score).filter(GasBalance.flag.isnot(None)).order_by(GasBalance.date).all()
    flagged = [(d, f, z) for d, f, z in g if f and f.split(":")[0] in ("WATCH", "SIGNAL")]
    print(f"\ngas_balance flagged days ({len(flagged)} total), most extreme:")
    for d, f, z in sorted(flagged, key=lambda x: -abs(x[2] or 0))[:8]:
        print(f"    {d}  z={z:+.2f}  {f}")


def flow_anomaly_old_vs_new(db):
    hr("FLOW_ANOMALY — old 7d-zscore (every anomalous day) vs new 30d-baseline + onset")
    zones = [z for (z,) in db.query(GeofenceEvent.zone).distinct().all()]
    tot_old = tot_new = 0
    for zone in zones:
        rows = db.query(GeofenceEvent.tanker_count).filter(GeofenceEvent.zone == zone).order_by(GeofenceEvent.date).all()
        counts = [r[0] for r in rows]  # oldest first
        old = new = 0
        anom = []
        for i in range(len(counts)):
            zo = _zscore(counts[i], counts[max(0, i - 7) : i], min_n=7)
            if zo and abs(zo[0]) >= 2.0:
                old += 1
            zn = _zscore(counts[i], counts[max(0, i - 30) : i], min_n=16)
            a = bool(zn and abs(zn[0]) >= 2.0)
            anom.append(a)
            if a and (i == 0 or not anom[i - 1]):  # onset only
                new += 1
        tot_old += old
        tot_new += new
        print(f"  {zone:9} old={old:4}  new(onset)={new:4}   (of {len(counts)} days)")
    print(f"  TOTAL     old={tot_old:4}  new={tot_new:4}")


def sharpening_onset_check(db):
    hr("SHARPENING — persistent fire-days vs onset events (the 3 chatty detectors)")

    rows = db.query(DaysOfSupplyHistory.date, DaysOfSupplyHistory.assessment).order_by(DaysOfSupplyHistory.date).all()
    tight = sum(1 for _, a in rows if a == "TIGHT")
    onset = sum(1 for i, (_, a) in enumerate(rows) if a == "TIGHT" and (i == 0 or rows[i - 1][1] != "TIGHT"))
    print(f"days_of_supply: TIGHT on {tight} readings → {onset} onset events (old fired all {tight})")

    def dv(t):
        return bool(t and "DIVERGENCE" in t)

    rows = db.query(SupplyDemandBalance.date, SupplyDemandBalance.divergence_type).order_by(SupplyDemandBalance.date).all()
    days = sum(1 for _, t in rows if dv(t))
    onset = sum(1 for i, (_, t) in enumerate(rows) if dv(t) and (i == 0 or not dv(rows[i - 1][1])))
    print(f"supply_demand: DIVERGENCE on {days} readings → {onset} onset events (old fired all {days})")

    rows = db.query(FreightProxyHistory.date, FreightProxyHistory.divergence_flag).order_by(FreightProxyHistory.date).all()
    days = sum(1 for _, f in rows if f)
    onset = sum(1 for i, (_, f) in enumerate(rows) if f and (i == 0 or rows[i - 1][1] != f))
    print(f"freight_divergence: flagged on {days} readings → {onset} onset events (old fired all {days}; of {len(rows)} rows)")


def main():
    db = SessionLocal()
    try:
        precision_old_vs_new(db)
        flow_anomaly_old_vs_new(db)
        sharpening_onset_check(db)
        calibration(db)
        recall_ground_truth(db)
    finally:
        db.close()
    print("\n" + "=" * 78)


if __name__ == "__main__":
    main()
