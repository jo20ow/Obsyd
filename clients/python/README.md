# obsyd (Python client)

A thin pandas client for the [OBSYD](https://obsyd.dev) public data API — a free
"gridstatus for Europe" over the official European power record (ENTSO-E, Energy-Charts
CC BY 4.0, GIE). Descriptive, not a forecast. AGPL-3.0.

## Install

No PyPI package yet — copy `obsyd.py` into your project (needs `requests` + `pandas`):

```bash
pip install requests pandas
```

## Use

```python
from obsyd import Obsyd

ob = Obsyd()  # or Obsyd("https://your-self-host")

# What's available
ob.zones()["enabled_keys"]                 # ['DE_LU','FR','NL', ...]
[s["key"] for s in ob.catalog()["series"]] # ['price.dayahead','load.actual','gen.B16', ...]

# A series as a DataFrame (index = UTC time)
df = ob.series("price.dayahead", "DE_LU", start="2024-01-01", resolution="daily")
df["value"].plot()

# Hourly residual load for Spain over a year
res = ob.series("residual.actual", "ES", start="2025-01-01", end="2026-01-01")

# Honest coverage
ob.status()["healthy"]
```

See the full API reference at `docs/API.md` or `https://obsyd.dev/api/docs`.
