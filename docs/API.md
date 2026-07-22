# OBSYD Public Data API (v1)

A free, versioned HTTP API over the canonical European power record. All data is from free, official, redistributable sources (ENTSO-E, Fraunhofer
Energy-Charts CC BY 4.0, GIE). Descriptive, not a forecast. AGPL-3.0.

**Base URL:** `https://obsyd.dev/api/v1`
**Interactive docs:** `https://obsyd.dev/api/docs` · **OpenAPI:** `https://obsyd.dev/api/openapi.json`
**Auth:** none (public). Lightly rate-limited per IP (~120 req/min).

## Endpoints

### `GET /api/v1/series`
One time series for one bidding zone over a date range — the core endpoint.

| Param | Default | Notes |
|-------|---------|-------|
| `series` | *(required)* | e.g. `price.dayahead`, `load.actual`, `residual.actual`, `gen.B16` |
| `zone` | *(required)* | e.g. `DE_LU`, `FR`, `ES` (see `/meta` for the enabled set) |
| `start` | 30 days ago | `YYYY-MM-DD` or ISO 8601 |
| `end` | *open* | no ceiling — everything on record (deliberately NOT "now": late-arriving hours must not be cut off) |
| `resolution` | `hourly` | `hourly` (raw store resolution — `.qh` series return 15-min steps) or `daily` (daily mean; rows carry `hours`, 24 = a settled day) |
| `format` | `json` | `json` (>100k points returns HTTP 200 with `available:false` + a reason — use csv/parquet), `csv` (streamed, unbounded), or `parquet` (unbounded; HTTP 501 if the server lacks pyarrow) |

Rate limit: ~120 req/min/IP applies to `/series`, `/genmix`, `/snapshot` and the
`/badge/*.svg` widgets below; the reference endpoints (`/meta`, `/zones`,
`/status`, `/capacity`, `/units`, `/series/catalog`) are not rate-limited.
"Nothing found" (unknown series, empty window) is HTTP 200 with
`available:false` + `reason`, not a 4xx.

```bash
# JSON, daily mean, last 30 days
curl "https://obsyd.dev/api/v1/series?series=price.dayahead&zone=DE_LU&resolution=daily"

# CSV export of a full year of hourly residual load for Spain → pandas
curl "https://obsyd.dev/api/v1/series?series=residual.actual&zone=ES&start=2025-01-01&end=2026-01-01&format=csv" -o es_residual_2025.csv
```

```python
import pandas as pd
url = "https://obsyd.dev/api/v1/series"
p = {"series": "price.dayahead", "zone": "FR", "start": "2024-01-01", "format": "csv"}
df = pd.read_csv(f"{url}?series={p['series']}&zone={p['zone']}&start={p['start']}&format=csv",
                 parse_dates=["datetime_utc"])
```

### `GET /api/v1/zones`
Every bidding zone in the registry with `label`, `has_flows`, `enabled` + the default zone.

### `GET /api/v1/capacity`
Installed generation capacity per production type (MW) for a zone-year (ENTSO-E A68 annual).
`?zone=&year=` (default: latest). Returns `total_mw` + per-type breakdown.

### `GET /api/v1/genmix`
Generation mix over time for one zone, wide shape (`{t, <fuel>: mean MW, ...}`).
`?zone=&start=&end=&resolution=daily|monthly&format=json|csv`. Caveat: unknown
zones silently fall back to the default zone (validate against `/zones`).
`/capacity` shares this fallback.

### `GET /api/v1/snapshot`
A recent window of ONE series across EVERY enabled zone in a single request —
grid-aligned: `{timestamps: [...], zones: {DE_LU: [v|null, ...], ...}}`.
`?series=&hours=` (default 168, max 744) or explicit `start`/`end`.

### `GET /api/v1/units`
Named production units (EIC, name, fuel, nominal MW) for one zone from the
ENTSO-E A71/A33 registry. Read the `note` in the response before summing — this
is the *published* unit list, not the full installed fleet.

### `GET /api/v1/series/catalog`
Every queryable series (key + unit), the enabled zones, and the overall coverage window.

### `GET /api/v1/meta`
Sources, licenses, attribution, enabled zones, available series, disclaimer.

### `GET /api/v1/status`
Honest data coverage: per-zone and per-source freshness (measured on the data's own
delivery date), and an overall `healthy` flag. "Here is exactly what is fresh and what is stale."

## Series keys

| Prefix | Meaning | Unit |
|--------|---------|------|
| `price.dayahead` | Day-ahead auction price | EUR/MWh |
| `load.actual` | Actual total load | MW |
| `load.forecast` | Day-ahead load forecast | MW |
| `wind.forecast` / `solar.forecast` | Day-ahead wind / solar forecast | MW |
| `residual.actual` / `residual.forecast` | Load − wind − solar (the price-driving quantity) | MW |
| `gen.<PSR>` | Actual generation by ENTSO-E production type (e.g. `gen.B16` solar, `gen.B18`/`B19` wind) | MW |
| `imbalance.price` | Imbalance / balancing price, hourly mean (single-TSO zones; DE-LU via country EIC) | EUR/MWh |
| `price.dayahead.qh` / `imbalance.price.qh` | Raw 15-minute auction / imbalance steps (SDAC trades quarter-hours since 2025-10) | EUR/MWh |
| `generation.forecast` | Day-ahead total generation forecast (A71) | MW |
| `consumption.<PSR>` | Consumption of consumption-type PSRs (e.g. pumped-storage pumping) | MW |
| `flow.<ZONE>` | Cross-border physical flow to `<ZONE>`, stored under the FROM zone; positive = FROM exports | MW |
| `sched.<ZONE>` | Scheduled (day-ahead auction) commercial exchange to `<ZONE>`, stored under the FROM zone on the same sorted-pair/net-sign convention as `flow.<ZONE>` — `flow − sched` is loop flow | MW |
| `hydro.reservoir` | Weekly reservoir filling (A72; hydro zones only) | MWh |
| `netpos.dayahead` | Signed day-ahead market net position (A25); positive = zone is a net exporter | MW |
| `outage.offline` / `outage.forced` | Generation capacity offline right now — all published unavailability / the A54 forced-outage subset (A77; today-only snapshot series, not backfillable — see `backend/power/outage_history.py`) | MW |
| `balancing.<product>.price.<up\|down>` / `balancing.<product>.vol.<up\|down>` | Activated balancing energy price/volume, `<product>` = `afrr`/`mfrr` (ENTSO-E A84/A83). Volume (A83) currently fails structurally at ENTSO-E for every zone tried — the `.vol.*` series are defined but empty. DE_LU is served via TenneT's control area only (one of four German TSOs), not the national total | EUR/MWh / MWh |
| `capacity.fcr.price` / `capacity.<afrr\|mfrr>.price.<pos\|neg>` | Procured balancing-CAPACITY price — volume-weighted average of accepted tenders (ENTSO-E A15), normalized to EUR/MW/h (FCR's native EUR-per-4h-block price divided by 4). DE_LU only (German LFC block; no per-zone equivalent) | EUR/MW/h |

Call `/api/v1/meta` for the live list. Values are hourly-canonical UTC; actuals carry a
~1 hour publication lag (the honest ceiling of free ENTSO-E data).

## Python client

```bash
pip install obsyd
```

```python
from obsyd import Obsyd
df = Obsyd().series("price.dayahead", "DE_LU", start="2024-01-01", resolution="daily")
```

DataFrames with tz-aware UTC indexes, typed errors, built-in 429 backoff.
Source + executable example notebooks: `clients/python/` in the repo.

## Embedding

Two ways to put live Obsyd data on your own page — no API key, no JS to write.

### Iframe widgets — `/embed/<ZONE>/<metric>`

A self-contained, auto-refreshing widget for one zone. `<metric>` is one of
`price` (day-ahead hourly curve), `genmix` (stacked generation mix) or `load`
(load vs. day-ahead forecast).

```html
<iframe
  src="https://obsyd.dev/embed/DE_LU/price"
  width="420" height="180"
  style="border: 0;"
  loading="lazy"
  title="OBSYD — DE-LU day-ahead price">
</iframe>
```

- The widget polls for fresh data every ~5 minutes on its own (matches the desk's
  own `POLL_FAST_MS` refresh cadence) — reload the iframe yourself only if you
  want to force it sooner.
- An unrecognized `<ZONE>` or `<metric>` renders an explicit "unknown" card
  linking back to obsyd.dev — never a silent fallback to a different zone. Check
  `GET /api/v1/zones` for the current enabled set.
- `/embed/*` is the **only** part of obsyd.dev that permits being framed —
  every other path sends `X-Frame-Options: DENY`.

### Status badges — `/api/v1/badge/<ZONE>/<metric>.svg`

A tiny flat SVG pill for a README, wiki page or status dashboard. `<metric>` is
`price` or `load`.

```markdown
![DE-LU day-ahead price](https://obsyd.dev/api/v1/badge/DE_LU/price.svg)
```

```html
<img src="https://obsyd.dev/api/v1/badge/DE_LU/load.svg" alt="DE-LU load">
```

- Cached 15 minutes (`Cache-Control: public, max-age=900`) — a badge is fetched
  by *your* readers' browsers/bots on their own schedule, not polled by us.
- An unknown zone/metric, or a momentary data gap, degrades to a neutral grey
  "no data" pill at HTTP 200 rather than a broken-image icon — a badge must
  never break whatever page it's embedded in.
- Shares the same ~120 req/min/IP budget as the rest of `/api/v1`.

### Attribution

Both forms already carry attribution baked in — the iframe widget's footer
("OBSYD · obsyd.dev — data: ENTSO-E") and the badge's `<title>` tooltip — so no
extra credit line is required on your page. If you build something custom on
top of `/api/v1/series` instead, keep the "Attribution & license" note below in
view somewhere.

## Attribution & license
Attribute ENTSO-E, Fraunhofer Energy-Charts (CC BY 4.0) and GIE. The service and its
source are AGPL-3.0 — self-host freely; network use requires publishing source changes.
