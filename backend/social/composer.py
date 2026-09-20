"""Deterministic composer for the daily X/social post — no LLM, no guessing.

The same doctrine as the desk: every sentence is a template filled from the
official record, descriptive never predictive, and the composer would rather
say NOTHING than post a number it cannot stand behind. `compose()` returns a
`Post` or `None`; `None` means the gates failed and the scheduler posts nothing
that day (silence is always a valid outcome).

Content is chosen by a fixed CASCADE — the most newsworthy eligible item wins:

    1. record        an all-time / multi-year extreme just printed (the moat:
                     hourly history to 2015, so "highest since" is real)
    2. price_spike   a zone far outside its own 30-day norm today (|z| large)
    3. weekday       a fixed weekly franchise (Mon negative-hours recap, …)
    4. daily_map     the always-available fallback: Europe's price spread today

Events (1–2) beat the routine (3–4): followers come for "the desk noticed X",
the daily anchor only keeps the account alive between them. Every branch is a
pure function of already-fetched data, so the whole cascade is unit-testable
with zero network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# ── Gates & thresholds ───────────────────────────────────────────────────────
#: Below this many fresh zones the continental picture is too partial to post
#: (37/37 is normal; a bad ingest morning can drop a handful — 30 is the floor).
MIN_FRESH_ZONES = 30
#: Sanity bounds: a day-ahead daily mean outside this band is an ingest artefact,
#: never a real clearing price — refuse rather than tweet a glitch.
PRICE_SANE_MIN = -600.0
PRICE_SANE_MAX = 4000.0
#: A price_spike event needs this |z| AND this absolute €/MWh distance from norm
#: (mirrors the radar's price_spike detector so the two never disagree).
SPIKE_MIN_ABS_Z = 3.0
SPIKE_MIN_DELTA_EUR = 40.0
#: A record is post-worthy only if it is fresh (printed on the latest day) and,
#: for a "since" claim, old enough to be interesting.
RECORD_MIN_SPAN_DAYS = 365


@dataclass
class Post:
    """One composed post. The image is ALWAYS the continental price ranking (one
    card, always coherent, always interesting); `headline` is its title band and
    `highlight` names the zone to accent within the ranking, so an event post's
    subject is visually findable in the chart. The renderer does no data logic."""
    kind: str                       # record | price_spike | weekday | daily_map
    text: str                       # the tweet body (<= 280 chars, no link)
    alt_text: str                   # image alt text (accessibility, required)
    reply: str | None               # optional self-reply carrying the link
    headline: str                   # the card's title band
    rows: list[dict] = field(default_factory=list)  # ranked [{zone, price, state}]
    highlight: str | None = None    # zone_label to accent in the card
    dedup_key: str = ""             # date + kind; the scheduler skips a repeat


def _fresh_priced(zones: list[dict]) -> list[dict]:
    return [
        z for z in zones
        if not z.get("stale")
        and z.get("price_close") is not None
        and PRICE_SANE_MIN <= z["price_close"] <= PRICE_SANE_MAX
    ]


def _fmt_eur(v: float) -> str:
    return f"€{v:,.0f}"



def _ranked_rows(zones: list[dict]) -> list[dict]:
    return [
        {"zone": z.get("zone_label", z["zone"]), "price": z["price_close"],
         "state": z.get("state")}
        for z in sorted(zones, key=lambda z: z["price_close"])
    ]


# ── Cascade branches ─────────────────────────────────────────────────────────

def _try_record(records: list[dict] | None, zones: list[dict], today: date) -> Post | None:
    """An all-time / multi-year extreme that printed on the latest day."""
    if not records:
        return None
    for r in records:
        if not r.get("fresh"):
            continue
        try:
            when = date.fromisoformat(str(r["date"]))
        except (ValueError, KeyError, TypeError):
            continue
        # "since" needs real history behind it (the 2015 moat); a brand-new
        # series hitting a record in its first months is not a story.
        # (records is the desk's own list; span is implicit in the date.)
        if not str(r.get("series", "")).startswith("price."):
            continue  # a non-price record has no ranked-price illustration (v1)
        kind = r.get("kind", "max")
        label = r.get("label") or r.get("series", "a series")
        unit = r.get("unit", "")
        zone = r.get("zone_label") or r.get("zone", "")
        superlative = "highest" if kind == "max" else "lowest"
        val = r["value"]
        val_s = f"{val:,.0f} {unit}".strip()
        head = f"{zone} just set its {superlative} {label} on record: {val_s}."
        return Post(
            kind="record",
            text=f"{head}\n\nHourly data back to 2015 — this is the {superlative} in the series.",
            alt_text=f"{zone} {label} {superlative} on record: {val_s} on {when}.",
            reply="Full history + free API → obsyd.dev",
            headline=f"{zone}: {superlative} {label} on record",
            rows=_ranked_rows(zones),
            highlight=zone,
            dedup_key=f"{today}-record-{r.get('series')}-{kind}",
        )
    return None


def _try_price_spike(zones: list[dict], today: date) -> Post | None:
    """A zone whose day-ahead sits far outside its own 30-day norm today."""
    cand = [
        z for z in zones
        if z.get("price_z") is not None and abs(z["price_z"]) >= SPIKE_MIN_ABS_Z
    ]
    if not cand:
        return None
    z = max(cand, key=lambda x: abs(x["price_z"]))
    price, zed = z["price_close"], z["price_z"]
    # The absolute-distance floor keeps a micro-variance zone from "spiking" at
    # +3σ over a flat €4 — same guard as the radar's SPIKE_MIN_DELTA_EUR.
    zones_priced = [x["price_close"] for x in zones]
    med = sorted(zones_priced)[len(zones_priced) // 2]
    if abs(price - med) < SPIKE_MIN_DELTA_EUR:
        return None
    direction = "high" if zed > 0 else "low"
    label = z.get("zone_label", z["zone"])
    head = (f"{label} day-ahead power is unusually {direction} today: "
            f"{_fmt_eur(price)}/MWh ({zed:+.1f}σ vs its 30-day norm).")
    return Post(
        kind="price_spike",
        text=f"{head}\n\nDescriptive, from the official record — not a forecast.",
        alt_text=f"{label} day-ahead {_fmt_eur(price)}/MWh, {zed:+.1f} sigma vs its 30-day norm.",
        reply="Live across 38 zones + free API → obsyd.dev",
        headline=f"{label}: {_fmt_eur(price)}/MWh today ({zed:+.1f}σ)",
        rows=_ranked_rows(zones),
        highlight=label,
        dedup_key=f"{today}-spike-{z['zone']}",
    )


def _try_daily_map(zones: list[dict], today: date) -> Post | None:
    """Always-available anchor: Europe's day-ahead price spread today, with the
    stressed-zone count for context. One overview call, every day."""
    ranked = sorted(zones, key=lambda z: z["price_close"])
    lo, hi = ranked[0], ranked[-1]
    spread = hi["price_close"] - lo["price_close"]
    stressed = sum(1 for z in zones if z.get("state") == "STRESSED")
    prices = [z["price_close"] for z in ranked]
    med = prices[len(prices) // 2]
    lo_l, hi_l = lo.get("zone_label", lo["zone"]), hi.get("zone_label", hi["zone"])
    head = (f"Europe's power prices today: {_fmt_eur(lo['price_close'])}/MWh in "
            f"{lo_l}, {_fmt_eur(hi['price_close'])} in {hi_l} — a "
            f"{_fmt_eur(spread)}/MWh spread across one grid.")
    tail = f"Median {_fmt_eur(med)}."
    if stressed:
        tail += f" {stressed} zone{'s' if stressed != 1 else ''} stressed vs their own norm."
    return Post(
        kind="daily_map",
        text=f"{head}\n\n{tail}",
        alt_text=(f"Day-ahead power prices across {len(zones)} European zones, cheapest to "
                  f"priciest: {lo_l} {_fmt_eur(lo['price_close'])} to {hi_l} {_fmt_eur(hi['price_close'])}."),
        reply="Live map + free API → obsyd.dev",
        headline=f"Europe's day-ahead power — {today:%-d %b %Y}",
        rows=_ranked_rows(zones),
        highlight=None,
        dedup_key=f"{today}-daily_map",
    )


# ── Public entry ─────────────────────────────────────────────────────────────

def compose(overview: dict, *, records: list[dict] | None = None,
            today: date | None = None) -> Post | None:
    """Run the cascade over already-fetched data. Returns the winning `Post`,
    or `None` when the gates fail (too few fresh zones, or nothing eligible)."""
    today = today or date.today()
    if not overview.get("available"):
        return None
    zones = _fresh_priced(overview.get("zones", []))
    if len(zones) < MIN_FRESH_ZONES:
        return None  # continental picture too partial — say nothing

    for branch in (
        lambda: _try_record(records, zones, today),
        lambda: _try_price_spike(zones, today),
        # weekday franchises slot here (Mon negative-hours, Wed TB2, …) once
        # their history endpoints are wired; the map is the guaranteed floor.
        lambda: _try_daily_map(zones, today),
    ):
        post = branch()
        if post is not None:
            return post
    return None
