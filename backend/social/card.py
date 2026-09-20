"""Render the daily-post image — a ranked day-ahead price card, drawn directly
with Pillow (no browser, no SVG toolchain, no system libs beyond the Pillow
wheel). One card serves every post kind: the continental price ranking, with an
optional highlighted zone and a headline band.

Palette matches the desk's redesign vocabulary (ink-blue accent on an off-white
or dark ground) so a post reads as Obsyd at a glance. 1600×900 (16:9, X's
preferred single-image ratio), rendered at 2× and downscaled for crisp text.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── Layout (logical px, ×SCALE at draw time) ─────────────────────────────────
W, H = 800, 450
SCALE = 2
PAD = 32
ROW_H = 15
BAR_X = 150
BAR_MAX = W - PAD - 70  # right edge of the longest bar, leaving room for €value

# ── Palette (light ground; the account posts one consistent look) ────────────
BG = (250, 250, 249)          # off-white ground
INK = (23, 23, 23)            # near-black text
MUTED = (120, 120, 128)       # captions
ACCENT = (29, 78, 216)        # ink-blue (#1d4ed8) — the redesign accent
BAR = (191, 205, 236)         # bar fill — one color (length carries the price)
BAR_HI = (29, 78, 216)        # highlighted zone bar (the event subject)

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Helvetica.ttc",                       # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",           # Debian/Ubuntu
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        p = Path(path)
        if p.exists() and (("Bold" in path) == bold or path.endswith(".ttc")):
            try:
                return ImageFont.truetype(path, size * SCALE)
            except OSError:
                continue
    return ImageFont.load_default()


def render_card(headline: str, rows: list[dict], *, highlight: str | None = None,
                footer: str = "obsyd.dev · day-ahead €/MWh · ENTSO-E") -> bytes:
    """Draw the ranked card and return PNG bytes. `rows` = [{zone, price, state}]
    cheapest-first (the composer sorts them). At most the extremes+middle are
    labelled if the list is long, so text never collides."""
    img = Image.new("RGB", (W * SCALE, H * SCALE), BG)
    d = ImageDraw.Draw(img)

    f_head = _font(20, bold=True)
    f_row = _font(9)
    f_val = _font(9, bold=True)
    f_foot = _font(9)

    d.text((PAD * SCALE, PAD * SCALE), headline, font=f_head, fill=INK)
    d.line([(PAD * SCALE, (PAD + 30) * SCALE), ((W - PAD) * SCALE, (PAD + 30) * SCALE)],
           fill=ACCENT, width=2 * SCALE)

    if not rows:
        img = img.resize((W, H), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    prices = [r["price"] for r in rows]
    pmin, pmax = min(prices), max(prices)
    # Bars scale from the cheapest (short) to priciest (full width); a negative
    # price still gets a visible stub so the row is never empty.
    zero_ref = min(pmin, 0.0)
    denom = (pmax - zero_ref) or 1.0

    top = PAD + 44
    n = len(rows)
    # If the list is tall, thin the row height to fit without scrolling.
    row_h = min(ROW_H, (H - top - 30) / n)

    # ONE price encoding (bar length); color carries only the highlight, never a
    # second data axis — a "stressed" red bar on a cheap zone reads as expensive
    # and fights the length. The stress nuance lives in the post TEXT instead.
    for i, r in enumerate(rows):
        y = top + i * row_h
        yc = (y + row_h / 2) * SCALE
        hi = highlight and r["zone"] == highlight
        color = BAR_HI if hi else BAR

        d.text((PAD * SCALE, yc), r["zone"], font=f_row,
               fill=INK if hi else MUTED, anchor="lm")

        frac = (r["price"] - zero_ref) / denom
        bar_w = max(3, frac * (BAR_MAX - BAR_X))
        d.rounded_rectangle(
            [(BAR_X * SCALE, (y + 1) * SCALE),
             ((BAR_X + bar_w) * SCALE, (y + row_h - 1) * SCALE)],
            radius=2 * SCALE, fill=color)
        d.text(((BAR_X + bar_w + 6) * SCALE, yc), f"€{r['price']:,.0f}",
               font=f_val, fill=INK if hi else MUTED, anchor="lm")

    d.text((PAD * SCALE, (H - 22) * SCALE), footer, font=f_foot, fill=MUTED)

    img = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
