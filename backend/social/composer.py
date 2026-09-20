"""Choose the day's post. Deterministic, no LLM, Posture B.

Order of preference each day:

    1. EVENT      something genuinely notable just happened — an all-time
                  day-ahead price record, or a zone far ABOVE its own norm
                  today. These are the "the desk noticed X" posts.
    2. FRANCHISE  the weekday's recurring format (backend/social/franchises),
                  with fresh data and a week-over-week rotating subject.
    3. nothing    neither fired → post nothing. Silence is a valid day.

`event_post()` is pure over already-fetched data (unit-tested); `compose()` is
the orchestrator that reads in-process and runs the cascade.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.social.post import Post

# ── Event gates ──────────────────────────────────────────────────────────────
MIN_FRESH_ZONES = 20
PRICE_SANE_MIN, PRICE_SANE_MAX = -600.0, 4000.0
SPIKE_MIN_Z = 3.0            # HIGH only — a cheap day is not a story
SPIKE_MIN_DELTA_EUR = 40.0   # and far in € from the field (radar's floor)


def _fresh_priced(zones: list[dict]) -> list[dict]:
    return [z for z in zones
            if not z.get("stale") and z.get("price_close") is not None
            and PRICE_SANE_MIN <= z["price_close"] <= PRICE_SANE_MAX]


def _bars_from_overview(zones: list[dict], highlight_zone: str) -> dict:
    ranked = sorted(zones, key=lambda z: z["price_close"])
    hl = next((z.get("zone_label", z["zone"]) for z in zones if z["zone"] == highlight_zone), None)
    return {"card": "bars", "title": "Europe's power prices today",
            "subtitle": "day-ahead, latest day, cheapest → priciest",
            "rows": [{"label": z.get("zone_label", z["zone"]), "value": z["price_close"],
                      "disp": f"€{z['price_close']:,.0f}"} for z in ranked],
            "highlight": hl, "footer": "obsyd.dev · €/MWh · ENTSO-E"}


def event_post(overview: dict, records: list[dict], now: datetime) -> Post | None:
    """A record or a HIGH spike, if either is present. Pure."""
    zones = _fresh_priced(overview.get("zones", []))
    if len(zones) < MIN_FRESH_ZONES:
        return None
    today = now.date()

    # 1) all-time day-ahead price record (canonical hourly series only — the
    # .qh/.hh variants have short histories and must not wear "on record").
    for r in records:
        if r.get("series") != "price.dayahead" or not r.get("fresh"):
            continue
        kind = r.get("kind", "max")
        sup = "highest" if kind == "max" else "lowest"
        z = r.get("zone_label") or r.get("zone", "")
        val = r["value"]
        return Post(
            kind="record",
            text=(f"{z} just printed its {sup} day-ahead power price on record: "
                  f"€{val:,.0f}/MWh.\n\nAcross the full hourly history on our desk — "
                  f"descriptive, from the official record."),
            alt_text=f"{z} day-ahead {sup} on record: €{val:,.0f}/MWh.",
            card=_bars_from_overview(zones, r.get("zone", "")),
            reply="Full history + free API → obsyd.dev",
            dedup_key=f"{today}-record-price.dayahead-{kind}",
        )

    # 2) a zone far ABOVE its own 30-day norm today
    cand = [z for z in zones if (z.get("price_z") or 0) >= SPIKE_MIN_Z]
    if cand:
        prices = sorted(z["price_close"] for z in zones)
        med = prices[len(prices) // 2]
        cand = [z for z in cand if z["price_close"] - med >= SPIKE_MIN_DELTA_EUR]
        if cand:
            z = max(cand, key=lambda x: x["price_z"])
            lbl = z.get("zone_label", z["zone"])
            return Post(
                kind="price_spike",
                text=(f"{lbl} day-ahead power is unusually expensive today: "
                      f"€{z['price_close']:,.0f}/MWh ({z['price_z']:+.1f}σ above its 30-day norm).\n\n"
                      f"Descriptive, from the official record — not a forecast."),
                alt_text=f"{lbl} day-ahead €{z['price_close']:,.0f}/MWh, {z['price_z']:+.1f} sigma above norm.",
                card=_bars_from_overview(zones, z["zone"]),
                reply="Live across 38 zones + free API → obsyd.dev",
                dedup_key=f"{today}-spike-{z['zone']}",
            )
    return None


def compose(db: Session, *, now: datetime | None = None) -> Post | None:
    """Orchestrate the cascade for `now`. Reads in-process."""
    now = now or datetime.now(UTC)

    from backend.routes.power import build_power_overview
    from backend.social import franchises
    from backend.social.records import fresh_price_records

    overview = build_power_overview(db)
    records = fresh_price_records(db, overview, now=now)

    post = event_post(overview, records, now)
    if post is not None:
        return post
    return franchises.for_weekday(db, now)
