"""
Market Intelligence Report — narrative text generated from live signal data.

Assembles a 4-paragraph report:
  1. Catalyst — what triggered the current situation
  2. Physical — what's happening in physical oil markets
  3. Market — what market pricing tells us
  4. Key Risk — what to watch next

Template rotation: each CAUSAL_CHAINS key has 5 variants, selected
by date-seeded random.choice() so the same report renders within a day
but a different variant appears the next day.

Scheduled: every 2 hours (alongside disruption score).
Cached: 2 hours in-memory.
"""

import asyncio
import json
import logging
import random
import re
import time
from datetime import date, datetime, timedelta, timezone

from backend.database import SessionLocal
from backend.models.analytics import (
    DisruptionScoreHistory,
    EIAPredictionHistory,
    TonneMilesHistory,
)
from backend.models.pro_features import CrackSpreadHistory
from backend.models.sentiment import SentimentScore
from backend.models.vessels import FloatingStorageEvent, VesselRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
_report_cache: dict | None = None
_report_cache_ts: float = 0.0
_report_lock = asyncio.Lock()
CACHE_TTL = 7200  # 2 hours

# ---------------------------------------------------------------------------
# Template rotation
# ---------------------------------------------------------------------------

CAUSAL_CHAINS = {
    ("hormuz_critical", "none"): [
        "Strait of Hormuz transit has collapsed {pct}% to {vessels} vessels — effectively closed since {disruption_name}. This represents approximately {oil_pct}% of global seaborne oil supply at risk.",
        "Physical flows through the Strait of Hormuz have plummeted {pct}% to just {vessels} vessels, rendering the chokepoint effectively shut since {disruption_name}. Roughly {oil_pct}% of global seaborne crude is directly affected.",
        "Hormuz shipping activity has contracted {pct}% to {vessels} vessels — a near-total halt since {disruption_name}. An estimated {oil_pct}% of the world's seaborne oil supply transits this corridor.",
        "The Strait of Hormuz is effectively closed. Transit volumes have fallen {pct}% to {vessels} vessels following {disruption_name}, putting approximately {oil_pct}% of global seaborne oil flows at risk.",
        "Only {vessels} vessels transited the Strait of Hormuz — a {pct}% collapse since {disruption_name}. The chokepoint normally handles roughly {oil_pct}% of global seaborne oil.",
    ],
    ("chokepoint_drop", "rerouting_high"): [
        "The {pct}% drop in {zone} transit is driving rerouting via Cape of Good Hope, now at {cape_share}% of combined Suez-Cape traffic.",
        "With {zone} transit down {pct}%, vessels are diverting around Africa — Cape of Good Hope now handles {cape_share}% of combined corridor traffic.",
        "The {pct}% contraction in {zone} flows has pushed Cape rerouting to {cape_share}%, as operators avoid Middle East and Red Sea corridors.",
        "{zone} volumes down {pct}% are forcing a sustained shift to Cape routing, which now accounts for {cape_share}% of combined traffic.",
        "Rerouting via the Cape of Good Hope has reached {cape_share}% as {zone} transit remains suppressed by {pct}%.",
    ],
    ("rerouting_high", "tonne_miles_elevated"): [
        "Elevated rerouting is extending average voyage distances to {avg_distance}nm, binding fleet capacity and pushing the Tonne-Miles Index to {tm_index}.",
        "The shift to Cape routing extends average voyage distances to {avg_distance}nm. The Tonne-Miles Index at {tm_index} reflects the additional fleet capacity now tied up in longer transits.",
        "Average voyage distances have stretched to {avg_distance}nm due to rerouting, lifting the Tonne-Miles Index to {tm_index} and tightening available tonnage.",
        "Longer Cape transits ({avg_distance}nm average) are absorbing fleet capacity — the Tonne-Miles Index at {tm_index} signals tightening tonnage supply.",
        "Fleet capacity is being consumed by extended routing. Average distances at {avg_distance}nm have driven the Tonne-Miles Index to {tm_index}.",
    ],
    ("rerouting_high", "tonne_miles_normal"): [
        "Despite {cape_share}% rerouting, the Tonne-Miles Index remains near baseline at {tm_index} — suggesting fewer vessels are moving overall, offsetting longer routes.",
        "The Tonne-Miles Index at {tm_index} has not spiked despite {cape_share}% Cape rerouting, indicating that reduced vessel activity is compensating for the added distance.",
        "Rerouting at {cape_share}% has not lifted the Tonne-Miles Index ({tm_index}) above baseline — a sign that overall fleet movement has slowed rather than redistributed.",
        "At {tm_index}, the Tonne-Miles Index remains flat despite elevated rerouting ({cape_share}%). Fewer vessels appear to be sailing, neutralizing the distance increase.",
        "Cape routing at {cape_share}% would normally spike tonne-miles, but the index at {tm_index} suggests fleet-wide activity has contracted, absorbing the longer routes.",
    ],
    ("crack_spread_high", "backwardation"): [
        "Crack spreads at the {percentile}th percentile (${crack_value}/bbl) combined with {spread_pct}% backwardation indicate the market is pricing near-term product supply tightness.",
        "The 3-2-1 crack at ${crack_value}/bbl ({percentile}th percentile) alongside {spread_pct}% backwardation points to acute near-term tightness in refined product markets.",
        "Refinery margins at ${crack_value}/bbl ({percentile}th percentile vs 1Y) and {spread_pct}% backwardation signal that the market expects immediate product supply stress.",
        "Near-term supply anxiety is visible in both crack spreads (${crack_value}/bbl, {percentile}th percentile) and the {spread_pct}% backwardation structure.",
        "Product markets are under pressure — cracks at ${crack_value}/bbl ({percentile}th percentile) and {spread_pct}% backwardation reflect acute near-term tightness.",
    ],
    ("crack_spread_high", "no_backwardation"): [
        "Crack spreads at the {percentile}th percentile (${crack_value}/bbl) signal elevated refinery margins, though the flat futures curve suggests the market expects the disruption to be temporary.",
        "Refinery margins remain elevated at ${crack_value}/bbl ({percentile}th percentile), but the absence of backwardation implies the market sees current supply stress as short-lived.",
        "The 3-2-1 crack at ${crack_value}/bbl ({percentile}th percentile) reflects strong refinery economics. However, a flat futures curve indicates limited concern about sustained tightness.",
        "Crack spreads at ${crack_value}/bbl ({percentile}th percentile) are elevated, yet the flat curve suggests traders expect normalization rather than prolonged disruption.",
        "Despite cracks at the {percentile}th percentile (${crack_value}/bbl), the flat futures structure signals that the market is not yet pricing a sustained supply crisis.",
    ],
    ("floating_storage_high", "any"): [
        "{fs_count} tankers detected in floating storage ({vlcc_count} VLCCs), indicating excess supply seeking deferred delivery or lack of discharge options.",
        "Floating storage has risen to {fs_count} vessels ({vlcc_count} VLCCs) — a sign of either contango-driven storage economics or congestion at discharge ports.",
        "{fs_count} vessels ({vlcc_count} VLCCs) are holding cargo at sea, pointing to oversupply conditions or blocked discharge routes.",
        "The floating storage count has reached {fs_count} ({vlcc_count} VLCCs), suggesting cargo is being held at sea due to either weak demand or logistical bottlenecks.",
        "With {fs_count} tankers in floating storage ({vlcc_count} VLCCs), the market is showing signs of excess supply or discharge constraints.",
    ],
    ("floating_storage_zero", "chokepoint_drop"): [
        "No floating storage detected — tankers appear to be anchoring near disrupted corridors rather than storing cargo at sea.",
        "Floating storage remains at zero despite the chokepoint disruption — vessels are holding position near affected waterways rather than seeking open-water storage.",
        "The absence of floating storage suggests tankers are anchoring near disrupted corridors awaiting passage, not storing crude speculatively.",
        "Zero floating storage despite major chokepoint disruption indicates vessels are clustering near affected straits rather than converting to at-sea storage.",
        "No floating storage activity detected. Tankers appear to be waiting near disrupted chokepoints for passage rather than repositioning as floating tanks.",
    ],
    ("houston_high", "eia_build"): [
        "Houston zone tanker count at {houston_count} ({houston_change} vs 30d avg) with {anchored_pct}% anchored suggests elevated import arrivals. AIS data points to a likely inventory build in this week's EIA report.",
        "Elevated tanker presence in Houston ({houston_count} vessels, {houston_change} vs 30d avg) with {anchored_pct}% at anchor indicates heavy import activity — consistent with an EIA inventory build.",
        "AIS data shows {houston_count} tankers in the Houston zone ({houston_change} vs 30d avg), with {anchored_pct}% anchored. This pattern historically correlates with rising crude inventories.",
        "Gulf Coast tanker activity is elevated at {houston_count} vessels ({houston_change} vs 30d avg). The high anchored ratio ({anchored_pct}%) suggests arrivals awaiting discharge — pointing to an EIA build.",
        "Houston tanker count at {houston_count} ({houston_change} vs avg), {anchored_pct}% anchored. Arrival patterns suggest import-driven inventory accumulation ahead of this week's EIA release.",
    ],
    ("houston_low", "eia_draw"): [
        "Below-average Houston tanker activity ({houston_count}, {houston_change} vs 30d avg) suggests reduced crude arrivals. AIS data points to a likely inventory draw.",
        "Houston zone tanker count has dropped to {houston_count} ({houston_change} vs 30d avg), indicating lower import volumes — consistent with an EIA inventory draw.",
        "AIS shows reduced Gulf Coast tanker presence ({houston_count} vessels, {houston_change} vs avg), suggesting lighter import flows and a probable EIA draw.",
        "Tanker activity in Houston is below average at {houston_count} ({houston_change} vs 30d). Reduced arrivals typically precede inventory drawdowns in EIA data.",
        "Gulf Coast vessel count at {houston_count} ({houston_change} vs average) points to softer crude imports, supporting a draw in this week's EIA report.",
    ],
    ("sentiment_negative", "disruption"): [
        "GDELT news sentiment at {risk_score}/10, dominated by {top_topics}, reinforcing the physical supply disruption signals.",
        "News sentiment remains negative ({risk_score}/10) with coverage focused on {top_topics}, consistent with the physical supply stress visible in AIS data.",
        "Media tone at {risk_score}/10 (topics: {top_topics}) aligns with the physical disruption — newsflow is reinforcing rather than diverging from market signals.",
        "GDELT risk score at {risk_score}/10 reflects heavy coverage of {top_topics}, supporting the disruption narrative visible in physical flow data.",
        "Sentiment at {risk_score}/10, driven by {top_topics} coverage, is directionally consistent with the chokepoint and rerouting signals.",
    ],
}

# Divergence templates
DIVERGENCE_TEMPLATES = [
    "Despite the physical supply disruption, {commodity} declined {change}%, suggesting demand-side concerns or expectations of diplomatic resolution are currently outweighing supply risk in market pricing.",
    "A notable divergence has emerged: physical supply signals remain stressed, yet {commodity} fell {change}%. The market may be pricing in demand destruction, strategic reserve releases, or a faster-than-expected resolution.",
    "Physical flow data points to acute supply stress, but {commodity} dropped {change}% — indicating that macro headwinds or anticipated policy responses are dominating the supply disruption narrative.",
    "{commodity} declined {change}% despite severe physical supply disruption, a divergence that typically resolves in one of two ways: either prices catch up to the physical reality, or the disruption proves shorter than the physical data implies.",
    "The {change}% drop in {commodity} contrasts sharply with physical supply indicators. This price-physical divergence suggests the market is weighting demand-side risks or policy intervention above the observable supply disruption.",
]

INVERSE_DIVERGENCE_TEMPLATES = [
    "{commodity} gained {change}% despite limited physical supply stress (Disruption Score: {score}/100), suggesting speculative positioning or anticipatory buying ahead of potential future disruptions.",
    "Prices are running ahead of fundamentals — {commodity} rose {change}% while physical flow data shows relatively normal conditions (Disruption Score: {score}/100).",
    "The {change}% rally in {commodity} is not supported by current physical supply data (Disruption Score: {score}/100). The move may reflect geopolitical risk premium or positioning ahead of scheduled events.",
    "{commodity} climbed {change}% with the Disruption Score at only {score}/100, pointing to a sentiment-driven rally rather than fundamental supply tightness.",
    "Physical data remains benign (Disruption Score: {score}/100) but {commodity} rose {change}%, suggesting the market is front-running a risk scenario not yet visible in shipping flows.",
]


def pick_template(key: tuple) -> str:
    """Pick a template variant seeded by today's date + key for daily consistency."""
    random.seed(date.today().isoformat() + str(key))
    return random.choice(CAUSAL_CHAINS[key])  # nosec B311


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------


def _gather_data(db) -> dict:
    """Collect all signal data from DB and signal modules."""
    data = {
        "market_prices": {"brent": None, "wti": None},
        "disruption_score": None,
        "tonne_miles": None,
        "eia_prediction": None,
        "floating_storage": {"active_count": 0, "vlcc_count": 0},
        "crack_spreads": None,
        "market_structure": None,
        "rerouting": {"cape_share": 0, "state": "normal"},
        "sentiment": None,
        "chokepoints": [],
    }

    # 1. Disruption score (latest)
    ds = (
        db.query(DisruptionScoreHistory)
        .order_by(DisruptionScoreHistory.date.desc(), DisruptionScoreHistory.id.desc())
        .first()
    )
    if ds:
        data["disruption_score"] = {
            "composite": ds.composite_score,
            "hormuz": ds.hormuz_component,
            "cape": ds.cape_component,
            "storage": ds.storage_component,
            "crack": ds.crack_component,
            "backwardation": ds.backwardation_component,
            "sentiment": ds.sentiment_component,
        }

    # 2. Tonne-miles (latest)
    tm = db.query(TonneMilesHistory).order_by(TonneMilesHistory.date.desc()).first()
    if tm:
        data["tonne_miles"] = {
            "index": tm.tonne_miles_index,
            "raw": tm.tonne_miles_raw,
            "cape_share": round((tm.cape_share or 0) * 100, 1),
            "avg_distance": tm.avg_distance,
        }
        data["rerouting"]["cape_share"] = round((tm.cape_share or 0) * 100, 1)
        if (tm.cape_share or 0) > 0.40:
            data["rerouting"]["state"] = "high"
        elif (tm.cape_share or 0) > 0.30:
            data["rerouting"]["state"] = "elevated"

    # 3. EIA prediction (latest)
    eia = db.query(EIAPredictionHistory).order_by(EIAPredictionHistory.date.desc()).first()
    if eia:
        change_pct = None
        if eia.tanker_count_30d_avg and eia.tanker_count_30d_avg > 0:
            change_pct = round((eia.tanker_count - eia.tanker_count_30d_avg) / eia.tanker_count_30d_avg * 100, 1)
        data["eia_prediction"] = {
            "prediction": eia.prediction,
            "tanker_count": eia.tanker_count,
            "tanker_count_30d_avg": eia.tanker_count_30d_avg,
            "change_pct": change_pct,
            "anchored_ratio": eia.anchored_ratio,
        }

    # 4. Floating storage
    fs_events = db.query(FloatingStorageEvent).filter(FloatingStorageEvent.status == "active").all()
    vlcc_count = 0
    for ev in fs_events:
        # Check if vessel is VLCC via registry
        if ev.mmsi:
            reg = db.query(VesselRegistry).filter(VesselRegistry.mmsi == ev.mmsi).first()
            if reg and reg.ship_class == "VLCC":
                vlcc_count += 1
    data["floating_storage"] = {
        "active_count": len(fs_events),
        "vlcc_count": vlcc_count,
    }

    # 5. Crack spreads (from DB history — avoids calling yfinance in report)
    crack = db.query(CrackSpreadHistory).order_by(CrackSpreadHistory.date.desc()).first()
    if crack:
        # Compute percentile from last 365 days
        rows = (
            db.query(CrackSpreadHistory.three_two_one_crack).order_by(CrackSpreadHistory.date.desc()).limit(365).all()
        )
        values = sorted(r[0] for r in rows if r[0] is not None)
        percentile = 0
        if values and len(values) >= 30:
            rank = sum(1 for v in values if v <= crack.three_two_one_crack)
            percentile = int(rank / len(values) * 100)
        data["crack_spreads"] = {
            "value": crack.three_two_one_crack,
            "percentile_1y": percentile,
        }

    # 6. Market structure (from disruption score backwardation component)
    # We read the raw spread from the market_structure signal
    try:
        from backend.signals.market_structure import _fetch_structure

        structure = _fetch_structure()
        if structure and "BRENT" in structure.get("curves", {}):
            brent_curve = structure["curves"]["BRENT"]
            data["market_structure"] = {
                "spread_pct": brent_curve.get("spread_pct", 0),
                "structure": brent_curve.get("structure", "flat"),
            }
    except Exception as e:
        logger.debug("Market structure fetch failed: %s", e)

    # 7. Sentiment
    sent = db.query(SentimentScore).order_by(SentimentScore.created_at.desc()).first()
    if sent:
        factors = []
        if sent.risk_factors:
            try:
                factors = json.loads(sent.risk_factors) if isinstance(sent.risk_factors, str) else sent.risk_factors
            except (json.JSONDecodeError, TypeError):
                pass
        data["sentiment"] = {
            "risk_score": sent.risk_score,
            "risk_factors": factors[:3] if factors else [],
        }

    # 8. Chokepoint anomalies (from historical_lookup)
    try:
        from backend.signals.historical_lookup import find_anomalies

        for cp in ("hormuz", "suez", "malacca", "bab_el_mandeb"):
            result = find_anomalies(cp, threshold_pct=40.0)
            current = result.get("current", {})
            drop = current.get("drop_pct", 0)
            if drop < -25:
                severity = "CRITICAL" if drop < -50 else "WARNING"
                # Find disruption context
                anomalies = result.get("anomalies", [])
                context = ""
                if anomalies:
                    last = anomalies[-1]
                    ctx_list = last.get("disruption_context", [])
                    raw = ctx_list[0] if ctx_list else ""
                    # Strip internal PortWatch type tags like "(OT)"
                    context = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip()
                data["chokepoints"].append(
                    {
                        "zone": result.get("chokepoint", cp),
                        "zone_short": cp,
                        "drop_pct": abs(round(drop, 0)),
                        "vessels": current.get("n_total", 0),
                        "severity": severity,
                        "disruption_name": context or "recent events",
                    }
                )
    except Exception as e:
        logger.debug("Chokepoint anomaly check failed: %s", e)

    # 9. Market prices (from yfinance cache — don't call live)
    try:
        from backend.providers.yfinance_provider import _price_cache

        if _price_cache:
            prices = _price_cache.get("prices", {})
            if "BRENT" in prices:
                b = prices["BRENT"]
                data["market_prices"]["brent"] = {
                    "price": b.get("current", 0),
                    "change_pct": b.get("change_pct", 0),
                }
            if "WTI" in prices:
                w = prices["WTI"]
                data["market_prices"]["wti"] = {
                    "price": w.get("current", 0),
                    "change_pct": w.get("change_pct", 0),
                }
    except Exception as e:
        logger.debug("Price cache read failed: %s", e)

    return data


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


def _build_signal_list(data: dict) -> list[dict]:
    """Identify active signals from gathered data, sorted by severity."""
    signals = []

    # Chokepoint disruptions
    for cp in data["chokepoints"]:
        if cp["drop_pct"] >= 80 and cp["zone_short"] == "hormuz":
            signals.append(
                {
                    "key": ("hormuz_critical", "none"),
                    "severity": "CRITICAL",
                    "topic": "chokepoint",
                    "params": {
                        "pct": int(cp["drop_pct"]),
                        "vessels": cp["vessels"],
                        "disruption_name": cp["disruption_name"],
                        "oil_pct": 21,  # Hormuz handles ~21% of global seaborne oil
                    },
                }
            )
        elif cp["drop_pct"] >= 25:
            cape_share = data["rerouting"]["cape_share"]
            if cape_share > 30:
                signals.append(
                    {
                        "key": ("chokepoint_drop", "rerouting_high"),
                        "severity": "HIGH",
                        "topic": "chokepoint",
                        "params": {
                            "pct": int(cp["drop_pct"]),
                            "zone": cp["zone"],
                            "cape_share": round(cape_share, 1),
                        },
                    }
                )

    # Rerouting + tonne-miles
    cape_share = data["rerouting"]["cape_share"]
    tm = data.get("tonne_miles")
    if cape_share > 30 and tm:
        if tm["index"] > 110:
            signals.append(
                {
                    "key": ("rerouting_high", "tonne_miles_elevated"),
                    "severity": "HIGH",
                    "topic": "physical",
                    "params": {
                        "avg_distance": int(tm["avg_distance"] or 0),
                        "tm_index": round(tm["index"], 1),
                    },
                }
            )
        else:
            signals.append(
                {
                    "key": ("rerouting_high", "tonne_miles_normal"),
                    "severity": "MEDIUM",
                    "topic": "physical",
                    "params": {
                        "cape_share": round(cape_share, 1),
                        "tm_index": round(tm["index"], 1),
                    },
                }
            )

    # Floating storage
    fs = data["floating_storage"]
    if fs["active_count"] > 3:
        signals.append(
            {
                "key": ("floating_storage_high", "any"),
                "severity": "MEDIUM",
                "topic": "physical",
                "params": {
                    "fs_count": fs["active_count"],
                    "vlcc_count": fs["vlcc_count"],
                },
            }
        )
    elif fs["active_count"] == 0 and data["chokepoints"]:
        signals.append(
            {
                "key": ("floating_storage_zero", "chokepoint_drop"),
                "severity": "LOW",
                "topic": "physical",
                "params": {},
            }
        )

    # Crack spreads + market structure
    crack = data.get("crack_spreads")
    ms = data.get("market_structure")
    if crack and crack["percentile_1y"] > 75:
        if ms and ms["spread_pct"] < 0:
            signals.append(
                {
                    "key": ("crack_spread_high", "backwardation"),
                    "severity": "HIGH",
                    "topic": "market",
                    "params": {
                        "percentile": crack["percentile_1y"],
                        "crack_value": round(crack["value"], 2),
                        "spread_pct": round(abs(ms["spread_pct"]), 1),
                    },
                }
            )
        else:
            signals.append(
                {
                    "key": ("crack_spread_high", "no_backwardation"),
                    "severity": "MEDIUM",
                    "topic": "market",
                    "params": {
                        "percentile": crack["percentile_1y"],
                        "crack_value": round(crack["value"], 2),
                    },
                }
            )

    # EIA prediction
    eia = data.get("eia_prediction")
    if eia:
        change_pct = eia.get("change_pct")
        if change_pct is not None:
            change_str = f"{change_pct:+.0f}%" if change_pct != 0 else "flat"
        else:
            change_str = "N/A"
        anchored_pct = round((eia.get("anchored_ratio") or 0) * 100, 1)

        if eia["prediction"] == "BUILD" and (change_pct or 0) > 0:
            signals.append(
                {
                    "key": ("houston_high", "eia_build"),
                    "severity": "MEDIUM",
                    "topic": "physical",
                    "params": {
                        "houston_count": eia["tanker_count"],
                        "houston_change": change_str,
                        "anchored_pct": anchored_pct,
                    },
                }
            )
        elif eia["prediction"] == "DRAW" and (change_pct or 0) < 0:
            signals.append(
                {
                    "key": ("houston_low", "eia_draw"),
                    "severity": "MEDIUM",
                    "topic": "physical",
                    "params": {
                        "houston_count": eia["tanker_count"],
                        "houston_change": change_str,
                        "anchored_pct": anchored_pct,
                    },
                }
            )

    # Sentiment
    sent = data.get("sentiment")
    if sent and sent["risk_score"] >= 6 and data["chokepoints"]:
        topics_str = ", ".join(sent["risk_factors"][:2]) if sent["risk_factors"] else "geopolitical risk"
        signals.append(
            {
                "key": ("sentiment_negative", "disruption"),
                "severity": "LOW",
                "topic": "sentiment",
                "params": {
                    "risk_score": sent["risk_score"],
                    "top_topics": topics_str,
                },
            }
        )

    # Sort by severity
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    signals.sort(key=lambda s: severity_order.get(s["severity"], 99))
    return signals


def build_report(data: dict | None = None) -> dict:
    """Build the 4-paragraph market intelligence report.

    Returns dict with paragraphs, metadata, and severity level.
    """
    db = SessionLocal()
    try:
        if data is None:
            data = _gather_data(db)

        signals = _build_signal_list(data)

        if not signals:
            return {
                "available": True,
                "severity": "LOW",
                "title": "Market Conditions Normal",
                "paragraphs": [
                    "No significant supply disruptions detected across monitored chokepoints. All transit corridors are operating within normal parameters.",
                    "Physical flow indicators — floating storage, rerouting ratios, and tonne-miles — are within baseline ranges. Market structure and refinery margins are not signaling acute supply stress at this time.",
                    "Key risk: monitor for emerging signals. Next EIA inventory report provides the near-term data catalyst.",
                ],
                "signals_active": 0,
                "disruption_score": data.get("disruption_score", {}).get("composite"),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

        # Determine overall severity
        top_severity = signals[0]["severity"]

        # Build title from top signal
        ds = data.get("disruption_score", {})
        composite = ds.get("composite", 0) if ds else 0
        if composite >= 75:
            title = "CRITICAL — Severe Supply Disruption"
        elif composite >= 50:
            title = "HIGH — Elevated Supply Stress"
        elif composite >= 25:
            title = "MODERATE — Supply Disruption Developing"
        else:
            title = "LOW — Minor Supply Signals"

        # ---------------------------------------------------------------
        # Paragraph 1: Catalyst — most severe chokepoint signal
        # ---------------------------------------------------------------
        para1_parts = []
        for s in signals:
            if s["topic"] == "chokepoint":
                template = pick_template(s["key"])
                para1_parts.append(template.format(**s["params"]))
                break

        if not para1_parts:
            # No chokepoint signal — use disruption score summary
            if composite > 0:
                para1_parts.append(
                    f"The Supply Disruption Index stands at {composite:.0f}/100, "
                    f"reflecting {'elevated' if composite >= 50 else 'moderate'} supply chain stress "
                    f"across monitored chokepoints and market indicators."
                )

        # ---------------------------------------------------------------
        # Paragraph 2: Physical — rerouting, tonne-miles, storage, EIA
        # ---------------------------------------------------------------
        para2_parts = []
        for s in signals:
            if s["topic"] == "physical":
                template = pick_template(s["key"])
                para2_parts.append(template.format(**s["params"]))

        if not para2_parts:
            tm = data.get("tonne_miles")
            if tm:
                para2_parts.append(
                    f"The Tonne-Miles Index is at {tm['index']:.0f} with average distances "
                    f"of {int(tm['avg_distance'] or 0)}nm, within normal operating range."
                )

        # ---------------------------------------------------------------
        # Paragraph 3: Market — cracks, backwardation, prices, divergence
        # ---------------------------------------------------------------
        para3_parts = []
        for s in signals:
            if s["topic"] == "market":
                template = pick_template(s["key"])
                para3_parts.append(template.format(**s["params"]))

        # Divergence detection
        physical_bullish = (
            any(s["severity"] == "CRITICAL" and s["topic"] == "chokepoint" for s in signals)
            or data["rerouting"]["cape_share"] > 30
            or (data.get("crack_spreads", {}).get("percentile_1y", 0) > 80)
        )

        brent = data["market_prices"].get("brent")
        wti = data["market_prices"].get("wti")

        price_falling = (brent and brent["change_pct"] < -3) or (wti and wti["change_pct"] < -3)

        if physical_bullish and price_falling:
            random.seed(date.today().isoformat() + "divergence")
            brent_change = brent["change_pct"] if brent else (wti["change_pct"] if wti else 0)
            commodity = "Brent" if brent and brent["change_pct"] < -3 else "WTI"
            divergence_text = random.choice(DIVERGENCE_TEMPLATES).format(  # nosec B311
                commodity=commodity,
                change=round(abs(brent_change), 1),
            )
            para3_parts.insert(0, divergence_text)

        # Inverse divergence: physical bearish but price rising
        physical_bearish = data["floating_storage"]["active_count"] > 5 or (composite < 20)

        price_rising = (brent and brent["change_pct"] > 3) or (wti and wti["change_pct"] > 3)

        if physical_bearish and price_rising:
            random.seed(date.today().isoformat() + "inverse_divergence")
            brent_change = brent["change_pct"] if brent else (wti["change_pct"] if wti else 0)
            commodity = "Brent" if brent and brent["change_pct"] > 3 else "WTI"
            inverse_text = random.choice(INVERSE_DIVERGENCE_TEMPLATES).format(  # nosec B311
                commodity=commodity,
                change=round(abs(brent_change), 1),
                score=round(composite, 0),
            )
            para3_parts.insert(0, inverse_text)

        if not para3_parts:
            crack = data.get("crack_spreads")
            if crack:
                para3_parts.append(
                    f"The 3-2-1 crack spread at ${crack['value']:.2f}/bbl "
                    f"({crack['percentile_1y']}th percentile vs 1Y) "
                    f"reflects {'tight' if crack['percentile_1y'] > 60 else 'balanced'} refinery margins."
                )

        # ---------------------------------------------------------------
        # Final sentence: always starts with "Key risk:"
        # ---------------------------------------------------------------
        key_risk_parts = []

        # Sentiment context
        for s in signals:
            if s["topic"] == "sentiment":
                template = pick_template(s["key"])
                key_risk_parts.append(template.format(**s["params"]))

        # Upcoming EIA
        now = datetime.now(timezone.utc)
        days_until_wed = (2 - now.weekday()) % 7
        if days_until_wed == 0 and now.hour >= 16:
            days_until_wed = 7
        next_eia = (now + timedelta(days=days_until_wed)).strftime("%A %B %d")

        eia = data.get("eia_prediction")
        if eia and eia["prediction"] != "NEUTRAL":
            key_risk_parts.append(
                f"Next EIA release ({next_eia}): AIS-based model signals a likely {eia['prediction']}."
            )

        if not key_risk_parts:
            key_risk_parts.append(
                f"monitor chokepoint transit volumes and rerouting patterns "
                f"for confirmation of signal persistence. Next EIA release: {next_eia}."
            )

        # Prepend "Key risk:" to the assembled sentence
        key_risk_text = "Key risk: " + " ".join(key_risk_parts)

        # Assemble paragraphs (filter empty) + key risk sentence
        paragraphs = [
            " ".join(para1_parts) if para1_parts else None,
            " ".join(para2_parts) if para2_parts else None,
            " ".join(para3_parts) if para3_parts else None,
        ]
        paragraphs = [p for p in paragraphs if p]
        paragraphs.append(key_risk_text)

        return {
            "available": True,
            "severity": top_severity,
            "title": title,
            "paragraphs": paragraphs,
            "signals_active": len(signals),
            "disruption_score": composite,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        db.close()


async def get_market_report() -> dict:
    """Get cached market intelligence report. Rebuilds every 2 hours."""
    global _report_cache, _report_cache_ts

    now = time.monotonic()
    if _report_cache and (now - _report_cache_ts) < CACHE_TTL:
        return _report_cache

    async with _report_lock:
        now = time.monotonic()
        if _report_cache and (now - _report_cache_ts) < CACHE_TTL:
            return _report_cache

        report = build_report()
        _report_cache = report
        _report_cache_ts = now
        return report
