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
| `end` | now | `YYYY-MM-DD` or ISO 8601 |
| `resolution` | `hourly` | `hourly` (raw) or `daily` (daily mean) |
| `format` | `json` | `json` (≤100k points), `csv` (streamed, unbounded), or `parquet` |

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
| `imbalance.price` | Imbalance / balancing price, 15-min → hourly (single-TSO zones; not DE) | EUR/MWh |

Call `/api/v1/meta` for the live list. Values are hourly-canonical UTC; actuals carry a
~1 hour publication lag (the honest ceiling of free ENTSO-E data).

## Attribution & license
Attribute ENTSO-E, Fraunhofer Energy-Charts (CC BY 4.0) and GIE. The service and its
source are AGPL-3.0 — self-host freely; network use requires publishing source changes.
