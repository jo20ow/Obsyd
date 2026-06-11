"""Eurostat monthly gas consumption (nrg_cb_gasm) — demand-model calibration.

Not part of the daily pipeline; used only to calibrate the heating/industrial
split. Gross inland consumption (IC_CAL_MG), natural gas (G3000), in TJ_GCV →
GWh. Per EU27 country, monthly. Free API, no key. JSON-stat decoded the easy
way: the query fixes every dimension except time, so value indices map 1:1 to
the time category.
"""

from __future__ import annotations

import logging
from datetime import date

import httpx

from backend.gas import raw_cache

logger = logging.getLogger(__name__)

BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/nrg_cb_gasm"
TJ_TO_GWH = 0.277778  # 1 TJ = 0.27778 GWh

EU27 = (
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "EL",  # EL = Greece in Eurostat
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
)


async def fetch_country(geo: str, since: str, *, overwrite: bool = False) -> dict:
    """Raw JSON-stat for one country (cached, monthly bucket)."""

    async def _do() -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                BASE,
                params={
                    "format": "JSON",
                    "geo": geo,
                    "nrg_bal": "IC_CAL_MG",
                    "siec": "G3000",
                    "unit": "TJ_GCV",
                    "sinceTimePeriod": since,
                },
            )
            resp.raise_for_status()
            return resp.json()

    return await raw_cache.fetch_or_cache("eurostat", f"nrg_cb_gasm_{geo}", date.today().replace(day=1), _do, overwrite=overwrite)


def parse_consumption(payload: dict) -> dict[str, float]:
    """JSON-stat → {YYYY-MM: GWh}. Empty dict on missing data / error shape."""
    try:
        time_index = payload["dimension"]["time"]["category"]["index"]
        values = payload["value"]
    except (KeyError, TypeError):
        return {}
    inv = {idx: month for month, idx in time_index.items()}
    out: dict[str, float] = {}
    for flat_idx, tj in values.items():
        month = inv.get(int(flat_idx))
        if month is not None and tj is not None:
            out[month] = float(tj) * TJ_TO_GWH
    return out


async def load_monthly_consumption(since: str = "2023-01", *, countries=EU27, overwrite: bool = False) -> dict[str, dict[str, float]]:
    """{country: {YYYY-MM: GWh}} of gross inland gas consumption."""
    out: dict[str, dict[str, float]] = {}
    for geo in countries:
        try:
            payload = await fetch_country(geo, since, overwrite=overwrite)
        except httpx.HTTPError as exc:
            logger.warning("eurostat: %s fetch failed: %s", geo, exc)
            continue
        series = parse_consumption(payload)
        if series:
            out[geo] = series
    return out


def eu_monthly_total(per_country: dict[str, dict[str, float]]) -> dict[str, float]:
    """Sum to an EU monthly total {YYYY-MM: GWh}."""
    total: dict[str, float] = {}
    for series in per_country.values():
        for month, gwh in series.items():
            total[month] = total.get(month, 0.0) + gwh
    return total
