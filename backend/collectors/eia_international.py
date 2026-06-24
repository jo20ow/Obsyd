"""EIA International Energy Statistics — per-country energy (US Gov, public domain).

ATLAS data node 1: petroleum production + consumption by country (ISO-3), annual.
API: https://api.eia.gov/v2/international/data/ (reuses the existing EIA_API_KEY).

Two API gotchas handled here:
  - The dataset returns BOTH countries and regional aggregates; we keep only
    countryRegionTypeId == 'c' (drops World/OPEC/OECD/continents).
  - The same product is returned in MIXED units (e.g. TBPD and QBTU) — we pin one
    unit per product via the `unit` facet so values are comparable across countries.
"""

import logging

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models.atlas import CountryEnergy

logger = logging.getLogger(__name__)

BASE = "https://api.eia.gov/v2/international/data/"

# (product label, activity label, EIA productId, EIA activityId, pinned unit).
# NOTE: EIA's productId depends on the activity — production lives under 53 ("Total
# petroleum and other liquids"), but consumption is only under 5 ("Petroleum and other
# liquids"); product 53 has no consumption rows. Both are petroleum liquids in TBPD, so
# they're comparable on the map. Gas/coal/electricity are sibling datasets (later nodes).
SERIES = [
    ("petroleum", "production", "53", "1", "TBPD"),
    ("petroleum", "consumption", "5", "2", "TBPD"),
]


def _api_key() -> str:
    k = settings.eia_api_key
    if hasattr(k, "get_secret_value"):
        k = k.get_secret_value()
    return k or ""


def _normalize_row(row: dict, product: str, activity: str, default_unit: str) -> dict | None:
    """Validate + flatten one EIA data row. Returns None for aggregates / invalid rows."""
    if row.get("countryRegionTypeId") != "c":
        return None  # regional aggregate (World/OPEC/continent) — not a country
    iso3 = row.get("countryRegionId")
    raw = row.get("value")
    if not iso3 or raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return {
        "iso3": iso3,
        "country_name": row.get("countryRegionName") or "",
        "product": product,
        "activity": activity,
        "period": str(row.get("period")),
        "value": value,
        "unit": row.get("unit") or default_unit,
    }


async def _fetch(client: httpx.AsyncClient, product_id: str, activity_id: str, unit: str, start_year: int) -> list[dict]:
    params = {
        "api_key": _api_key(),
        "frequency": "annual",
        "data[0]": "value",
        "facets[productId][]": product_id,
        "facets[activityId][]": activity_id,
        "facets[unit][]": unit,
        "start": str(start_year),
        "offset": "0",
        "length": "5000",
    }
    resp = await client.get(BASE, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json().get("response", {}).get("data", [])


def _upsert(db: Session, rec: dict) -> None:
    existing = (
        db.query(CountryEnergy)
        .filter_by(iso3=rec["iso3"], product=rec["product"], activity=rec["activity"], period=rec["period"])
        .first()
    )
    if existing:
        existing.value = rec["value"]
        existing.unit = rec["unit"]
        existing.country_name = rec["country_name"] or existing.country_name
    else:
        db.add(CountryEnergy(**rec))


async def ingest_eia_international(db: Session, start_year: int = 2010) -> dict:
    if not _api_key():
        logger.warning("EIA International: no API key set; skipping")
        return {"status": "skipped", "reason": "no_key"}

    written = 0
    async with httpx.AsyncClient() as client:
        for product, activity, product_id, activity_id, unit in SERIES:
            try:
                rows = await _fetch(client, product_id, activity_id, unit, start_year)
            except Exception as e:
                logger.warning("EIA International fetch failed (%s/%s): %s", product, activity, e)
                continue
            kept = 0
            for row in rows:
                rec = _normalize_row(row, product, activity, unit)
                if rec is None:
                    continue
                _upsert(db, rec)
                kept += 1
            db.commit()
            written += kept
            logger.info("EIA International: %s/%s → %d country-rows (of %d)", product, activity, kept, len(rows))
    return {"status": "ok", "written": written}
