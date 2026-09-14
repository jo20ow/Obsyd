#!/usr/bin/env python3
# Renders docs/launch/negative-price-map-*.png (4 Europa-Karten, Negativpreis-Stunden).
# 1) Input neg_monthly.csv NEBEN dieses Skript legen — auf dem VPS erzeugen mit:
#    sqlite3 -csv 'file:/home/obsyd/obsyd/obsyd.db?mode=ro' "SELECT z.key,
#      strftime('%Y-%m', ph.ts_utc, 'unixepoch'), SUM(ph.value<0) FROM power_hourly ph
#      JOIN zone_dim z ON z.id=ph.zone_id WHERE ph.series_id=1
#      AND ph.ts_utc >= strftime('%s','2023-01-01') GROUP BY 1,2 ORDER BY 1,2;"
# 2) python3 render-negative-price-map.py  -> schreibt neg_map.html daneben
# 3) Screenshot: playwright-core (frontend/node_modules), CHROMIUM_PATH setzen,
#    Element .wrap bei deviceScaleFactor 2 schiessen (vgl. record-demo.mjs).
# Vor dem Posten: Stichtag-Strings ("to Sep 10" / "Sep 10, 2026") und die
# Annotations-Zahlen (Finland/Spain) aktualisieren!
import os
import csv, json, math
from collections import defaultdict

SCRATCH = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

# ---- data: Jan-Sep sums per zone per year
sums = defaultdict(lambda: defaultdict(int))
for z, ym, neg in csv.reader(open(f"{SCRATCH}/neg_monthly.csv")):
    y, m = ym.split("-")
    if int(m) <= 9:
        sums[z][y] += int(neg)
YEARS = ["2023", "2024", "2025", "2026"]

# ---- Lambert conformal conic, std parallels 40/65, ref lon 12
p1, p2, lon0, lat0 = math.radians(40), math.radians(65), 12.0, math.radians(52)
n = math.log(math.cos(p1) / math.cos(p2)) / math.log(
    math.tan(math.pi / 4 + p2 / 2) / math.tan(math.pi / 4 + p1 / 2))
F = math.cos(p1) * math.tan(math.pi / 4 + p1 / 2) ** n / n
r0 = F / math.tan(math.pi / 4 + lat0 / 2) ** n

def project(lon, lat):
    la = math.radians(max(min(lat, 84), -80))
    r = F / math.tan(math.pi / 4 + la / 2) ** n
    t = n * math.radians(lon - lon0)
    return r * math.sin(t), r0 - r * math.cos(t)

zones = json.load(open(f"{REPO}/frontend/public/geo/eu-zones.geojson"))
zfeats = [f for f in zones["features"] if f["properties"].get("zone")]
world = json.load(open(f"{REPO}/frontend/public/geo/world-110m.geojson"))

def rings(geom):
    if geom["type"] == "Polygon":
        return geom["coordinates"]
    if geom["type"] == "MultiPolygon":
        return [r for poly in geom["coordinates"] for r in poly]
    return []

# fit: projected bbox of zones
pts = [project(x, y) for f in zfeats for r in rings(f["geometry"]) for x, y in r]
xs, ys = [p[0] for p in pts], [p[1] for p in pts]
minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
W, H, PAD = 350, 440, 8
scale = min((W - 2 * PAD) / (maxx - minx), (H - 2 * PAD) / (maxy - miny))
ox = (W - (maxx - minx) * scale) / 2
oy = (H - (maxy - miny) * scale) / 2

def to_px(lon, lat):
    x, y = project(lon, lat)
    return (x - minx) * scale + ox, (maxy - y) * scale + oy

def path_d(geom, dec=1):
    d = []
    for r in rings(geom):
        step = max(1, len(r) // 400)
        cs = r[::step]
        d.append("M" + "L".join(f"{x:.{dec}f} {y:.{dec}f}" for x, y in (to_px(a, b) for a, b in cs)) + "Z")
    return "".join(d)

def in_europe(geom):
    for r in rings(geom):
        for x, y in r[::10] or r:
            if -26 <= x <= 46 and 32 <= y <= 72:
                return True
    return False

world_paths = "".join(
    f'<path d="{path_d(f["geometry"])}" fill="#e8e6e1" stroke="#ffffff" stroke-width="0.5"/>'
    for f in world["features"] if in_europe(f["geometry"]))

BINS = [(0, "#f1efe9"), (50, "#dbeafe"), (150, "#b6d0f5"), (300, "#82abe9"),
        (450, "#4b7fdd"), (10**9, "#143a92")]

def color(v):
    if v == 0:
        return BINS[0][1]
    for hi, c in BINS[1:]:
        if v <= hi:
            return c
    return BINS[-1][1]

def centroid_px(zkey):
    f = next(f for f in zfeats if f["properties"]["zone"] == zkey)
    big = max(rings(f["geometry"]), key=len)
    xs = [to_px(a, b) for a, b in big]
    return sum(p[0] for p in xs) / len(xs), sum(p[1] for p in xs) / len(xs)

LABELS = {"2023": "2023", "2024": "2024", "2025": "2025", "2026": "2026 (to Sep 10)"}
ANN = {"2023": [("FI", "Finland 218 h", 16, 0)],
       "2026": [("FI", "Finland 43 h", 16, 0), ("ES", "Spain 855 h", 0, 4)]}

panels = []
for y in YEARS:
    zp = "".join(
        f'<path d="{path_d(f["geometry"])}" fill="{color(sums[f["properties"]["zone"]][y])}" '
        f'stroke="#ffffff" stroke-width="0.7" fill-rule="evenodd"/>'
        for f in zfeats)
    ann = ""
    for zkey, txt, dx, dy in ANN.get(y, []):
        cx, cy = centroid_px(zkey)
        ann += (f'<text x="{cx+dx:.0f}" y="{cy+dy:.0f}" text-anchor="middle" font-size="12.5" '
                f'font-weight="700" fill="#1a1a1a" stroke="#ffffff" stroke-width="3.5" '
                f'paint-order="stroke">{txt}</text>')
    panels.append(
        f'<div class="panel"><h2>{LABELS[y]}</h2>'
        f'<svg width="{W}" height="{H}"><rect width="{W}" height="{H}" fill="#fbfaf8"/>'
        f'{world_paths}{zp}{ann}</svg></div>')

legend = "".join(
    f'<span class="ls"><span class="sw" style="background:{c}"></span>{t}</span>'
    for c, t in [("#f1efe9", "0"), ("#dbeafe", "1–50"), ("#b6d0f5", "51–150"),
                 ("#82abe9", "151–300"), ("#4b7fdd", "301–450"), ("#143a92", ">450")])

html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
body {{ margin:0; background:#ffffff; font-family:-apple-system,'Helvetica Neue',Arial,sans-serif; color:#1a1a1a; }}
.wrap {{ padding:34px 38px 24px; width:fit-content; }}
h1 {{ font-size:24px; margin:0 0 6px; font-weight:700; letter-spacing:-0.2px; }}
.sub {{ font-size:13.5px; color:#555; margin:0 0 14px; max-width:730px; line-height:1.5; }}
.legend {{ display:flex; flex-wrap:wrap; gap:8px 14px; align-items:center; margin:0 0 10px; font-size:11.5px; color:#555; }}
.ls {{ display:inline-flex; align-items:center; gap:5px; }}
.sw {{ width:13px; height:13px; border-radius:3px; display:inline-block; }}
#panels {{ display:grid; grid-template-columns:repeat(2, auto); gap:10px 14px; width:fit-content; }}
.panel h2 {{ font-size:15px; font-weight:700; margin:0 0 3px; text-align:center; color:#333; }}
.foot {{ font-size:11px; color:#777; margin-top:12px; line-height:1.5; max-width:730px; }}
</style></head><body><div class="wrap">
<div class="legend"><span style="font-weight:600;color:#444">Negative-price hours, Jan&ndash;Sep</span>{legend}</div>
<div id="panels">{"".join(panels)}</div>
<p class="foot">An hour counts as negative when its day-ahead auction price (hourly average) cleared below &euro;0.
Italy has never cleared negative: its zones share a &euro;0 price floor. Grey = outside the covered bidding zones.
Data: ENTSO-E Transparency Platform, Jan 2023 &ndash; Sep 10, 2026 &middot; aggregated via the open-source desk obsyd.dev</p>
</div></body></html>"""

open(f"{SCRATCH}/neg_map.html", "w").write(html)
print("ok", len(html) // 1024, "KB")
