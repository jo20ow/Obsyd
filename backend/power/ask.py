"""The question box — a DETERMINISTIC query answerer over the series store.

Owner direction 2026-09-13: generic ranking panels answer questions nobody
asked; the professional arrives WITH a question ("compare negative hours in
Finland 2019 to 2025") and wants THE answer. This module parses a constrained
natural grammar and answers from the store — no LLM, by standing owner rule:
a hallucinated number is exactly the "schwammige Zahl" the quality doctrine
exists to prevent. What the grammar cannot parse fails VISIBLY with
suggestions; it never guesses silently.

Grammar (English + German keywords):
    <metric phrase> <zone name(s)> [<year> [bis|to|-] <year> | seit|since <year>]

  * metric  — curated synonym table below; longest phrase match wins
  * zones   — registry keys, labels, and country names; a country that maps
              to several bidding zones (Norway, Sweden, Denmark, Italy)
              becomes a comparison across all of them
  * years   — a range aggregates per YEAR; a single year breaks into MONTHS;
              nothing defaults to the full record per year

Every metric declares its aggregation (counts are SUMMED, prices/levels are
AVERAGED) and the answer echoes the full interpretation back — the reader
always sees how the question was understood. Coverage honesty is part of the
answer: years asked for but not on record are named, never silently trimmed.

Aggregation runs IN SQLite (GROUP BY period on the indexed (series,zone) key),
so an eight-year question is one grouped scan, not 70k rows into Python.

PREMIUM (backend/premium.py): served by /api/v1/ask behind require_pro while
the preview is tested hidden.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.energy import SeriesDim, ZoneDim
from backend.power.zones import ZONE_REGISTRY

# ─── metric table ─────────────────────────────────────────────────────────────
# phrases: longest match wins (checked in descending phrase length, so
# "negative hours" beats "hours" and "residual load" beats "load").
# agg: "sum" (counts, costs — a year is the total) | "mean" (prices, levels,
# intensities — a year is the average level).

METRICS: list[dict] = [
    {"id": "negative_hours", "label": "Negative-price hours",
     "series": ["price.negative_hours"], "agg": "sum", "unit": "h",
     "phrases": ["negative hours", "negative stunden", "negativstunden",
                 "negative price hours", "negative prices", "negative preise"]},
    {"id": "price", "label": "Day-ahead price",
     "series": ["price.dayahead"], "agg": "mean", "unit": "EUR/MWh",
     "phrases": ["day-ahead price", "day ahead price", "spot price", "power price",
                 "strompreis", "spotpreis", "price", "preis", "preise"]},
    {"id": "co2", "label": "CO₂ intensity (est., lifecycle)",
     "series": ["co2.intensity.lifecycle"], "agg": "mean", "unit": "gCO2eq/kWh",
     "phrases": ["carbon intensity", "co2 intensity", "co2", "co₂", "carbon",
                 "emissionen", "emissions"]},
    {"id": "load", "label": "Load",
     "series": ["load.actual"], "agg": "mean", "unit": "MW",
     "phrases": ["load", "demand", "stromverbrauch", "verbrauch", "last"]},
    {"id": "residual", "label": "Residual load",
     "series": ["residual.actual"], "agg": "mean", "unit": "MW",
     "phrases": ["residual load", "residuallast", "residual"]},
    {"id": "wind", "label": "Wind generation (on+offshore)",
     "series": ["gen.B18", "gen.B19"], "agg": "mean", "unit": "MW",
     "combine_note": "sum of per-technology mean outputs",
     "phrases": ["wind generation", "wind output", "winderzeugung", "wind"]},
    {"id": "solar", "label": "Solar generation",
     "series": ["gen.B16"], "agg": "mean", "unit": "MW",
     "phrases": ["solar generation", "solar output", "solarerzeugung", "solar", "pv"]},
    {"id": "imbalance", "label": "Imbalance price",
     "series": ["imbalance.price"], "agg": "mean", "unit": "EUR/MWh",
     "phrases": ["imbalance price", "imbalance", "ausgleichsenergiepreis"]},
    {"id": "tb2", "label": "Top-bottom spread TB2 (2h)",
     "series": ["spread.tb2"], "agg": "sum", "unit": "EUR/MW",
     "phrases": ["tb2", "battery spread", "storage spread", "top-bottom spread",
                 "top bottom spread", "tb spread", "speicher spread"]},
    {"id": "tb1", "label": "Top-bottom spread TB1 (1h)",
     "series": ["spread.tb1"], "agg": "sum", "unit": "EUR/MW", "phrases": ["tb1"]},
    {"id": "tb4", "label": "Top-bottom spread TB4 (4h)",
     "series": ["spread.tb4"], "agg": "sum", "unit": "EUR/MW", "phrases": ["tb4"]},
    {"id": "capture_solar", "label": "Solar capture factor",
     "series": ["capture.B16.factor"], "agg": "mean", "unit": "ratio",
     "combine_note": "unweighted mean of monthly value factors",
     "phrases": ["solar capture factor", "solar capture", "capture factor",
                 "capture rate", "kannibalisierung", "capture"]},
    {"id": "hydro", "label": "Hydro reservoir filling",
     "series": ["hydro.reservoir"], "agg": "mean", "unit": "MWh",
     "phrases": ["hydro reservoir", "reservoir", "füllstand", "speicherfüllstand"]},
    {"id": "congestion", "label": "Congestion-management cost (DE, network security)",
     "series": ["congestion.cost.security"], "agg": "sum", "unit": "EUR",
     "phrases": ["congestion costs", "congestion cost", "redispatch costs",
                 "redispatch", "netzengpass", "congestion"]},
    {"id": "da_imbalance", "label": "Imbalance − day-ahead spread",
     "series": ["spread.da_imbalance"], "agg": "mean", "unit": "EUR/MWh",
     "phrases": ["imbalance spread", "da imbalance spread"]},
]

# ─── zone name table ─────────────────────────────────────────────────────────

_COUNTRY_ZONES: dict[str, list[str]] = {
    "germany": ["DE_LU"], "deutschland": ["DE_LU"],
    "austria": ["AT"], "österreich": ["AT"], "oesterreich": ["AT"],
    "belgium": ["BE"], "belgien": ["BE"],
    "bulgaria": ["BG"], "bulgarien": ["BG"],
    "switzerland": ["CH"], "schweiz": ["CH"],
    "czechia": ["CZ"], "czech republic": ["CZ"], "tschechien": ["CZ"],
    "spain": ["ES"], "spanien": ["ES"],
    "finland": ["FI"], "finnland": ["FI"],
    "france": ["FR"], "frankreich": ["FR"],
    "great britain": ["GB"], "britain": ["GB"], "uk": ["GB"],
    "großbritannien": ["GB"], "england": ["GB"],
    "greece": ["GR"], "griechenland": ["GR"],
    "croatia": ["HR"], "kroatien": ["HR"],
    "hungary": ["HU"], "ungarn": ["HU"],
    "ireland": ["IE_SEM"], "irland": ["IE_SEM"],
    "netherlands": ["NL"], "holland": ["NL"], "niederlande": ["NL"],
    "poland": ["PL"], "polen": ["PL"],
    "portugal": ["PT"],
    "romania": ["RO"], "rumänien": ["RO"],
    "slovenia": ["SI"], "slowenien": ["SI"],
    "slovakia": ["SK"], "slowakei": ["SK"],
    "denmark": ["DK1", "DK2"], "dänemark": ["DK1", "DK2"], "daenemark": ["DK1", "DK2"],
    "norway": ["NO1", "NO2", "NO3", "NO4", "NO5"],
    "norwegen": ["NO1", "NO2", "NO3", "NO4", "NO5"],
    "sweden": ["SE1", "SE2", "SE3", "SE4"],
    "schweden": ["SE1", "SE2", "SE3", "SE4"],
    "italy": ["IT_NORD", "IT_CENTRO_NORD", "IT_CENTRO_SUD", "IT_SUD",
              "IT_CALABRIA", "IT_SARDEGNA", "IT_SICILIA"],
    "italien": ["IT_NORD", "IT_CENTRO_NORD", "IT_CENTRO_SUD", "IT_SUD",
                "IT_CALABRIA", "IT_SARDEGNA", "IT_SICILIA"],
}


def _zone_phrases() -> list[tuple[str, list[str]]]:
    """(phrase, zones) sorted longest-first — country names, registry labels
    and raw keys all match; longest phrase wins so 'it-nord' beats 'it'."""
    table: dict[str, list[str]] = dict(_COUNTRY_ZONES)
    for key, meta in ZONE_REGISTRY.items():
        table.setdefault(key.lower().replace("_", " "), [key])
        table.setdefault(key.lower().replace("_", "-"), [key])
        table.setdefault(key.lower(), [key])
        table.setdefault(meta["label"].lower(), [key])
    return sorted(table.items(), key=lambda kv: -len(kv[0]))


EXAMPLES = [
    "compare negative hours in Finland from 2019 to 2025",
    "solar capture factor Germany since 2022",
    "TB2 Spain 2025",
    "CO2 intensity Poland vs France 2021 to 2026",
    "congestion costs Germany since 2022",
]

_YEAR_RE = re.compile(r"\b(20[0-9]{2})\b")


def parse_query(q: str) -> dict:
    """Parse or refuse — never guess. Returns either
    {ok: True, metric, zones, year_from, year_to} or {ok: False, problem,
    suggestions}."""
    ql = " " + re.sub(r"[^\w\säöüß-]", " ", q.lower()) + " "

    # Longest matching phrase wins GLOBALLY — sorted per-metric would let a
    # metric's longest synonym pull rank for its short ones ("stromverbrauch"
    # must not make bare "load" beat "residual load").
    metric = None
    all_phrases = sorted(
        ((phrase, m) for m in METRICS for phrase in m["phrases"]),
        key=lambda pm: -len(pm[0]),
    )
    for phrase, m in all_phrases:
        if f" {phrase} " in ql or f" {phrase}s " in ql:
            metric = m
            ql = ql.replace(f" {phrase} ", " ", 1)
            break
    if metric is None:
        return {"ok": False, "problem": "metric",
                "message": "No metric recognised in the question.",
                "known_metrics": [m["label"] for m in METRICS],
                "examples": EXAMPLES}

    zones: list[str] = []
    for phrase, zs in _zone_phrases():
        pat = f" {phrase} "
        if pat in ql:
            for z in zs:
                if z not in zones:
                    zones.append(z)
            ql = ql.replace(pat, " ")
    if not zones:
        return {"ok": False, "problem": "zone",
                "message": "No zone or country recognised in the question.",
                "examples": EXAMPLES}

    years = [int(y) for y in _YEAR_RE.findall(ql)]
    now_year = datetime.now(UTC).year
    if len(years) >= 2:
        year_from, year_to = min(years), max(years)
    elif len(years) == 1:
        if re.search(r"\b(seit|since|from|ab)\b", ql):
            year_from, year_to = years[0], now_year
        else:
            year_from = year_to = years[0]
    else:
        year_from, year_to = None, now_year  # full record

    return {"ok": True, "metric": metric, "zones": zones[:8],
            "year_from": year_from, "year_to": min(year_to, now_year)}


# ─── answering ────────────────────────────────────────────────────────────────

_AGG_SQL = {"sum": "SUM(value)", "mean": "AVG(value)"}


def _aggregate(db: Session, series_key: str, zone: str, fmt: str, agg: str,
               start_ts: int | None, end_ts: int) -> dict[str, tuple[float, int]]:
    sid = db.query(SeriesDim.id).filter(SeriesDim.key == series_key).scalar()
    zid = db.query(ZoneDim.id).filter(ZoneDim.key == zone).scalar()
    if sid is None or zid is None:
        return {}
    sql = text(f"""
        SELECT strftime('{fmt}', ts_utc, 'unixepoch') AS p,
               {_AGG_SQL[agg]} AS v, COUNT(*) AS n
          FROM power_hourly
         WHERE series_id = :sid AND zone_id = :zid
           AND ts_utc >= :a AND ts_utc < :b
         GROUP BY p
    """)
    rows = db.execute(sql, {"sid": sid, "zid": zid,
                            "a": start_ts if start_ts is not None else 0,
                            "b": end_ts}).all()
    return {p: (float(v), int(n)) for p, v, n in rows if v is not None}


def _oldest(db: Session, series_key: str, zone: str) -> int | None:
    sid = db.query(SeriesDim.id).filter(SeriesDim.key == series_key).scalar()
    zid = db.query(ZoneDim.id).filter(ZoneDim.key == zone).scalar()
    if sid is None or zid is None:
        return None
    return db.execute(text(
        "SELECT MIN(ts_utc) FROM power_hourly WHERE series_id=:s AND zone_id=:z"
    ), {"s": sid, "z": zid}).scalar()


def _fmt_value(v: float, unit: str) -> str:
    if unit == "h":
        return f"{v:,.0f} h"
    if unit == "EUR":
        return f"€{v / 1e6:,.0f}M" if abs(v) >= 1e6 else f"€{v:,.0f}"
    if unit == "EUR/MW":
        return f"€{v / 1000:,.1f}k/MW" if abs(v) >= 10_000 else f"€{v:,.0f}/MW"
    if unit == "MW":
        return f"{v / 1000:,.1f} GW" if abs(v) >= 1000 else f"{v:,.0f} MW"
    if unit == "ratio":
        return f"×{v:.2f}"
    return f"{v:,.1f} {unit}"


def answer(db: Session, q: str, *, now: datetime | None = None) -> dict:
    parsed = parse_query(q)
    if not parsed["ok"]:
        return {"available": False, "query": q, **{k: v for k, v in parsed.items() if k != "ok"}}

    now = now or datetime.now(UTC)
    metric, zones = parsed["metric"], parsed["zones"]
    year_from, year_to = parsed["year_from"], parsed["year_to"]

    monthly = year_from == year_to and year_from is not None
    fmt = "%Y-%m" if monthly else "%Y"
    start_ts = (int(datetime(year_from, 1, 1, tzinfo=UTC).timestamp())
                if year_from is not None else None)
    end_ts = int(datetime(year_to + 1, 1, 1, tzinfo=UTC).timestamp())

    per_zone: dict[str, dict[str, tuple[float, int]]] = {}
    coverage: list[str] = []
    for zone in zones:
        combined: dict[str, tuple[float, int]] = {}
        for skey in metric["series"]:
            for p, (v, n) in _aggregate(db, skey, zone, fmt, metric["agg"],
                                        start_ts, end_ts).items():
                pv, pn = combined.get(p, (0.0, 0))
                combined[p] = (pv + v, max(pn, n))
        if combined:
            per_zone[zone] = combined
        # Coverage honesty: years asked for that predate the record are named.
        oldest = min((o for o in (_oldest(db, s, zone) for s in metric["series"])
                      if o is not None), default=None)
        label = ZONE_REGISTRY[zone]["label"] if zone in ZONE_REGISTRY else zone
        if oldest is None:
            coverage.append(f"{label}: no {metric['label']} data on record.")
        else:
            first = datetime.fromtimestamp(oldest, tz=UTC)
            if year_from is not None and first.year > year_from:
                coverage.append(
                    f"{label}: record starts {first.date()} — "
                    f"{year_from}–{first.year - 1} not on record."
                )

    if not per_zone:
        return {"available": False, "query": q,
                "message": "Question understood, but no data in that window.",
                "interpreted": _echo(metric, zones, year_from, year_to, monthly),
                "coverage": coverage}

    periods = sorted({p for zc in per_zone.values() for p in zc})
    rows = [
        {"period": p,
         **{z: round(per_zone[z][p][0], 2) for z in zones if p in per_zone.get(z, {})}}
        for p in periods
    ]

    # The takeaway sentence uses complete periods only — the running year/month
    # is a fragment wearing a period's label.
    current = now.strftime(fmt)
    sentence = _sentence(metric, zones, per_zone,
                         [p for p in periods if p < current] or periods)

    primary = metric["series"][0]
    return {
        "available": True,
        "query": q,
        "interpreted": _echo(metric, zones, year_from, year_to, monthly),
        "unit": metric["unit"],
        "agg": metric["agg"],
        "zone_labels": {z: ZONE_REGISTRY[z]["label"] for z in zones if z in ZONE_REGISTRY},
        "rows": rows,
        "sentence": sentence,
        "coverage": coverage,
        "partial_period": current if any(p == current for p in periods) else None,
        "download_url": (
            f"/api/v1/series?series={primary}&zone={zones[0]}"
            + (f"&start={year_from}-01-01" if year_from else "")
            + f"&end={year_to + 1}-01-01&format=csv"
        ),
        "note": ("Descriptive aggregation of the published record — "
                 + ("totals per period. " if metric["agg"] == "sum" else "mean level per period. ")
                 + (metric.get("combine_note", "") and f"Composite: {metric['combine_note']}. ")
                 + "Not a forecast."),
    }


def _echo(metric: dict, zones: list[str], year_from, year_to, monthly: bool) -> dict:
    return {
        "metric": metric["label"],
        "series": metric["series"],
        "zones": zones,
        "from": year_from,
        "to": year_to,
        "grain": "monthly" if monthly else "yearly",
        "aggregation": ("total per period" if metric["agg"] == "sum"
                        else "mean per period"),
    }


def _sentence(metric: dict, zones: list[str], per_zone: dict, periods: list[str]) -> str:
    if not periods:
        return ""
    unit = metric["unit"]
    z0 = zones[0]
    label0 = ZONE_REGISTRY[z0]["label"] if z0 in ZONE_REGISTRY else z0
    zc = per_zone.get(z0, {})
    have = [p for p in periods if p in zc]
    if not have:
        return ""
    latest = have[-1]
    parts = [f"{label0}: {_fmt_value(zc[latest][0], unit)} in {latest}"]
    if len(have) > 1:
        first = have[0]
        parts.append(f"vs {_fmt_value(zc[first][0], unit)} in {first}")
        peak = max(have, key=lambda p: zc[p][0])
        if peak not in (first, latest):
            parts.append(f"peak {_fmt_value(zc[peak][0], unit)} in {peak}")
    if len(zones) > 1:
        others = [z for z in zones[1:] if latest in per_zone.get(z, {})][:2]
        for z in others:
            zl = ZONE_REGISTRY[z]["label"] if z in ZONE_REGISTRY else z
            parts.append(f"{zl}: {_fmt_value(per_zone[z][latest][0], unit)} in {latest}")
    return " · ".join(parts) + "."
