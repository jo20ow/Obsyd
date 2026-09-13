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
    # Yearly grain computes the GENERATION-WEIGHTED annual factor from raw
    # hours (capture_psr below) — an unweighted mean of monthly factors
    # overweights winter (tiny volumes, factor near 1) and reads too high.
    # Monthly grain keeps reading the stored exact monthly factors.
    {"id": "capture_solar", "label": "Solar capture factor",
     "series": ["capture.B16.factor"], "agg": "mean", "unit": "ratio",
     "capture_psr": "B16",
     "combine_note": "unweighted mean of monthly value factors",
     "phrases": ["solar capture factor", "solar capture", "capture factor",
                 "capture rate", "kannibalisierung", "capture"]},
    {"id": "capture_wind", "label": "Wind onshore capture factor",
     "series": ["capture.B19.factor"], "agg": "mean", "unit": "ratio",
     "capture_psr": "B19",
     "combine_note": "unweighted mean of monthly value factors",
     "phrases": ["wind capture factor", "wind capture"]},
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
    # Columns = FUELS (one zone), not zones: the mix is a multi-series
    # question by nature. `series` resolves dynamically (every gen.<PSR> the
    # zone has) — see answer_structured.
    {"id": "mix", "label": "Generation mix (per fuel)",
     "series": [], "columns": "fuels", "agg": "mean", "unit": "MW",
     "phrases": ["generation mix", "energy mix", "power mix", "strommix",
                 "erzeugungsmix", "mix"]},
]

METRIC_BY_ID = {m["id"]: m for m in METRICS}

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

#: English display names for the filter UI's place list (the German synonyms
#: stay parse-only). Countries first, then individual zones from the registry.
_PLACE_NAMES = [
    "Austria", "Belgium", "Bulgaria", "Croatia", "Czechia", "Denmark",
    "Finland", "France", "Germany", "Great Britain", "Greece", "Hungary",
    "Ireland", "Italy", "Netherlands", "Norway", "Poland", "Portugal",
    "Romania", "Slovakia", "Slovenia", "Spain", "Sweden", "Switzerland",
]


def filter_options(*, now: datetime | None = None) -> dict:
    """What the filter UI can offer — metrics, places, year bounds. The metric
    table stays the single source; the UI never hardcodes it."""
    now = now or datetime.now(UTC)
    places = [
        {"label": n, "zones": _COUNTRY_ZONES[n.lower()]} for n in _PLACE_NAMES
    ] + [
        {"label": meta["label"], "zones": [k]} for k, meta in ZONE_REGISTRY.items()
    ]
    return {
        "metrics": [
            {"id": m["id"], "label": m["label"], "unit": m["unit"],
             "aggregation": "total per period" if m["agg"] == "sum" else "mean per period",
             "columns": m.get("columns", "zones")}
            for m in METRICS
        ],
        "places": places,
        "years": {"min": 2015, "max": now.year},
    }


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


#: Generation-weighted capture factor per YEAR, from raw hours — the number
#: the market means by an annual value factor. The join follows capture.py's
#: id-first rule; guards mirror its spirit (a fragment of a year is not a
#: year: at least ~3 months of daylight generation and half a year of prices).
_CAPTURE_YEAR_SQL = """
SELECT strftime('%Y', g.ts_utc, 'unixepoch') AS y,
       SUM(p.value * g.value) / SUM(g.value) AS capture,
       COUNT(*) AS n
  FROM power_hourly g
  JOIN power_hourly p
    ON p.ts_utc = g.ts_utc AND p.series_id = :pid AND p.zone_id = :zid
 WHERE g.series_id = :gid AND g.zone_id = :zid
   AND g.ts_utc >= :a AND g.ts_utc < :b
 GROUP BY y
"""
_BASELOAD_YEAR_SQL = """
SELECT strftime('%Y', ts_utc, 'unixepoch') AS y, AVG(value) AS bl, COUNT(*) AS n
  FROM power_hourly
 WHERE series_id = :pid AND zone_id = :zid AND ts_utc >= :a AND ts_utc < :b
 GROUP BY y
"""
_MIN_GEN_HOURS_YEAR = 1000
_MIN_PRICE_HOURS_YEAR = 24 * 180


def _capture_factor_yearly(db: Session, psr: str, zone: str,
                           start_ts: int | None, end_ts: int) -> dict[str, tuple[float, int]]:
    pid = db.query(SeriesDim.id).filter(SeriesDim.key == "price.dayahead").scalar()
    gid = db.query(SeriesDim.id).filter(SeriesDim.key == f"gen.{psr}").scalar()
    zid = db.query(ZoneDim.id).filter(ZoneDim.key == zone).scalar()
    if pid is None or gid is None or zid is None:
        return {}
    params = {"pid": pid, "gid": gid, "zid": zid,
              "a": start_ts if start_ts is not None else 0, "b": end_ts}
    capture = {y: (float(c), int(n))
               for y, c, n in db.execute(text(_CAPTURE_YEAR_SQL), params).all()
               if c is not None and n >= _MIN_GEN_HOURS_YEAR}
    baseload = {y: float(bl)
                for y, bl, n in db.execute(text(_BASELOAD_YEAR_SQL),
                                           {k: params[k] for k in ("pid", "zid", "a", "b")}).all()
                if bl and bl > 0 and n >= _MIN_PRICE_HOURS_YEAR}
    return {y: (c / baseload[y], n) for y, (c, n) in capture.items() if y in baseload}


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


def _zone_label(z: str) -> str:
    return ZONE_REGISTRY[z]["label"] if z in ZONE_REGISTRY else z


def _coverage_note(label: str, oldest: int | None, year_from: int | None,
                   year_to: int, monthly: bool) -> str | None:
    """The honesty line about the record's left edge: years asked for but
    missing are named, and a FIRST year that starts mid-year is called
    partial (the owner audit that forced this: a '2018' bar that was really
    Oct–Dec 2018 wore a full year's label)."""
    if oldest is None:
        return None
    first = datetime.fromtimestamp(oldest, tz=UTC)
    parts = []
    if year_from is not None and first.year > year_from:
        parts.append(f"{year_from}–{first.year - 1} not on record")
    if (not monthly and (first.month, first.day) != (1, 1)
            and (year_from is None or first.year >= year_from)
            and first.year <= year_to):
        parts.append(f"{first.year} is a partial year")
    if not parts:
        return None
    return f"{label}: record starts {first.date()} — " + "; ".join(parts) + "."


def answer_structured(db: Session, metric_id: str, zones: list[str],
                      year_from: int | None, year_to: int | None,
                      *, now: datetime | None = None) -> dict:
    """The core answerer — the filter UI calls this directly; the text parser
    (answer below) delegates here. Columns are ZONES for ordinary metrics and
    FUELS for the mix (one place at a time)."""
    now = now or datetime.now(UTC)
    metric = METRIC_BY_ID.get(metric_id)
    if metric is None:
        return {"available": False, "problem": "metric",
                "message": f"Unknown metric {metric_id!r}.",
                "known_metrics": [m["id"] for m in METRICS]}
    zones = [z for z in zones if z in ZONE_REGISTRY][:8]
    if not zones:
        return {"available": False, "problem": "zone",
                "message": "No known zone given.", "examples": EXAMPLES}
    year_to = min(year_to or now.year, now.year)

    monthly = year_from == year_to and year_from is not None
    fmt = "%Y-%m" if monthly else "%Y"
    start_ts = (int(datetime(year_from, 1, 1, tzinfo=UTC).timestamp())
                if year_from is not None else None)
    end_ts = int(datetime(year_to + 1, 1, 1, tzinfo=UTC).timestamp())

    coverage: list[str] = []
    fuels_mode = metric.get("columns") == "fuels"
    weighted_capture = False

    if fuels_mode:
        # One place; each fuel the zone reports becomes a column.
        zone = zones[0]
        if len(zones) > 1:
            coverage.append(
                f"The mix answers one place at a time — showing {_zone_label(zone)}."
            )
        gen_keys = [k for (k,) in db.query(SeriesDim.key)
                    .filter(SeriesDim.key.like("gen.%")).order_by(SeriesDim.key).all()]
        per_col: dict[str, dict[str, tuple[float, int]]] = {}
        for key in gen_keys:
            agg = _aggregate(db, key, zone, fmt, metric["agg"], start_ts, end_ts)
            if agg:
                per_col[key.removeprefix("gen.")] = agg
        from backend.power.entsoe_grid import PSR_LABELS
        column_labels = {c: PSR_LABELS.get(c, c) for c in per_col}
        oldest = min((o for o in (_oldest(db, f"gen.{c}", zone) for c in per_col)
                      if o is not None), default=None)
        note = _coverage_note(_zone_label(zone), oldest, year_from, year_to, monthly)
        if note:
            coverage.append(note)
        display_zones = [zone]
    else:
        per_col = {}
        # Capture factors: the YEARLY figure is generation-weighted from raw
        # hours (see _capture_factor_yearly); monthly keeps the stored exact
        # monthly factors.
        weighted_capture = bool(metric.get("capture_psr")) and not monthly
        oldest_series = ([f"gen.{metric['capture_psr']}"] if weighted_capture
                         else metric["series"])
        for zone in zones:
            if weighted_capture:
                combined = _capture_factor_yearly(db, metric["capture_psr"], zone,
                                                  start_ts, end_ts)
            else:
                combined = {}
                for skey in metric["series"]:
                    for p, (v, n) in _aggregate(db, skey, zone, fmt, metric["agg"],
                                                start_ts, end_ts).items():
                        pv, pn = combined.get(p, (0.0, 0))
                        combined[p] = (pv + v, max(pn, n))
            if combined:
                per_col[zone] = combined
            oldest = min((o for o in (_oldest(db, s, zone) for s in oldest_series)
                          if o is not None), default=None)
            if oldest is None:
                coverage.append(f"{_zone_label(zone)}: no {metric['label']} data on record.")
            else:
                note = _coverage_note(_zone_label(zone), oldest, year_from, year_to, monthly)
                if note:
                    coverage.append(note)
        column_labels = {z: _zone_label(z) for z in zones}
        display_zones = zones

    if not per_col:
        return {"available": False,
                "message": "Understood, but no data in that window.",
                "interpreted": _echo(metric, display_zones, year_from, year_to, monthly),
                "coverage": coverage}

    columns = list(per_col)
    periods = sorted({p for c in per_col.values() for p in c})
    rows = [
        {"period": p, **{c: round(per_col[c][p][0], 2) for c in columns if p in per_col[c]}}
        for p in periods
    ]

    # The takeaway sentence uses complete periods only — the running year/month
    # is a fragment wearing a period's label.
    current = now.strftime(fmt)
    complete = [p for p in periods if p < current] or periods
    if fuels_mode:
        sentence = _sentence_fuels(display_zones[0], per_col, column_labels,
                                   complete, metric["unit"])
    else:
        sentence = _sentence(metric, columns, per_col, complete)

    if fuels_mode:
        download = (f"/api/v1/genmix?zone={display_zones[0]}"
                    + (f"&start={year_from}-01-01" if year_from else "")
                    + f"&end={year_to + 1}-01-01&resolution=monthly&format=csv")
    else:
        primary = metric["series"][0]
        download = (f"/api/v1/series?series={primary}&zone={columns[0]}"
                    + (f"&start={year_from}-01-01" if year_from else "")
                    + f"&end={year_to + 1}-01-01&format=csv")

    return {
        "available": True,
        "interpreted": _echo(metric, display_zones, year_from, year_to, monthly),
        "unit": metric["unit"],
        "agg": metric["agg"],
        "columns": columns,
        "column_labels": column_labels,
        "column_kind": "fuels" if fuels_mode else "zones",
        "rows": rows,
        "sentence": sentence,
        "coverage": coverage,
        "partial_period": current if any(p == current for p in periods) else None,
        "download_url": download,
        "note": ("Descriptive aggregation of the published record — "
                 + ("totals per period. " if metric["agg"] == "sum" else "mean level per period. ")
                 + ("Generation-weighted annual factor (Σ price·gen ÷ Σ gen, ÷ the year's "
                    "baseload mean) — an unweighted mean of monthly factors would overweight "
                    "winter and read higher. "
                    if weighted_capture else
                    (metric.get("combine_note", "") and f"Composite: {metric['combine_note']}. "))
                 + "Not a forecast."),
    }


def answer(db: Session, q: str, *, now: datetime | None = None) -> dict:
    """Text front-end for the same answerer — kept for the API and the tests;
    the app's filter UI calls answer_structured directly."""
    parsed = parse_query(q)
    if not parsed["ok"]:
        return {"available": False, "query": q,
                **{k: v for k, v in parsed.items() if k != "ok"}}
    out = answer_structured(db, parsed["metric"]["id"], parsed["zones"],
                            parsed["year_from"], parsed["year_to"], now=now)
    return {"query": q, **out}


def _echo(metric: dict, zones: list[str], year_from, year_to, monthly: bool) -> dict:
    return {
        "metric": metric["label"],
        "series": metric["series"] or ["gen.<PSR>"],
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
    zc = per_zone.get(z0, {})
    have = [p for p in periods if p in zc]
    if not have:
        return ""
    latest = have[-1]
    parts = [f"{_zone_label(z0)}: {_fmt_value(zc[latest][0], unit)} in {latest}"]
    if len(have) > 1:
        first = have[0]
        parts.append(f"vs {_fmt_value(zc[first][0], unit)} in {first}")
        peak = max(have, key=lambda p: zc[p][0])
        if peak not in (first, latest):
            parts.append(f"peak {_fmt_value(zc[peak][0], unit)} in {peak}")
    if len(zones) > 1:
        others = [z for z in zones[1:] if latest in per_zone.get(z, {})][:2]
        for z in others:
            parts.append(f"{_zone_label(z)}: {_fmt_value(per_zone[z][latest][0], unit)} in {latest}")
    return " · ".join(parts) + "."


def _sentence_fuels(zone: str, per_col: dict, labels: dict, periods: list[str],
                    unit: str) -> str:
    """Top fuels in the latest complete period, with the change since the
    first period for the leader — the mix question in one line."""
    if not periods:
        return ""
    latest = periods[0] if len(periods) == 1 else periods[-1]
    latest_vals = sorted(
        ((c, per_col[c][latest][0]) for c in per_col if latest in per_col[c]),
        key=lambda cv: -cv[1],
    )
    if not latest_vals:
        return ""
    top = latest_vals[:3]
    parts = [f"{_zone_label(zone)} {latest}: "
             + ", ".join(f"{labels.get(c, c)} {_fmt_value(v, unit)}" for c, v in top)]
    first = periods[0]
    lead = top[0][0]
    if first != latest and first in per_col.get(lead, {}):
        parts.append(
            f"{labels.get(lead, lead)} was {_fmt_value(per_col[lead][first][0], unit)} in {first}"
        )
    return " · ".join(parts) + "."
