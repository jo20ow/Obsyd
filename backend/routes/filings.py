"""Company filings + fundamentals (SEC EDGAR) — the equities research dimension.

GET /api/filings/search?q=       — ticker/name lookup over the security master, FREE
GET /api/filings/company?ticker= — profile + recent filings (linked to sec.gov), FREE
GET /api/filings/financials?ticker= — headline financials (revenue/NI/assets/equity), FREE

Live EDGAR fetches are cached per company (SEC fair-access). All data public domain.
Not investment advice.
"""

import time

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.edgar.client import (
    extract_key_financials,
    fetch_companyfacts,
    fetch_submissions,
    parse_recent_filings,
)
from backend.models.company import Company

router = APIRouter(prefix="/api/filings", tags=["filings"])

_COMPANY_TTL = 3600        # recent filings — 1h
_FIN_TTL = 24 * 3600       # fundamentals — 24h
_company_cache: dict[str, tuple[float, list]] = {}
_fin_cache: dict[str, tuple[float, dict]] = {}


def _lookup(db: Session, ticker: str) -> Company | None:
    return db.query(Company).filter(Company.ticker == (ticker or "").strip().upper()).first()


@router.get("/search")
async def search(q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=50), db: Session = Depends(get_db)):
    """Lookup companies by ticker prefix or name substring (exact ticker ranked first)."""
    term = q.strip().upper()
    rows = (
        db.query(Company)
        .filter(or_(Company.ticker.like(f"{term}%"), Company.title.ilike(f"%{q.strip()}%")))
        .limit(limit * 4)
        .all()
    )

    def rank(c: Company) -> int:
        if c.ticker == term:
            return 0
        if c.ticker.startswith(term):
            return 1
        return 2

    rows = sorted(rows, key=lambda c: (rank(c), c.ticker))[:limit]
    return {"results": [{"ticker": c.ticker, "cik": c.cik, "title": c.title} for c in rows]}


@router.get("/company")
async def company(ticker: str = Query(...), db: Session = Depends(get_db)):
    """Company profile + up to 20 most-recent EDGAR filings (each linked to sec.gov)."""
    c = _lookup(db, ticker)
    if not c:
        return {"available": False, "reason": f"unknown ticker: {(ticker or '').strip().upper()}"}

    now = time.monotonic()
    cached = _company_cache.get(c.cik)
    if cached and now - cached[0] < _COMPANY_TTL:
        filings = cached[1]
    else:
        try:
            subs = await fetch_submissions(c.cik)
            filings = parse_recent_filings(subs, c.cik, limit=20)
        except Exception:
            return {"available": False, "ticker": c.ticker, "reason": "EDGAR fetch failed — try again shortly."}
        _company_cache[c.cik] = (now, filings)

    return {"available": True, "ticker": c.ticker, "cik": c.cik, "name": c.title, "filings": filings}


@router.get("/financials")
async def financials(ticker: str = Query(...), db: Session = Depends(get_db)):
    """Latest-annual headline financials from XBRL company facts."""
    c = _lookup(db, ticker)
    if not c:
        return {"available": False, "reason": f"unknown ticker: {(ticker or '').strip().upper()}"}

    now = time.monotonic()
    cached = _fin_cache.get(c.cik)
    if cached and now - cached[0] < _FIN_TTL:
        metrics = cached[1]
    else:
        try:
            facts = await fetch_companyfacts(c.cik)
            metrics = extract_key_financials(facts)
        except Exception:
            return {"available": False, "ticker": c.ticker, "reason": "EDGAR fetch failed — try again shortly."}
        _fin_cache[c.cik] = (now, metrics)

    return {"available": True, "ticker": c.ticker, "cik": c.cik, "name": c.title, "metrics": metrics}
