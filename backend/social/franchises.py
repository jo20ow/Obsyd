"""The weekly franchise rotation — one recurring format per weekday, each with
FRESH data, and the subject-heavy formats rotating week-over-week so the feed
never repeats itself. Deterministic (no LLM), descriptive (Posture B): every
sentence is a template over the official record.

    Mon  price_leaderboard   cheapest ↔ priciest zone, last 7d
    Tue  negative_hours      hours below €0 by zone, last 7d
    Wed  battery_tb2         what a 2h battery earned, last 7d      [premium series]
    Thu  co2_clean_dirty     cleanest vs dirtiest grid, yesterday
    Fri  renewable_peak      the week's highest wind+solar-vs-demand zone-day
    Sat  trend_callback      "then vs now" — a rotating multi-year story (the moat)
    Sun  europe_now          the continental price snapshot

The composer checks an EVENT override first (records / real high spikes); a
franchise is the routine that fills the ordinary day. Any builder may return
None (missing data) — the composer then falls through and, failing everything,
posts nothing. Silence is a valid day.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.power.hourly_store import day_hour_ts, read_hourly
from backend.power.zones import ENABLED_ZONES, ZONE_REGISTRY
from backend.social.post import Post

_DAY = 86400


def _label(zone: str) -> str:
    return ZONE_REGISTRY.get(zone, {}).get("label", zone)


def _country(zone: str) -> str:
    """A human place name for prose (leaderboards keep the zone code)."""
    return _COUNTRY.get(zone, _label(zone))


_COUNTRY = {
    "DE_LU": "Germany", "FR": "France", "NL": "the Netherlands", "BE": "Belgium",
    "AT": "Austria", "ES": "Spain", "PT": "Portugal", "PL": "Poland", "CZ": "Czechia",
    "HU": "Hungary", "RO": "Romania", "GR": "Greece", "FI": "Finland", "CH": "Switzerland",
    "GB": "Great Britain", "SK": "Slovakia", "SI": "Slovenia", "HR": "Croatia", "BG": "Bulgaria",
}


def _window(days_back: int, *, now: datetime) -> tuple[int, int]:
    end = day_hour_ts(now.date().isoformat(), 0)          # today 00:00 UTC
    return end - days_back * _DAY, end


def _zone_agg(db: Session, series: str, start_ts: int, end_ts: int, how: str) -> dict[str, float]:
    """{zone: aggregate} of `series` over [start, end) for every enabled zone."""
    out: dict[str, float] = {}
    for z in ENABLED_ZONES:
        pts = [v for _, v in read_hourly(db, series, z, start_ts, end_ts) if v is not None]
        if not pts:
            continue
        out[z] = sum(pts) if how == "sum" else sum(pts) / len(pts)
    return out


def _reply(link: str = "obsyd.dev") -> str:
    return f"Live across 38 European zones + free API → {link}"


# ── Mon — price leaderboard ──────────────────────────────────────────────────

def price_leaderboard(db: Session, now: datetime) -> Post | None:
    s, e = _window(7, now=now)
    means = _zone_agg(db, "price.dayahead", s, e, "mean")
    if len(means) < 20:
        return None
    ranked = sorted(means.items(), key=lambda kv: kv[1])
    lo_z, lo_v = ranked[0]
    hi_z, hi_v = ranked[-1]
    rows = [{"label": _label(z), "value": v, "disp": f"€{v:,.0f}"} for z, v in ranked]
    text = (f"Europe's power prices last week: cheapest in {_label(lo_z)} at "
            f"€{lo_v:,.0f}/MWh, priciest in {_label(hi_z)} at €{hi_v:,.0f} — a "
            f"€{hi_v - lo_v:,.0f}/MWh gap across one connected grid.")
    return Post(
        kind="price_leaderboard",
        text=text,
        alt_text=(f"Day-ahead power price by European zone, 7-day mean, cheapest to priciest: "
                  f"{_label(lo_z)} €{lo_v:,.0f} to {_label(hi_z)} €{hi_v:,.0f}."),
        card={"card": "bars", "title": "Europe's power prices last week",
              "subtitle": "day-ahead, 7-day mean, cheapest → priciest",
              "rows": rows, "highlight": _label(hi_z),
              "footer": "obsyd.dev · €/MWh · ENTSO-E"},
        reply=_reply(),
        dedup_key=f"{now.date()}-price_leaderboard",
    )


# ── Tue — negative-price hours ───────────────────────────────────────────────

def negative_hours(db: Session, now: datetime) -> Post | None:
    s, e = _window(7, now=now)
    tot = _zone_agg(db, "price.negative_hours", s, e, "sum")
    tot = {z: v for z, v in tot.items() if v and v > 0}
    if not tot:
        return None
    ranked = sorted(tot.items(), key=lambda kv: -kv[1])
    total = sum(tot.values())
    top_z, top_v = ranked[0]
    rows = [{"label": _label(z), "value": v, "disp": f"{v:,.0f} h"} for z, v in ranked[:14]]
    text = (f"Last week Europe saw {total:,.0f} hours of negative power prices — "
            f"moments when there was more wind & solar than demand.\n\n"
            f"{_label(top_z)} led with {top_v:,.0f} h.")
    return Post(
        kind="negative_hours",
        text=text,
        alt_text=(f"Hours of negative power prices by zone last week; {_label(top_z)} highest "
                  f"at {top_v:,.0f} of {total:,.0f} total."),
        card={"card": "bars", "title": "Negative power prices last week",
              "subtitle": "hours below €0, by zone — renewable oversupply",
              "rows": rows, "highlight": _label(top_z),
              "footer": "obsyd.dev · hours below €0, day-ahead · ENTSO-E"},
        reply=_reply(),
        dedup_key=f"{now.date()}-negative_hours",
    )


# ── Wed — battery TB2 leaderboard ────────────────────────────────────────────

def battery_tb2(db: Session, now: datetime) -> Post | None:
    s, e = _window(7, now=now)
    means = _zone_agg(db, "spread.tb2", s, e, "mean")
    if len(means) < 15:
        return None
    ranked = sorted(means.items(), key=lambda kv: -kv[1])
    rows = [{"label": _label(z), "value": v, "disp": f"€{v:,.0f}"} for z, v in ranked[:14]]
    top = ranked[:3]
    lead = ", ".join(f"{_label(z)} €{v:,.0f}" for z, v in top)
    text = (f"What a 2-hour battery could have earned in the day-ahead market last week "
            f"(top-bottom spread, €/MW-day):\n\n{lead}.\n\n"
            f"Buy low, sell high — the arbitrage the grid pays for flexibility.")
    return Post(
        kind="battery_tb2",
        text=text,
        alt_text=(f"2-hour battery day-ahead arbitrage (TB2) by zone last week; top: {lead}."),
        card={"card": "bars", "title": "What a 2h battery earned last week",
              "subtitle": "day-ahead top-bottom spread (TB2), 7-day mean",
              "rows": rows, "highlight": _label(top[0][0]),
              "footer": "obsyd.dev · €/MW-day, day-ahead only · TB convention: Modo"},
        reply=_reply(),
        dedup_key=f"{now.date()}-battery_tb2",
    )


# ── Thu — cleanest vs dirtiest grid (CO₂) ────────────────────────────────────

def co2_clean_dirty(db: Session, now: datetime) -> Post | None:
    # Yesterday's daily mean per zone from hourly lifecycle CO₂.
    y = (now.date() - timedelta(days=1))
    s = day_hour_ts(y.isoformat(), 0)
    e = s + _DAY
    means = _zone_agg(db, "co2.intensity.lifecycle", s, e, "mean")
    means = {z: v for z, v in means.items() if v is not None}
    if len(means) < 15:
        return None
    ranked = sorted(means.items(), key=lambda kv: kv[1])
    clean_z, clean_v = ranked[0]
    dirty_z, dirty_v = ranked[-1]
    ratio = dirty_v / clean_v if clean_v else 0
    rows = [{"label": _label(z), "value": v, "disp": f"{v:,.0f}g"} for z, v in ranked]
    text = (f"Cleanest power in Europe yesterday: {_label(clean_z)} at {clean_v:,.0f} g CO₂/kWh. "
            f"Dirtiest: {_label(dirty_z)} at {dirty_v:,.0f} g — {ratio:.0f}× more.\n\n"
            f"Same market, same day. (Estimated, production-based.)")
    return Post(
        kind="co2_clean_dirty",
        text=text,
        alt_text=(f"Estimated grid CO₂ intensity by zone yesterday, cleanest to dirtiest: "
                  f"{_label(clean_z)} {clean_v:,.0f}g to {_label(dirty_z)} {dirty_v:,.0f}g."),
        card={"card": "bars", "title": "Cleanest vs dirtiest grid yesterday",
              "subtitle": "estimated CO₂ intensity, g/kWh (lifecycle)",
              "rows": rows, "highlight": _label(clean_z),
              "footer": "obsyd.dev · est. gCO₂eq/kWh · IPCC AR5 factors × ENTSO-E mix"},
        reply=_reply(),
        dedup_key=f"{now.date()}-co2_clean_dirty",
    )


# ── Fri — renewable peak of the week ─────────────────────────────────────────

def renewable_peak(db: Session, now: datetime) -> Post | None:
    from backend.models.energy import PowerGrid

    lo = (now.date() - timedelta(days=8)).isoformat()
    rows = (db.query(PowerGrid)
            .filter(PowerGrid.date >= lo, PowerGrid.date < now.date().isoformat(),
                    PowerGrid.load_hours == 24, PowerGrid.gen_hours == 24)
            .all())
    best = None
    for r in rows:
        if not (r.load_mw and r.wind_mw is not None and r.solar_mw is not None):
            continue
        share = (r.wind_mw + r.solar_mw) / r.load_mw
        if best is None or share > best[0]:
            best = (share, r)
    if best is None or best[0] < 0.8:
        return None
    share, r = best
    pct = round(share * 100)
    wind_pct = round(r.wind_mw / r.load_mw * 100)
    text = (f"This week's renewable peak: on {r.date}, wind & solar in {_label(r.zone)} met "
            f"{pct}% of the region's electricity demand"
            + (" — more than it used." if pct >= 100 else ".") +
            f"\n\nWind alone: {wind_pct}%.")
    return Post(
        kind="renewable_peak",
        text=text,
        alt_text=(f"{_label(r.zone)} on {r.date}: wind and solar met {pct}% of demand, "
                  f"wind alone {wind_pct}%."),
        card={"card": "bars", "title": f"Renewable peak: {_label(r.zone)}, {r.date}",
              "subtitle": "wind + solar vs demand, daily mean",
              "rows": [
                  {"label": "Electricity demand", "value": 100, "disp": "100%"},
                  {"label": "Wind + solar", "value": pct, "disp": f"{pct}%"},
                  {"label": "Wind alone", "value": wind_pct, "disp": f"{wind_pct}%"},
              ], "highlight": "Wind + solar",
              "footer": "obsyd.dev · wind+solar vs demand, daily mean · ENTSO-E"},
        reply=_reply(),
        dedup_key=f"{now.date()}-renewable_peak",
    )


# ── Sat — the rotating "then vs now" trend (the moat) ────────────────────────
#: Each entry rotates in by ISO-week: (metric_id, [zones], title, colors).
_TREND_ROTATION = [
    ("negative_hours", ["ES", "FI"], "From never to routine: negative power prices",
     "hours below €0 per year", ["blue", "amber"]),
    ("capture_solar", ["DE_LU"], "Solar's shrinking payday in Germany",
     "solar capture factor (× baseload price)", ["blue"]),
    ("negative_hours", ["NL", "DE_LU"], "Negative power prices: the new normal",
     "hours below €0 per year", ["blue", "amber"]),
    ("capture_wind", ["DK1"], "What Danish wind earns, year by year",
     "wind capture factor (× baseload price)", ["blue"]),
    ("negative_hours", ["FR", "PL"], "Negative prices reach the nuclear & coal grids",
     "hours below €0 per year", ["blue", "amber"]),
]


def trend_callback(db: Session, now: datetime) -> Post | None:
    from backend.power.ask import answer_structured

    metric, zones, title, ysub, colors = _TREND_ROTATION[now.isocalendar().week % len(_TREND_ROTATION)]
    out = answer_structured(db, metric, zones, 2015, now.year - 1, now=now)
    if not out.get("available"):
        return None
    rows = out["rows"]
    years = sorted(int(r["period"]) for r in rows)
    if len(years) < 4:
        return None
    by_year = {int(r["period"]): r for r in rows}
    series = []
    for z, color in zip(zones, colors):
        series.append({"name": _country(z), "color": color,
                       "values": [by_year.get(y, {}).get(z) for y in years]})
    # A prose contrast: first vs last complete year, for the lead zone.
    lead = zones[0]
    vals = [(y, by_year.get(y, {}).get(lead)) for y in years]
    first = next(((y, v) for y, v in vals if v is not None), None)
    last = next(((y, v) for y, v in reversed(vals) if v is not None), None)
    if not first or not last:
        return None
    is_capture = metric.startswith("capture")
    fmt = (lambda v: f"×{v:.2f}") if is_capture else (lambda v: f"{v:,.0f} h")
    text = (f"{_country(lead)}, then vs now:\n\n"
            f"{first[0]}: {fmt(first[1])}\n{last[0]}: {fmt(last[1])}\n\n"
            + ("Europe's energy transition, made visible." if not is_capture
               else "The more you build, the less each unit earns when it runs — 'cannibalisation'."))
    return Post(
        kind="trend_callback",
        text=text,
        alt_text=f"{title}: {_country(lead)} {fmt(first[1])} in {first[0]} to {fmt(last[1])} in {last[0]}.",
        card={"card": "trend", "title": title, "subtitle": ysub,
              "years": years, "series": series,
              "footer": "obsyd.dev · " + ysub + " · ENTSO-E, since 2015"},
        reply=_reply(),
        dedup_key=f"{now.date()}-trend-{metric}-{'_'.join(zones)}",
    )


# ── Sun — Europe now snapshot ────────────────────────────────────────────────

def europe_now(db: Session, now: datetime) -> Post | None:
    from backend.routes.power import build_power_overview

    ov = build_power_overview(db)
    zs = [z for z in ov.get("zones", [])
          if not z.get("stale") and z.get("price_close") is not None]
    if len(zs) < 20:
        return None
    ranked = sorted(zs, key=lambda z: z["price_close"])
    lo, hi = ranked[0], ranked[-1]
    rows = [{"label": z.get("zone_label", z["zone"]), "value": z["price_close"],
             "disp": f"€{z['price_close']:,.0f}"} for z in ranked]
    spread = hi["price_close"] - lo["price_close"]
    text = (f"Europe's power right now: €{lo['price_close']:,.0f}/MWh in "
            f"{lo.get('zone_label', lo['zone'])}, €{hi['price_close']:,.0f} in "
            f"{hi.get('zone_label', hi['zone'])} — a €{spread:,.0f} spread across one grid.")
    return Post(
        kind="europe_now",
        text=text,
        alt_text=(f"Day-ahead power price across {len(zs)} European zones, cheapest to priciest: "
                  f"{lo.get('zone_label', lo['zone'])} €{lo['price_close']:,.0f} to "
                  f"{hi.get('zone_label', hi['zone'])} €{hi['price_close']:,.0f}."),
        card={"card": "bars", "title": "Europe's power prices right now",
              "subtitle": "day-ahead, latest day, cheapest → priciest",
              "rows": rows, "highlight": hi.get("zone_label", hi["zone"]),
              "footer": "obsyd.dev · €/MWh · ENTSO-E"},
        reply=_reply(),
        dedup_key=f"{now.date()}-europe_now",
    )


#: weekday (Mon=0 … Sun=6) → builder.
WEEKDAY = {
    0: price_leaderboard,
    1: negative_hours,
    2: battery_tb2,
    3: co2_clean_dirty,
    4: renewable_peak,
    5: trend_callback,
    6: europe_now,
}


def for_weekday(db: Session, now: datetime) -> Post | None:
    """The franchise for `now`'s weekday, or None if its data isn't there —
    the caller (composer) then falls through to the next fallback."""
    builder = WEEKDAY[now.weekday()]
    return builder(db, now)
