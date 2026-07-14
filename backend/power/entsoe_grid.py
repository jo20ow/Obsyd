"""ENTSO-E electricity grid data: total load (A65) + generation mix (A75).

Fetches per bidding zone for the DE-LU area (EIC 10Y1001A1001A82H):
  A65 documentType → Actual Total Load → daily mean MW
  A75 documentType → Actual Generation per Production Type → daily mean MW
    B16 = Solar PV
    B18 = Wind Offshore
    B19 = Wind Onshore

wind_mw = B18 + B19 (combined onshore + offshore)
solar_mw = B16

Residual load = load − wind − solar is DERIVED on read, not stored.

Shares token / base URL / XML helpers with backend.gas.entsoe to avoid
duplication. Caches raw XML under 'entsoe_load' and 'entsoe_genmix' keys in
the raw_cache filesystem store.
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.gas import raw_cache
from backend.gas.entsoe import ENTSOE_BASE, _localname, _parse_utc, _token
from backend.models.energy import PowerGenMix, PowerGrid
from backend.power.daily import daily_from_hours, days_to_derive
from backend.power.hourly_store import upsert_day_hours

logger = logging.getLogger(__name__)

# German-Luxembourg bidding zone EIC code (same as entsoe_prices.py)
DE_LU_EIC = "10Y1001A1001A82H"

# psrType codes for renewable generation (A75)
PSR_SOLAR = "B16"
PSR_WIND_OFFSHORE = "B18"
PSR_WIND_ONSHORE = "B19"

# ENTSO-E psrType code → readable label (A75 generation types)
PSR_LABELS: dict[str, str] = {
    "B01": "Biomass",
    "B02": "Lignite",
    # B03/B07/B08/B13 were missing, so the mix legend read "gen.B03" — the raw code, between
    # "Fossil Gas" and "Hard Coal". B03 is coal-derived gas and DE-LU burns it. Every generation
    # code in the ENTSO-E list (B01-B20) belongs here; B21-B24 are network elements, not fuels.
    "B03": "Fossil Coal-derived gas",
    "B04": "Fossil Gas",
    "B05": "Hard Coal",
    "B06": "Oil",
    "B07": "Oil Shale",
    "B08": "Peat",
    "B09": "Geothermal",
    "B13": "Marine",
    "B10": "Hydro Pumped Storage",
    "B11": "Hydro Run-of-river",
    "B12": "Hydro Reservoir",
    "B14": "Nuclear",
    "B15": "Other Renewable",
    "B16": "Solar",
    "B17": "Waste",
    "B18": "Wind Offshore",
    "B19": "Wind Onshore",
    "B20": "Other",
}

_RESOLUTION_HOURS = {"PT60M": 1.0, "PT30M": 0.5, "PT15M": 0.25}


# ─── parsers ─────────────────────────────────────────────────────────────────


def parse_load(xml_text: str) -> dict[str, float]:
    """Parse an A65 GL_MarketDocument into {YYYY-MM-DD: daily_mean_mw}.

    Walks TimeSeries → Period → Point, reads <quantity> (MW), buckets each
    hourly value to its UTC calendar day, and returns the daily MEAN in MW.

    Namespace-agnostic (matches local tag names only). Unlike the gas B04
    parser (which SUMS energy = MW × hours → GWh), here we collect raw MW
    values and average them to get daily-mean load in MW.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A65 XML parse error: {exc}") from exc

    by_day: dict[str, list[float]] = {}

    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        for period in (e for e in ts.iter() if _localname(e.tag) == "Period"):
            start_el = next(
                (e for e in period.iter() if _localname(e.tag) == "start"), None
            )
            res_el = next(
                (e for e in period.iter() if _localname(e.tag) == "resolution"), None
            )
            if start_el is None or res_el is None:
                continue
            start = _parse_utc(start_el.text)
            res_hours = _RESOLUTION_HOURS.get((res_el.text or "").strip())
            if start is None or res_hours is None:
                continue
            for point in (e for e in period.iter() if _localname(e.tag) == "Point"):
                pos = next(
                    (e.text for e in point if _localname(e.tag) == "position"), None
                )
                qty = next(
                    (e.text for e in point if _localname(e.tag) == "quantity"), None
                )
                if pos is None or qty is None:
                    continue
                try:
                    ts_time = start + timedelta(hours=res_hours * (int(pos) - 1))
                    mw = float(qty)
                except (ValueError, TypeError):
                    continue
                day = ts_time.astimezone(timezone.utc).strftime("%Y-%m-%d")
                by_day.setdefault(day, []).append(mw)

    return {day: sum(vals) / len(vals) for day, vals in by_day.items() if vals}


#: Suffix marking a CONSUMPTION series in a parsed A75 result. ENTSO-E publishes
#: storage technologies TWICE in the same document: once as generation
#: (inBiddingZone_Domain) and once as consumption (outBiddingZone_Domain) —
#: pumped storage (B10) pumping is the common case. Keying only on psrType, as
#: this parser did until 2026-07-12, silently AVERAGED the two into one
#: meaningless number (measured on DE-LU 2026-06: generation 1253 MW, pumping
#: 1579 MW → stored 1416 MW) and let pumping inflate reported generation, which
#: in turn made the A75 coverage guard (backend/power/coverage.py) too generous.
CONSUMPTION_SUFFIX = "_CONS"


def _series_psr_key(ts) -> str | None:
    """psrType of one TimeSeries, suffixed when the series is CONSUMPTION.

    Direction comes from the domain element: outBiddingZone_Domain = energy
    leaving the zone's grid into the unit (pumping / charging).
    """
    psr = next((e.text for e in ts.iter() if _localname(e.tag) == "psrType"), None)
    if psr is None:
        return None
    is_consumption = any(
        _localname(e.tag).startswith("outBiddingZone_Domain") for e in ts.iter()
    )
    return f"{psr}{CONSUMPTION_SUFFIX}" if is_consumption else psr


def is_consumption_key(psr_key: str) -> bool:
    return psr_key.endswith(CONSUMPTION_SUFFIX)


def base_psr(psr_key: str) -> str:
    """'B10_CONS' → 'B10'."""
    return psr_key[: -len(CONSUMPTION_SUFFIX)] if is_consumption_key(psr_key) else psr_key


def parse_generation_by_type(xml_text: str) -> dict[str, dict[str, float]]:
    """Parse an A75 GL_MarketDocument into {YYYY-MM-DD: {psrType: daily_mean_mw}}.

    Walks every TimeSeries, reads its <psrType>, then walks its Periods/Points
    collecting hourly MW quantities. Buckets per UTC day per psrType.
    Returns the DAILY MEAN in MW for each psrType found.

    Key differences from gas.entsoe.parse_generation (B04 parser):
      1. Does NOT filter to a single psrType — collects ALL types present in
         the XML and keys results by psrType string (B16, B18, B19, etc.).
      2. Aggregation is MEAN (not SUM): load/renewable capacity is measured
         in instantaneous MW, not energy volume.
      3. Returns a nested dict {date: {psr: mean_mw}} instead of {date: gwh}.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A75 XML parse error: {exc}") from exc

    # by_day[date][psr] = list[float]  (MW readings)
    by_day: dict[str, dict[str, list[float]]] = {}

    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        psr = _series_psr_key(ts)
        if psr is None:
            continue
        for period in (e for e in ts.iter() if _localname(e.tag) == "Period"):
            start_el = next(
                (e for e in period.iter() if _localname(e.tag) == "start"), None
            )
            res_el = next(
                (e for e in period.iter() if _localname(e.tag) == "resolution"), None
            )
            if start_el is None or res_el is None:
                continue
            start = _parse_utc(start_el.text)
            res_hours = _RESOLUTION_HOURS.get((res_el.text or "").strip())
            if start is None or res_hours is None:
                continue
            for point in (e for e in period.iter() if _localname(e.tag) == "Point"):
                pos = next(
                    (e.text for e in point if _localname(e.tag) == "position"), None
                )
                qty = next(
                    (e.text for e in point if _localname(e.tag) == "quantity"), None
                )
                if pos is None or qty is None:
                    continue
                try:
                    ts_time = start + timedelta(hours=res_hours * (int(pos) - 1))
                    mw = float(qty)
                except (ValueError, TypeError):
                    continue
                day = ts_time.astimezone(timezone.utc).strftime("%Y-%m-%d")
                by_day.setdefault(day, {}).setdefault(psr, []).append(mw)

    return {
        day: {psr: sum(vals) / len(vals) for psr, vals in psr_map.items() if vals}
        for day, psr_map in by_day.items()
    }


def parse_load_hourly(xml_text: str) -> dict[str, dict[int, float]]:
    """Parse an A65 GL_MarketDocument into {YYYY-MM-DD: {hour_utc: mean_mw}}.

    Same walk as parse_load, but keeps the hour-of-day (UTC 0-23) instead of
    collapsing to a daily mean. Sub-hourly slots (PT15M/PT30M) and overlapping
    TimeSeries are averaged within each hour.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A65 XML parse error: {exc}") from exc

    by_day_hour: dict[str, dict[int, list[float]]] = {}
    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        for period in (e for e in ts.iter() if _localname(e.tag) == "Period"):
            start_el = next((e for e in period.iter() if _localname(e.tag) == "start"), None)
            res_el = next((e for e in period.iter() if _localname(e.tag) == "resolution"), None)
            if start_el is None or res_el is None:
                continue
            start = _parse_utc(start_el.text)
            res_hours = _RESOLUTION_HOURS.get((res_el.text or "").strip())
            if start is None or res_hours is None:
                continue
            for point in (e for e in period.iter() if _localname(e.tag) == "Point"):
                pos = next((e.text for e in point if _localname(e.tag) == "position"), None)
                qty = next((e.text for e in point if _localname(e.tag) == "quantity"), None)
                if pos is None or qty is None:
                    continue
                try:
                    ts_time = start + timedelta(hours=res_hours * (int(pos) - 1))
                    mw = float(qty)
                except (ValueError, TypeError):
                    continue
                utc = ts_time.astimezone(timezone.utc)
                day = utc.strftime("%Y-%m-%d")
                by_day_hour.setdefault(day, {}).setdefault(utc.hour, []).append(mw)

    return {
        day: {h: sum(v) / len(v) for h, v in hours.items() if v}
        for day, hours in by_day_hour.items()
    }


def parse_generation_hourly(xml_text: str) -> dict[str, dict[str, dict[int, float]]]:
    """Parse an A75/A69 GL_MarketDocument into {date: {psrType: {hour_utc: mean_mw}}}.

    Hourly counterpart of parse_generation_by_type — keeps the hour-of-day so the
    per-hour wind/solar forecast can be subtracted from the hourly load forecast.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A75 XML parse error: {exc}") from exc

    by: dict[str, dict[str, dict[int, list[float]]]] = {}
    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        psr = _series_psr_key(ts)
        if psr is None:
            continue
        for period in (e for e in ts.iter() if _localname(e.tag) == "Period"):
            start_el = next((e for e in period.iter() if _localname(e.tag) == "start"), None)
            res_el = next((e for e in period.iter() if _localname(e.tag) == "resolution"), None)
            if start_el is None or res_el is None:
                continue
            start = _parse_utc(start_el.text)
            res_hours = _RESOLUTION_HOURS.get((res_el.text or "").strip())
            if start is None or res_hours is None:
                continue
            for point in (e for e in period.iter() if _localname(e.tag) == "Point"):
                pos = next((e.text for e in point if _localname(e.tag) == "position"), None)
                qty = next((e.text for e in point if _localname(e.tag) == "quantity"), None)
                if pos is None or qty is None:
                    continue
                try:
                    ts_time = start + timedelta(hours=res_hours * (int(pos) - 1))
                    mw = float(qty)
                except (ValueError, TypeError):
                    continue
                utc = ts_time.astimezone(timezone.utc)
                day = utc.strftime("%Y-%m-%d")
                by.setdefault(day, {}).setdefault(psr, {}).setdefault(utc.hour, []).append(mw)

    return {
        day: {
            psr: {h: sum(v) / len(v) for h, v in hours.items() if v}
            for psr, hours in psrs.items()
        }
        for day, psrs in by.items()
    }


def build_hourly_forecast(
    load_by_hour: dict[int, float],
    gen_by_hour: dict[str, dict[int, float]],
) -> list[dict]:
    """Combine hourly load + wind + solar forecasts into a 24-point residual series.

    residual = load − (wind_offshore + wind_onshore) − solar, per hour. wind/solar
    (and thus residual) are None for an hour with no renewable forecast. Returns
    [{hour, load_mw, wind_mw, solar_mw, residual_mw}] ordered by hour.
    """
    off = gen_by_hour.get(PSR_WIND_OFFSHORE, {})
    on = gen_by_hour.get(PSR_WIND_ONSHORE, {})
    solar = gen_by_hour.get(PSR_SOLAR, {})
    out: list[dict] = []
    for hour in sorted(load_by_hour):
        load = load_by_hour[hour]
        wind = None
        if hour in off or hour in on:
            wind = (off.get(hour) or 0.0) + (on.get(hour) or 0.0)
        s = solar.get(hour)
        resid = round(load - wind - s, 2) if wind is not None and s is not None else None
        out.append({
            "hour": hour,
            "load_mw": round(load, 2),
            "wind_mw": round(wind, 2) if wind is not None else None,
            "solar_mw": round(s, 2) if s is not None else None,
            "residual_mw": resid,
        })
    return out


# ─── fetch ────────────────────────────────────────────────────────────────────


async def _fetch_zone_month(
    eic: str,
    month_start: date,
    doctype: str,
    extra_params: dict,
    *,
    overwrite: bool = False,
    cache_source: str | None = None,
) -> str:
    """Fetch one zone's XML for a calendar month (raw XML, cached).

    `doctype` is "A65" or "A75"; `extra_params` carries doctype-specific
    params (e.g. outBiddingZone_Domain for A65, processType for A75).
    Cache keys are scoped by doctype so A65 and A75 don't collide.

    Cache sources:
      A65 → "entsoe_load"
      A75 → "entsoe_genmix"
    """
    nxt = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    period_start = f"{month_start:%Y%m%d}0000"
    period_end = f"{nxt:%Y%m%d}0000"

    # A65 covers BOTH actual load (processType A16) and the day-ahead forecast (A01);
    # callers must pass a distinct cache_source for the forecast so the two don't collide.
    if cache_source is None:
        cache_source = "entsoe_load" if doctype == "A65" else "entsoe_genmix"
    cache_key = f"{eic}_{month_start:%Y-%m}"

    async def _do() -> dict:
        params = {
            "securityToken": _token(),
            "documentType": doctype,
            "periodStart": period_start,
            "periodEnd": period_end,
        }
        params.update(extra_params)
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.get(ENTSOE_BASE, params=params)
            resp.raise_for_status()
            return {"xml": resp.text}

    payload = await raw_cache.fetch_or_cache(
        cache_source, cache_key, month_start, _do, overwrite=overwrite
    )
    return payload.get("xml", "")


# ─── ingest ───────────────────────────────────────────────────────────────────


async def ingest_load_forecast(
    db: Session,
    days: list[str],
    *,
    eic: str = DE_LU_EIC,
    zone: str = "DE_LU",
    overwrite: bool = False,
) -> dict:
    """Fetch the ENTSO-E day-ahead total-load FORECAST (A65, processType A01) for the
    month(s) spanning `days` and upsert daily-mean MW into PowerLoadForecast.

    Same document type + parser as actual load — only processType differs (A01 vs A16).
    The A01 series extends to the last published day-ahead day (tomorrow), which is why
    `days` should include the +1 frontier. Returns {"days","written"} or a skip marker.
    """
    from backend.models.energy import PowerLoadForecast

    if not days:
        return {"days": 0, "written": 0}
    if not settings.entsoe_api_token:
        logger.warning("entsoe_grid.ingest_load_forecast: ENTSOE_API_TOKEN not set — skipping")
        return {"skipped": "no token"}

    wanted = set(days)
    months = sorted({datetime.strptime(d, "%Y-%m-%d").date().replace(day=1) for d in days})

    load_by_day: dict[str, float] = {}
    wind_by_day: dict[str, float] = {}
    solar_by_day: dict[str, float] = {}
    load_hourly_by_day: dict[str, dict[int, float]] = {}
    gen_hourly_by_day: dict[str, dict[str, dict[int, float]]] = {}
    for month_start in months:
        # ── A65/A01 day-ahead load forecast ──
        try:
            xml = await _fetch_zone_month(
                eic, month_start, "A65",
                {"processType": "A01", "outBiddingZone_Domain": eic},
                overwrite=overwrite, cache_source="entsoe_load_forecast",
            )
        except httpx.HTTPError as exc:
            logger.warning("entsoe_grid: A65/A01 %s fetch failed: %s", month_start, exc)
            xml = ""
        if xml:
            for day, hours in parse_load_hourly(xml).items():
                if day in wanted:
                    load_hourly_by_day[day] = hours

        # ── A69/A01 day-ahead wind + solar forecast (same parser as A75 genmix) ──
        try:
            gxml = await _fetch_zone_month(
                eic, month_start, "A69",
                {"processType": "A01", "in_Domain": eic},
                overwrite=overwrite, cache_source="entsoe_gen_forecast",
            )
        except httpx.HTTPError as exc:
            logger.warning("entsoe_grid: A69/A01 %s fetch failed: %s", month_start, exc)
            gxml = ""
        if gxml:
            for day, by_psr in parse_generation_hourly(gxml).items():
                if day in wanted:
                    gen_hourly_by_day[day] = by_psr

    # The daily forecast means come from the hourly forecast shape, by the same rule as the
    # actuals (power/daily.py): a forecast for solar is published for the daylight hours in some
    # zones, and dividing by those hours forecasts a sun that shines all night. The day filter of
    # the actuals does NOT apply — a forecast is for a day that has not happened yet, which is
    # the whole point of it.
    for day in sorted(set(load_hourly_by_day) | set(gen_hourly_by_day)):
        derived = daily_from_hours(load_hourly_by_day.get(day, {}), gen_hourly_by_day.get(day, {}))
        if derived["load_mw"] is not None:
            load_by_day[day] = derived["load_mw"]
        if derived["mix"]:
            wind_by_day[day] = derived["wind_mw"]
            solar_by_day[day] = derived["solar_mw"]

    written = 0
    for day in sorted(set(load_by_day) | set(wind_by_day) | set(solar_by_day)):
        row = (
            db.query(PowerLoadForecast)
            .filter(PowerLoadForecast.date == day, PowerLoadForecast.zone == zone)
            .first()
        )
        load = load_by_day.get(day)
        wind = wind_by_day.get(day)
        solar = solar_by_day.get(day)
        # Hourly residual-load shape (tomorrow's price-driving curve), if hourly load present.
        hourly = load_hourly_by_day.get(day)
        hourly_json = (
            json.dumps(build_hourly_forecast(hourly, gen_hourly_by_day.get(day, {})))
            if hourly else None
        )
        if row is None:
            if load is None:
                continue  # forecast_mw is required — skip a day with only wind/solar
            db.add(PowerLoadForecast(
                date=day, zone=zone, forecast_mw=round(load, 2),
                wind_forecast_mw=round(wind, 2) if wind is not None else None,
                solar_forecast_mw=round(solar, 2) if solar is not None else None,
                hourly_forecast=hourly_json,
            ))
            written += 1
        elif overwrite:
            if load is not None:
                row.forecast_mw = round(load, 2)
            if wind is not None:
                row.wind_forecast_mw = round(wind, 2)
            if solar is not None:
                row.solar_forecast_mw = round(solar, 2)
            if hourly_json is not None:
                row.hourly_forecast = hourly_json
            written += 1
    db.commit()

    # ── Hourly forecast series → power_hourly (load/wind/solar/residual forecast) ──
    fc_series: dict[str, dict[str, dict[int, float]]] = {
        "load.forecast": {}, "wind.forecast": {}, "solar.forecast": {}, "residual.forecast": {},
    }
    for day, load_hours in load_hourly_by_day.items():
        for p in build_hourly_forecast(load_hours, gen_hourly_by_day.get(day, {})):
            h = p["hour"]
            for key, val in (
                ("load.forecast", p["load_mw"]),
                ("wind.forecast", p["wind_mw"]),
                ("solar.forecast", p["solar_mw"]),
                ("residual.forecast", p["residual_mw"]),
            ):
                if val is not None:
                    fc_series[key].setdefault(day, {})[h] = val
    for series_key, day_hours in fc_series.items():
        if day_hours:
            upsert_day_hours(db, series_key, zone, day_hours, unit="MW")

    return {"days": len(load_by_day), "written": written}


async def ingest_generation_forecast(
    db: Session,
    days: list[str],
    *,
    eic: str = DE_LU_EIC,
    zone: str = "DE_LU",
    overwrite: bool = False,
) -> dict:
    """Fetch the ENTSO-E day-ahead TOTAL generation forecast (A71, processType A01)
    and upsert it as the hourly series `generation.forecast`.

    A71 shares the GL_MarketDocument shape with A65 (quantity points, no psrType),
    so the load parsers do the work; only the document type, the domain param
    (in_Domain, like A69) and the cache source differ. Complements load/wind/solar
    forecast for the forecast-vs-actual view.
    """
    if not days:
        return {"days": 0, "written": 0}
    if not settings.entsoe_api_token:
        logger.warning("entsoe_grid.ingest_generation_forecast: ENTSOE_API_TOKEN not set — skipping")
        return {"skipped": "no token"}

    wanted = set(days)
    months = sorted({datetime.strptime(d, "%Y-%m-%d").date().replace(day=1) for d in days})

    by_day: dict[str, dict[int, float]] = {}
    for month_start in months:
        try:
            xml = await _fetch_zone_month(
                eic, month_start, "A71",
                {"processType": "A01", "in_Domain": eic},
                overwrite=overwrite, cache_source="entsoe_gen_total_forecast",
            )
        except httpx.HTTPError as exc:
            logger.warning("entsoe_grid: A71/A01 %s fetch failed: %s", month_start, exc)
            continue
        if not xml:
            continue
        for day, hours in parse_load_hourly(xml).items():
            if day in wanted:
                by_day[day] = hours

    written = upsert_day_hours(db, "generation.forecast", zone, by_day, unit="MW") if by_day else 0
    return {"days": len(by_day), "written": written}


async def ingest_grid(
    db: Session,
    days: list[str],
    *,
    eic: str = DE_LU_EIC,
    zone: str = "DE_LU",
    overwrite: bool = False,
) -> dict:
    """Fetch A65 (total load) + A75 (generation mix) for the month(s) spanning
    `days`, compute per-day daily-mean MW for load / wind / solar, and upsert
    into PowerGrid.

    wind_mw = B18 (offshore) + B19 (onshore) combined daily mean.
    solar_mw = B16 (solar PV) daily mean.

    Returns {"days": n, "written": n} on success, or {"skipped": "no token"}
    if ENTSOE_API_TOKEN is not configured.
    """
    if not days:
        return {"days": 0, "written": 0}
    if not settings.entsoe_api_token:
        logger.warning("entsoe_grid.ingest_grid: ENTSOE_API_TOKEN not set — skipping")
        return {"skipped": "no token"}

    wanted = set(days)
    months = sorted(
        {datetime.strptime(d, "%Y-%m-%d").date().replace(day=1) for d in days}
    )

    # Accumulate per-day values across month fetches
    load_by_day: dict[str, float] = {}
    gen_by_day: dict[str, dict[str, float]] = {}  # date → {psr: mean_mw}
    # Hourly shape → power_hourly (the new canonical store; roadmap Block 1)
    load_hourly_by_day: dict[str, dict[int, float]] = {}
    gen_hourly_by_day: dict[str, dict[str, dict[int, float]]] = {}  # date → {psr: {hour: mw}}

    for month_start in months:
        # ── A65 Total Load ──────────────────────────────────────────────────
        try:
            load_xml = await _fetch_zone_month(
                eic,
                month_start,
                "A65",
                {
                    "processType": "A16",
                    "outBiddingZone_Domain": eic,
                },
                overwrite=overwrite,
            )
        except httpx.HTTPError as exc:
            logger.warning("entsoe_grid: A65 %s fetch failed: %s", month_start, exc)
            load_xml = ""

        if load_xml:
            for day, mean_mw in parse_load(load_xml).items():
                if day in wanted:
                    load_by_day[day] = mean_mw
            for day, hours in parse_load_hourly(load_xml).items():
                if day in wanted:
                    load_hourly_by_day[day] = hours

        # ── A75 Generation Mix ──────────────────────────────────────────────
        try:
            gen_xml = await _fetch_zone_month(
                eic,
                month_start,
                "A75",
                {
                    "processType": "A16",
                    "in_Domain": eic,
                },
                overwrite=overwrite,
            )
        except httpx.HTTPError as exc:
            logger.warning("entsoe_grid: A75 %s fetch failed: %s", month_start, exc)
            gen_xml = ""

        if gen_xml:
            for day, psr_map in parse_generation_by_type(gen_xml).items():
                if day in wanted:
                    gen_by_day[day] = psr_map
            for day, by_psr in parse_generation_hourly(gen_xml).items():
                if day in wanted:
                    gen_hourly_by_day[day] = by_psr

    # ── Upsert the daily tables — DERIVED FROM THE HOURLY SHAPE ────────────
    #
    # Not from `parse_load` / `parse_generation_by_type` (the daily-mean parses) any more. Those
    # divided by the number of PUBLISHED points, which stored the current day's nine morning hours
    # as a day, made a zone that omits solar at night (PT: 18 points) a third sunnier than it is,
    # and counted revised, overlapping points twice. The hour maps below are the same ones that go
    # into the canonical store, deduped per hour — one parse, one answer. See power/daily.py.
    #
    # Only days that are OVER become rows: a day that is not finished is not a day, and everything
    # downstream reads "the latest row" as one.
    all_days = wanted & (load_hourly_by_day.keys() | gen_hourly_by_day.keys())
    written = 0
    for day in days_to_derive(all_days):
        gen_of_day = {
            base_psr(psr): hours
            for psr, hours in gen_hourly_by_day.get(day, {}).items()
            if not is_consumption_key(psr)   # pumping is not generation
        }
        row = daily_from_hours(load_hourly_by_day.get(day, {}), gen_of_day)
        _upsert_grid(db, day, zone, row)
        _upsert_generation_mix(db, day, zone, row["mix"])
        written += 1

    db.commit()

    # ── Hourly actuals → power_hourly (load, per-fuel generation, residual) ──
    # Additive to the daily-mean tables above; the canonical hourly store powers
    # range queries + export. Reuses build_hourly_forecast for the residual shape.
    if load_hourly_by_day:
        upsert_day_hours(db, "load.actual", zone, load_hourly_by_day, unit="MW")
    # Generation and CONSUMPTION (pumped-storage pumping) are distinct series —
    # the document publishes both under the same psrType and they must never be
    # merged (see CONSUMPTION_SUFFIX).
    gen_series: dict[str, dict[str, dict[int, float]]] = {}  # psr_key → {day → {hour → mw}}
    for day, by_psr in gen_hourly_by_day.items():
        for psr, hours in by_psr.items():
            gen_series.setdefault(psr, {})[day] = hours
    for psr_key, day_hours in gen_series.items():
        prefix = "consumption" if is_consumption_key(psr_key) else "gen"
        upsert_day_hours(db, f"{prefix}.{base_psr(psr_key)}", zone, day_hours, unit="MW")
    resid_by_day: dict[str, dict[int, float]] = {}
    for day, load_hours in load_hourly_by_day.items():
        if day not in gen_hourly_by_day:
            continue
        rr = {
            p["hour"]: p["residual_mw"]
            for p in build_hourly_forecast(load_hours, gen_hourly_by_day[day])
            if p["residual_mw"] is not None
        }
        if rr:
            resid_by_day[day] = rr
    if resid_by_day:
        upsert_day_hours(db, "residual.actual", zone, resid_by_day, unit="MW")

    logger.info(
        "entsoe_grid.ingest_grid: %d/%d days written (zone=%s)",
        written,
        len(days),
        zone,
    )
    return {"days": len(days), "written": written}


def _upsert_generation_mix(
    db: Session,
    day: str,
    zone: str,
    mix: dict[str, float],
) -> None:
    """Upsert one day's generation mix (psrType → daily-mean MW, already day-averaged).

    `mix` comes from power/daily.py, so every fuel is averaged over the 24 hours of the day —
    the hours a zone does not publish are the zeros it does not send, not points to divide by.

    CONSUMPTION series (pumped-storage pumping) never reach here: the mix is what the zone
    GENERATED, and counting pumping as generation inflated both the mix and the generation total
    that backend/power/coverage.py divides by load.

    Note: does NOT call db.commit() — the caller commits once per zone-month.
    """
    for code, mean_mw in mix.items():
        label = PSR_LABELS.get(code, code)
        existing = (
            db.query(PowerGenMix)
            .filter(
                PowerGenMix.date == day,
                PowerGenMix.zone == zone,
                PowerGenMix.psr_type == label,
            )
            .first()
        )
        if existing:
            existing.gen_mw = mean_mw
        else:
            db.add(PowerGenMix(date=day, zone=zone, psr_type=label, gen_mw=mean_mw))


def _upsert_grid(db: Session, day: str, zone: str, row: dict) -> None:
    """Upsert one day's PowerGrid row from a power/daily.py `daily_from_hours` result.

    Writes the hour counts as well: they are what lets a reader — and the Dunkelflaute predicate —
    see how much of the day a mean stands on. Unlike the old upsert, a None is WRITTEN rather than
    skipped: this row is the whole truth about the day as the store knows it, and silently keeping
    a previous value would resurrect the number the re-derivation is here to correct.
    """
    existing = (
        db.query(PowerGrid)
        .filter(PowerGrid.date == day, PowerGrid.zone == zone)
        .first()
    )
    target = existing or PowerGrid(date=day, zone=zone)
    target.load_mw = row["load_mw"]
    target.wind_mw = row["wind_mw"]
    target.solar_mw = row["solar_mw"]
    target.residual_mw = row["residual_mw"]
    target.load_hours = row["load_hours"]
    target.gen_hours = row["gen_hours"]
    if existing is None:
        db.add(target)
