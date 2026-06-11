"""Open-Meteo HDD ingestion (free, no key).

Heating-degree-days drive the temperature-sensitive part of gas demand. For
each country we take a small population-weighted basket of cities, fetch daily
mean temperature from the Open-Meteo historical archive, compute the
population-weighted national mean, then HDD = max(0, base - T_mean). The 8
basket countries are ~85% of EU heating-gas demand.

  HDD = max(0, HDD_BASE - T_mean),  HDD_BASE = 15.5 °C (configurable)

Stored per (date, country) in gas_weather.
"""

from __future__ import annotations

import logging
from datetime import date

import httpx
from sqlalchemy.orm import Session

from backend.gas import raw_cache
from backend.models.gas import GasWeather

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HDD_BASE = 15.5  # °C

# Population-weighted city baskets per country (lat, lon, weight). Weights are
# rough metro populations (millions); only relative weights matter.
CITY_BASKETS: dict[str, list[tuple[float, float, float]]] = {
    "DE": [(52.52, 13.41, 3.7), (53.55, 9.99, 1.9), (48.14, 11.58, 1.5), (50.94, 6.96, 1.1), (50.11, 8.68, 0.8)],
    "FR": [(48.85, 2.35, 11.0), (45.76, 4.84, 1.7), (43.30, 5.37, 1.6), (43.60, 1.44, 1.0), (50.63, 3.06, 1.0)],
    "IT": [(41.90, 12.50, 4.3), (45.46, 9.19, 3.2), (40.85, 14.27, 3.1), (45.07, 7.69, 1.8), (44.49, 11.34, 1.0)],
    "NL": [(52.37, 4.90, 2.5), (51.92, 4.48, 1.0), (52.08, 4.30, 1.0), (51.44, 5.47, 0.8)],
    "ES": [(40.42, -3.70, 6.7), (41.39, 2.17, 5.6), (39.47, -0.38, 1.6), (37.39, -5.98, 1.5)],
    "BE": [(50.85, 4.35, 2.1), (51.22, 4.40, 1.0), (51.05, 3.72, 0.5)],
    "AT": [(48.21, 16.37, 2.8), (47.07, 15.44, 0.6), (48.30, 14.29, 0.5)],
    "PL": [(52.23, 21.01, 3.1), (50.06, 19.95, 1.0), (51.11, 17.04, 0.9), (54.35, 18.65, 1.5)],
}

# Basket countries are ~85% of EU heating gas; scale the EU total up by this.
COVERAGE_SCALE = 1.0 / 0.85


async def fetch_city_temps(lat: float, lon: float, start: str, end: str, *, overwrite: bool = False) -> dict[str, float]:
    """Daily mean temperature for one point over [start, end] (raw-cached)."""
    dt = date.fromisoformat(start)

    async def _do() -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                ARCHIVE_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": start,
                    "end_date": end,
                    "daily": "temperature_2m_mean",
                    "timezone": "UTC",
                },
            )
            resp.raise_for_status()
            return resp.json()

    key = f"{lat:.2f}_{lon:.2f}_{start}_{end}"
    payload = await raw_cache.fetch_or_cache("openmeteo", key, dt, _do, overwrite=overwrite)
    daily = payload.get("daily", {})
    times = daily.get("time", [])
    temps = daily.get("temperature_2m_mean", [])
    return {t: v for t, v in zip(times, temps) if v is not None}


def hdd(t_mean: float, base: float = HDD_BASE) -> float:
    return max(0.0, base - t_mean)


async def ingest_country(db: Session, country: str, start: str, end: str, *, overwrite: bool = False) -> int:
    """Population-weighted mean temp → HDD per day for one country."""
    basket = CITY_BASKETS[country]
    # date -> [(temp, weight), ...]
    acc: dict[str, list[tuple[float, float]]] = {}
    for lat, lon, weight in basket:
        temps = await fetch_city_temps(lat, lon, start, end, overwrite=overwrite)
        for d, t in temps.items():
            acc.setdefault(d, []).append((t, weight))

    written = 0
    for d, pairs in acc.items():
        wsum = sum(w for _, w in pairs)
        if wsum == 0:
            continue
        t_mean = sum(t * w for t, w in pairs) / wsum
        _upsert(db, d, country, round(t_mean, 2), round(hdd(t_mean), 2))
        written += 1
    db.commit()
    return written


async def ingest_weather(db: Session, days: list[str], *, overwrite: bool = False) -> dict:
    """Ingest HDD for every basket country over the span of `days`."""
    if not days:
        return {"days": 0, "countries": 0, "written": 0}
    start, end = min(days), max(days)
    total = 0
    for country in CITY_BASKETS:
        try:
            total += await ingest_country(db, country, start, end, overwrite=overwrite)
        except httpx.HTTPError as exc:
            logger.warning("weather: %s fetch failed: %s", country, exc)
    logger.info("weather.ingest: %d country-days over %s..%s", total, start, end)
    return {"days": len(days), "countries": len(CITY_BASKETS), "written": total}


def _upsert(db: Session, day: str, country: str, t_mean: float, hdd_val: float) -> None:
    existing = db.get(GasWeather, (day, country))
    if existing:
        existing.t_mean = t_mean
        existing.hdd = hdd_val
    else:
        db.add(GasWeather(date=day, country=country, t_mean=t_mean, hdd=hdd_val))
