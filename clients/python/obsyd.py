"""Python client for the OBSYD public data API — "gridstatus for Europe".

    from obsyd import Obsyd
    ob = Obsyd()                                  # defaults to https://obsyd.dev
    df = ob.series("price.dayahead", "DE_LU", start="2024-01-01", resolution="daily")
    wide = ob.snapshot("price.dayahead", hours=168)   # last week, every zone
    mix = ob.genmix("ES", resolution="daily")

Free public API, no key. Tabular endpoints return pandas DataFrames (CSV under
the hood — the server streams it and it is never row-capped). Errors raise
typed exceptions instead of returning empty frames: `ObsydNoData` when the API
answers "nothing here" (with the server's reason), `ObsydRateLimited` after
retries on 429, `ObsydBadRequest`/`ObsydServerError` for the rest.

Descriptive, official, redistributable data (ENTSO-E; Energy-Charts CC BY 4.0;
GIE) — not a forecast. Requires: requests, pandas.
"""
from __future__ import annotations

import datetime as _dt
import difflib
import io
import random
import time
import warnings
from typing import Sequence

import pandas as pd
import requests

__version__ = "0.2.0"

_RETRY_STATUSES = {429, 502, 503, 504}


class ObsydError(Exception):
    """Base class for every error this client raises deliberately."""


class ObsydHTTPError(ObsydError):
    """An HTTP error response from the API."""

    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


class ObsydBadRequest(ObsydHTTPError):
    """400 — malformed datetime, start >= end, or an invalid parameter."""


class ObsydRateLimited(ObsydHTTPError):
    """429 after retries were exhausted (the public API allows ~120 req/min/IP)."""


class ObsydServerError(ObsydHTTPError):
    """5xx — including 501 when the server cannot produce parquet."""


class ObsydNoData(ObsydError):
    """The API answered but has nothing for this query.

    Raised when a JSON payload carries ``available: false`` (``.reason`` holds
    the server's explanation) and when a CSV response parses to zero rows.
    Check ``Obsyd().catalog()`` for valid series keys and the coverage window.
    """

    def __init__(self, reason: str, series: str | None = None, zone: str | None = None):
        self.reason = reason
        self.series = series
        self.zone = zone
        super().__init__(reason)


def _ts(value) -> str | None:
    """Normalize str | date | datetime to what the API accepts (ISO 8601)."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    raise TypeError(f"start/end must be str, date or datetime — got {type(value).__name__}")


class Obsyd:
    """Client for one OBSYD instance (hosted obsyd.dev or your self-host)."""

    def __init__(
        self,
        base_url: str = "https://obsyd.dev",
        timeout: float = 60,
        max_retries: int = 3,
        session: requests.Session | None = None,
    ):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = session or requests.Session()
        # Unconditional: requests pre-populates a default UA, so setdefault would
        # never fire — and the obsyd-python/ UA is how server logs measure client
        # adoption. Callers who need a custom UA can set it after construction.
        self._session.headers["User-Agent"] = f"obsyd-python/{__version__}"
        self._zone_keys: set[str] | None = None  # lazy cache for validation

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _get(self, path: str, **params) -> requests.Response:
        params = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base}/api/v1/{path}"
        for attempt in range(self.max_retries + 1):
            try:
                r = self._session.get(url, params=params, timeout=self.timeout)
            except requests.ConnectionError:
                if attempt >= self.max_retries:
                    raise
                self._sleep(attempt, None)
                continue
            if r.status_code in _RETRY_STATUSES and attempt < self.max_retries:
                self._sleep(attempt, r.headers.get("Retry-After"))
                continue
            if r.ok:
                return r
            detail = self._detail(r)
            if r.status_code == 400:
                raise ObsydBadRequest(400, detail)
            if r.status_code == 429:
                raise ObsydRateLimited(429, detail)
            if r.status_code == 501:
                raise ObsydServerError(501, f"{detail} (hint: use format='csv')")
            if r.status_code >= 500:
                raise ObsydServerError(r.status_code, detail)
            raise ObsydHTTPError(r.status_code, detail)
        raise ObsydError("unreachable")  # pragma: no cover

    @staticmethod
    def _sleep(attempt: int, retry_after: str | None) -> None:
        delay = 2**attempt + random.uniform(0, 0.5)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        time.sleep(delay)

    @staticmethod
    def _detail(r: requests.Response) -> str:
        try:
            return r.json().get("detail", r.text[:200])
        except ValueError:
            return r.text[:200]

    def _check_json_available(self, payload: dict) -> dict:
        if isinstance(payload, dict) and payload.get("available") is False:
            raise ObsydNoData(
                payload.get("reason", "no data for this query"),
                series=payload.get("series"),
                zone=payload.get("zone"),
            )
        return payload

    def _validate_zone(self, zone: str) -> None:
        """Fail loudly on unknown zones — parts of the API silently fall back
        to the default zone otherwise, which would hand you DE_LU data for a
        typo'd key."""
        if self._zone_keys is None:
            try:
                self._zone_keys = set(self.zones().get("enabled_keys") or [])
            except ObsydError:
                self._zone_keys = set()  # validation is best-effort, never a blocker
        if self._zone_keys and zone not in self._zone_keys:
            hint = difflib.get_close_matches(zone, self._zone_keys, n=3)
            suffix = f" — did you mean {', '.join(hint)}?" if hint else ""
            raise ValueError(f"unknown zone {zone!r}{suffix} (see Obsyd().zones())")

    def _csv_frame(self, r: requests.Response, index_col: str, *, series=None, zone=None) -> pd.DataFrame:
        # The API can answer a format=csv request with a JSON available:false body.
        if r.headers.get("content-type", "").startswith("application/json"):
            self._check_json_available(r.json())
        df = pd.read_csv(io.StringIO(r.text))
        if df.empty:
            raise ObsydNoData(
                "the response contained no rows — check the series key, zone and "
                "coverage window via Obsyd().catalog()",
                series=series,
                zone=zone,
            )
        if index_col in df.columns:
            df[index_col] = pd.to_datetime(df[index_col], utc=True)
            df = df.set_index(index_col)
        if attribution := r.headers.get("x-attribution"):
            df.attrs["attribution"] = attribution
        return df

    # ── reference endpoints (dicts, not rate-limited) ────────────────────────

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

    # ── tabular endpoints (DataFrames) ───────────────────────────────────────

    def series(
        self,
        series: str,
        zone: str,
        start=None,
        end=None,
        resolution: str = "hourly",
        format: str = "csv",
    ) -> pd.DataFrame:
        """One series for one zone (index = tz-aware UTC time, column ``value``).

        `series` e.g. 'price.dayahead' (or '.qh' for raw 15-min steps),
        'load.actual', 'residual.actual', 'gen.B16', 'flow.FR'.
        `resolution` 'hourly' or 'daily' — daily rows carry an ``hours`` column
        (24 = a settled day). `start`/`end` accept str, date or datetime;
        `end` is open by default (everything on record).
        `format='parquet'` for bulk pulls (needs pyarrow on both ends).
        """
        self._validate_zone(zone)
        index_col = "date" if resolution == "daily" else "datetime_utc"
        if format == "parquet":
            r = self._get(
                "series", series=series, zone=zone, start=_ts(start), end=_ts(end),
                resolution=resolution, format="parquet",
            )
            try:
                df = pd.read_parquet(io.BytesIO(r.content))
            except ImportError as e:  # pragma: no cover - depends on local env
                raise ImportError("parquet needs pyarrow: pip install obsyd[parquet]") from e
            if df.empty:
                raise ObsydNoData("the response contained no rows", series=series, zone=zone)
            if index_col in df.columns:
                df[index_col] = pd.to_datetime(df[index_col], utc=True)
                df = df.set_index(index_col)
        else:
            r = self._get(
                "series", series=series, zone=zone, start=_ts(start), end=_ts(end),
                resolution=resolution, format="csv",
            )
            df = self._csv_frame(r, index_col, series=series, zone=zone)
        df.attrs.update({"series": series, "zone": zone, "resolution": resolution})
        return df

    def series_multi(
        self,
        series: str,
        zones: Sequence[str],
        start=None,
        end=None,
        resolution: str = "hourly",
        pause: float = 0.5,
    ) -> pd.DataFrame:
        """One series across many zones (wide: one column per zone).

        Loops `/series` with a polite `pause` between requests (all 37 zones in
        about 20 s, well inside the public rate limit). Zones without data are
        skipped with a warning. For "recent window, every zone" prefer
        :meth:`snapshot` — that is a single request.
        """
        frames: dict[str, pd.Series] = {}
        for i, zone in enumerate(zones):
            if i:
                time.sleep(pause)
            try:
                frames[zone] = self.series(series, zone, start=start, end=end, resolution=resolution)["value"]
            except ObsydNoData as e:
                warnings.warn(f"{zone}: {e.reason}", stacklevel=2)
        if not frames:
            raise ObsydNoData(f"no zone returned data for {series!r}", series=series)
        df = pd.DataFrame(frames)
        df.attrs.update({"series": series, "resolution": resolution})
        return df

    def snapshot(self, series: str = "price.dayahead", hours: int = 168, start=None, end=None) -> pd.DataFrame:
        """A recent window of one series across every enabled zone, ONE request.

        Wide frame: index = UTC timestamps, one column per zone (gaps = NaN).
        `hours` ≤ 744.
        """
        payload = self._check_json_available(
            self._get("snapshot", series=series, hours=hours, start=_ts(start), end=_ts(end)).json()
        )
        idx = pd.to_datetime(payload["timestamps"], utc=True)
        df = pd.DataFrame(payload["zones"], index=idx)
        df.index.name = "datetime_utc"
        df.attrs.update({"series": series, "unit": payload.get("unit")})
        return df

    def genmix(self, zone: str = "DE_LU", start=None, end=None, resolution: str = "monthly") -> pd.DataFrame:
        """Generation mix over time (wide: one column per fuel, mean MW).

        `resolution` 'daily' or 'monthly'.
        """
        self._validate_zone(zone)
        r = self._get(
            "genmix", zone=zone, start=_ts(start), end=_ts(end),
            resolution=resolution, format="csv",
        )
        df = self._csv_frame(r, "t", zone=zone)
        df.attrs.update({"zone": zone, "resolution": resolution, "unit": "MW"})
        return df

    def capacity(self, zone: str, year: int | None = None) -> pd.DataFrame:
        """Installed capacity per production type (ENTSO-E A68) for one zone."""
        self._validate_zone(zone)
        payload = self._check_json_available(self._get("capacity", zone=zone, year=year).json())
        df = pd.DataFrame(payload["data"])
        df.attrs.update({k: payload.get(k) for k in ("zone", "year", "unit", "total_mw")})
        return df

    def units(self, zone: str) -> pd.DataFrame:
        """Named production units (EIC, fuel, nominal MW) for one zone.

        The `note` in ``df.attrs`` explains what this is NOT (not the full
        installed fleet) — read it before summing.
        """
        self._validate_zone(zone)
        payload = self._check_json_available(self._get("units", zone=zone).json())
        df = pd.DataFrame(payload["units"])
        df.attrs.update({k: payload.get(k) for k in ("zone", "year", "count", "published_capacity_mw", "note")})
        return df
