"""World Bank Open Data — per-country macro indicators (CC BY 4.0, no API key).

ATLAS macro node: GDP, GDP/capita, growth, industry + manufacturing (% GDP), trade
(% GDP), population, inflation. ISO-3 keyed (matches CountryEnergy → clean join).

API: https://api.worldbank.org/v2/country/all/indicator/{code}?format=json&date=Y1:Y2
The dataset mixes real countries and regional/income AGGREGATES (World, EU, Arab World,
"High income", …); we exclude them via the country list (region.value == 'Aggregates').
"""

import logging

import httpx
from sqlalchemy.orm import Session

from backend.models.atlas import CountryMacro

logger = logging.getLogger(__name__)

BASE = "https://api.worldbank.org/v2"

# (friendly metric key, World Bank indicator code, human label).
METRICS = [
    ("gdp_usd", "NY.GDP.MKTP.CD", "GDP (current US$)"),
    ("gdp_per_capita", "NY.GDP.PCAP.CD", "GDP per capita (current US$)"),
    ("gdp_growth", "NY.GDP.MKTP.KD.ZG", "GDP growth (annual %)"),
    ("industry_pct_gdp", "NV.IND.TOTL.ZS", "Industry (incl. construction) value added (% of GDP)"),
    ("manufacturing_pct_gdp", "NV.IND.MANF.ZS", "Manufacturing value added (% of GDP)"),
    ("trade_pct_gdp", "NE.TRD.GNFS.ZS", "Trade (% of GDP)"),
    ("population", "SP.POP.TOTL", "Population, total"),
    ("inflation", "FP.CPI.TOTL.ZG", "Inflation, consumer prices (annual %)"),
    ("co2_per_capita", "EN.GHG.CO2.PC.CE.AR5", "CO2 emissions excl. LULUCF per capita (t)"),
    ("renewable_energy_pct", "EG.FEC.RNEW.ZS", "Renewable energy (% of final energy consumption)"),
    ("energy_imports_pct", "EG.IMP.CONS.ZS", "Energy imports, net (% of energy use)"),
    ("unemployment_pct", "SL.UEM.TOTL.ZS", "Unemployment (% of total labor force, ILO)"),
    ("current_account_pct_gdp", "BN.CAB.XOKA.GD.ZS", "Current account balance (% of GDP)"),
    ("electricity_access_pct", "EG.ELC.ACCS.ZS", "Access to electricity (% of population)"),
]


def _normalize_row(row: dict, metric: str, code: str, countries: dict) -> dict | None:
    """Validate + flatten one WB data row. None for aggregates / null / non-numeric values."""
    iso3 = row.get("countryiso3code")
    if iso3 not in countries:  # aggregate (World/EU/income group) or unknown → drop
        return None
    raw = row.get("value")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return {
        "iso3": iso3,
        "country_name": countries[iso3],
        "metric": metric,
        "indicator_code": code,
        "period": str(row.get("date")),
        "value": value,
    }


async def _fetch_countries(client: httpx.AsyncClient) -> dict:
    """ISO-3 → name for REAL countries only (region != 'Aggregates')."""
    resp = await client.get(f"{BASE}/country", params={"format": "json", "per_page": "400"}, timeout=60)
    resp.raise_for_status()
    rows = resp.json()[1]
    return {r["id"]: r["name"] for r in rows if (r.get("region") or {}).get("value") != "Aggregates"}


async def _fetch_indicator(client: httpx.AsyncClient, code: str, start: int, end: int) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        resp = await client.get(
            f"{BASE}/country/all/indicator/{code}",
            params={"format": "json", "per_page": "20000", "date": f"{start}:{end}", "page": str(page)},
            timeout=90,
        )
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, list) or len(body) < 2 or not body[1]:
            break
        meta, rows = body[0], body[1]
        out.extend(rows)
        if page >= (meta.get("pages") or 1):
            break
        page += 1
    return out


def _upsert(db: Session, rec: dict) -> None:
    existing = (
        db.query(CountryMacro)
        .filter_by(iso3=rec["iso3"], metric=rec["metric"], period=rec["period"])
        .first()
    )
    if existing:
        existing.value = rec["value"]
        existing.indicator_code = rec["indicator_code"]
        existing.country_name = rec["country_name"] or existing.country_name
    else:
        db.add(CountryMacro(**rec))


async def ingest_worldbank(db: Session, start_year: int = 2010, end_year: int = 2024) -> dict:
    written = 0
    async with httpx.AsyncClient() as client:
        try:
            countries = await _fetch_countries(client)
        except Exception as e:
            logger.warning("World Bank: country list fetch failed: %s", e)
            return {"status": "error", "reason": "country_list"}

        for metric, code, _label in METRICS:
            try:
                rows = await _fetch_indicator(client, code, start_year, end_year)
            except Exception as e:
                logger.warning("World Bank fetch failed (%s): %s", metric, e)
                continue
            kept = 0
            for row in rows:
                rec = _normalize_row(row, metric, code, countries)
                if rec is None:
                    continue
                _upsert(db, rec)
                kept += 1
            db.commit()
            written += kept
            logger.info("World Bank: %s → %d country-rows (of %d)", metric, kept, len(rows))
    return {"status": "ok", "written": written}
