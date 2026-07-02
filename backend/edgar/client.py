"""SEC EDGAR client — free, no key, US-complete company filings + fundamentals.

SEC fair-access requires a descriptive User-Agent with contact info; without it
EDGAR returns 403. Usage here is low (per-company, cached upstream), well within
the ~10 req/s guidance. All data is public domain.

Pure parsers (`parse_recent_filings`, `extract_key_financials`) are separated from
the network fetchers so they can be unit-tested against sample JSON.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.orm import Session

from backend.models.company import Company

logger = logging.getLogger(__name__)

# SEC requires a UA identifying the app + a contact (URL/email).
_UA = {"User-Agent": "Obsyd/1.0 (+https://obsyd.dev)", "Accept-Encoding": "gzip, deflate"}

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# Candidate US-GAAP tags per headline metric (companies tag revenue differently).
_METRIC_TAGS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss"],
    "total_assets": ["Assets"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
}


def pad_cik(cik) -> str:
    """EDGAR canonical zero-padded 10-char CIK string."""
    return str(cik).lstrip("0").zfill(10) if str(cik).strip() else str(cik)


async def _get_json(url: str) -> dict | list:
    async with httpx.AsyncClient(headers=_UA, timeout=20, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def fetch_company_tickers() -> list[dict]:
    """[{cik, ticker, title}] from company_tickers.json (cik zero-padded)."""
    raw = await _get_json(_TICKERS_URL)
    rows = raw.values() if isinstance(raw, dict) else raw
    out = []
    for r in rows:
        tk = (r.get("ticker") or "").strip().upper()
        if not tk:
            continue
        out.append({"cik": pad_cik(r.get("cik_str")), "ticker": tk, "title": r.get("title") or tk})
    return out


async def fetch_submissions(cik: str) -> dict:
    return await _get_json(_SUBMISSIONS_URL.format(cik=pad_cik(cik)))


async def fetch_companyfacts(cik: str) -> dict:
    return await _get_json(_FACTS_URL.format(cik=pad_cik(cik)))


def parse_recent_filings(submissions: dict, cik: str, limit: int = 20) -> list[dict]:
    """Parallel-array `filings.recent` → [{form, date, accession, primary_doc, url}]."""
    recent = (submissions or {}).get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    cik_int = int(pad_cik(cik))
    out = []
    for i in range(min(len(forms), len(dates), len(accs))):
        acc = accs[i]
        doc = docs[i] if i < len(docs) else ""
        acc_nodash = acc.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc}" if doc else \
              f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/"
        out.append({"form": forms[i], "date": dates[i], "accession": acc, "primary_doc": doc, "url": url})
        if len(out) >= limit:
            break
    return out


def _latest_annual(series: list[dict]) -> dict | None:
    """Pick the most recent annual (fp==FY / 10-K) entry, else the most recent entry."""
    if not series:
        return None
    annual = [s for s in series if s.get("fp") == "FY" or s.get("form") == "10-K"]
    pool = annual or series
    return max(pool, key=lambda s: (s.get("end") or "", s.get("filed") or ""))


def extract_key_financials(facts: dict) -> dict:
    """companyfacts → {revenue, net_income, total_assets, equity} latest-annual snapshots.

    Each value is {value, unit, fiscal_year, period_end, form} or None if unavailable.
    """
    gaap = (facts or {}).get("facts", {}).get("us-gaap", {})
    out: dict[str, dict | None] = {}
    for metric, tags in _METRIC_TAGS.items():
        best = None
        for tag in tags:
            units = gaap.get(tag, {}).get("units", {})
            unit = "USD" if "USD" in units else (next(iter(units), None))
            if not unit:
                continue
            entry = _latest_annual(units[unit])
            if entry is None:
                continue
            cand = {
                "value": entry.get("val"),
                "unit": unit,
                "fiscal_year": entry.get("fy"),
                "period_end": entry.get("end"),
                "form": entry.get("form"),
            }
            # Companies migrate tags (e.g. "Revenues" → "RevenueFromContract…"), leaving a
            # deprecated tag with stale data. Take the freshest entry ACROSS candidate tags,
            # not the first tag that has any data.
            if best is None or (cand["period_end"] or "") > (best["period_end"] or ""):
                best = cand
        out[metric] = best
    return out


async def load_company_tickers(db: Session) -> dict:
    """Upsert the full ticker→CIK→title universe into the Company table. Fail-soft."""
    try:
        rows = await fetch_company_tickers()
    except Exception as exc:  # noqa: BLE001 — collector must not crash startup/scheduler
        logger.warning("edgar: company_tickers fetch failed: %s", exc)
        return {"loaded": 0, "error": str(exc)}

    existing = {c.ticker: c for c in db.query(Company).all()}
    n = 0
    for r in rows:
        cur = existing.get(r["ticker"])
        if cur:
            cur.cik, cur.title = r["cik"], r["title"]
        else:
            db.add(Company(cik=r["cik"], ticker=r["ticker"], title=r["title"]))
        n += 1
    db.commit()
    logger.info("edgar: loaded %d companies", n)
    return {"loaded": n}
