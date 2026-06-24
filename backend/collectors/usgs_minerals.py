"""USGS Mineral Commodity Summaries — per-country mine production (public domain).

ATLAS "Rohstoffe" node: world mine production by country for strategic minerals.
Source: USGS MCS 2025 Data Release (ScienceBase DOI 10.5066/P13XCP3R) — one CSV
(MCS2025_World_Data.csv) with COMMODITY / COUNTRY / TYPE / UNIT_MEAS / PROD_2023 /
PROD_EST_2024 columns. Each commodity has several TYPE rows (capacity/reserves/forms);
we pick the "Mine production …" row per commodity. ISO-3 keyed via usgs_country_map.
"""

import csv
import io
import logging
import zipfile

import httpx
from sqlalchemy.orm import Session

from backend.collectors.usgs_country_map import USGS_AGGREGATES, USGS_NAME_TO_ISO3
from backend.models.atlas import CountryResource

logger = logging.getLogger(__name__)

# Pinned to the MCS 2025 release; bump the item id + names on a new annual release.
SB_ITEM = "https://www.sciencebase.gov/catalog/item/677eaf95d34e760b392c4970?format=json"
ZIP_NAME = "World_Data_Release_MCS_2025.zip"
CSV_NAME = "MCS2025_World_Data.csv"

# (friendly key, USGS commodity name, TYPE substring identifying the mine-production row).
COMMODITIES = [
    ("lithium", "Lithium", "lithium content"),
    ("gold", "Gold", "gold content"),
    ("iron_ore", "Iron Ore", "usable ore"),
    ("rare_earths", "Rare earths", "rare-earth-oxide"),
    ("cobalt", "Cobalt", "cobalt content"),
    ("copper", "Copper", "recoverable copper"),
    ("nickel", "Nickel", "nickel content"),
    ("bauxite", "Bauxite", "bauxite"),
    ("zinc", "Zinc", "zinc content"),
    ("potash", "Potash", "potassium oxide"),
]

# CSV column for the latest-year production estimate (note the space in the header).
_COL_2024 = "PROD_EST_ 2024"
_COL_2023 = "PROD_2023"


def _parse_value(raw) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if not s or s.upper() in ("W", "NA", "—", "--", "XX", "(1)"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _records_from_rows(rows: list[dict], unmapped: set | None = None) -> list[dict]:
    """Pure transform: USGS CSV rows → normalized CountryResource records."""
    out: list[dict] = []
    for key, name, sub in COMMODITIES:
        for r in rows:
            if (r.get("COMMODITY") or "").strip() != name:
                continue
            t = (r.get("TYPE") or "").strip().lower()
            if "mine production" not in t or sub not in t:
                continue
            country = (r.get("COUNTRY") or "").strip()
            if country in USGS_AGGREGATES:
                continue
            iso3 = USGS_NAME_TO_ISO3.get(country)
            if not iso3:
                if unmapped is not None:
                    unmapped.add(country)
                continue
            unit = (r.get("UNIT_MEAS") or "").strip()
            for col, period in ((_COL_2023, "2023"), (_COL_2024, "2024")):
                val = _parse_value(r.get(col))
                if val is None:
                    continue
                out.append({
                    "iso3": iso3, "country_name": country, "commodity": key,
                    "period": period, "value": val, "unit": unit,
                })
    return out


async def _download_csv(client: httpx.AsyncClient) -> str:
    item = (await client.get(SB_ITEM, timeout=60)).raise_for_status().json()
    file_obj = next(f for f in item["files"] if f["name"] == ZIP_NAME)
    resp = await client.get(file_obj["url"], timeout=90)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        return zf.read(CSV_NAME).decode("utf-8-sig")


def _upsert(db: Session, rec: dict) -> None:
    existing = (
        db.query(CountryResource)
        .filter_by(iso3=rec["iso3"], commodity=rec["commodity"], period=rec["period"])
        .first()
    )
    if existing:
        existing.value = rec["value"]
        existing.unit = rec["unit"]
        existing.country_name = rec["country_name"] or existing.country_name
    else:
        db.add(CountryResource(**rec))


async def ingest_usgs_minerals(db: Session) -> dict:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            csv_text = await _download_csv(client)
        except Exception as e:
            logger.warning("USGS minerals: download/parse failed: %s", e)
            return {"status": "error", "reason": str(e)[:80]}

    rows = list(csv.DictReader(io.StringIO(csv_text)))
    unmapped: set = set()
    recs = _records_from_rows(rows, unmapped)
    for rec in recs:
        _upsert(db, rec)
    db.commit()
    if unmapped:
        logger.info("USGS minerals: %d unmapped country names skipped: %s", len(unmapped), sorted(unmapped))
    logger.info("USGS minerals: wrote %d records across %d commodities", len(recs), len(COMMODITIES))
    return {"status": "ok", "written": len(recs), "unmapped": len(unmapped)}
