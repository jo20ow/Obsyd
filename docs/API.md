# OBSYD Public Data API (v1)

A free, versioned HTTP API over the canonical European power record — "gridstatus for
Europe." All data is from free, official, redistributable sources (ENTSO-E, Fraunhofer
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

Rate limit: ~120 req/min/IP applies to `/series`, `/genmix` and `/snapshot`; the
reference endpoints (`/meta`, `/zones`, `/status`, `/capacity`, `/units`,
`/series/catalog`) are not rate-limited. "Nothing found" (unknown series, empty
window) is HTTP 200 with `available:false` + `reason`, not a 4xx.

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
| `hydro.reservoir` | Weekly reservoir filling (A72; hydro zones only) | MWh |

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

## Attribution & license
Attribute ENTSO-E, Fraunhofer Energy-Charts (CC BY 4.0) and GIE. The service and its
source are AGPL-3.0 — self-host freely; network use requires publishing source changes.
