# Negative-Price Detection (Power Day-Ahead) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-day min/max prices + negative-hour count to the ENTSO-E A44 ingest and surface negative-price days on the Power Day-Ahead panel in the ENERGY tab.

**Architecture:** A new `PowerPriceDaily` table stores richer per-day stats (mean, min, max, negative_hours). The `ingest_day_ahead` function writes BOTH the existing `EnergyPrice(POWER_DE)` row (for scorecard/spark compatibility) AND a `PowerPriceDaily` row. The `/api/power/day-ahead` route reads `PowerPriceDaily` when available, falling back to `EnergyPrice` if the table is empty. The frontend marks negative-price days with a red `FlagDot`.

**Tech Stack:** Python/SQLAlchemy (backend), FastAPI (routes), React + Recharts (frontend), pytest, ruff, eslint

---

## File Map

| Action | File | What changes |
|--------|------|--------------|
| Modify | `backend/power/entsoe_prices.py` | Add `parse_day_ahead_stats()`, refactor ingest to upsert both tables |
| Modify | `backend/models/energy.py` | Add `PowerPriceDaily` model |
| Modify | `backend/routes/power.py` | Enrich `/day-ahead` to read `PowerPriceDaily`, add `negative_days` + `negative` flag |
| Modify | `frontend/src/components/PowerDayAheadPanel.jsx` | Add `FlagDot` marker for negative days, show `negative_days` count |
| Create | `backend/tests/test_negative_prices.py` | Unit + integration tests for parser, ingest, route |

---

## Task 1: Add `PowerPriceDaily` model

**Files:**
- Modify: `backend/models/energy.py`

- [ ] **Step 1: Add the model class**

Open `backend/models/energy.py` and append after `PowerGenMix`:

```python
class PowerPriceDaily(Base):
    """Rich per-day electricity price stats for negative-price detection.

    One row per (date, zone). Stores mean/min/max price and a count of hours
    where the auction price was negative (EUR/MWh < 0) — a renewable-oversupply
    signature common in DE spring/summer.

    `mean_price` mirrors EnergyPrice(symbol="POWER_DE").close so the scorecard
    and spark-spread paths never need to touch this table.

    Source: ENTSO-E A44 (Day-Ahead Prices), DE-LU bidding zone.
    Idempotent upsert by (date, zone).
    """

    __tablename__ = "power_price_daily"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)   # YYYY-MM-DD
    zone: Mapped[str] = mapped_column(String, nullable=False, index=True)   # e.g. "DE_LU"
    mean_price: Mapped[float] = mapped_column(Float, nullable=False)        # EUR/MWh daily mean
    min_price: Mapped[float] = mapped_column(Float, nullable=False)         # EUR/MWh daily min
    max_price: Mapped[float] = mapped_column(Float, nullable=False)         # EUR/MWh daily max
    negative_hours: Mapped[int] = mapped_column(nullable=False, default=0)  # count of hours < 0 EUR/MWh
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("date", "zone", name="uq_power_price_daily_date_zone"),
    )
```

The `Integer` column type is inferred by SQLAlchemy from `int` annotation. No import needed beyond what's already in the file.

- [ ] **Step 2: Verify the model imports cleanly**

```bash
cd /Users/johannesweisser/Projects/Obsyd && .venv/bin/python -c "from backend.models.energy import PowerPriceDaily; print('OK')"
```

Expected output: `OK`

- [ ] **Step 3: Commit**

```bash
git -C /Users/johannesweisser/Projects/Obsyd add backend/models/energy.py
git -C /Users/johannesweisser/Projects/Obsyd commit -m "feat(power): add PowerPriceDaily model (min/max/negative_hours)"
```

---

## Task 2: Add `parse_day_ahead_stats` parser

**Files:**
- Modify: `backend/power/entsoe_prices.py`

- [ ] **Step 1: Write the failing test (parser only, no imports of new function yet)**

Create `/Users/johannesweisser/Projects/Obsyd/backend/tests/test_negative_prices.py`:

```python
"""Negative-price detection — parser, ingest, and route tests."""
from __future__ import annotations

import pytest

# ─── XML helpers (same pattern as test_power_prices.py) ────────────────────

_NS = "urn:iec62325.351:tc57wg16:451-6:publicationdocument:7:0"


def _a44(ts_blocks: str, ns: str = _NS) -> str:
    return (
        f'<?xml version="1.0"?>'
        f'<Publication_MarketDocument xmlns="{ns}">'
        f"<type>A44</type>"
        f"{ts_blocks}"
        f"</Publication_MarketDocument>"
    )


def _ts(start: str, end: str, prices: list[float], res: str = "PT60M") -> str:
    pts = "".join(
        f"<Point><position>{i + 1}</position><price.amount>{p}</price.amount></Point>"
        for i, p in enumerate(prices)
    )
    return (
        f"<TimeSeries>"
        f"<Period>"
        f"<timeInterval><start>{start}</start><end>{end}</end></timeInterval>"
        f"<resolution>{res}</resolution>"
        f"{pts}"
        f"</Period>"
        f"</TimeSeries>"
    )


# ─── parse_day_ahead_stats unit tests ───────────────────────────────────────


def test_stats_all_positive():
    """All-positive day: negative_hours=0, correct min/max/mean."""
    from backend.power.entsoe_prices import parse_day_ahead_stats

    prices = [10.0, 20.0, 30.0, 40.0]  # mean=25, min=10, max=40
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-01T04:00Z", prices))
    result = parse_day_ahead_stats(xml)
    assert "2026-05-01" in result
    day = result["2026-05-01"]
    assert day["mean"] == pytest.approx(25.0)
    assert day["min"] == pytest.approx(10.0)
    assert day["max"] == pytest.approx(40.0)
    assert day["negative_hours"] == 0


def test_stats_with_negative_hours():
    """Two negative hours → negative_hours=2, min is the most-negative value."""
    from backend.power.entsoe_prices import parse_day_ahead_stats

    prices = [-50.0, -10.0, 30.0, 80.0]  # mean=12.5, min=-50, max=80
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-01T04:00Z", prices))
    result = parse_day_ahead_stats(xml)
    day = result["2026-05-01"]
    assert day["negative_hours"] == 2
    assert day["min"] == pytest.approx(-50.0)
    assert day["max"] == pytest.approx(80.0)
    assert day["mean"] == pytest.approx(12.5)


def test_stats_all_negative():
    """All prices negative → negative_hours = number of prices."""
    from backend.power.entsoe_prices import parse_day_ahead_stats

    prices = [-5.0, -10.0, -15.0]
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-01T03:00Z", prices))
    result = parse_day_ahead_stats(xml)
    day = result["2026-05-01"]
    assert day["negative_hours"] == 3
    assert day["min"] == pytest.approx(-15.0)


def test_stats_mean_matches_parse_day_ahead_prices():
    """mean from parse_day_ahead_stats must equal parse_day_ahead_prices for same XML."""
    from backend.power.entsoe_prices import parse_day_ahead_prices, parse_day_ahead_stats

    prices = [20.0, -5.0, 100.0, 35.0] * 6  # 24 hours
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", prices))
    stats = parse_day_ahead_stats(xml)
    old = parse_day_ahead_prices(xml)
    assert stats["2026-05-01"]["mean"] == pytest.approx(old["2026-05-01"])


def test_stats_empty_document():
    from backend.power.entsoe_prices import parse_day_ahead_stats

    assert parse_day_ahead_stats(_a44("")) == {}


def test_stats_two_days():
    """Multi-day XML buckets correctly into separate day entries."""
    from backend.power.entsoe_prices import parse_day_ahead_stats

    d1 = [10.0] * 24
    d2 = [-20.0] * 24
    xml = _a44(
        _ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", d1)
        + _ts("2026-05-02T00:00Z", "2026-05-03T00:00Z", d2)
    )
    result = parse_day_ahead_stats(xml)
    assert result["2026-05-01"]["negative_hours"] == 0
    assert result["2026-05-02"]["negative_hours"] == 24
    assert result["2026-05-02"]["min"] == pytest.approx(-20.0)
```

- [ ] **Step 2: Run the tests to confirm they fail (function doesn't exist yet)**

```bash
cd /Users/johannesweisser/Projects/Obsyd && .venv/bin/python -m pytest backend/tests/test_negative_prices.py::test_stats_all_positive -v 2>&1 | tail -5
```

Expected: FAIL with `ImportError` or `cannot import name 'parse_day_ahead_stats'`

- [ ] **Step 3: Add `parse_day_ahead_stats` to `backend/power/entsoe_prices.py`**

Insert the new function after `parse_day_ahead_prices` (before the `# ─── fetch ───` comment):

```python
def parse_day_ahead_stats(xml_text: str) -> dict[str, dict]:
    """Parse an A44 document into {YYYY-MM-DD: {mean, min, max, negative_hours}}.

    Same Period/Point walk as parse_day_ahead_prices, but collects ALL hourly
    prices per UTC day and computes:
      mean          — daily mean EUR/MWh (identical to parse_day_ahead_prices output)
      min           — lowest hourly price (can be negative)
      max           — highest hourly price
      negative_hours — count of hours where price < 0 (renewable-oversupply signal)

    Namespace-agnostic (matches local tag names only).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A44 XML parse error: {exc}") from exc

    by_day: dict[str, list[float]] = {}

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
                price_str = next(
                    (e.text for e in point if _localname(e.tag) == "price.amount"), None
                )
                if pos is None or price_str is None:
                    continue
                try:
                    ts_time = start + timedelta(hours=res_hours * (int(pos) - 1))
                    price = float(price_str)
                except (ValueError, TypeError):
                    continue
                day = ts_time.astimezone(timezone.utc).strftime("%Y-%m-%d")
                by_day.setdefault(day, []).append(price)

    result: dict[str, dict] = {}
    for day, prices in by_day.items():
        if not prices:
            continue
        result[day] = {
            "mean": sum(prices) / len(prices),
            "min": min(prices),
            "max": max(prices),
            "negative_hours": sum(1 for p in prices if p < 0),
        }
    return result
```

- [ ] **Step 4: Run parser tests to confirm they pass**

```bash
cd /Users/johannesweisser/Projects/Obsyd && .venv/bin/python -m pytest backend/tests/test_negative_prices.py -k "test_stats" -v 2>&1 | tail -20
```

Expected: All 6 `test_stats_*` tests PASS.

- [ ] **Step 5: Run existing power-prices tests to confirm nothing is broken**

```bash
cd /Users/johannesweisser/Projects/Obsyd && .venv/bin/python -m pytest backend/tests/test_power_prices.py -v 2>&1 | tail -15
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git -C /Users/johannesweisser/Projects/Obsyd add backend/power/entsoe_prices.py backend/tests/test_negative_prices.py
git -C /Users/johannesweisser/Projects/Obsyd commit -m "feat(power): add parse_day_ahead_stats with min/max/negative_hours"
```

---

## Task 3: Update ingest to upsert `PowerPriceDaily`

**Files:**
- Modify: `backend/power/entsoe_prices.py`

- [ ] **Step 1: Write the failing ingest test**

Append to `backend/tests/test_negative_prices.py`:

```python
# ─── ingest tests ────────────────────────────────────────────────────────────


async def test_ingest_writes_power_price_daily(db_session, monkeypatch):
    """ingest_day_ahead upserts a PowerPriceDaily row alongside EnergyPrice."""
    from pydantic import SecretStr

    from backend.models.energy import PowerPriceDaily
    from backend.power import entsoe_prices

    # 3 hours: 2 positive, 1 negative
    prices = [-20.0, 40.0, 60.0]
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-01T03:00Z", prices))

    async def fake_fetch(eic, month_start, *, overwrite=False):
        return xml

    monkeypatch.setattr(entsoe_prices, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(entsoe_prices.settings, "entsoe_api_token", SecretStr("tok"))

    result = await entsoe_prices.ingest_day_ahead(db_session, ["2026-05-01"])
    assert result["written"] == 1

    row = (
        db_session.query(PowerPriceDaily)
        .filter_by(date="2026-05-01", zone="DE_LU")
        .first()
    )
    assert row is not None
    assert row.negative_hours == 1
    assert row.min_price == pytest.approx(-20.0)
    assert row.max_price == pytest.approx(60.0)
    assert row.mean_price == pytest.approx((-20.0 + 40.0 + 60.0) / 3)


async def test_ingest_power_price_daily_idempotent(db_session, monkeypatch):
    """Re-running ingest updates the existing PowerPriceDaily row (no duplicate)."""
    from pydantic import SecretStr

    from backend.models.energy import PowerPriceDaily
    from backend.power import entsoe_prices

    xml_v1 = _a44(_ts("2026-05-01T00:00Z", "2026-05-01T03:00Z", [-10.0, 20.0, 30.0]))
    xml_v2 = _a44(_ts("2026-05-01T00:00Z", "2026-05-01T03:00Z", [5.0, 10.0, 15.0]))
    call_n = {"n": 0}

    async def fake_fetch(eic, month_start, *, overwrite=False):
        xml = xml_v1 if call_n["n"] == 0 else xml_v2
        call_n["n"] += 1
        return xml

    monkeypatch.setattr(entsoe_prices, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(entsoe_prices.settings, "entsoe_api_token", SecretStr("tok"))

    await entsoe_prices.ingest_day_ahead(db_session, ["2026-05-01"])
    await entsoe_prices.ingest_day_ahead(db_session, ["2026-05-01"], overwrite=True)

    rows = db_session.query(PowerPriceDaily).filter_by(date="2026-05-01", zone="DE_LU").all()
    assert len(rows) == 1
    assert rows[0].negative_hours == 0  # v2 has no negatives
    assert rows[0].min_price == pytest.approx(5.0)
```

- [ ] **Step 2: Run the new ingest tests to confirm they fail**

```bash
cd /Users/johannesweisser/Projects/Obsyd && .venv/bin/python -m pytest backend/tests/test_negative_prices.py::test_ingest_writes_power_price_daily -v 2>&1 | tail -10
```

Expected: FAIL (PowerPriceDaily not written yet).

- [ ] **Step 3: Update `ingest_day_ahead` in `backend/power/entsoe_prices.py`**

Replace the import at the top of the file:

```python
from backend.models.energy import EnergyPrice
```

with:

```python
from backend.models.energy import EnergyPrice, PowerPriceDaily
```

Replace the body of `ingest_day_ahead` — specifically the section from `prices_by_day: dict[str, float] = {}` through `db.commit()`:

```python
    # Zone label for PowerPriceDaily (derived from EIC, but a readable key is fine)
    zone = "DE_LU"

    stats_by_day: dict[str, dict] = {}
    for month_start in months:
        try:
            xml = await _fetch_zone_month(eic, month_start, overwrite=overwrite)
        except httpx.HTTPError as exc:
            logger.warning("entsoe_prices: %s fetch failed: %s", month_start, exc)
            continue
        if not xml:
            continue
        for day, stats in parse_day_ahead_stats(xml).items():
            if day in wanted:
                stats_by_day[day] = stats

    written = 0
    for day, stats in stats_by_day.items():
        mean = stats["mean"]
        # Keep EnergyPrice(POWER_DE) identical — scorecard + spark-spread use it.
        _upsert(db, day, symbol, mean)
        # Upsert the richer per-day stats row.
        _upsert_daily(db, day, zone, stats)
        written += 1
    db.commit()
    logger.info(
        "entsoe_prices.ingest_day_ahead: %d/%d days written (symbol=%s)",
        written,
        len(days),
        symbol,
    )
    return {"days": len(days), "written": written}
```

Also add the `_upsert_daily` helper after the existing `_upsert` function:

```python
def _upsert_daily(db: Session, day: str, zone: str, stats: dict) -> None:
    """Upsert one PowerPriceDaily row from a stats dict (mean/min/max/negative_hours)."""
    existing = (
        db.query(PowerPriceDaily)
        .filter(PowerPriceDaily.date == day, PowerPriceDaily.zone == zone)
        .first()
    )
    if existing:
        existing.mean_price = stats["mean"]
        existing.min_price = stats["min"]
        existing.max_price = stats["max"]
        existing.negative_hours = stats["negative_hours"]
    else:
        db.add(
            PowerPriceDaily(
                date=day,
                zone=zone,
                mean_price=stats["mean"],
                min_price=stats["min"],
                max_price=stats["max"],
                negative_hours=stats["negative_hours"],
            )
        )
```

Note: Also remove the now-unused local variable `prices_by_day` and the old loop that built it. The new code above replaces that entire block.

- [ ] **Step 4: Run all ingest tests**

```bash
cd /Users/johannesweisser/Projects/Obsyd && .venv/bin/python -m pytest backend/tests/test_negative_prices.py -v 2>&1 | tail -20
```

Expected: All tests PASS (6 parser + 2 ingest = 8 so far).

- [ ] **Step 5: Run the original power-prices tests to confirm backward compat**

```bash
cd /Users/johannesweisser/Projects/Obsyd && .venv/bin/python -m pytest backend/tests/test_power_prices.py -v 2>&1 | tail -15
```

Expected: All PASS (the `EnergyPrice` write path is unchanged).

- [ ] **Step 6: Commit**

```bash
git -C /Users/johannesweisser/Projects/Obsyd add backend/power/entsoe_prices.py backend/tests/test_negative_prices.py
git -C /Users/johannesweisser/Projects/Obsyd commit -m "feat(power): ingest upserts PowerPriceDaily (min/max/negative_hours) alongside EnergyPrice"
```

---

## Task 4: Enrich `/api/power/day-ahead` route

**Files:**
- Modify: `backend/routes/power.py`

- [ ] **Step 1: Write the failing route test**

Append to `backend/tests/test_negative_prices.py`:

```python
# ─── route tests ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    """Prevent dependency_overrides leaking between test files."""
    yield
    from backend.main import app
    app.dependency_overrides.clear()


def _make_client(db) -> "TestClient":
    from fastapi.testclient import TestClient
    from backend.database import get_db
    from backend.main import app
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


def _seed_daily(db, rows: list[dict]) -> None:
    from backend.models.energy import PowerPriceDaily
    from datetime import datetime
    for r in rows:
        db.add(
            PowerPriceDaily(
                date=r["date"],
                zone="DE_LU",
                mean_price=r["mean_price"],
                min_price=r["min_price"],
                max_price=r["max_price"],
                negative_hours=r["negative_hours"],
            )
        )
    db.commit()


def test_route_enriched_fields_present(db_session):
    """When PowerPriceDaily rows exist, each data point has negative_hours + negative flag."""
    from datetime import date, timedelta
    today = date.today()
    d1 = (today - timedelta(days=3)).isoformat()
    d2 = (today - timedelta(days=2)).isoformat()

    _seed_daily(db_session, [
        {"date": d1, "mean_price": 50.0, "min_price": -10.0, "max_price": 90.0, "negative_hours": 2},
        {"date": d2, "mean_price": 60.0, "min_price": 5.0,  "max_price": 100.0, "negative_hours": 0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/power/day-ahead?days=120")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True

    row_d1 = next(r for r in body["data"] if r["date"] == d1)
    assert row_d1["negative_hours"] == 2
    assert row_d1["negative"] is True
    assert row_d1["min_price"] == pytest.approx(-10.0)

    row_d2 = next(r for r in body["data"] if r["date"] == d2)
    assert row_d2["negative"] is False
    assert row_d2["negative_hours"] == 0


def test_route_negative_days_count(db_session):
    """negative_days count equals the number of rows where negative_hours > 0."""
    from datetime import date, timedelta
    today = date.today()
    rows = [
        {"date": (today - timedelta(days=i)).isoformat(),
         "mean_price": 50.0, "min_price": -5.0, "max_price": 80.0, "negative_hours": 1}
        for i in range(1, 4)
    ]
    rows.append({"date": (today - timedelta(days=4)).isoformat(),
                 "mean_price": 50.0, "min_price": 5.0, "max_price": 80.0, "negative_hours": 0})
    _seed_daily(db_session, rows)
    client = _make_client(db_session)
    body = client.get("/api/power/day-ahead?days=120").json()
    assert body["negative_days"] == 3


def test_route_fallback_when_no_daily_table(db_session):
    """If PowerPriceDaily is empty, route falls back to EnergyPrice (available=False when both empty)."""
    client = _make_client(db_session)
    body = client.get("/api/power/day-ahead?days=120").json()
    assert body["available"] is False


def test_route_latest_has_negative_hours(db_session):
    """latest object includes negative_hours and negative fields."""
    from datetime import date, timedelta
    today = date.today()
    d1 = (today - timedelta(days=1)).isoformat()
    _seed_daily(db_session, [
        {"date": d1, "mean_price": 30.0, "min_price": -50.0, "max_price": 80.0, "negative_hours": 5},
    ])
    client = _make_client(db_session)
    body = client.get("/api/power/day-ahead?days=120").json()
    assert body["latest"]["negative_hours"] == 5
    assert body["latest"]["negative"] is True
```

- [ ] **Step 2: Run route tests to confirm they fail**

```bash
cd /Users/johannesweisser/Projects/Obsyd && .venv/bin/python -m pytest backend/tests/test_negative_prices.py -k "test_route" -v 2>&1 | tail -15
```

Expected: FAIL (route doesn't return enriched fields yet).

- [ ] **Step 3: Update `backend/routes/power.py`**

Add `PowerPriceDaily` to the import:

```python
from backend.models.energy import EnergyPrice, PowerGenMix, PowerGrid, PowerPriceDaily, SparkSpreadHistory
```

Replace the `get_day_ahead` function body entirely:

```python
@router.get("/day-ahead")
async def get_day_ahead(
    days: int = Query(120, ge=1, le=1500),
    db: Session = Depends(get_db),
):
    """ENTSO-E day-ahead electricity prices for DE-LU (EUR/MWh). Free tier.

    When PowerPriceDaily rows are available, each data point includes:
      close         — daily mean EUR/MWh (identical to EnergyPrice.close)
      min_price     — daily minimum EUR/MWh (can be negative)
      negative_hours — hours where the auction price was < 0
      negative      — true if negative_hours > 0
    negative_days counts how many days in the window had at least one negative hour.

    Falls back to EnergyPrice-only behaviour if PowerPriceDaily is empty.
    """
    date_from, date_to = _window(days)

    # Primary path: richer PowerPriceDaily table
    daily_rows = (
        db.query(PowerPriceDaily)
        .filter(
            PowerPriceDaily.zone == "DE_LU",
            PowerPriceDaily.date >= date_from,
            PowerPriceDaily.date <= date_to,
        )
        .order_by(PowerPriceDaily.date.asc())
        .all()
    )

    if daily_rows:
        def _daily_dict(r: PowerPriceDaily) -> dict:
            return {
                "date": r.date,
                "close": r.mean_price,
                "min_price": r.min_price,
                "max_price": r.max_price,
                "negative_hours": r.negative_hours,
                "negative": r.negative_hours > 0,
            }

        data = [_daily_dict(r) for r in daily_rows]
        latest = data[-1]
        negative_days = sum(1 for d in data if d["negative"])
        return {
            "available": True,
            "symbol": "POWER_DE",
            "unit": "EUR/MWh",
            "from": date_from,
            "to": date_to,
            "negative_days": negative_days,
            "latest": latest,
            "data": data,
        }

    # Fallback: legacy EnergyPrice rows (no min/negative_hours available)
    rows = (
        db.query(EnergyPrice)
        .filter(
            EnergyPrice.symbol == "POWER_DE",
            EnergyPrice.date >= date_from,
            EnergyPrice.date <= date_to,
        )
        .order_by(EnergyPrice.date.asc())
        .all()
    )
    if not rows:
        return {
            "available": False,
            "reason": "no POWER_DE data yet — run power backfill (ingest_day_ahead)",
        }
    data = [{"date": r.date, "close": r.close} for r in rows]
    return {
        "available": True,
        "symbol": "POWER_DE",
        "unit": "EUR/MWh",
        "from": date_from,
        "to": date_to,
        "negative_days": 0,
        "latest": data[-1],
        "data": data,
    }
```

- [ ] **Step 4: Run route tests**

```bash
cd /Users/johannesweisser/Projects/Obsyd && .venv/bin/python -m pytest backend/tests/test_negative_prices.py -k "test_route" -v 2>&1 | tail -20
```

Expected: All 5 route tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
cd /Users/johannesweisser/Projects/Obsyd && .venv/bin/python -m pytest -q 2>&1 | tail -20
```

Expected: All tests PASS. No regressions.

- [ ] **Step 6: Commit**

```bash
git -C /Users/johannesweisser/Projects/Obsyd add backend/routes/power.py backend/tests/test_negative_prices.py
git -C /Users/johannesweisser/Projects/Obsyd commit -m "feat(power): enrich /api/power/day-ahead with negative_hours, negative flag, negative_days"
```

---

## Task 5: Frontend — negative-price day markers on the chart

**Files:**
- Modify: `frontend/src/components/PowerDayAheadPanel.jsx`

- [ ] **Step 1: Add `FlagDot` and wire up negative-price markers**

Replace the entire content of `frontend/src/components/PowerDayAheadPanel.jsx`:

```jsx
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

// Render a red marker on days where negative_hours > 0; invisible otherwise.
// Mirrors the FlagDot pattern from GasBalancePanel.
function NegativeDot({ cx, cy, payload }) {
  if (!payload?.negative || cx == null || cy == null) return null
  return <circle cx={cx} cy={cy} r={3} fill="#f87171" stroke="none" />
}

export default function PowerDayAheadPanel() {
  const { data, loading, error } = useFetchWithError(`${API}/power/day-ahead?days=120`)

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">POWER DAY-AHEAD // FETCH ERROR</div>
      </div>
    )
  if (!data?.available && !loading) return null

  const rows = data?.data ?? []
  const latest = data?.latest ?? rows[rows.length - 1]
  const close = latest?.close
  const negativeDays = data?.negative_days ?? 0

  return (
    <Panel
      id="power-day-ahead"
      title="POWER DAY-AHEAD · DE-LU"
      info="ENTSO-E day-ahead electricity prices for the DE-LU bidding zone (EUR/MWh). Each point is the daily mean of 24 hourly auction results from the ENTSO-E Transparency Platform (A44). Red markers indicate days with at least one negative-price hour (renewable oversupply). Free, official redistributable data."
      collapsible
      headerRight={
        close != null && (
          <span className="font-mono text-[10px] text-cyan-glow font-bold">
            {close.toFixed(1)} €/MWh
          </span>
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading power prices…
        </div>
      )}
      {!loading && data?.available && latest && (
        <>
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-baseline gap-3">
              <span className="font-mono text-3xl font-bold text-cyan-glow">
                {close?.toFixed(1)}
              </span>
              <span className="font-mono text-[10px] text-neutral-600">EUR/MWh</span>
              <span className="font-mono text-[10px] text-neutral-600">{latest.date}</span>
            </div>
            <div className="font-mono text-[10px] text-neutral-600 mt-1">
              ENTSO-E A44 · DE-LU bidding zone · daily mean of hourly prices
              {negativeDays > 0 && (
                <span className="ml-2 text-red-400">
                  · {negativeDays} negative-price {negativeDays === 1 ? 'day' : 'days'}
                </span>
              )}
            </div>
          </div>
          {rows.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={70}>
                <AreaChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }}
                    tickFormatter={fmtDate}
                    interval="preserveStartEnd"
                    minTickGap={60}
                  />
                  <YAxis
                    tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }}
                    width={30}
                  />
                  <Tooltip
                    contentStyle={CHART_TOOLTIP_STYLE}
                    formatter={(v) => [`${Number(v).toFixed(1)} €/MWh`, 'Day-Ahead']}
                    labelFormatter={fmtDate}
                  />
                  <Area
                    type="monotone"
                    dataKey="close"
                    stroke="#22d3ee"
                    fill="#22d3ee"
                    fillOpacity={0.06}
                    strokeWidth={1.5}
                    dot={<NegativeDot />}
                    activeDot={{ r: 3, fill: '#22d3ee' }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </Panel>
  )
}
```

- [ ] **Step 2: Run ESLint**

```bash
cd /Users/johannesweisser/Projects/Obsyd/frontend && npx eslint src/components/PowerDayAheadPanel.jsx 2>&1
```

Expected: No errors.

- [ ] **Step 3: Run frontend build**

```bash
cd /Users/johannesweisser/Projects/Obsyd/frontend && npm run build 2>&1 | tail -20
```

Expected: Build succeeds (no errors; warnings about bundle size are acceptable).

- [ ] **Step 4: Commit**

```bash
git -C /Users/johannesweisser/Projects/Obsyd add frontend/src/components/PowerDayAheadPanel.jsx
git -C /Users/johannesweisser/Projects/Obsyd commit -m "feat(power): mark negative-price days on day-ahead chart with red dot"
```

---

## Task 6: Local backfill verification

**Files:** none (verification only)

- [ ] **Step 1: Re-run ingest for 120 days to populate PowerPriceDaily**

```bash
cd /Users/johannesweisser/Projects/Obsyd && .venv/bin/python - <<'EOF'
import asyncio, sys
sys.path.insert(0, ".")

from datetime import date, timedelta
from backend.database import SessionLocal, engine, Base
from backend.models.energy import PowerPriceDaily
from backend.power.entsoe_prices import ingest_day_ahead

# Ensure new table exists
Base.metadata.create_all(bind=engine)

today = date.today()
days = [(today - timedelta(days=i)).isoformat() for i in range(1, 121)]

async def run():
    db = SessionLocal()
    try:
        result = await ingest_day_ahead(db, days)
        print("ingest result:", result)
        total = db.query(PowerPriceDaily).count()
        neg = db.query(PowerPriceDaily).filter(PowerPriceDaily.negative_hours > 0).count()
        print(f"PowerPriceDaily rows: {total}, with negative hours: {neg}")
        sample = db.query(PowerPriceDaily).filter(PowerPriceDaily.negative_hours > 0).first()
        if sample:
            print(f"Sample negative day: {sample.date}, neg_hours={sample.negative_hours}, min={sample.min_price:.2f}")
    finally:
        db.close()

asyncio.run(run())
EOF
```

Expected output: ingest result shows days written; PowerPriceDaily rows > 0; negative-hours days > 0 (DE has many negative-price hours in spring, so expect 10–40+ days in a 120-day winter window, more in spring).

- [ ] **Step 2: Start backend and curl the route**

```bash
cd /Users/johannesweisser/Projects/Obsyd && .venv/bin/uvicorn backend.main:app --port 8129 &
sleep 3
curl -s "http://localhost:8129/api/power/day-ahead?days=120" | .venv/bin/python -m json.tool | head -40
kill %1
```

Expected: Response has `available: true`, `negative_days` > 0, each data row has `negative_hours` and `negative`.

---

## Task 7: Ruff check + full pytest suite

**Files:** none (verification only)

- [ ] **Step 1: Ruff check backend**

```bash
cd /Users/johannesweisser/Projects/Obsyd && .venv/bin/ruff check backend/ 2>&1
```

Expected: No errors. If any unused-import warnings appear from the refactored ingest, fix them now.

- [ ] **Step 2: Full pytest suite**

```bash
cd /Users/johannesweisser/Projects/Obsyd && .venv/bin/python -m pytest -q 2>&1 | tail -20
```

Expected: All tests PASS, 0 failures.

---

## Task 8: Final commit

- [ ] **Step 1: Verify branch and status**

```bash
git -C /Users/johannesweisser/Projects/Obsyd status
git -C /Users/johannesweisser/Projects/Obsyd log --oneline -5
```

- [ ] **Step 2: Stage any remaining unstaged files and create the final commit**

```bash
git -C /Users/johannesweisser/Projects/Obsyd add -A
git -C /Users/johannesweisser/Projects/Obsyd commit -m "feat(power): negative-price detection (daily min + negative hours) in day-ahead"
```

---

## Self-Review

**Spec coverage check:**

| Requirement | Task |
|---|---|
| `parse_day_ahead_stats(xml) -> {date: {mean, min, max, negative_hours}}` | Task 2 |
| `PowerPriceDaily(date, zone, mean_price, min_price, max_price, negative_hours)` | Task 1 |
| Ingest upserts BOTH EnergyPrice AND PowerPriceDaily | Task 3 |
| Idempotent upserts | Task 3 (`_upsert_daily` checks existing) |
| Route enriched: `close`, `min_price`, `negative_hours`, `negative`, `negative_days`, `latest` | Task 4 |
| Route fallback to EnergyPrice if PowerPriceDaily empty | Task 4 |
| Frontend FlagDot-style red marker for negative days | Task 5 |
| Frontend `negative_days` count in sub-headline | Task 5 |
| Test: A44 XML with negative hour → parser correct | Task 2 |
| Test: ingest writes PowerPriceDaily | Task 3 |
| Test: route enrichment (TestClient + autouse fixture) | Task 4 |
| Local backfill verification | Task 6 |
| Ruff clean | Task 7 |
| Full pytest suite | Task 7 |
| ESLint + npm build | Task 5 |
| Final commit with required message | Task 8 |

**Placeholder scan:** No TBD/TODO/placeholder text. All code blocks are complete.

**Type consistency:** `parse_day_ahead_stats` returns `dict[str, dict]` with keys `mean`, `min`, `max`, `negative_hours` — used consistently in `_upsert_daily`, in the route's `_daily_dict`, and in all test assertions.
