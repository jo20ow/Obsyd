"""Eurostat nrg_cb_gasm JSON-stat parsing tests."""

from __future__ import annotations

from backend.gas import eurostat


def _payload(month_to_tj: dict[str, float]) -> dict:
    months = list(month_to_tj)
    return {
        "value": {str(i): month_to_tj[m] for i, m in enumerate(months)},
        "dimension": {"time": {"category": {"index": {m: i for i, m in enumerate(months)}}}},
    }


def test_parse_consumption_tj_to_gwh():
    out = eurostat.parse_consumption(_payload({"2026-01": 100000.0, "2026-02": 50000.0}))
    assert abs(out["2026-01"] - 27777.8) < 0.5   # 100000 TJ × 0.277778
    assert abs(out["2026-02"] - 13888.9) < 0.5


def test_parse_consumption_skips_nulls_and_bad_shape():
    p = _payload({"2026-01": 100000.0})
    p["value"]["1"] = None  # a null value at a nonexistent-time index is ignored
    assert "2026-01" in eurostat.parse_consumption(p)
    assert eurostat.parse_consumption({}) == {}
    assert eurostat.parse_consumption({"value": {}}) == {}


def test_eu_monthly_total_sums_countries():
    per_country = {
        "DE": {"2026-01": 1000.0, "2026-02": 800.0},
        "FR": {"2026-01": 500.0, "2026-02": 400.0},
    }
    total = eurostat.eu_monthly_total(per_country)
    assert total["2026-01"] == 1500.0
    assert total["2026-02"] == 1200.0


def test_eu27_uses_eurostat_greece_code():
    assert "EL" in eurostat.EU27   # Eurostat uses EL, not GR
    assert "GR" not in eurostat.EU27
