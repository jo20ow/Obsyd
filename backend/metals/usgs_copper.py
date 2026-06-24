"""USGS Mineral Industry Surveys — monthly copper supply collector.

Downloads mis-YYYYMM-coppe.xlsx files (public domain) from the USGS S3 bucket,
parses the three target series (T2, T4, T10) and upserts into CopperSupply.

Not every calendar month has a published MIS file; the collector walks the last
`months_back` months and silently skips 403/404 responses.

Caching: raw XLSX bytes are cached on disk under data/raw/usgs_copper/<YYYY-MM>/
using a .bin extension (adapts the JSON cache convention from gas.raw_cache to
binary payloads). Subsequent runs skip re-fetching existing files unless
overwrite=True.
"""

from __future__ import annotations

import logging
import os
import re
from calendar import month_name
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

# ── URL template ──────────────────────────────────────────────────────────────

USGS_URL = (
    "https://d9-wret.s3.us-west-2.amazonaws.com/assets/palladium/production/"
    "s3fs-public/media/files/mis-{ym}-coppe.xlsx"
)

# ── MONTH NAME → number map ───────────────────────────────────────────────────

_MONTH_MAP: dict[str, int] = {m.lower(): i for i, m in enumerate(month_name) if m}

# ── Raw-bytes cache (analogous to gas.raw_cache but for binary) ───────────────

DATA_ROOT = Path("data/raw")


def _bin_cache_path(ym: str) -> Path:
    """data/raw/usgs_copper/<YYYY-MM>/mis-<ym>-coppe.bin"""
    return DATA_ROOT / "usgs_copper" / ym[:7] / f"mis-{ym}-coppe.bin"


def _read_cached_bytes(ym: str) -> bytes | None:
    path = _bin_cache_path(ym)
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _write_cached_bytes(ym: str, data: bytes) -> None:
    path = _bin_cache_path(ym)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".bin.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# ── Footnote cleaner ──────────────────────────────────────────────────────────

_FOOTNOTE_RE = re.compile(r"[a-zA-Z,\s]+$")


def _clean_value(raw) -> Optional[float]:
    """Convert a raw cell value to float, stripping commas and footnote markers.

    Examples:
      92900        → 92900.0
      "36,000 e"  → 36000.0
      "2,360 r, e"→ 2360.0
      "103,000 r" → 103000.0
      NaN / None  → None
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        import math

        if math.isnan(raw):
            return None
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    # Remove commas (thousands separators)
    s = s.replace(",", "")
    # Strip trailing footnote markers: letters, spaces, commas at the right end
    s = _FOOTNOTE_RE.sub("", s).strip()
    try:
        return float(s)
    except ValueError:
        return None


# ── Column finder ─────────────────────────────────────────────────────────────

def _find_col(df: pd.DataFrame, label: str) -> int | None:
    """Search the first 5 rows of df for a cell whose stripped string matches
    `label` (case-insensitive). Returns the column index or None."""
    target = label.lower().strip()
    for row_idx in range(min(5, len(df))):
        for col_idx in range(len(df.columns)):
            cell = df.iat[row_idx, col_idx]
            if cell is None:
                continue
            cell_s = str(cell).strip().lower()
            if cell_s == target:
                return col_idx
    return None


# ── Monthly extractor ─────────────────────────────────────────────────────────

def _extract_monthly(df: pd.DataFrame, value_col_label: str) -> dict[str, float | None]:
    """Extract (YYYY-MM-01 → value) pairs from a USGS MIS table sheet.

    The table layout:
      - First few rows: title + units note + multi-row header.
      - Then: a year row (entire cell = integer year string, e.g. "2024"),
        followed by 12 month rows ("January" .. "December"), then a
        "January–December" annual subtotal row (skipped), then the next year.

    Args:
        df:               DataFrame read with header=None from the sheet.
        value_col_label:  The header text to match (e.g. "Total", "Total refined").

    Returns:
        dict mapping "YYYY-MM-01" → float | None  (None if cell unparseable).
    """
    col = _find_col(df, value_col_label)
    if col is None:
        logger.warning("_extract_monthly: column %r not found", value_col_label)
        return {}

    result: dict[str, float | None] = {}
    current_year: int | None = None

    for _, row in df.iterrows():
        cell0 = row.iloc[0]
        if cell0 is None:
            continue
        s = str(cell0).strip()

        # Year row: cell is a bare 4-digit year (or float like 2024.0)
        try:
            year_val = int(float(s))
            if 2000 <= year_val <= 2100 and len(s.split(".")[0]) == 4:
                current_year = year_val
                continue
        except (ValueError, TypeError):
            pass

        if current_year is None:
            continue

        # Month row: cell matches a known month name (strip whitespace)
        month_num = _MONTH_MAP.get(s.lower())
        if month_num is None:
            continue  # annual subtotal or other non-month row

        date_str = f"{current_year}-{month_num:02d}-01"
        value = _clean_value(row.iloc[col])
        result[date_str] = value

    return result


# ── XLSX parser ───────────────────────────────────────────────────────────────

def parse_mis_xlsx(xlsx_bytes: bytes) -> dict[str, dict]:
    """Parse a USGS MIS copper XLSX and return a merged monthly dict.

    Returns:
        { "YYYY-MM-01": {
              "us_mine_production": float | None,
              "us_refined_production": float | None,
              "us_refined_stocks": float | None,
          } }

    Missing sheets or columns are skipped defensively — only the metrics
    that could be parsed will be non-None.
    """
    buf = BytesIO(xlsx_bytes)
    try:
        sheets = pd.read_excel(
            buf,
            sheet_name=["T2", "T4", "T10"],
            header=None,
            engine="openpyxl",
        )
    except Exception as exc:
        logger.error("parse_mis_xlsx: failed to open workbook: %s", exc)
        return {}

    # ── T2: Mine production — first "Total" col (index 3) ──────────────────
    mine: dict[str, float | None] = {}
    if "T2" in sheets:
        try:
            mine = _extract_monthly(sheets["T2"], "Total")
        except Exception as exc:
            logger.warning("parse_mis_xlsx: T2 extraction failed: %s", exc)

    # ── T4: Refined production — "Total refined" col ───────────────────────
    refined: dict[str, float | None] = {}
    if "T4" in sheets:
        try:
            refined = _extract_monthly(sheets["T4"], "Total refined")
        except Exception as exc:
            logger.warning("parse_mis_xlsx: T4 extraction failed: %s", exc)

    # ── T10: Stocks — "Total refined" col ─────────────────────────────────
    stocks: dict[str, float | None] = {}
    if "T10" in sheets:
        try:
            stocks = _extract_monthly(sheets["T10"], "Total refined")
        except Exception as exc:
            logger.warning("parse_mis_xlsx: T10 extraction failed: %s", exc)

    # ── Merge: union of all dates ──────────────────────────────────────────
    all_dates = set(mine) | set(refined) | set(stocks)
    result: dict[str, dict] = {}
    for d in all_dates:
        result[d] = {
            "us_mine_production": mine.get(d),
            "us_refined_production": refined.get(d),
            "us_refined_stocks": stocks.get(d),
        }
    return result


# ── HTTP fetch with binary cache ──────────────────────────────────────────────

async def _fetch_mis_month(ym: str, *, overwrite: bool = False) -> bytes | None:
    """Download (or load from cache) the MIS XLSX for month `ym` (YYYYMM).

    Returns the raw bytes on success, None if the file is not available
    (HTTP 403/404 or network error).
    """
    if not overwrite:
        cached = _read_cached_bytes(ym)
        if cached is not None:
            logger.debug("usgs_copper: cache hit for %s", ym)
            return cached

    url = USGS_URL.format(ym=ym)
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code == 200:
            data = resp.content
            _write_cached_bytes(ym, data)
            logger.info("usgs_copper: fetched %s (%d bytes)", ym, len(data))
            return data
        else:
            logger.debug("usgs_copper: %s → HTTP %d (not available)", ym, resp.status_code)
            return None
    except Exception as exc:
        logger.warning("usgs_copper: fetch %s failed: %s", ym, exc)
        return None


# ── Ingest entry point ────────────────────────────────────────────────────────

async def ingest_copper_supply(db, *, months_back: int = 18, overwrite: bool = False) -> dict:
    """Walk the last `months_back` calendar months, fetch available MIS files,
    parse them and upsert into CopperSupply. Newer files win on conflict.

    Returns:
        {"files": n, "rows_written": n}
    """
    from backend.models.metals import CopperSupply

    today = date.today()
    # Build list of YYYYMM strings for the last months_back months
    ym_list: list[str] = []
    for i in range(months_back):
        # Step back i months from today
        year = today.year
        month = today.month - i
        while month <= 0:
            month += 12
            year -= 1
        ym_list.append(f"{year}{month:02d}")

    # Union of all parsed rows across files: date → metrics dict.
    # Iterate oldest→newest so newer files naturally override.
    all_rows: dict[str, dict] = {}
    files_fetched = 0

    for ym in reversed(ym_list):
        xlsx_bytes = await _fetch_mis_month(ym, overwrite=overwrite)
        if xlsx_bytes is None:
            continue
        files_fetched += 1
        parsed = parse_mis_xlsx(xlsx_bytes)
        all_rows.update(parsed)  # newer file wins

    if not all_rows:
        logger.info("usgs_copper: no data parsed from %d months back", months_back)
        return {"files": files_fetched, "rows_written": 0}

    rows_written = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for date_str, metrics in all_rows.items():
        existing = db.query(CopperSupply).filter(CopperSupply.date == date_str).first()
        if existing:
            # Update all three metrics
            existing.us_mine_production = metrics["us_mine_production"]
            existing.us_refined_production = metrics["us_refined_production"]
            existing.us_refined_stocks = metrics["us_refined_stocks"]
        else:
            db.add(
                CopperSupply(
                    date=date_str,
                    us_mine_production=metrics["us_mine_production"],
                    us_refined_production=metrics["us_refined_production"],
                    us_refined_stocks=metrics["us_refined_stocks"],
                    created_at=now,
                )
            )
            rows_written += 1

    db.commit()
    total = db.query(CopperSupply).count()
    logger.info(
        "usgs_copper: ingested %d months, %d files, %d new rows (total in DB: %d)",
        months_back,
        files_fetched,
        rows_written,
        total,
    )
    return {"files": files_fetched, "rows_written": rows_written}
