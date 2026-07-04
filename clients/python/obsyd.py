"""Thin Python client for the OBSYD public data API — "gridstatus for Europe".

    from obsyd import Obsyd
    ob = Obsyd()                                  # defaults to https://obsyd.dev
    df = ob.series("price.dayahead", "DE_LU", start="2024-01-01", resolution="daily")
    print(ob.zones()["enabled_keys"])             # which bidding zones are live
    print([s["key"] for s in ob.catalog()["series"]])

Fetches via the public API (free, no key). Series are returned as pandas DataFrames
(via CSV under the hood). Descriptive, official, redistributable data — not a forecast.
Requires: requests, pandas.
"""
from __future__ import annotations

import io

import pandas as pd
import requests


class Obsyd:
    def __init__(self, base_url: str = "https://obsyd.dev", timeout: int = 60):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        r = requests.get(f"{self.base}/api/v1/{path}", params=params, timeout=self.timeout)
        r.raise_for_status()
        return r

    def zones(self) -> dict:
        """Registry of bidding zones (key/label/has_flows/enabled)."""
        return self._get("zones").json()

    def catalog(self) -> dict:
        """Available series (key+unit), enabled zones, coverage window."""
        return self._get("series/catalog").json()

    def meta(self) -> dict:
        """Sources, licenses, attribution, disclaimer."""
        return self._get("meta").json()

    def status(self) -> dict:
        """Per-zone / per-source freshness + overall health."""
        return self._get("status").json()

    def series(
        self,
        series: str,
        zone: str,
        start: str | None = None,
        end: str | None = None,
        resolution: str = "hourly",
    ) -> pd.DataFrame:
        """One series for one zone as a DataFrame (index = UTC time, column = value).

        `series` e.g. 'price.dayahead', 'load.actual', 'residual.actual', 'gen.B16'.
        `resolution` 'hourly' or 'daily'. `start`/`end` are YYYY-MM-DD or ISO 8601.
        """
        r = self._get(
            "series", series=series, zone=zone, start=start, end=end,
            resolution=resolution, format="csv",
        )
        df = pd.read_csv(io.StringIO(r.text))
        tcol = "date" if resolution == "daily" else "datetime_utc"
        if tcol in df.columns:
            df[tcol] = pd.to_datetime(df[tcol])
            df = df.set_index(tcol)
        return df
