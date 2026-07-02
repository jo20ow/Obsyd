"""Crypto read endpoints — free real-time-ish quotes for the cross-asset terminal.

GET /api/crypto/prices          — latest basket (price, 24h change, market cap), FREE
GET /api/crypto/history?symbol= — daily close history for one asset (charting), FREE

Envelope matches the rest of the app: {"available": bool, "data": [...]}.
Source: CoinGecko free public API (no key). Not investment advice.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.crypto.coingecko import CRYPTO_ASSETS
from backend.database import get_db
from backend.models.crypto import CryptoPrice

router = APIRouter(prefix="/api/crypto", tags=["crypto"])

_SYMBOLS = {sym for _gid, sym, _name in CRYPTO_ASSETS}


@router.get("/prices")
async def get_crypto_prices(db: Session = Depends(get_db)):
    """Latest quote per asset (most recent stored date), sorted by market cap desc."""
    latest_date = db.query(func.max(CryptoPrice.date)).scalar()
    if not latest_date:
        return {"available": False, "reason": "No crypto data yet — check back shortly."}

    rows = db.query(CryptoPrice).filter(CryptoPrice.date == latest_date).all()
    data = sorted(
        (
            {
                "symbol": r.symbol,
                "name": r.name,
                "price_usd": r.price_usd,
                "change_24h_pct": r.change_24h_pct,
                "market_cap": r.market_cap,
            }
            for r in rows
        ),
        key=lambda d: d["market_cap"] or 0,
        reverse=True,
    )
    return {"available": True, "date": latest_date, "unit": "USD", "data": data}


@router.get("/history")
async def get_crypto_history(
    symbol: str = Query(..., description="Ticker, e.g. BTC"),
    days: int = Query(90, ge=1, le=1500),
    db: Session = Depends(get_db),
):
    """Daily close history for one asset (ascending) — for the sparkline/chart."""
    sym = symbol.upper()
    if sym not in _SYMBOLS:
        return {"available": False, "symbol": sym, "reason": f"unknown symbol: {sym}"}

    start = (datetime.utcnow().date() - timedelta(days=days)).isoformat()
    rows = (
        db.query(CryptoPrice)
        .filter(CryptoPrice.symbol == sym, CryptoPrice.date >= start)
        .order_by(CryptoPrice.date.asc())
        .all()
    )
    if not rows:
        return {"available": False, "symbol": sym, "reason": "No history yet."}
    return {
        "available": True,
        "symbol": sym,
        "name": rows[-1].name,
        "unit": "USD",
        "data": [{"date": r.date, "price": r.price_usd} for r in rows],
    }
