"""Embeddable SVG badges — tiny, dependency-free status images for READMEs/dashboards.

GET /api/v1/badge/{zone}/{metric}.svg   metric in {price, load}

Template-based f-string SVG (no cairosvg/matplotlib/pillow — one more dependency for a
20x220px pill is not worth it). Reads go through the SAME bounded paths every other v1
endpoint uses (PowerPriceDaily for price, hourly_store.read_hourly for load) — this route
adds no new query patterns, just a tiny image encoding of numbers other endpoints already
serve as JSON.

Badges are rendered as <img> tags inside someone else's README/dashboard, which the
*viewer's* browser (or a caching proxy/bot) fetches independently of anything Obsyd
controls — hence the 15-minute Cache-Control (badges refresh far slower than the desk's
own panels) and the unconditional HTTP 200: an <img> with a broken-image icon in a
stranger's README reflects badly on the whole project, so an unknown zone/metric or a
momentary data gap degrades to a neutral grey "no data" pill rather than a 404/500 that
GitHub renders as a broken image.
"""
from __future__ import annotations

from datetime import datetime, timezone
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.energy import PowerPriceDaily
from backend.power.hourly_store import read_hourly
from backend.power.zones import POWER_ZONES
from backend.routes.api_v1 import _rate_limit  # same v1 per-IP budget — badges ARE v1 traffic

router = APIRouter(prefix="/api/v1/badge", tags=["embed"])

VALID_METRICS = {"price", "load"}

#: A viewer's browser/bot refetches an <img> on its own schedule — 15 min matches the
#: ~30-min ingest cadence closely enough without hammering us on every README render.
CACHE_CONTROL = "public, max-age=900"

_ATTRIBUTION = "data: ENTSO-E Transparency Platform, via obsyd.dev"

# ── Flat, dark badge chrome — same palette family as the desk (cyan-glow accent,
#    #0f1115-ish surface) so a badge dropped into a README still reads as "Obsyd". ──
_HEIGHT = 20
_PAD_X = 8
_CHAR_W = 6.35  # rough average glyph width @ 11px sans-serif — generous, never clips
_FONT = "Verdana,Geneva,DejaVu Sans,sans-serif"
_FONT_SIZE = 11
_MIN_WIDTH = 90

_OK_BG = "#12141a"
_OK_BORDER = "#262a33"
_OK_TEXT = "#c9ccd6"

_GREY_BG = "#1a1c22"
_GREY_BORDER = "#2a2d36"
_GREY_TEXT = "#6b7280"


def _badge_svg(text: str, *, bg: str, border: str, fill: str, title: str) -> str:
    width = max(_MIN_WIDTH, int(_PAD_X * 2 + len(text) * _CHAR_W))
    esc_text = escape(text)
    esc_title = escape(title)
    # `escape()` alone only handles & < > — safe for an ELEMENT BODY (<title>/<text>
    # above and below), but `aria-label` is an ATTRIBUTE VALUE: a `"` in `text` would
    # close the attribute early and let whatever follows (e.g. a zone path segment
    # like `x" onload="alert(1)//`) be parsed as a new attribute. `text` here can
    # originate straight from the URL's `{zone}` path segment (see the unknown-
    # zone/metric branch in `badge()` below), so it's untrusted — escape quotes too,
    # for this attribute sink specifically.
    esc_attr = escape(text, {'"': "&quot;", "'": "&apos;"})
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{_HEIGHT}" '
        f'role="img" aria-label="{esc_attr}">'
        f"<title>{esc_title}</title>"
        f'<rect width="{width}" height="{_HEIGHT}" rx="3" fill="{bg}"/>'
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{_HEIGHT - 1}" rx="3" '
        f'fill="none" stroke="{border}"/>'
        f'<text x="{_PAD_X}" y="14" font-family="{_FONT}" font-size="{_FONT_SIZE}" '
        # textLength + lengthAdjust: the SVG renderer compresses glyph spacing (and,
        # if still needed, the glyphs themselves) to fit exactly this width instead of
        # letting the text overflow the pill. `_CHAR_W` is only an average — real
        # Verdana-at-11px glyphs (especially caps/digits) run a bit wider, so without
        # this a long badge could clip past its own rounded-rect background.
        f'textLength="{width - 2 * _PAD_X}" lengthAdjust="spacingAndGlyphs" '
        f'fill="{fill}">{esc_text}</text>'
        f"</svg>"
    )


def _ok_badge(text: str) -> str:
    return _badge_svg(
        text, bg=_OK_BG, border=_OK_BORDER, fill=_OK_TEXT,
        title=f"OBSYD · {text} — {_ATTRIBUTION}",
    )


def _no_data_badge(text: str) -> str:
    return _badge_svg(
        text, bg=_GREY_BG, border=_GREY_BORDER, fill=_GREY_TEXT,
        title=f"OBSYD · {text}",
    )


def _fmt_eur_per_mwh(value: float) -> str:
    """€X/MWh — sign BEFORE the currency symbol for a negative price (e.g. "−€5/MWh",
    never Python's default "€-5/MWh": the minus reads as "negative five euros", not
    "euro-negative-five"). Negative day-ahead prices are a real, headline state for
    this product (renewable oversupply), not an edge case to hide.

    `round()` on a float with no `ndigits` returns a plain Python `int` — ints have no
    signed zero, so a value like -0.2 rounds to the int `0`, not a float `-0.0` that
    would otherwise format as the confusing "€-0/MWh".
    """
    n = round(value)
    return f"−€{abs(n)}/MWh" if n < 0 else f"€{n}/MWh"


def _svg_response(svg: str) -> Response:
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": CACHE_CONTROL},
    )


@router.get("/{zone}/{metric}.svg")
def badge(
    zone: str,
    metric: str,
    db: Session = Depends(get_db),
    _rl: None = Depends(_rate_limit),
):
    """One data point as a tiny flat SVG pill — for READMEs, wikis, status dashboards.

    metric=price  -> latest PowerPriceDaily mean (EUR/MWh) + its delivery date.
    metric=load   -> latest published load.actual hourly point (GW) + its UTC hour.

    Unknown zone/metric or genuinely no data yet: a neutral grey "no data" pill,
    HTTP 200 (see module docstring — badges must never break a README). A transient
    DB error inside the reads degrades to the same grey pill; the one residual case
    this guard cannot reach is a failure inside the `get_db` dependency itself,
    which still surfaces as a 500 before this function runs.
    """
    if zone not in POWER_ZONES or metric not in VALID_METRICS:
        return _svg_response(_no_data_badge(f"{zone} · no data"))

    zone_label = POWER_ZONES[zone]["label"]

    try:
        if metric == "price":
            row = (
                db.query(PowerPriceDaily)
                .filter(PowerPriceDaily.zone == zone)
                .order_by(PowerPriceDaily.date.desc())
                .first()
            )
            if row is None:
                return _svg_response(_no_data_badge(f"{zone_label} · no data"))
            text = f"{zone_label} day-ahead · {_fmt_eur_per_mwh(row.mean_price)} · {row.date}"
            return _svg_response(_ok_badge(text))

        # metric == "load"
        now = datetime.now(timezone.utc)
        start_ts = int(now.timestamp()) - 6 * 3600  # a handful of recent hours — bounded read
        points = read_hourly(db, "load.actual", zone, start_ts=start_ts, max_rows=12)
        if not points:
            return _svg_response(_no_data_badge(f"{zone_label} · no data"))
        ts, value = points[-1]
        hh = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")
        gw = value / 1000.0
        text = f"{zone_label} load · {gw:.1f} GW · {hh} UTC"
        return _svg_response(_ok_badge(text))
    except Exception:
        # The docstrings promise an unconditional 200 — a momentary DB hiccup must
        # render as the same grey pill as "no data yet", never a broken image.
        return _svg_response(_no_data_badge(f"{zone_label} · no data"))
