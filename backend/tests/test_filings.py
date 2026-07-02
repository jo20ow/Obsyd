"""SEC EDGAR filings vertical: pure parsers + search/company/financials endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.edgar.client import extract_key_financials, parse_recent_filings
from backend.main import app
from backend.models.company import Company
from backend.routes import filings as filings_routes

SUBS = {
    "filings": {
        "recent": {
            "form": ["8-K", "10-Q", "10-K"],
            "filingDate": ["2026-07-02", "2026-05-20", "2026-02-20"],
            "reportDate": ["2026-06-28", "2026-04-26", "2026-01-26"],
            "accessionNumber": ["0000320193-26-000060", "0000320193-26-000052", "0000320193-26-000010"],
            "primaryDocument": ["aapl-8k.htm", "aapl-10q.htm", "aapl-10k.htm"],
        }
    }
}

FACTS = {
    "facts": {
        "us-gaap": {
            "Revenues": {"units": {"USD": [
                {"end": "2025-09-28", "val": 100, "fy": 2025, "fp": "Q4", "form": "10-Q"},
                {"end": "2025-09-28", "val": 600, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
            ]}},
            "NetIncomeLoss": {"units": {"USD": [{"end": "2025-09-28", "val": 300, "fy": 2025, "fp": "FY", "form": "10-K"}]}},
            "Assets": {"units": {"USD": [{"end": "2025-09-28", "val": 900, "fy": 2025, "fp": "FY", "form": "10-K"}]}},
            # StockholdersEquity intentionally absent → None
        }
    }
}


@pytest.fixture
def client(db_session):
    from backend.database import get_db
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _clear_caches():
    filings_routes._company_cache.clear()
    filings_routes._fin_cache.clear()
    yield


def _seed(db):
    db.add(Company(cik="0000320193", ticker="AAPL", title="Apple Inc."))
    db.add(Company(cik="0001045810", ticker="NVDA", title="NVIDIA CORP"))
    db.add(Company(cik="0000789019", ticker="MSFT", title="MICROSOFT CORP"))
    db.commit()


# ─── pure parsers ──────────────────────────────────────────────────────────


def test_parse_recent_filings_builds_urls_and_limit():
    out = parse_recent_filings(SUBS, "0000320193", limit=2)
    assert len(out) == 2
    assert out[0]["form"] == "8-K" and out[0]["date"] == "2026-07-02"
    assert out[0]["url"] == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000060/aapl-8k.htm"


def test_extract_key_financials_prefers_annual_and_handles_missing():
    m = extract_key_financials(FACTS)
    assert m["revenue"]["value"] == 600  # FY, not the Q4 100
    assert m["revenue"]["fiscal_year"] == 2025
    assert m["net_income"]["value"] == 300
    assert m["total_assets"]["value"] == 900
    assert m["equity"] is None  # absent tag → None, not a crash


# ─── endpoints ──────────────────────────────────────────────────────────────


def test_search_ranks_exact_ticker_and_matches_name(client, db_session):
    _seed(db_session)
    r = client.get("/api/filings/search?q=aapl").json()["results"]
    assert r[0]["ticker"] == "AAPL"
    names = {x["ticker"] for x in client.get("/api/filings/search?q=apple").json()["results"]}
    assert "AAPL" in names  # name substring match


def test_company_endpoint_returns_filings(client, db_session, monkeypatch):
    _seed(db_session)

    async def fake_subs(cik):
        return SUBS

    monkeypatch.setattr(filings_routes, "fetch_submissions", fake_subs)
    body = client.get("/api/filings/company?ticker=aapl").json()
    assert body["available"] is True and body["cik"] == "0000320193"
    assert len(body["filings"]) == 3 and body["filings"][0]["form"] == "8-K"


def test_financials_endpoint(client, db_session, monkeypatch):
    _seed(db_session)

    async def fake_facts(cik):
        return FACTS

    monkeypatch.setattr(filings_routes, "fetch_companyfacts", fake_facts)
    body = client.get("/api/filings/financials?ticker=AAPL").json()
    assert body["available"] is True
    assert body["metrics"]["revenue"]["value"] == 600


def test_unknown_ticker(client, db_session):
    _seed(db_session)
    assert client.get("/api/filings/company?ticker=ZZZZ").json()["available"] is False
    assert client.get("/api/filings/financials?ticker=ZZZZ").json()["available"] is False
