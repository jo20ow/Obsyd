"""Per-series metadata — the catalog's data-dictionary layer.

The provider survey's core finding: at gridstatus/Ember every dataset carries
its contract ON THE WIRE (description, source, cadence, licence, caveats),
while Obsyd kept all of that as prose in docs/API.md and docstrings. This
module turns that prose into fields: `series_meta(key)` returns

    description  what the series IS, in one or two sentences
    source       the named upstream + document type
    cadence      how the data arrives (resolution + ingest rhythm + lag)
    licence      the redistribution terms of the upstream
    caveat       the ⚠ that must travel with the number (None when clean)

served per-series by /api/v1/series/catalog and rendered by the /data pages.
The completeness test (test_series_meta) enforces that every catalog key
resolves to non-empty description/source/cadence/licence — a series without
its contract fails CI, which is the lückenlos rule made mechanical.

Wording is aligned with docs/API.md's series table; when the two disagree,
API.md wins and this file follows.
"""
from __future__ import annotations

from backend.power.entsoe_grid import PSR_LABELS
from backend.power.zones import ZONE_REGISTRY

# ── shared source/licence strings (one spelling everywhere) ──────────────────

ENTSOE = "ENTSO-E Transparency Platform"
LIC_ENTSOE = "free reuse with attribution (ENTSO-E terms)"
LIC_EC = "CC BY 4.0 (Fraunhofer Energy-Charts)"
LIC_ELEXON = 'free incl. commercial reuse — "Contains BMRS data © Elexon Limited copyright and database right"'
LIC_NESO = "NESO Open Data Licence (free incl. commercial reuse, attribution)"
LIC_SMARD = "CC BY 4.0 (Bundesnetzagentur | SMARD.de)"
LIC_DERIVED = "derived by OBSYD from the sources above; AGPL-3.0 methodology"

HOURLY_INGEST = "hourly values; ingested every ~30 min as the source publishes (~1h lag)"
DA_CADENCE = "published daily after the ~12:45 CET auction for the next delivery day"

QH_NOTE = ("Since 2025-10 SDAC clears 15-minute products; the hourly series is the "
           "mean of the four quarter-hours")


def _zone_label(z: str) -> str:
    return ZONE_REGISTRY[z]["label"] if z in ZONE_REGISTRY else z


# ── static keys ──────────────────────────────────────────────────────────────

_STATIC: dict[str, dict] = {
    "price.dayahead": {
        "description": "Day-ahead auction clearing price per bidding zone — the reference price of the European power market.",
        "source": f"{ENTSOE} (12.1.D, doc A44)",
        "cadence": DA_CADENCE,
        "licence": LIC_ENTSOE,
        "caveat": QH_NOTE + " (raw steps: price.dayahead.qh).",
    },
    "price.dayahead.qh": {
        "description": "Raw 15-minute day-ahead auction steps — the market's native product since SDAC's 2025-10 switch.",
        "source": f"{ENTSOE} (12.1.D, doc A44)",
        "cadence": DA_CADENCE,
        "licence": LIC_ENTSOE,
        "caveat": "Zones that still trade hourly (CH, IE-SEM) have no 15-min steps.",
    },
    "imbalance.price": {
        "description": "Imbalance / balancing-settlement price, hourly mean — what being out of balance cost.",
        "source": f"{ENTSOE} (imbalance prices; DE-LU via the country EIC / reBAP)",
        "cadence": "hourly means of the settlement periods; settles with a lag of hours to days",
        "licence": LIC_ENTSOE,
        "caveat": "Single-TSO zones; settlement values can be restated (see the revisions ledger).",
    },
    "imbalance.price.qh": {
        "description": "Raw 15-minute imbalance-settlement price steps.",
        "source": f"{ENTSOE} (imbalance prices)",
        "cadence": "15-min settlement periods, published with a lag of hours to days",
        "licence": LIC_ENTSOE,
        "caveat": None,
    },
    "imbalance.price.hh": {
        "description": "Raw half-hourly GB system price (single-price settlement).",
        "source": "Elexon Insights (BMRS system prices)",
        "cadence": "half-hourly settlement periods, ~30 min ingest",
        "licence": LIC_ELEXON,
        "caveat": None,
    },
    "price.mid": {
        "description": "GB Market Index (MID): the volume-weighted price of GB short-term trades, hourly mean — GB's honest free price signal.",
        "source": "Elexon Insights (Market Index, APX provider)",
        "cadence": "half-hourly source data, hourly means; ~30 min ingest",
        "licence": LIC_ELEXON,
        "caveat": "Deliberately NOT a day-ahead auction price — GB's auctions (N2EX/EPEX) are licensed and not redistributable.",
    },
    "price.mid.hh": {
        "description": "GB Market Index (MID) at its raw half-hourly resolution.",
        "source": "Elexon Insights (Market Index, APX provider)",
        "cadence": "half-hourly; ~30 min ingest",
        "licence": LIC_ELEXON,
        "caveat": "Not an auction price (see price.mid).",
    },
    "price.negative_hours": {
        "description": "Count of negative day-ahead hours per zone per day (one point at 00:00 UTC) — the most press-cited European power statistic, as a queryable series.",
        "source": "derived from the day-ahead auction record (ENTSO-E 12.1.D)",
        "cadence": "daily, written by the nightly derived-statistics job",
        "licence": LIC_DERIVED,
        "caveat": "Resolution-weighted: four negative quarter-hours count as one hour. Absent for zones without an auction feed (GB).",
    },
    "load.actual": {
        "description": "Actual total electrical load (demand) per bidding zone.",
        "source": f"{ENTSOE} (6.1.A, doc A65); GB: Elexon INDO + NESO embedded estimates",
        "cadence": HOURLY_INGEST,
        "licence": LIC_ENTSOE,
        "caveat": "GB load folds in NESO's estimated embedded generation (published separately as *.embedded.est).",
    },
    "load.forecast": {
        "description": "The TSOs' own day-ahead total load forecast.",
        "source": f"{ENTSOE} (6.1.B, doc A65 process A01)",
        "cadence": DA_CADENCE,
        "licence": LIC_ENTSOE,
        "caveat": "The source's forecast, not OBSYD's — graded on /api/v1/scoreboard.",
    },
    "residual.actual": {
        "description": "Residual load: actual load minus wind and solar — the demand thermal and hydro fleets must cover, and the biggest price driver.",
        "source": "derived from ENTSO-E load and generation actuals",
        "cadence": HOURLY_INGEST,
        "licence": LIC_DERIVED,
        "caveat": None,
    },
    "residual.forecast": {
        "description": "Day-ahead residual-load expectation from the TSOs' own load, wind and solar forecasts.",
        "source": "derived from ENTSO-E day-ahead forecasts",
        "cadence": DA_CADENCE,
        "licence": LIC_DERIVED,
        "caveat": "Built from the source's forecasts; OBSYD forecasts nothing.",
    },
    "generation.forecast": {
        "description": "Day-ahead total generation forecast.",
        "source": f"{ENTSOE} (doc A71)",
        "cadence": DA_CADENCE,
        "licence": LIC_ENTSOE,
        "caveat": None,
    },
    "wind.forecast": {
        "description": "The TSOs' own day-ahead wind generation forecast.",
        "source": f"{ENTSOE} (wind & solar forecast, doc A69)",
        "cadence": DA_CADENCE,
        "licence": LIC_ENTSOE,
        "caveat": "The source's forecast, not OBSYD's — graded on /api/v1/scoreboard.",
    },
    "solar.forecast": {
        "description": "The TSOs' own day-ahead solar generation forecast.",
        "source": f"{ENTSOE} (wind & solar forecast, doc A69)",
        "cadence": DA_CADENCE,
        "licence": LIC_ENTSOE,
        "caveat": "The source's forecast, not OBSYD's — graded on /api/v1/scoreboard.",
    },
    "wind.actual": {
        "description": "Actual wind generation (on- + offshore).",
        "source": f"{ENTSOE} (doc A75); GB incl. NESO embedded-wind estimate",
        "cadence": HOURLY_INGEST,
        "licence": LIC_ENTSOE,
        "caveat": None,
    },
    "solar.actual": {
        "description": "Actual solar generation.",
        "source": f"{ENTSOE} (doc A75); GB incl. NESO embedded-solar estimate",
        "cadence": HOURLY_INGEST,
        "licence": LIC_ENTSOE,
        "caveat": "TSO-visible fleet only — behind-the-meter PV is not in the source at all.",
    },
    "wind.embedded.est": {
        "description": "NESO's half-hourly ESTIMATE of GB's distribution-connected wind fleet (hourly means) — published separately so the modelled share of GB's mix stays inspectable.",
        "source": "NESO Data Portal (demand update feed)",
        "cadence": "half-hourly source, hourly means; ~30 min ingest",
        "licence": LIC_NESO,
        "caveat": "An estimate by the source, already folded into GB load/wind totals.",
    },
    "solar.embedded.est": {
        "description": "NESO's half-hourly ESTIMATE of GB's embedded solar fleet (hourly means).",
        "source": "NESO Data Portal (demand update feed)",
        "cadence": "half-hourly source, hourly means; ~30 min ingest",
        "licence": LIC_NESO,
        "caveat": "An estimate by the source, already folded into GB load/solar totals.",
    },
    "hydro.reservoir": {
        "description": "Weekly hydro-reservoir filling level per zone.",
        "source": f"{ENTSOE} (doc A72)",
        "cadence": "weekly, published with a few days' lag",
        "licence": LIC_ENTSOE,
        "caveat": "Hydro zones only (Nordics, Alps, Iberia).",
    },
    "netpos.dayahead": {
        "description": "Signed day-ahead market net position per zone; positive = net exporter.",
        "source": f"{ENTSOE} (doc A25)",
        "cadence": DA_CADENCE,
        "licence": LIC_ENTSOE,
        "caveat": None,
    },
    "outage.offline": {
        "description": "Generation capacity offline RIGHT NOW: every published unavailability, revision-aware (highest revision per event; withdrawn events vanish).",
        "source": f"{ENTSOE} (unavailability of generation units, doc A77/A80)",
        "cadence": "snapshot series written every ingest — today-only, not backfillable",
        "licence": LIC_ENTSOE,
        "caveat": "A missed hour is gone for good: the source takes filings down once over. Most raw messages are withdrawn revisions — counting them would fabricate gigawatts.",
    },
    "outage.forced": {
        "description": "The forced (unplanned) subset of capacity offline right now.",
        "source": f"{ENTSOE} (doc A77, A54 forced subset)",
        "cadence": "snapshot series, today-only, not backfillable",
        "licence": LIC_ENTSOE,
        "caveat": "See outage.offline.",
    },
    "congestion.cost.security": {
        "description": "German congestion management: the TSOs' monthly network-security cost bundle (redispatch incl. feed-in management, reserve activations).",
        "source": "Bundesnetzagentur | SMARD.de (download manager)",
        "cadence": "monthly totals, published with a 3–4 month lag; full history re-read weekly",
        "licence": LIC_SMARD,
        "caveat": "Germany only (stored under DE-LU), since 2022-07. SMARD's own bundle definition — deliberately not re-labelled as redispatch-only.",
    },
    "congestion.cost.countertrading": {
        "description": "German congestion management: monthly countertrading cost.",
        "source": "Bundesnetzagentur | SMARD.de (download manager)",
        "cadence": "monthly totals, 3–4 month publication lag",
        "licence": LIC_SMARD,
        "caveat": "Germany only, since 2022-07.",
    },
    "co2.intensity.lifecycle": {
        "description": "ESTIMATED CO₂ intensity of the zone's own generation: the published mix weighted with per-technology life-cycle emission factors (IPCC AR5 medians via the Electricity Maps factor set).",
        "source": "derived from ENTSO-E/Elexon generation; factors: IPCC AR5 Annex III / Electricity Maps",
        "cadence": "hourly, recomputed every 3 h over a trailing window",
        "licence": LIC_DERIVED,
        "caveat": "An estimate and labelled as one: production-based (imports ignored), technology-average factors (±10–20% vs plant-calibrated), storage discharge priced at the hour's average. Full factor table in the repo.",
    },
    "co2.intensity.direct": {
        "description": "ESTIMATED direct (combustion-only) CO₂ intensity of the zone's own generation.",
        "source": "derived from ENTSO-E/Elexon generation; operational emission factors",
        "cadence": "hourly, recomputed every 3 h",
        "licence": LIC_DERIVED,
        "caveat": "See co2.intensity.lifecycle — same method, combustion factors only.",
    },
    "spread.da_imbalance": {
        "description": "Imbalance settlement price minus day-ahead price, per hour — the European reading of the DART spread: what being out of balance cost versus having bought day-ahead.",
        "source": "derived from ENTSO-E day-ahead and imbalance prices",
        "cadence": "hourly, nightly derived-statistics job",
        "licence": LIC_DERIVED,
        "caveat": "Only hours where BOTH legs exist; structurally absent for GB (no auction feed).",
    },
    "price.ida1.qh": {
        "description": "Intraday auction IDA1 clearing prices (15-min products, full CET delivery day).",
        "source": f"{ENTSOE} (doc A44 / contract A07)",
        "cadence": "daily per auction; ingested three times a day",
        "licence": LIC_ENTSOE,
        "caveat": "TP submission is voluntary: currently Spain only, from 2026. Continuous intraday remains licensed and absent.",
    },
    "price.ida2.qh": {
        "description": "Intraday auction IDA2 clearing prices (15-min products, full CET delivery day).",
        "source": f"{ENTSOE} (doc A44 / contract A07)",
        "cadence": "daily per auction",
        "licence": LIC_ENTSOE,
        "caveat": "Spain only, from 2026 (voluntary submission).",
    },
    "price.ida3.qh": {
        "description": "Intraday auction IDA3 clearing prices (15-min products, afternoon half-day — the market's own span).",
        "source": f"{ENTSOE} (doc A44 / contract A07)",
        "cadence": "daily per auction",
        "licence": LIC_ENTSOE,
        "caveat": "Spain only, from 2026 (voluntary submission).",
    },
}


# Premium-preview series: metadata exists so pro sessions get full pages;
# the catalog's premium filter keeps them invisible to the free tier.
def _tb_meta(n: int) -> dict:
    return {
        "description": (f"Daily day-ahead top-bottom spread TB{n}: the sum of the day's {n} highest "
                        f"hourly prices minus its {n} lowest — what a {n}-hour storage system cycling "
                        "once per day would have captured in the day-ahead auction alone."),
        "source": "derived from ENTSO-E day-ahead prices (TB convention: Modo Energy)",
        "cadence": "one point per UTC day, nightly derived-statistics job",
        "licence": LIC_DERIVED,
        "caveat": ("A price-spread statistic, NOT a battery revenue benchmark: real assets also earn "
                   "in intraday, balancing and ancillary markets. No efficiency applied; days under "
                   "20 priced hours skipped; absent for GB."),
    }


for _n in (1, 2, 4):
    _STATIC[f"spread.tb{_n}"] = _tb_meta(_n)


# ── pattern families ─────────────────────────────────────────────────────────

def _capture_meta(psr: str, metric: str) -> dict:
    tech = PSR_LABELS.get(psr, psr)
    if metric == "factor":
        desc = (f"Monthly value factor of {tech}: capture price ÷ the month's baseload mean. "
                "Below 1.00 the technology earned less than baseload — for solar and wind, "
                "that is cannibalisation.")
    elif metric == "price_floor0":
        desc = (f"Monthly capture price of {tech} with negative hours priced at €0 — the named "
                "floor variant; the spread to the unfloored capture price is the negative-price "
                "exposure in EUR/MWh.")
    else:
        desc = (f"Monthly capture price of {tech}: the generation-weighted average day-ahead "
                "price the technology actually achieved.")
    return {
        "description": desc,
        "source": "derived from ENTSO-E day-ahead prices × per-technology generation",
        "cadence": "one point per complete month (1st, 00:00 UTC), nightly job",
        "licence": LIC_DERIVED,
        "caveat": ("Day-ahead only, TSO-visible fleet only (behind-the-meter PV absent; coverage "
                   "varies by zone/technology). Never a 'PPA value'."),
    }


def _conv_meta(metric: str, counterparty: str) -> dict:
    what = {
        "full": "hours with |spread| ≤ 1 EUR/MWh (ACER's 'full price convergence' band)",
        "low": "hours with |spread| > 10 EUR/MWh (ACER's 'low' band)",
        "hours": "hours priced on both sides (the denominator; moderate = hours − full − low)",
        "spread": "mean absolute day-ahead spread",
    }.get(metric, metric)
    return {
        "description": (f"Daily price-convergence statistic for the border with {_zone_label(counterparty)}: "
                        f"{what}. Bands follow ACER MMR 2024."),
        "source": "derived from ENTSO-E day-ahead prices; band definitions: ACER MMR 2024",
        "cadence": "one point per UTC day per border, nightly job",
        "licence": LIC_DERIVED,
        "caveat": ("Stored under the border's sorted-first zone. ACER's caveat travels with it: "
                   "full convergence is not an objective."),
    }


def series_meta(key: str) -> dict:
    """The metadata contract for one series key. Always returns all five
    fields; an unrecognised key gets honest placeholders rather than a raise
    (the completeness test keeps the placeholder path unreachable for every
    catalogued family)."""
    if key in _STATIC:
        return {**_STATIC[key], "caveat": _STATIC[key].get("caveat")}
    parts = key.split(".")
    if key.startswith("gen."):
        tech = PSR_LABELS.get(parts[1], parts[1])
        return {
            "description": f"Actual generation of {tech}.",
            "source": f"{ENTSOE} (actual generation per production type, doc A75); GB: Elexon FUELINST",
            "cadence": HOURLY_INGEST,
            "licence": LIC_ENTSOE,
            "caveat": "Aggregated TSO-visible output — not a plant meter.",
        }
    if key.startswith("consumption."):
        tech = PSR_LABELS.get(parts[1], parts[1])
        return {
            "description": f"Consumption of {tech} (e.g. pumped-storage pumping).",
            "source": f"{ENTSOE} (doc A75, consumption leg)",
            "cadence": HOURLY_INGEST,
            "licence": LIC_ENTSOE,
            "caveat": None,
        }
    if key.startswith("flow."):
        return {
            "description": f"Physical cross-border flow toward {_zone_label(parts[1])}, stored under the border's sorted-first zone; positive = that zone exports.",
            "source": "Fraunhofer Energy-Charts (/cbpf, country-level metered flows)",
            "cadence": HOURLY_INGEST,
            "licence": LIC_EC,
            "caveat": "Country-level — bidding sub-zones have no physical feed (see sched.* for the bidding-zone grain).",
        }
    if key.startswith("sched."):
        return {
            "description": f"Scheduled commercial exchange toward {_zone_label(parts[1])} (what the market agreed to move), same sorted-pair/sign convention as flow.*.",
            "source": f"{ENTSOE} (scheduled exchanges, doc A09)",
            "cadence": HOURLY_INGEST,
            "licence": LIC_ENTSOE,
            "caveat": "flow − sched is loop/transit flow — not a claim about a single interconnector.",
        }
    if key.startswith("ntc."):
        return {
            "description": f"Day-ahead NTC offered to the auction toward {_zone_label(parts[1])} — DIRECTED, one series per direction, never netted.",
            "source": f"{ENTSOE} (doc A61, contract A01)",
            "cadence": DA_CADENCE,
            "licence": LIC_ENTSOE,
            "caveat": "NTC-allocated borders only (23 of 63) — the flow-based Core region and the Nordics publish none by market design. Offered capacity, not a physical limit: utilization can exceed 100%.",
        }
    if key.startswith("capture.") and len(parts) == 3:
        return _capture_meta(parts[1], parts[2])
    if key.startswith("conv.") and len(parts) == 3:
        return _conv_meta(parts[1], parts[2])
    if key.startswith("balancing.") and len(parts) == 4:
        product = parts[1].upper() if parts[1] != "afrr" else "aFRR"
        product = "mFRR" if parts[1] == "mfrr" else product
        measure = "activation price" if parts[2] == "price" else "activated volume"
        return {
            "description": f"Activated balancing energy: {product} {measure}, direction {parts[3]}.",
            "source": f"{ENTSOE} (docs A84 prices / A83 volumes)",
            "cadence": "control-area months, ingested daily",
            "licence": LIC_ENTSOE,
            "caveat": ("DE-LU is served via TenneT's control area only (one of four German TSOs). "
                       "The .vol.* series are defined but currently answer empty at the source."),
        }
    if key.startswith("capacity."):
        return {
            "description": "Procured balancing-capacity price: volume-weighted average of accepted tenders, normalised to EUR/MW/h.",
            "source": f"{ENTSOE} (doc A15)",
            "cadence": "daily tenders; history starts 2025-11-27 (the source's own publication floor)",
            "licence": LIC_ENTSOE,
            "caveat": "German LFC block only. Deeper history exists at regelleistung.net but carries no reuse licence.",
        }
    return {
        "description": "No description recorded for this series yet.",
        "source": "see /api/v1/meta",
        "cadence": "see the coverage window",
        "licence": "see /api/v1/meta",
        "caveat": None,
    }
