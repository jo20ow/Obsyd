# obsyd (Python client)

A pandas client for the [OBSYD](https://obsyd.dev) public data API — a free
a free European power desk over the official European power record (ENTSO-E, Energy-Charts
CC BY 4.0, GIE). 37 bidding zones, day-ahead prices at the market's real 15-minute
resolution, load/residual load, generation mix, cross-border flows. Descriptive,
not a forecast. AGPL-3.0. No API key.

## Install

```bash
pip install obsyd            # + pip install obsyd[parquet] for bulk pulls
```

## Use

```python
from obsyd import Obsyd

ob = Obsyd()  # or Obsyd("https://your-self-host")

# One series, one zone → DataFrame (tz-aware UTC index)
df = ob.series("price.dayahead", "DE_LU", start="2024-01-01", resolution="daily")
df["value"].plot()

# The last week of prices across every zone — a single request
wide = ob.snapshot("price.dayahead", hours=168)

# 15-minute auction resolution (SDAC trades quarter-hours since Oct 2025)
qh = ob.series("price.dayahead.qh", "DE_LU", start="2026-07-01")

# Generation mix, capacity, named units
mix = ob.genmix("ES", resolution="daily")
cap = ob.capacity("DE_LU")

# One series over many zones (polite pacing built in)
res = ob.series_multi("residual.actual", ["DE_LU", "FR", "ES"], start="2026-01-01")

# What exists: series keys, zones, coverage window, freshness
ob.catalog(); ob.zones(); ob.status()
```

## Methods

| Method | Returns | Notes |
|---|---|---|
| `series(series, zone, start, end, resolution, format)` | DataFrame | `hourly`/`daily`; daily carries `hours` (24 = settled day); `format="parquet"` for bulk |
| `series_multi(series, zones, ...)` | wide DataFrame | one column per zone, 0.5 s pause between requests |
| `snapshot(series, hours≤744, ...)` | wide DataFrame | every enabled zone, one request |
| `genmix(zone, start, end, resolution)` | wide DataFrame | one column per fuel, mean MW |
| `capacity(zone, year)` / `units(zone)` | DataFrame | read `df.attrs["note"]` on `units` before summing |
| `zones()` / `catalog()` / `meta()` / `status()` | dict | reference endpoints, not rate-limited |

`df.attrs` carries `series`/`zone`/`unit`/`attribution` where known.

## Errors — the client raises, it never returns silently-empty frames

- `ObsydNoData` — the API has nothing for this query (`.reason` holds the server's
  explanation, e.g. unknown series or empty coverage window).
- `ObsydRateLimited` — 429 after built-in retries (public API ≈ 120 req/min/IP;
  the client retries 429/5xx with exponential backoff, `max_retries=3`).
- `ObsydBadRequest` / `ObsydServerError` — 400 / 5xx with the server's detail.
- `ValueError` — unknown zone key, with close-match suggestions (guards against a
  server-side silent fallback to the default zone).

Requests are sent with a `obsyd-python/<version>` User-Agent.

## Examples

Executable notebooks in [`examples/`](examples/): quarter-hour prices,
solar capture rates & negative-price hours, cross-border flows.

Full API reference: [`docs/API.md`](../../docs/API.md) or the interactive docs at
`https://obsyd.dev/api/docs`.

## Development

```bash
pip install -e ".[dev]"
python -m pytest -q          # mocked, hermetic
python -m pytest -m live     # smoke against obsyd.dev (before releases)
```

Releases: bump `__version__` in `obsyd.py`, merge, push tag `client-vX.Y.Z` —
GitHub Actions builds and publishes to PyPI via Trusted Publishing.
