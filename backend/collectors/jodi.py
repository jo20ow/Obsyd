"""
JODI Oil World Database Collector.

Downloads annual CSV files from JODI (Joint Organisations Data Initiative)
and extracts crude oil production, refinery intake (consumption proxy),
and closing stock levels for the top-10 oil-producing countries.

Data source: https://www.jodidata.org/oil/database/data-downloads.aspx
Monthly updates, no API key required.
"""

import csv
import io
import logging
from datetime import datetime

import httpx

from backend.database import SessionLocal
from backend.models.jodi import JODIProduction

logger = logging.getLogger(__name__)

BASE_URL = "https://www.jodidata.org/_resources/files/downloads/oil-data/annual-csv/primary"

# Top-10 oil producers (ISO 3166-1 alpha-2)
TARGET_COUNTRIES = {
    "SA": "Saudi Arabia",
    "RU": "Russia",
    "US": "United States",
    "IQ": "Iraq",
    "CA": "Canada",
    "CN": "China",
    "AE": "UAE",
    "IR": "Iran",
    "BR": "Brazil",
    "KW": "Kuwait",
}

# JODI flow codes we care about
FLOWS = {
    "INDPROD": "production",     # Industrial production
    "REFINOBS": "consumption",   # Refinery intake (demand proxy)
    "CLOSTLV": "stocks",         # Closing stock level
}


def _parse_csv(text: str) -> dict:
    """
    Parse JODI CSV and extract relevant rows for target countries.

    Returns: {(country, date): {"production": float|None, "consumption": float|None, "stocks": float|None}}
    """
    result = {}
    reader = csv.DictReader(io.StringIO(text))

    for row in reader:
        country = row.get("REF_AREA", "")
        if country not in TARGET_COUNTRIES:
            continue

        product = row.get("ENERGY_PRODUCT", "")
        if product != "CRUDEOIL":
            continue

        unit = row.get("UNIT_MEASURE", "")
        if unit != "KBBL":
            continue

        flow = row.get("FLOW_BREAKDOWN", "")
        if flow not in FLOWS:
            continue

        value_str = row.get("OBS_VALUE", "-")
        if value_str in ("-", "x", ""):
            continue

        try:
            value = float(value_str)
        except (ValueError, TypeError):
            continue

        date = row.get("TIME_PERIOD", "")
        key = (country, date)

        if key not in result:
            result[key] = {"production": None, "consumption": None, "stocks": None}

        result[key][FLOWS[flow]] = value

    return result


async def collect_jodi():
    """Fetch JODI CSV files for current + previous year, store in database."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        years = [now.year, now.year - 1]

        async with httpx.AsyncClient(timeout=60.0) as client:
            for year in years:
                url = f"{BASE_URL}/{year}.csv"
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                except httpx.HTTPError as e:
                    logger.warning(f"JODI: failed to fetch {year}.csv: {e}")
                    continue

                data = _parse_csv(resp.text)
                count = 0

                for (country, date), values in data.items():
                    if all(v is None for v in values.values()):
                        continue

                    existing = (
                        db.query(JODIProduction)
                        .filter(JODIProduction.country == country, JODIProduction.date == date)
                        .first()
                    )

                    if existing:
                        existing.production = values["production"]
                        existing.consumption = values["consumption"]
                        existing.stocks = values["stocks"]
                        existing.fetched_at = datetime.utcnow()
                    else:
                        db.add(JODIProduction(
                            country=country,
                            country_name=TARGET_COUNTRIES.get(country, country),
                            date=date,
                            production=values["production"],
                            consumption=values["consumption"],
                            stocks=values["stocks"],
                        ))
                    count += 1

                db.commit()
                logger.info(f"JODI: {year} — {count} rows stored/updated")

    except Exception as e:
        logger.error(f"JODI collection failed: {e}")
    finally:
        db.close()
