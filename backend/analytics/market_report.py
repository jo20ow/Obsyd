"""
Market Intelligence Report v2 — Deep Analysis Engine.

5-section narrative report:
  1. CATALYST — what is driving the market right now
  2. HISTORICAL CONTEXT — comparison with past events
  3. PHYSICAL FLOWS + CONTRADICTIONS — what's happening and what doesn't add up
  4. MARKET IMPLICATIONS + SECTOR — pricing, spreads, equities
  5. OUTLOOK + KEY RISKS — trajectory, conditions, what to watch

Template rotation: date-seeded random for daily consistency.
Cached: 30 minutes with smart invalidation.
"""

import asyncio
import json
import logging
import random
import re
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func

from backend.database import SessionLocal
from backend.models.analytics import (
    DaysOfSupplyHistory,
    DisruptionScoreHistory,
    EIAPredictionHistory,
    FreightProxyHistory,
    MarketReport,
    SupplyDemandBalance,
    TonneMilesHistory,
)
from backend.models.pro_features import CrackSpreadHistory, EquitySnapshot
from backend.models.sentiment import SentimentScore
from backend.models.vessels import FloatingStorageEvent, GeofenceEvent, VesselRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
_report_cache: dict | None = None
_report_cache_ts: float = 0.0
_report_lock = asyncio.Lock()
CACHE_TTL = 1800  # 30 minutes

# ---------------------------------------------------------------------------
# CAUSAL_CHAINS — 5 variants per key, date-seeded rotation
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
    ("freight_proxy_divergence", "rerouting_high"): [
        "Tanker equities have declined over 5 days despite Cape rerouting at {cape_share}% — financial markets may be pricing in resolution ahead of physical normalization.",
        "A divergence between tanker stocks and physical data: equities falling while Cape rerouting holds at {cape_share}%, suggesting the market expects a shorter disruption than the shipping data implies.",
        "Financial markets are leading bearish: tanker equities down despite {cape_share}% Cape rerouting. Historically, this divergence resolves either through physical normalization or equity rebound.",
        "Despite {cape_share}% of traffic rerouting via Cape, tanker equities have weakened — the market appears to discount the duration of the current disruption.",
        "The tanker equity proxy has fallen while Cape rerouting remains at {cape_share}%, a financial-physical divergence that warrants monitoring for resolution direction.",
    ],
    ("eia_ais_divergence", "any"): [
        "Real-time AIS data from Houston ({houston_count} vessels, {houston_change} vs avg) diverges from EIA baseline expectations — a potential surprise in this week's report.",
        "EIA and AIS signals are diverging: Houston tanker activity at {houston_count} vessels ({houston_change} vs avg) does not match the official outlook, suggesting a possible inventory surprise.",
        "Houston AIS data ({houston_count} vessels, {houston_change} vs avg) contradicts EIA expectations. These divergences have historically preceded inventory surprises.",
        "A notable gap between EIA forecasts and real-time AIS: Houston zone at {houston_count} vessels ({houston_change} vs average), pointing to a potential mismatch in this week's inventory data.",
        "AIS vessel tracking shows {houston_count} tankers in Houston ({houston_change} vs avg), diverging from EIA assumptions — watch for an inventory surprise on Wednesday.",
    ],
    ("days_of_supply_tight", "disruption"): [
        "US commercial crude stocks provide {days} days of supply at current consumption rates, {deviation:.1f} days below the 5-year seasonal average — a buffer that shrinks further if the current disruption persists.",
        "At {days} days of supply ({deviation:.1f} days below seasonal norm), US crude inventory coverage is tight. Extended chokepoint disruptions would further compress this already thin cushion.",
        "Inventory coverage at {days} days ({deviation:.1f} days below 5Y avg) leaves limited margin for sustained supply disruption. The 4-week trend of {trend:+.1f} days adds to the tightening pressure.",
        "US crude stocks cover {days} days of consumption, {deviation:.1f} days below seasonal average. Combined with the ongoing supply disruption, the inventory buffer is uncomfortably thin.",
        "Days of supply at {days} ({deviation:.1f} vs 5Y seasonal) signals tightening fundamentals that could amplify price response to any further supply shock.",
    ],
    ("days_of_supply_comfortable", "any"): [
        "US days of supply at {days} days ({deviation:+.1f} vs 5Y avg) provides a near-term cushion against the ongoing chokepoint disruption.",
        "Inventory coverage at {days} days of supply ({deviation:+.1f} vs seasonal norm) gives the market some buffer against current supply chain stress.",
        "At {days} days of supply, US crude inventories sit {deviation:+.1f} days above the 5-year average, providing a degree of insulation from the current disruption.",
        "US commercial days of supply ({days} days, {deviation:+.1f} vs seasonal) remains comfortable, limiting the urgency of the physical supply disruption.",
        "With {days} days of inventory coverage ({deviation:+.1f} vs 5Y avg), the US has adequate near-term supply despite the ongoing chokepoint stress.",
    ],
}

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


# ---------------------------------------------------------------------------
# Report Generator
# ---------------------------------------------------------------------------


class MarketReportGenerator:
    """Builds 5-section market intelligence reports from live signal data."""

    def __init__(self, db_session):
        self.db = db_session
        self.today = date.today()
        random.seed(self.today.isoformat())

    # === PUBLIC ===

    def generate(self) -> dict:
        """Main entry: collect data, rank signals, build 5 sections."""
        data = self._collect_all_data()
        signals = self._rank_signals(data)

        sections = {}

        # Section 1: CATALYST (always present)
        sections["catalyst"] = self._build_catalyst(signals, data)

        # Section 2: HISTORICAL CONTEXT
        try:
            sections["historical"] = self._build_historical_context(signals, data)
        except Exception as e:
            logger.warning("Historical context skipped: %s", e)
            sections["historical"] = None

        # Section 3: PHYSICAL FLOWS + CONTRADICTIONS
        try:
            sections["physical"] = self._build_physical_analysis(signals, data)
        except Exception as e:
            logger.warning("Physical analysis skipped: %s", e)
            sections["physical"] = None

        # Section 4: MARKET + SECTOR
        try:
            sections["market"] = self._build_market_sector(signals, data)
        except Exception as e:
            logger.warning("Market analysis skipped: %s", e)
            sections["market"] = None

        # Section 5: OUTLOOK + RISKS
        try:
            sections["outlook"] = self._build_outlook(signals, data)
        except Exception as e:
            logger.warning("Outlook skipped: %s", e)
            sections["outlook"] = None

        # Headlines for free-user teaser
        headlines = {}
        for key, text in sections.items():
            if text:
                first_sentence = text.split(". ")[0] + "."
                if len(first_sentence) > 200:
                    # Truncate at last complete sentence within 200 chars
                    truncated = first_sentence[:200]
                    last_dot = truncated.rfind(".")
                    if last_dot > 20:
                        first_sentence = truncated[: last_dot + 1]
                    else:
                        # No good sentence boundary — use first 200 chars with ellipsis
                        last_space = truncated.rfind(" ")
                        first_sentence = truncated[:last_space] + "..." if last_space > 20 else truncated + "..."
                headlines[key] = first_sentence

        # Full report
        full_paragraphs = [text for text in sections.values() if text]
        full_report = "\n\n".join(full_paragraphs)

        # Severity + title
        ds = data.get("disruption_score") or {}
        composite = ds.get("composite", 0)
        if composite >= 75:
            title = "CRITICAL — Severe Supply Disruption"
            severity = "CRITICAL"
        elif composite >= 50:
            title = "HIGH — Elevated Supply Stress"
            severity = "HIGH"
        elif composite >= 25:
            title = "MODERATE — Supply Disruption Developing"
            severity = "MODERATE"
        else:
            title = "LOW — Minor Supply Signals"
            severity = "LOW"

        sections_available = [k for k, v in sections.items() if v]

        # Historical events count
        hist_events = data.get("historical_events", [])

        result = {
            "available": True,
            "severity": severity,
            "title": title,
            "full_report": full_report,
            "catalyst": sections.get("catalyst", ""),
            "sections": sections,
            "headlines": headlines,
            "sections_available": sections_available,
            "signals_count": len(signals),
            "historical_events_compared": len(hist_events),
            "disruption_score": composite,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Persist to DB
        try:
            self.db.add(
                MarketReport(
                    date=self.today.isoformat(),
                    full_report=full_report,
                    sections_json=json.dumps(sections, default=str),
                    headlines_json=json.dumps(headlines, default=str),
                    signals_count=len(signals),
                    disruption_score=composite,
                )
            )
            self.db.commit()
        except Exception as e:
            logger.debug("Report persistence failed: %s", e)
            self.db.rollback()

        return result

    # === SECTION 1: CATALYST ===

    def _build_catalyst(self, signals, data):
        """What is the dominant signal driving the market?"""
        parts = []

        # Disruption score intro when elevated
        ds = data.get("disruption_score") or {}
        composite = ds.get("composite", 0)
        if composite > 50:
            level = "HIGH" if composite < 75 else "CRITICAL"
            parts.append(
                self._pick(
                    [
                        f"The OBSYD Supply Disruption Index stands at {composite:.0f}/100 ({level}), reflecting convergent stress across multiple indicators.",
                        f"Multiple disruption signals are converging — the Supply Disruption Index at {composite:.0f}/100 ({level}) reflects simultaneous stress across chokepoints, pricing, and fleet behavior.",
                        f"At {composite:.0f}/100, the Supply Disruption Index is in {level} territory, driven by concurrent anomalies across several monitored dimensions.",
                    ]
                )
            )

        # Lead chokepoint signal
        for s in signals:
            if s["topic"] == "chokepoint":
                parts.append(self._pick_chain(s["key"]).format(**s["params"]))
                break

        if not parts:
            if composite > 0:
                parts.append(
                    f"The Supply Disruption Index stands at {composite:.0f}/100, "
                    f"reflecting {'elevated' if composite >= 50 else 'moderate'} supply chain stress "
                    f"across monitored chokepoints and market indicators."
                )
            else:
                return "No significant signals detected across monitored chokepoints and market indicators."

        return " ".join(parts)

    # === SECTION 2: HISTORICAL CONTEXT ===

    def _build_historical_context(self, signals, data):
        """Compare current situation with past events from anomaly data."""
        events = data.get("historical_events", [])
        if not events:
            return None

        # Current disruption
        chokepoints = data.get("chokepoints", [])
        if not chokepoints:
            return None

        current = chokepoints[0]  # Most severe
        current_drop = current["drop_pct"]
        parts = []

        # Find events at the same chokepoint
        same_cp = [e for e in events if not e.get("ongoing")]
        severe = [e for e in same_cp if abs(e.get("max_drop_pct", 0)) > 40]

        if same_cp:
            # Find closest precedent by severity
            same_cp.sort(key=lambda e: abs(e.get("max_drop_pct", 0)), reverse=True)
            closest = same_cp[0]
            closest_drop = abs(closest.get("max_drop_pct", 0))
            if closest.get("disruption_context"):
                closest_name = closest["disruption_context"][0]
                closest_name = re.sub(r"\s*\([^)]*\)\s*$", "", closest_name).strip()
            else:
                cp_name = closest.get("chokepoint", "").replace("_", " ").title()
                start = closest.get("start_date", "")
                if start and cp_name:
                    try:
                        dt = datetime.strptime(start, "%Y-%m-%d")
                        closest_name = f"the {cp_name} disruption of {dt.strftime('%b %Y')}"
                    except ValueError:
                        closest_name = f"a prior {cp_name} event"
                else:
                    closest_name = f"a prior {cp_name} event" if cp_name else "a prior event"
            b7 = closest.get("brent_change_7d_pct")
            b30 = closest.get("brent_change_30d_pct")

            if current_drop > closest_drop:
                parts.append(
                    self._pick(
                        [
                            f"The current {current_drop:.0f}% disruption is the most severe in the dataset. The closest precedent is {closest_name} ({closest_drop:.0f}% drop), which saw Brent move {b7:+.1f}% over 7 days and {b30:+.1f}% over 30 days."
                            if b7 is not None and b30 is not None
                            else f"The current {current_drop:.0f}% disruption exceeds every recorded event in the dataset. The closest comparison is {closest_name} at {closest_drop:.0f}%.",
                            f"No historical precedent matches the current {current_drop:.0f}% disruption in severity. The nearest comparison — {closest_name} at {closest_drop:.0f}% — resulted in a {b7:+.1f}% Brent move over 7 days."
                            if b7 is not None
                            else f"At {current_drop:.0f}%, this disruption exceeds every prior event in the dataset, including {closest_name} ({closest_drop:.0f}%).",
                            f"This {current_drop:.0f}% contraction exceeds every recorded event. During the most comparable episode ({closest_name}, {closest_drop:.0f}%), Brent moved {b7:+.1f}% in the first week."
                            if b7 is not None
                            else f"The current {current_drop:.0f}% contraction is unprecedented in the dataset. The closest event was {closest_name} at {closest_drop:.0f}%.",
                        ]
                    )
                )
            elif b7 is not None:
                parts.append(
                    self._pick(
                        [
                            f"The current disruption ({current_drop:.0f}%) is comparable to {closest_name} ({closest_drop:.0f}%), during which Brent moved {b7:+.1f}% over 7 days.",
                            f"Historical precedent: {closest_name} saw a similar {closest_drop:.0f}% drop, with Brent reacting {b7:+.1f}% (7d){f' and {b30:+.1f}% (30d)' if b30 is not None else ''}.",
                        ]
                    )
                )

        # Average price reaction across severe events
        if len(severe) >= 3:
            impacts_7d = [e["brent_change_7d_pct"] for e in severe if e.get("brent_change_7d_pct") is not None]
            impacts_30d = [e["brent_change_30d_pct"] for e in severe if e.get("brent_change_30d_pct") is not None]
            if len(impacts_7d) >= 3:
                avg_7d = sum(impacts_7d) / len(impacts_7d)
                avg_30d = sum(impacts_30d) / len(impacts_30d) if impacts_30d else 0
                parts.append(
                    self._pick(
                        [
                            f"Across {len(impacts_7d)} severe disruptions (>40% drop) in the dataset, the average Brent reaction was {avg_7d:+.1f}% at 7 days and {avg_30d:+.1f}% at 30 days.",
                            f"Historical average for severe disruptions (>40% drop, n={len(impacts_7d)}): Brent {avg_7d:+.1f}% after 7 days, {avg_30d:+.1f}% after 30 days.",
                            f"The dataset contains {len(impacts_7d)} comparable severe events. Mean Brent response: {avg_7d:+.1f}% (7d), {avg_30d:+.1f}% (30d).",
                        ]
                    )
                )

                # If mean impact is near zero, add contextual interpretation
                if abs(avg_7d) < 1.5 and abs(avg_30d) < 1.5:
                    parts.append(
                        "Historically, severe transit disruptions have had limited sustained "
                        "impact on Brent prices, suggesting the market tends to price in "
                        "resolution quickly."
                    )

        # Current Brent vs historical
        brent = data["market_prices"].get("brent")
        if brent and same_cp and same_cp[0].get("brent_at_start"):
            hist_price = same_cp[0]["brent_at_start"]
            parts.append(
                self._pick(
                    [
                        f"Brent currently trades at ${brent['price']:.2f}, compared to ${hist_price:.2f} during the most comparable prior event.",
                        f"For context, Brent was at ${hist_price:.2f} during the closest historical precedent — it now stands at ${brent['price']:.2f}.",
                    ]
                )
            )

        return " ".join(parts) if parts else None

    # === SECTION 3: PHYSICAL FLOWS + CONTRADICTIONS ===

    def _build_physical_analysis(self, signals, data):
        """Physical flow analysis with explicit contradiction flagging."""
        parts = []
        contradictions = []

        rerouting = data["rerouting"]
        tm = data.get("tonne_miles") or {}
        cape_share = rerouting["cape_share"]
        tm_index = tm.get("index", 100)

        # Rerouting + tonne-miles
        if cape_share > 30:
            if tm_index > 110:
                parts.append(
                    self._pick_chain(("rerouting_high", "tonne_miles_elevated")).format(
                        avg_distance=int(tm.get("avg_distance") or 0),
                        tm_index=round(tm_index, 1),
                    )
                )
            else:
                parts.append(
                    self._pick_chain(("rerouting_high", "tonne_miles_normal")).format(
                        cape_share=round(cape_share, 1),
                        tm_index=round(tm_index, 1),
                    )
                )
                # Contradiction: high rerouting but flat tonne-miles
                contradictions.append(
                    self._pick(
                        [
                            f"This is a notable divergence: {cape_share:.0f}% Cape rerouting would normally spike tonne-miles demand, but the index at {tm_index:.0f} suggests fleet-wide activity has contracted. Fewer ships are sailing, not just sailing further.",
                            f"The flat Tonne-Miles Index ({tm_index:.0f}) despite {cape_share:.0f}% rerouting reveals an important dynamic: the disruption has reduced overall shipping volume, not merely redirected it.",
                            f"A {cape_share:.0f}% Cape share should elevate tonne-miles significantly. That the index remains at {tm_index:.0f} implies the fleet is shrinking its active footprint — vessels are anchoring, not rerouting.",
                        ]
                    )
                )

        # Floating storage contradictions
        fs = data["floating_storage"]
        has_critical = any(s["severity"] == "CRITICAL" and s["topic"] == "chokepoint" for s in signals)

        if fs["active_count"] == 0 and has_critical:
            contradictions.append(
                self._pick(
                    [
                        "Floating storage remains at zero despite a major chokepoint closure — a departure from the 2020 pattern where contango-driven storage peaked at over 100 VLCCs. The current zero reading suggests tankers are waiting for passage rather than seeking storage economics, indicating the market expects eventual reopening.",
                        "The absence of floating storage during a chokepoint crisis is unusual. In previous disruptions, vessels unable to discharge typically converted to floating storage within 10-14 days. The current zero count implies operators still expect the disruption to resolve relatively quickly.",
                        "Zero floating storage alongside a near-total chokepoint shutdown is a contrarian signal. Either tankers are anchoring at origin rather than loading, or the market anticipates a resolution before storage economics become attractive.",
                    ]
                )
            )
        elif fs["active_count"] > 3:
            parts.append(
                self._pick_chain(("floating_storage_high", "any")).format(
                    fs_count=fs["active_count"],
                    vlcc_count=fs["vlcc_count"],
                )
            )

        # Zone trends (7-day)
        zone_trends = self._get_zone_trends_7d()
        if zone_trends:
            down = [z for z, pct in zone_trends.items() if pct < -10]
            up = [z for z, pct in zone_trends.items() if pct > 10]

            if down:
                zones_str = ", ".join(z.replace("geofence_", "").title() for z in down)
                parts.append(
                    self._pick(
                        [
                            f"Over the past 7 days, vessel activity has declined notably in {zones_str}, reinforcing the physical contraction visible in aggregate data.",
                            f"7-day trend shows continued decline in {zones_str} zone activity, consistent with sustained disruption rather than normalization.",
                            f"Week-over-week, {zones_str} traffic continues to deteriorate — no signs of recovery in physical flow data.",
                        ]
                    )
                )
            if up:
                # Separate disrupted chokepoints from normal zones
                disrupted_map = {cp["zone_short"]: cp for cp in data.get("chokepoints", [])}
                up_disrupted = [z for z in up if z.replace("geofence_", "") in disrupted_map]
                up_normal = [z for z in up if z not in up_disrupted]

                for z in up_disrupted:
                    zone_key = z.replace("geofence_", "")
                    zone_label = zone_key.title()
                    cp = disrupted_map[zone_key]
                    parts.append(
                        self._pick(
                            [
                                f"7-day trend shows a marginal uptick in {zone_label} from near-zero levels, though transit remains {cp['drop_pct']:.0f}% below baseline.",
                                f"While {zone_label} shows a slight recovery over 7 days, this is from a near-zero base — transit is still {cp['drop_pct']:.0f}% below normal.",
                            ]
                        )
                    )

                if up_normal:
                    zones_str = ", ".join(z.replace("geofence_", "").title() for z in up_normal)
                    parts.append(
                        self._pick(
                            [
                                f"In contrast, {zones_str} vessel activity has increased over 7 days, potentially absorbing some redirected traffic.",
                                f"7-day data shows rising activity in {zones_str}, suggesting partial traffic redistribution.",
                            ]
                        )
                    )

        # Append contradictions
        parts.extend(contradictions)

        return " ".join(parts) if parts else None

    # === SECTION 4: MARKET + SECTOR ===

    def _build_market_sector(self, signals, data):
        """Financial signals + equity sector implications."""
        parts = []

        # Price-physical divergence
        divergence = self._check_divergence(signals, data)
        if divergence:
            parts.append(divergence)

        # Crack spreads + futures curve
        cs = data.get("crack_spreads") or {}
        ms = data.get("market_structure") or {}
        percentile = cs.get("percentile_1y", 0)

        if percentile > 75:
            if ms.get("spread_pct", 0) < 0:
                parts.append(
                    self._pick_chain(("crack_spread_high", "backwardation")).format(
                        percentile=percentile,
                        crack_value=round(cs["value"], 2),
                        spread_pct=round(abs(ms["spread_pct"]), 1),
                    )
                )
            else:
                parts.append(
                    self._pick_chain(("crack_spread_high", "no_backwardation")).format(
                        percentile=percentile,
                        crack_value=round(cs["value"], 2),
                    )
                )

        # Crack spread trend (30d)
        vs_30d = cs.get("vs_30d_pct")
        if vs_30d is not None:
            if vs_30d > 15:
                parts.append(
                    self._pick(
                        [
                            f"Crack spreads have widened {vs_30d:.0f}% over 30 days, indicating accelerating refinery margin pressure.",
                            f"The {vs_30d:.0f}% expansion in cracks over 30 days suggests the product market is tightening faster than crude.",
                        ]
                    )
                )
            elif vs_30d < -15:
                parts.append(
                    self._pick(
                        [
                            f"Despite elevated absolute levels, cracks have narrowed {abs(vs_30d):.0f}% over 30 days — the margin expansion may be plateauing.",
                            f"Cracks have contracted {abs(vs_30d):.0f}% from their 30-day peak, suggesting the market is beginning to price in demand response.",
                        ]
                    )
                )

        # Equity sector highlights
        eq = self._get_equity_highlights()
        if eq:
            tanker_avg = eq.get("tanker_avg")
            if tanker_avg is not None and abs(tanker_avg) > 2:
                if tanker_avg > 0:
                    parts.append(
                        self._pick(
                            [
                                f"Tanker equities are responding — the sector averaged {tanker_avg:+.1f}% today, consistent with expectations of elevated freight rates during sustained rerouting.",
                                f"The tanker sector ({tanker_avg:+.1f}% avg) is pricing in higher day rates as Cape rerouting extends voyage durations and binds fleet capacity.",
                            ]
                        )
                    )
                else:
                    parts.append(
                        self._pick(
                            [
                                f"Tanker equities declined {tanker_avg:.1f}% on average despite physical supply disruption — the market may be pricing demand destruction over freight rate upside.",
                                f"The tanker sector fell {abs(tanker_avg):.1f}% on average, a bearish read that suggests investors see demand risk outweighing the freight rate tailwind from rerouting.",
                            ]
                        )
                    )

            top = eq.get("top")
            if top and abs(top["change_pct"]) > 3:
                parts.append(
                    self._pick(
                        [
                            f"Notable mover: {top['ticker']} {top['change_pct']:+.1f}% ({top['name']}).",
                            f"{top['ticker']} led the energy complex at {top['change_pct']:+.1f}%.",
                        ]
                    )
                )

            hc = eq.get("highest_corr")
            if hc and hc["corr"] > 0.7:
                parts.append(
                    self._pick(
                        [
                            f"Highest WTI correlation: {hc['ticker']} (r={hc['corr']:.2f} over 30d) — the most price-sensitive equity in the current environment.",
                            f"{hc['ticker']} maintains the strongest WTI linkage (r={hc['corr']:.2f}, 30d), making it the most direct equity proxy for crude price moves.",
                        ]
                    )
                )

        # EIA prediction
        eia = data.get("eia_prediction") or {}
        if eia.get("prediction") and eia["prediction"] != "NEUTRAL":
            change_pct = eia.get("change_pct")
            anchored_pct = round((eia.get("anchored_ratio") or 0) * 100, 1)
            houston_count = eia.get("tanker_count", 0)

            if change_pct is not None:
                change_str = f"{change_pct:+.0f}%" if change_pct != 0 else "flat"
                key = ("houston_high", "eia_build") if eia["prediction"] == "BUILD" else ("houston_low", "eia_draw")
                parts.append(
                    self._pick_chain(key).format(
                        houston_count=houston_count,
                        houston_change=change_str,
                        anchored_pct=anchored_pct,
                    )
                )
            else:
                pred_word = eia["prediction"].lower()
                parts.append(
                    self._pick(
                        [
                            f"AIS shows {houston_count} tankers in the Houston zone with {anchored_pct}% anchored, consistent with an EIA inventory {pred_word}.",
                            f"Gulf Coast tanker count at {houston_count} with {anchored_pct}% anchored points to a probable EIA {pred_word}.",
                            f"AIS data shows {houston_count} tankers near Houston, suggesting {'reduced import flows and ' if pred_word == 'draw' else ''}a probable EIA {pred_word}.",
                        ]
                    )
                )

            hit_rate = eia.get("hit_rate")
            total = eia.get("total_predictions", 0)
            if hit_rate and total >= 8:
                parts.append(
                    self._pick(
                        [
                            f"The AIS-based model has called {hit_rate:.0f}% of EIA outcomes correctly over {total} weeks — a directional indicator, not a precise forecast.",
                            f"Historical accuracy: {hit_rate:.0f}% over {total} weeks. Useful as a directional signal, not a volume prediction.",
                        ]
                    )
                )

        # Freight proxy divergence
        fp = data.get("freight_proxy") or {}
        if fp.get("divergence") == "FREIGHT_PROXY_DIVERGENCE":
            cape_share = data["rerouting"]["cape_share"]
            parts.append(
                self._pick_chain(("freight_proxy_divergence", "rerouting_high")).format(
                    cape_share=round(cape_share, 0),
                )
            )

        # Supply-demand balance
        sd = data.get("supply_demand") or {}
        if sd.get("balance") is not None:
            balance = sd["balance"]
            if abs(balance) > 0.3:
                direction = "surplus" if balance > 0 else "deficit"
                parts.append(
                    self._pick(
                        [
                            f"Global supply-demand balance at {balance:+.1f} mb/d ({direction.upper()}) according to EIA STEO.",
                            f"EIA STEO projects a {abs(balance):.1f} mb/d {direction}, providing {'bearish' if balance > 0 else 'bullish'} macro context.",
                        ]
                    )
                )
        if sd.get("divergence_type") == "EIA_AIS_DIVERGENCE" and sd.get("houston_count"):
            houston_dev = sd.get("houston_deviation", 0)
            change_str = f"{houston_dev:+.0f}% vs avg" if houston_dev else ""
            parts.append(
                self._pick_chain(("eia_ais_divergence", "any")).format(
                    houston_count=sd["houston_count"],
                    houston_change=change_str,
                )
            )

        # Days of supply
        dos = data.get("days_of_supply") or {}
        if dos.get("days") and dos.get("deviation") is not None:
            has_disruption = any(s["severity"] in ("CRITICAL", "HIGH") for s in signals)
            if dos["assessment"] == "TIGHT" and has_disruption:
                parts.append(
                    self._pick_chain(("days_of_supply_tight", "disruption")).format(
                        days=dos["days"],
                        deviation=dos["deviation"],
                        trend=dos.get("trend_4w", 0),
                    )
                )
            elif dos["assessment"] == "COMFORTABLE":
                parts.append(
                    self._pick_chain(("days_of_supply_comfortable", "any")).format(
                        days=dos["days"],
                        deviation=dos["deviation"],
                    )
                )

        return " ".join(parts) if parts else None

    # === SECTION 5: OUTLOOK + KEY RISKS ===

    def _build_outlook(self, signals, data):
        """Forward-looking observations and key risks."""
        parts = []
        risks = []

        rerouting = data["rerouting"]
        fs = data["floating_storage"]
        cape_share = rerouting["cape_share"]

        # Disruption duration
        duration_days = data.get("disruption_duration_days")
        if duration_days and duration_days > 0:
            parts.append(
                self._pick(
                    [
                        f"The current disruption has persisted for {duration_days} days with no signs of normalization in physical flow data.",
                        f"Day {duration_days} of the disruption — transit data shows no recovery trend.",
                        f"{duration_days} days into the disruption, physical indicators continue to show stress rather than stabilization.",
                    ]
                )
            )

            # Duration-based risks
            if duration_days > 7 and fs["active_count"] == 0:
                risks.append(
                    self._pick(
                        [
                            "If the disruption extends beyond 14 days, floating storage accumulation becomes likely as tankers exhaust discharge options. The current zero floating storage count is unlikely to persist.",
                            "Extended closure beyond two weeks typically triggers floating storage buildup. With zero floating storage currently, this transition would represent a significant shift in fleet behavior.",
                            f"Historical patterns suggest floating storage begins accumulating 10-14 days into a major chokepoint closure. At day {duration_days} with zero storage, the fleet is approaching that threshold.",
                        ]
                    )
                )

            if duration_days > 3 and cape_share > 35:
                risks.append(
                    self._pick(
                        [
                            f"Sustained Cape rerouting at {cape_share:.0f}% adds 10-15 days to Middle East-Europe voyages. If maintained for 30+ days, the effective global tanker fleet shrinks by an estimated 5-8%, structurally tightening the freight market.",
                            f"Every week of {cape_share:.0f}% Cape routing absorbs additional fleet capacity. Beyond 30 days, the compounding effect on available tonnage could push tanker day rates significantly higher.",
                        ]
                    )
                )

        # Sentiment risk
        sent = data.get("sentiment") or {}
        if sent.get("risk_score", 0) > 6:
            risks.append(
                self._pick(
                    [
                        f"GDELT risk score at {sent['risk_score']:.0f}/10 — elevated media negativity can amplify price volatility independent of physical fundamentals.",
                        f"News sentiment at {sent['risk_score']:.0f}/10 adds a feedback risk: negative media coverage can accelerate positioning changes beyond what physical data warrants.",
                    ]
                )
            )

        # EIA upcoming
        now = datetime.now(timezone.utc)
        days_until_wed = (2 - now.weekday()) % 7
        if days_until_wed == 0 and now.hour >= 16:
            days_until_wed = 7
        next_eia = (now + timedelta(days=days_until_wed)).strftime("%A %B %d")

        eia = data.get("eia_prediction") or {}
        if eia.get("prediction") and eia["prediction"] != "NEUTRAL":
            parts.append(f"Next EIA release ({next_eia}): AIS-based model signals a likely {eia['prediction']}.")

        # Build "Key risk:" line
        if risks:
            parts.append("Key risk: " + risks[0])
            if len(risks) > 1:
                parts.append("Also watching: " + risks[1])
        else:
            parts.append(
                self._pick(
                    [
                        "Key risk: duration — the market is pricing a short disruption, but physical data shows no normalization. The gap between market expectation and physical reality typically resolves with a sharp move.",
                        "Key risk: the longer physical disruption persists without price fully reflecting it, the sharper the eventual adjustment tends to be.",
                    ]
                )
            )

        return " ".join(parts) if parts else None

    # === HELPERS ===

    def _pick(self, templates):  # nosec B311
        """Pick a template variant using the date-seeded RNG."""
        return templates[random.randint(0, len(templates) - 1)]  # nosec B311

    def _pick_chain(self, key):
        """Pick from CAUSAL_CHAINS using date-seeded RNG."""
        if key not in CAUSAL_CHAINS:
            return ""
        return self._pick(CAUSAL_CHAINS[key])

    def _collect_all_data(self) -> dict:
        """Gather all signal data from DB and signal modules."""
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
            "historical_events": [],
            "disruption_duration_days": None,
            "freight_proxy": None,
            "supply_demand": None,
            "days_of_supply": None,
        }

        # 1. Disruption score
        ds = (
            self.db.query(DisruptionScoreHistory)
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

        # 2. Tonne-miles
        tm = self.db.query(TonneMilesHistory).order_by(TonneMilesHistory.date.desc()).first()
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

        # 3. EIA prediction
        eia = self.db.query(EIAPredictionHistory).order_by(EIAPredictionHistory.date.desc()).first()
        if eia:
            change_pct = None
            if eia.tanker_count_30d_avg and eia.tanker_count_30d_avg > 0:
                change_pct = round((eia.tanker_count - eia.tanker_count_30d_avg) / eia.tanker_count_30d_avg * 100, 1)
            # Hit rate
            scored = self.db.query(EIAPredictionHistory).filter(EIAPredictionHistory.correct.isnot(None)).all()
            total = len(scored)
            correct = sum(1 for p in scored if p.correct == 1)
            data["eia_prediction"] = {
                "prediction": eia.prediction,
                "tanker_count": eia.tanker_count,
                "tanker_count_30d_avg": eia.tanker_count_30d_avg,
                "change_pct": change_pct,
                "anchored_ratio": eia.anchored_ratio,
                "hit_rate": round(correct / total * 100, 0) if total > 0 else None,
                "total_predictions": total,
            }

        # 4. Floating storage
        fs_events = self.db.query(FloatingStorageEvent).filter(FloatingStorageEvent.status == "active").all()
        vlcc_count = 0
        for ev in fs_events:
            if ev.mmsi:
                reg = self.db.query(VesselRegistry).filter(VesselRegistry.mmsi == ev.mmsi).first()
                if reg and reg.ship_class == "VLCC":
                    vlcc_count += 1
        data["floating_storage"] = {"active_count": len(fs_events), "vlcc_count": vlcc_count}

        # 5. Crack spreads (from DB — avoids yfinance call)
        crack = self.db.query(CrackSpreadHistory).order_by(CrackSpreadHistory.date.desc()).first()
        if crack:
            rows = (
                self.db.query(CrackSpreadHistory.three_two_one_crack)
                .order_by(CrackSpreadHistory.date.desc())
                .limit(365)
                .all()
            )
            values = sorted(r[0] for r in rows if r[0] is not None)
            percentile = 0
            if values and len(values) >= 30:
                rank = sum(1 for v in values if v <= crack.three_two_one_crack)
                percentile = int(rank / len(values) * 100)

            # 30d trend
            vs_30d = None
            if len(values) >= 30:
                avg_30d = sum(values[-30:]) / 30
                if avg_30d > 0:
                    vs_30d = round((crack.three_two_one_crack - avg_30d) / avg_30d * 100, 1)

            data["crack_spreads"] = {
                "value": crack.three_two_one_crack,
                "percentile_1y": percentile,
                "vs_30d_pct": vs_30d,
            }

        # 6. Market structure
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
        sent = self.db.query(SentimentScore).order_by(SentimentScore.created_at.desc()).first()
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

        # 8. Chokepoint anomalies + historical events
        try:
            from backend.signals.historical_lookup import find_anomalies

            for cp in ("hormuz", "suez", "malacca", "bab_el_mandeb"):
                result = find_anomalies(cp, threshold_pct=40.0)
                current = result.get("current", {})
                drop = current.get("drop_pct", 0)

                # Collect historical events (for section 2)
                for evt in result.get("anomalies", []):
                    evt["chokepoint"] = cp
                    data["historical_events"].append(evt)

                if drop < -25:
                    severity = "CRITICAL" if drop < -50 else "WARNING"
                    anomalies = result.get("anomalies", [])
                    context = ""
                    if anomalies:
                        last = anomalies[-1]
                        ctx_list = last.get("disruption_context", [])
                        raw = ctx_list[0] if ctx_list else ""
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

                    # Disruption duration from ongoing anomaly
                    if anomalies and anomalies[-1].get("ongoing"):
                        start = anomalies[-1].get("start_date")
                        if start:
                            try:
                                start_dt = datetime.strptime(start, "%Y-%m-%d")
                                duration = (datetime.now() - start_dt).days
                                if (
                                    data["disruption_duration_days"] is None
                                    or duration > data["disruption_duration_days"]
                                ):
                                    data["disruption_duration_days"] = duration
                            except ValueError:
                                pass
        except Exception as e:
            logger.debug("Chokepoint anomaly check failed: %s", e)

        # 9. Market prices (from yfinance cache)
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

        # 10. Freight proxy
        fp = self.db.query(FreightProxyHistory).order_by(FreightProxyHistory.date.desc()).first()
        if fp:
            data["freight_proxy"] = {
                "index": fp.proxy_index,
                "divergence": fp.divergence_flag,
                "brent_corr": fp.brent_corr_30d,
                "rerouting_corr": fp.rerouting_corr_30d,
            }

        # 11. Supply-demand balance
        sd = self.db.query(SupplyDemandBalance).order_by(SupplyDemandBalance.date.desc()).first()
        if sd:
            data["supply_demand"] = {
                "balance": sd.implied_balance,
                "production": sd.world_production,
                "consumption": sd.world_consumption,
                "divergence_type": sd.divergence_type,
                "houston_deviation": sd.houston_deviation,
                "houston_count": sd.houston_ais_tankers,
            }

        # 12. Days of supply
        dos = self.db.query(DaysOfSupplyHistory).order_by(DaysOfSupplyHistory.date.desc()).first()
        if dos:
            data["days_of_supply"] = {
                "days": dos.commercial_days,
                "total_days": dos.total_days,
                "avg_5y": dos.avg_5y_days,
                "deviation": dos.deviation,
                "trend_4w": dos.trend_4w,
                "assessment": dos.assessment,
            }

        return data

    def _rank_signals(self, data: dict) -> list[dict]:
        """Identify active signals, sorted by severity."""
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
                            "oil_pct": 21,
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
                        "params": {},
                    }
                )
            else:
                signals.append(
                    {
                        "key": ("rerouting_high", "tonne_miles_normal"),
                        "severity": "MEDIUM",
                        "topic": "physical",
                        "params": {},
                    }
                )

        # Floating storage
        fs = data["floating_storage"]
        if fs["active_count"] > 3:
            signals.append(
                {"key": ("floating_storage_high", "any"), "severity": "MEDIUM", "topic": "physical", "params": {}}
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

        # Crack spreads
        crack = data.get("crack_spreads")
        ms = data.get("market_structure")
        if crack and crack["percentile_1y"] > 75:
            sub = "backwardation" if ms and ms.get("spread_pct", 0) < 0 else "no_backwardation"
            signals.append(
                {
                    "key": ("crack_spread_high", sub),
                    "severity": "HIGH" if sub == "backwardation" else "MEDIUM",
                    "topic": "market",
                    "params": {},
                }
            )

        # EIA prediction
        eia = data.get("eia_prediction")
        if eia and eia.get("prediction") != "NEUTRAL":
            signals.append({"key": "eia", "severity": "MEDIUM", "topic": "market", "params": {}})

        # Sentiment
        sent = data.get("sentiment")
        if sent and sent["risk_score"] >= 6 and data["chokepoints"]:
            top_topics = ", ".join(sent.get("risk_factors", [])[:3]) or "geopolitical risk"
            signals.append(
                {
                    "key": ("sentiment_negative", "disruption"),
                    "severity": "LOW",
                    "topic": "sentiment",
                    "params": {"risk_score": sent["risk_score"], "top_topics": top_topics},
                }
            )

        severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        signals.sort(key=lambda s: severity_order.get(s["severity"], 99))
        return signals

    def _get_zone_trends_7d(self) -> dict[str, float]:
        """7-day zone trends: compare last 3 days vs prior 3 days."""
        today = self.today.isoformat()
        d3 = (self.today - timedelta(days=3)).isoformat()
        d7 = (self.today - timedelta(days=7)).isoformat()

        # Recent 3 days
        recent = (
            self.db.query(GeofenceEvent.zone, func.avg(GeofenceEvent.tanker_count))
            .filter(GeofenceEvent.date >= d3, GeofenceEvent.date <= today)
            .group_by(GeofenceEvent.zone)
            .all()
        )
        recent_map = {z: avg for z, avg in recent if avg}

        # Prior 3 days
        prior = (
            self.db.query(GeofenceEvent.zone, func.avg(GeofenceEvent.tanker_count))
            .filter(GeofenceEvent.date >= d7, GeofenceEvent.date < d3)
            .group_by(GeofenceEvent.zone)
            .all()
        )
        prior_map = {z: avg for z, avg in prior if avg}

        trends = {}
        for zone in set(recent_map) | set(prior_map):
            r = recent_map.get(zone, 0)
            p = prior_map.get(zone, 0)
            if p > 0:
                trends[zone] = round((r - p) / p * 100, 1)

        return trends

    def _get_equity_highlights(self) -> dict | None:
        """Get top/bottom performers and sector averages from EquitySnapshot."""
        latest_date = self.db.query(func.max(EquitySnapshot.date)).scalar()
        if not latest_date:
            return None

        snaps = self.db.query(EquitySnapshot).filter(EquitySnapshot.date == latest_date).all()
        if not snaps:
            return None

        result = {}

        # Tanker sector average
        tankers = [s for s in snaps if s.sector and "tanker" in s.sector.lower()]
        if tankers:
            tanker_changes = [s.change_pct for s in tankers if s.change_pct is not None]
            if tanker_changes:
                result["tanker_avg"] = round(sum(tanker_changes) / len(tanker_changes), 1)

        # Top performer
        with_change = [s for s in snaps if s.change_pct is not None]
        if with_change:
            top = max(with_change, key=lambda s: s.change_pct)
            result["top"] = {"ticker": top.ticker, "name": top.name, "change_pct": top.change_pct}

        # Highest WTI correlation
        with_corr = [s for s in snaps if s.wti_corr_30d is not None]
        if with_corr:
            best = max(with_corr, key=lambda s: abs(s.wti_corr_30d))
            result["highest_corr"] = {"ticker": best.ticker, "corr": best.wti_corr_30d}

        return result if result else None

    def _check_divergence(self, signals, data) -> str | None:
        """Check if price direction contradicts physical signals."""
        ds = data.get("disruption_score") or {}
        composite = ds.get("composite", 0)
        brent = data["market_prices"].get("brent")
        wti = data["market_prices"].get("wti")

        physical_bullish = (
            any(s["severity"] == "CRITICAL" and s["topic"] == "chokepoint" for s in signals)
            or data["rerouting"]["cape_share"] > 30
            or (data.get("crack_spreads", {}).get("percentile_1y", 0) > 80)
        )
        price_falling = (brent and brent["change_pct"] < -3) or (wti and wti["change_pct"] < -3)

        if physical_bullish and price_falling:
            random.seed(self.today.isoformat() + "divergence")
            change = brent["change_pct"] if brent and brent["change_pct"] < -3 else wti["change_pct"]
            commodity = "Brent" if brent and brent["change_pct"] < -3 else "WTI"
            return random.choice(DIVERGENCE_TEMPLATES).format(  # nosec B311
                commodity=commodity, change=round(abs(change), 1)
            )

        physical_bearish = data["floating_storage"]["active_count"] > 5 or composite < 20
        price_rising = (brent and brent["change_pct"] > 3) or (wti and wti["change_pct"] > 3)

        if physical_bearish and price_rising:
            random.seed(self.today.isoformat() + "inverse_divergence")
            change = brent["change_pct"] if brent and brent["change_pct"] > 3 else wti["change_pct"]
            commodity = "Brent" if brent and brent["change_pct"] > 3 else "WTI"
            return random.choice(INVERSE_DIVERGENCE_TEMPLATES).format(  # nosec B311
                commodity=commodity, change=round(abs(change), 1), score=round(composite, 0)
            )

        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_report() -> dict:
    """Build report using a fresh DB session."""
    db = SessionLocal()
    try:
        gen = MarketReportGenerator(db)
        return gen.generate()
    finally:
        db.close()


async def get_market_report() -> dict:
    """Get cached market intelligence report. Rebuilds every 30 min."""
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
