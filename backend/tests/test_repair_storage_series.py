"""The one-off repair of the storage rows the old A75 parser corrupted.

The parser fix (2026-07-12) stops NEW rows being wrong; this script rewrites the
HISTORY. It must (a) read only from the raw cache — never the network, (b) touch
ONLY storage rows, (c) leave a zone-month with no cached document alone and SAY
so rather than silently pretending it was repaired.
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.gas import raw_cache
from backend.models.energy import PowerGenMix
from backend.power.hourly_store import read_hourly, upsert_day_hours
from backend.scripts import repair_storage_series as rss
from backend.tests.test_power_grid import _a75_gen, _gen_ts

_MONTH = date(2026, 4, 1)
_DE_EIC = "10Y1001A1001A82H"


def _cache_a75(tmp_path, monkeypatch, xml: str, eic: str = _DE_EIC) -> None:
    monkeypatch.setattr(raw_cache, "DATA_ROOT", tmp_path)
    raw_cache.write_cached(rss.CACHE_SOURCE, f"{eic}_{_MONTH:%Y-%m}", _MONTH, {"xml": xml})


def _doc_with_pumping() -> str:
    return _a75_gen(
        _gen_ts("B10", "2026-04-01T00:00Z", 1_253.0, direction="in")
        + _gen_ts("B10", "2026-04-01T00:00Z", 1_579.0, direction="out")
        + _gen_ts("B16", "2026-04-01T00:00Z", 8_000.0)
    )


def test_repairs_the_averaged_value_and_adds_the_pumping_series(db_session, tmp_path, monkeypatch):
    # The corrupted state the old parser left behind: one averaged B10 number,
    # counted as generation, and no consumption series at all.
    upsert_day_hours(db_session, "gen.B10", "DE_LU", {"2026-04-01": {h: 1_416.0 for h in range(24)}},
                     unit="MW")
    db_session.add(PowerGenMix(date="2026-04-01", zone="DE_LU",
                               psr_type="Hydro Pumped Storage", gen_mw=1_416.0))
    db_session.commit()
    _cache_a75(tmp_path, monkeypatch, _doc_with_pumping())

    res = rss.repair_zone_month(db_session, "DE_LU", _DE_EIC, _MONTH)
    assert res["storage"] is True and res["codes"] == ["B10"]

    assert read_hourly(db_session, "gen.B10", "DE_LU")[0][1] == 1_253.0
    assert read_hourly(db_session, "consumption.B10", "DE_LU")[0][1] == 1_579.0
    mix = db_session.query(PowerGenMix).filter_by(
        date="2026-04-01", zone="DE_LU", psr_type="Hydro Pumped Storage").one()
    assert mix.gen_mw == 1_253.0


def test_leaves_non_storage_series_untouched(db_session, tmp_path, monkeypatch):
    """Only psrTypes published in BOTH directions were corrupted. Everything else
    must be left exactly as it is — this is a repair, not a re-ingest."""
    db_session.add(PowerGenMix(date="2026-04-01", zone="DE_LU",
                               psr_type="Solar", gen_mw=1.0))  # deliberately absurd
    db_session.commit()
    _cache_a75(tmp_path, monkeypatch, _doc_with_pumping())

    rss.repair_zone_month(db_session, "DE_LU", _DE_EIC, _MONTH)

    solar = db_session.query(PowerGenMix).filter_by(
        date="2026-04-01", zone="DE_LU", psr_type="Solar").one()
    assert solar.gen_mw == 1.0, "the repair must not rewrite series it did not corrupt"
    assert read_hourly(db_session, "gen.B16", "DE_LU") == []


def test_document_without_pumping_is_a_no_op(db_session, tmp_path, monkeypatch):
    _cache_a75(tmp_path, monkeypatch,
               _a75_gen(_gen_ts("B16", "2026-04-01T00:00Z", 8_000.0)))
    res = rss.repair_zone_month(db_session, "DE_LU", _DE_EIC, _MONTH)
    assert res == {"cached": True, "storage": False}


def test_missing_cache_is_reported_not_fetched(db_session, tmp_path, monkeypatch):
    """No cached document → no repair, and the run must COUNT it. Silently
    skipping would leave wrong rows behind while claiming success."""
    monkeypatch.setattr(raw_cache, "DATA_ROOT", tmp_path)  # empty cache
    res = rss.repair_zone_month(db_session, "DE_LU", _DE_EIC, _MONTH)
    assert res == {"cached": False}

    total = rss.run(db_session, date(2026, 4, 1), date(2026, 4, 30))
    assert total["missing_cache"] == total["zone_months"] > 0
    assert total["repaired"] == 0


def test_run_counts_repaired_zone_months(db_session, tmp_path, monkeypatch):
    _cache_a75(tmp_path, monkeypatch, _doc_with_pumping())
    total = rss.run(db_session, date(2026, 4, 1), date(2026, 4, 30))
    assert total["repaired"] == 1  # only DE_LU has a cached document
    assert total["hourly"] == 48   # 24 generation + 24 pumping points
    assert total["daily"] == 1


def test_dry_run_writes_nothing(db_session, tmp_path, monkeypatch):
    _cache_a75(tmp_path, monkeypatch, _doc_with_pumping())
    res = rss.repair_zone_month(db_session, "DE_LU", _DE_EIC, _MONTH, dry_run=True)
    assert res["storage"] is True
    assert read_hourly(db_session, "gen.B10", "DE_LU") == []
    assert db_session.query(PowerGenMix).count() == 0


@pytest.mark.parametrize("codes,expected", [
    ({"B10": 1.0, "B10_CONS": 2.0, "B16": 3.0}, {"B10"}),
    ({"B16": 3.0}, set()),
])
def test_storage_psrs_detects_only_two_directional_types(codes, expected):
    assert rss.storage_psrs({"2026-04-01": codes}) == expected
