"""Render the daily-post image — data-driven, drawn with Pillow (no browser, no
SVG toolchain, no system libs beyond the Pillow wheel).

A franchise never draws pixels: it returns a card SPEC (a dict), and `render()`
dispatches on spec["card"] to one of a small set of renderers:

  * "bars"  — a horizontal-bar leaderboard/ranking (price ranking, battery TB2,
              CO₂, weekly negative hours). Each row carries a numeric `value`
              (bar length) and a pre-formatted `disp` string (what's printed),
              so the renderer stays dumb; one row may be `highlight`ed.
  * "trend" — grouped bars over years (the "then vs now" story), one colored
              series per country/metric with a legend.

Palette matches the desk's redesign vocabulary (ink-blue accent on an off-white
ground). 1600×900 at 2× for crisp text.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, S = 800, 450, 2
PAD = 40

BG = (250, 250, 249)
INK = (23, 23, 23)
MUTED = (120, 120, 128)
ACCENT = (29, 78, 216)     # ink-blue #1d4ed8 — the redesign accent
BAR = (206, 216, 240)      # calm bar fill (length carries the value)
GRID = (228, 228, 226)

#: Named series colors for trend cards — CVD-separated, on-brand.
PALETTE = {
    "blue": ACCENT,
    "amber": (217, 119, 6),
    "teal": (13, 148, 136),
    "slate": (100, 116, 139),
}

_FONTS = (
    "/System/Library/Fonts/Helvetica.ttc",                 # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",     # Debian/Ubuntu (VPS)
)


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONTS:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size * S)
            except OSError:
                continue
    return ImageFont.load_default()


def _header(d: ImageDraw.ImageDraw, title: str, subtitle: str | None) -> int:
    """Draw the title band (+ optional subtitle) and accent rule. Returns the y
    (logical px) where content may begin."""
    lines = _wrap(title, 34)
    y = PAD
    for ln in lines[:2]:
        d.text((PAD * S, y * S), ln, font=_font(24), fill=INK)
        y += 32
    rule_y = y + 4
    d.line([(PAD * S, rule_y * S), ((W - PAD) * S, rule_y * S)], fill=ACCENT, width=2 * S)
    y = rule_y + 14
    if subtitle:
        d.text((PAD * S, y * S), subtitle, font=_font(13), fill=MUTED)
        y += 24
    return y


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= width:
            cur = f"{cur} {w}".strip()
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _footer(d: ImageDraw.ImageDraw, text: str) -> None:
    d.text((PAD * S, (H - 26) * S), text, font=_font(10), fill=MUTED)


def _finish(img: Image.Image) -> bytes:
    img = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def render_bars(title: str, rows: list[dict], *, subtitle: str | None = None,
                footer: str = "obsyd.dev", highlight: str | None = None) -> bytes:
    """Horizontal-bar leaderboard. `rows` = [{label, value, disp}] in the order
    to draw (caller sorts). Bars scale to the max |value|; `disp` is printed at
    the bar end; the `highlight` label (if any) is drawn in accent."""
    img = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(img)
    top = _header(d, title, subtitle)
    if not rows:
        _footer(d, footer)
        return _finish(img)

    # A row with value None is a SEPARATOR (a "⋯" gap between the two ends of a
    # top-N/bottom-N view) — it draws no bar and doesn't count toward the scale.
    vals = [r["value"] for r in rows if r.get("value") is not None]
    if not vals:
        _footer(d, footer)
        return _finish(img)
    zero_ref = min(min(vals), 0.0)
    denom = (max(vals) - zero_ref) or 1.0
    bar_x = PAD + 150
    bar_max = W - PAD - 70

    n = len(rows)
    row_h = min(22, (H - top - 30) / n)
    for i, r in enumerate(rows):
        y = top + i * row_h
        yc = (y + row_h / 2) * S
        if r.get("value") is None:  # separator
            d.text((PAD * S, yc), "⋯", font=_font(10), fill=MUTED, anchor="lm")
            continue
        hi = highlight is not None and r["label"] == highlight
        d.text((PAD * S, yc), str(r["label"]), font=_font(11),
               fill=INK if hi else MUTED, anchor="lm")
        frac = (r["value"] - zero_ref) / denom
        bw = max(3, frac * (bar_max - bar_x))
        d.rounded_rectangle(
            [(bar_x * S, (y + 2) * S), ((bar_x + bw) * S, (y + row_h - 2) * S)],
            radius=2 * S, fill=ACCENT if hi else BAR)
        d.text(((bar_x + bw + 6) * S, yc), str(r["disp"]), font=_font(11),
               fill=INK if hi else MUTED, anchor="lm")

    _footer(d, footer)
    return _finish(img)


def render_trend(title: str, years: list[int], series: list[dict], *,
                 subtitle: str | None = None, footer: str = "obsyd.dev") -> bytes:
    """Grouped bars over years. `series` = [{name, color, values}] where values
    aligns with `years`; color is a PALETTE name. A legend names each series."""
    img = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(img)
    top = _header(d, title, subtitle)

    # legend
    lx = PAD
    for s in series:
        c = PALETTE.get(s["color"], ACCENT)
        d.rounded_rectangle([(lx * S, top * S), ((lx + 14) * S, (top + 10) * S)],
                            radius=2 * S, fill=c)
        d.text(((lx + 20) * S, (top + 5) * S), s["name"], font=_font(11), fill=INK, anchor="lm")
        lx += 30 + len(s["name"]) * 8
    plot_top = top + 26

    all_vals = [v for s in series for v in s["values"] if v is not None]
    mx = max(all_vals) if all_vals else 1.0
    step = _nice_step(mx)
    px0, px1 = PAD + 34, W - PAD
    py0, py1 = plot_top, H - 60
    g = 0
    while g <= mx * 1.02:
        y = py1 - (g / (mx or 1)) * (py1 - py0)
        d.line([(px0 * S, y * S), (px1 * S, y * S)], fill=GRID, width=1 * S)
        lbl = f"{g:.1f}" if step < 1 else str(int(g))
        d.text(((px0 - 6) * S, y * S), lbl, font=_font(9), fill=MUTED, anchor="rm")
        g += step

    n = len(years)
    gw = (px1 - px0) / n
    k = len(series)
    bw = gw * 0.7 / k
    for i, yr in enumerate(years):
        cx = px0 + i * gw + gw / 2
        for j, s in enumerate(series):
            v = s["values"][i]
            if not v:
                continue
            h = (v / (mx or 1)) * (py1 - py0)
            x = cx + (j - (k - 1) / 2) * bw
            d.rounded_rectangle(
                [((x - bw / 2) * S, (py1 - h) * S), ((x + bw / 2) * S, py1 * S)],
                radius=2 * S, fill=PALETTE.get(s["color"], ACCENT))
        lbl = str(yr) if (yr % 5 == 0 or yr == years[-1]) else str(yr)[2:]
        d.text((cx * S, (py1 + 6) * S), lbl, font=_font(9), fill=MUTED, anchor="mt")

    _footer(d, footer)
    return _finish(img)


def _nice_step(mx: float) -> float:
    for step in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000):
        if mx / step <= 5:
            return step
    return 10000


def render(spec: dict) -> bytes:
    """Dispatch a card spec to its renderer."""
    kind = spec.get("card", "bars")
    if kind == "trend":
        return render_trend(spec["title"], spec["years"], spec["series"],
                            subtitle=spec.get("subtitle"),
                            footer=spec.get("footer", "obsyd.dev"))
    return render_bars(spec["title"], spec.get("rows", []),
                       subtitle=spec.get("subtitle"),
                       footer=spec.get("footer", "obsyd.dev"),
                       highlight=spec.get("highlight"))
