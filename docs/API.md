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
| `format` | `json` | `json` (>100k points returns HTTP 200 with `available:false` + a reason — use csv/parquet), `csv` (streamed), or `parquet` (HTTP 501 if the server lacks pyarrow). Every format shares a per-request scan cap of 1,500,000 rows — a wider range returns `available:false` with a reason to narrow `start`/`end` |

Rate limit: the ~120 req/min/IP budget applies to **every** `/api/v1` endpoint —
data (`/series`, `/genmix`, `/snapshot`), reference (`/meta`, `/zones`, `/status`,
`/capacity`, `/units`, `/series/catalog`) and the `/badge/*.svg` widgets alike.
On top of that, the heavier scans — `/series`, `/genmix`, `/snapshot`,
`/series/catalog`, `/status`, `/quality/summary`, `/quality/revisions`,
`/scoreboard/ranking`, `/scoreboard/profile` and
`/api/power/units/history` — share a concurrency
guard: at most 8 heavy queries run at once server-wide; an excess request gets an
immediate HTTP 503 with a retry message rather than queueing.
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

Per-unit hourly *output* (ENTSO-E A73; DE-LU so far) is deliberately **not** a
`/api/v1/series` key — 85+ EIC-named series would drown the catalog. It lives on
`GET /api/power/units/generation?zone=` (each unit's own latest published reading —
the TSOs publish at different speeds, up to the regulation's D+5, so every row
carries its own `unit_latest_hour_utc`/`unit_lag_days` and the summed total mixes
timestamps, not a snapshot) and `GET /api/power/units/history?zone=&unit=<EIC>&hours=`
(capped at 744 hours per request).

### `GET /api/v1/series/catalog`
Every queryable series (key + unit), the enabled zones, and the overall coverage window.

### `GET /api/v1/meta`
Sources, licenses, attribution, enabled zones, available series, disclaimer.

### `GET /api/v1/status`
Honest data coverage: per-zone and per-source freshness (measured on the data's own
delivery date), and an overall `healthy` flag. "Here is exactly what is fresh and what is stale."

### `GET /api/v1/quality/summary`
The Honest-Record matrix over enabled zones × charter series (`load.actual`,
`price.dayahead`, `price.dayahead.qh`, `gen.B16`/`B18`/`B19`, plus the reserved
zone-level key `_zone`): per cell the trailing-30d/90d completeness (mean
`hours_present/hours_expected` over days WITH quality rows), flagged days (30d),
restatement count (30d) and the latest arrival lag in seconds (last fetch's
wall-clock minus the newest hour it brought — **negative** for day-ahead series,
whose frontier runs ahead of the clock). Series a zone doesn't carry are omitted;
`_zone` cells appear only on flagged days. Computed once per ~15 min (cached),
heavy-guarded. Descriptive: every number states what the source published.

### `GET /api/v1/quality/series`
Daily quality rows for ONE series+zone, newest first — the drill-down behind a
summary cell — plus arrival-lag stats (median + p90) over the same window.

| Param | Default | Notes |
|-------|---------|-------|
| `series` | *(required)* | one of the charter keys above, or `_zone` (zone-level flags) — anything else is HTTP 400 listing the valid keys |
| `zone` | *(required)* | enabled bidding zone key — unknown zones are HTTP 400 listing the valid keys |
| `days` | 90 | trailing window, max 365 |

Each row: `date`, `hours_present`, `hours_expected`, `flags` (decoded list —
`rule`, affected `hours` as ISO UTC, `detail`). Flags describe the published
data (`zero_run`, `pv_at_night`, `step_jump`, `gen_below_load_exports`), never
the market. A valid-but-empty combination is HTTP 200 with `available:false`.

### `GET /api/v1/quality/revisions`
The revision ledger for ONE series+zone: every time the source re-published a
different value for an hour it had already published (beyond a float-noise
epsilon), with `old_value`/`new_value`, `observed_at` and `delta_pct` — plus
`restated_hours`, a roll-up of hours restated more than once
(`n_revisions`, `last_change_pct`). `delta_pct` divides by the *absolute*
previous value, so its sign is always the direction of movement (a negative
price restated further down is a negative delta, not a sign flip). The roll-up
is computed over the rows the `mature` filter left — toggling `mature` changes
`restated_hours` too. Heavy-guarded, row-capped (20k/request).

| Param | Default | Notes |
|-------|---------|-------|
| `series` | *(required)* | any catalog series key (see `/series/catalog`); derived `residual.*` series are not ledgered and answer `available:false` with the reason |
| `zone` | *(required)* | enabled bidding zone key |
| `days` | 30 | trailing window over `observed_at`, max 365 |
| `mature` | `true` | `true`: only restatements observed >48 h after the hour they restate (settled data changed); `false`: include the routine provisional fill-in too |

The ledger is forward-only (accrues from first deploy — history before that is
unrecoverable), so `as_of` here is the newest arrival-log timestamp for the
series+zone: the last moment the source was polled and could have restated
something. All `/quality/*` responses carry `as_of`/`age_days`/`stale`, and all
timestamps are ISO 8601 UTC like the rest of `/api/v1`.

### The forecast scoreboard — `/api/v1/scoreboard/*`

OBSYD **grades ENTSO-E's own published D-1 forecasts** (load, wind, solar, and
the derived residual = load − wind − solar) against ENTSO-E's published actuals
— it makes no forecast of its own. The two yardsticks are naive baselines built
from published actuals alone: *persistence* (actual 24 h ago) and *seasonal*
(actual 168 h ago); `skill_x = 1 − mae/mae_x` — positive means the published
forecast beat the naive baseline.

**Bias sign convention** (declared on the wire as `bias_convention`):
`bias = mean(forecast − actual)` in MW — **positive = the published forecast
leaned HIGH**. The older `/api/power/forecast-error` endpoint reports the
**opposite** sign (`bias_mw = mean(actual − forecast)`) and stays unchanged for
its readers.

Aggregates over daily rows are **day-weighted by `n_hours`** (exact per-hour
window means; rmse recombined quadratically); days whose baseline MAE is NULL
drop out of the skill ratio only, never out of the headline mae. `mape` exists
for `load` only (wind/solar hit honest zeros at night, residual crosses zero).
All `/scoreboard/*` responses carry `as_of`/`age_days`/`stale`.

### `GET /api/v1/scoreboard/summary`
One zone's report card: per carried series (`load`/`residual`/`wind`/`solar`)
the trailing **30/90/365-day** aggregates — `days_covered`, `n_hours`, `mae`,
`rmse`, `bias`, `mape` (load), `skill_persistence`, `skill_seasonal`.
`?zone=` *(required)* — unknown zones are HTTP 400 listing the valid keys; a
zone with no scored days is HTTP 200 with `available:false`.

### `GET /api/v1/scoreboard/ranking`
All enabled zones ranked per series by the **comparable** metric, best (lowest
error) first: `load` by MAPE (%); `wind`/`solar` by **nMAE** (100 × window MAE
÷ A68 installed capacity of the matching technology — wind = onshore +
offshore, each zone+type at its own latest A68 year); `residual` by absolute
MAE with an explicit `caveat` (absolute MW — zones of different size are not
comparable on it). Zones with scored days but **no A68 capacity** for the
technology are listed unranked (`nmae_pct: null`, `rank: null`) with a
`signposted` reason — never silently hidden. `?window=` one of `30|90|365`
days (default 90; anything else is HTTP 400 listing the valid windows).
Computed once per ~15 min per window (cached), heavy-guarded.

### `GET /api/v1/scoreboard/monthly`
UTC calendar-month aggregates for ONE zone+series over the full scored history,
oldest first — has the published forecast been getting better or worse? Each
row: `month` (`YYYY-MM`), `days`, `n_hours`, `mae`, `rmse`, `bias`, `mape`
(load), `skill_persistence`, `skill_seasonal`. `?zone=&series=` *(both
required)* — bad values are HTTP 400 listing the valid keys.

### `GET /api/v1/scoreboard/profile`
Forecast error by **hour of day (0–23, UTC)** for ONE zone+series over a
trailing window: per bucket the mean absolute error, mean `bias` and `n`.
Computed on-read from the canonical hourly store through the scoring engine's
own pair table (wind's actual = `gen.B18`+`gen.B19` summed), so it can never
grade different series than the scoreboard. Buckets are UTC — a zone's
local-time features (morning ramp, solar noon) appear shifted by its offset.
`?zone=&series=&window=` (`window` 1–365 days, default 90; out-of-range is
HTTP 422). Heavy-guarded.

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
| `ntc.<ZONE>` | Day-ahead NTC offered to the auction toward `<ZONE>` (ENTSO-E A61), stored under the FROM zone — DIRECTED, one series per direction, never netted (A→B and B→A are independent capacities). NTC-allocated borders only (23 of 63); the flow-based Core region and Nordics publish none by market design. Feeds the utilization fields on `/api/power/borders` (`capacity_source: "ntc"` \| `"p95_proxy"`, `util_latest_pct`, `util_p95_pct`, `hours_ge_90_pct`) and `/api/power/flows/hourly` (`ntc_mw`, `utilization_pct` — exact bidding-zone borders only). Offered capacity, not a physical limit: utilization can exceed 100% after intraday/countertrading | MW |
| `hydro.reservoir` | Weekly reservoir filling (A72; hydro zones only) | MWh |
| `netpos.dayahead` | Signed day-ahead market net position (A25); positive = zone is a net exporter | MW |
| `outage.offline` / `outage.forced` | Generation capacity offline right now — all published unavailability / the A54 forced-outage subset (A77; today-only snapshot series, not backfillable — see `backend/power/outage_history.py`) | MW |
| `balancing.<product>.price.<up\|down>` / `balancing.<product>.vol.<up\|down>` | Activated balancing energy price/volume, `<product>` = `afrr`/`mfrr` (ENTSO-E A84/A83). Volume (A83) currently fails structurally at ENTSO-E for every zone tried — the `.vol.*` series are defined but empty. DE_LU is served via TenneT's control area only (one of four German TSOs), not the national total | EUR/MWh / MWh |
| `capacity.fcr.price` / `capacity.<afrr\|mfrr>.price.<pos\|neg>` | Procured balancing-CAPACITY price — volume-weighted average of accepted tenders (ENTSO-E A15), normalized to EUR/MW/h (FCR's native EUR-per-4h-block price divided by 4). DE_LU only (German LFC block; no per-zone equivalent). History starts 2025-11-27 — that is ENTSO-E's own publication floor for this dataset (a full 2024→ backfill sweep answered empty Acknowledgements before that date); deeper history exists at regelleistung.net but carries no reuse licence (see `docs/findings/2026-07-20-regelleistung-capacity-prices.md`) | EUR/MW/h |

Call `/api/v1/meta` for the live list. Values are hourly-canonical UTC; actuals carry a
~1 hour publication lag (the honest ceiling of free ENTSO-E data).

## Data revisions & reproducibility

Recent windows are deliberately re-fetched with overwrite every night (the daily
reverify), so recently served values can be restated when ENTSO-E revises its own
publication. Responses reflect the store at request time — there is no
historical-snapshot pinning. If you need bit-identical reproducibility, archive
what you pulled (and record the pull date), or self-host a frozen copy (see
"Known Limitations" in the README).

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
