"""CO₂ intensity (backend/power/co2.py): the arithmetic is a weighted mean; the
honesty lives in the edges. Every test guards one way the estimate could quietly
become a lie — a fuel with no factor silently counted, storage double-counted,
a near-empty hour published as a reading, or upstream restatements ledgered
twice through the derived series.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.models.energy import PowerRevision, SeriesDim
from backend.power.co2 import (
    DIRECT_G_PER_KWH,
    EXCLUDED_STORAGE_PSRS,
    LIFECYCLE_G_PER_KWH,
    MIN_TOTAL_MW,
    SERIES_DIRECT,
    SERIES_LIFECYCLE,
    compute_and_store_range,
)
from backend.power.entsoe_grid import PSR_LABELS
from backend.power.hourly_store import (
    REVISION_EXCLUDED_PREFIXES,
    read_hourly,
    upsert_hourly,
)

_T0 = int(datetime(2026, 3, 2, tzinfo=timezone.utc).timestamp())  # 00:00 UTC
_END = _T0 + 24 * 3600


def _seed(db, series: str, values: dict[int, float]) -> None:
    upsert_hourly(db, series, "DE_LU", [(_T0 + h * 3600, v) for h, v in values.items()], unit="MW")


# ─── the factor table itself ──────────────────────────────────────────────────


def test_factor_tables_cover_every_generation_psr():
    """Every PSR the mix parser can emit is either priced or explicitly excluded
    as storage — a new ENTSO-E code must force a conscious decision here, never
    a silent zero."""
    generation_psrs = set(PSR_LABELS) - EXCLUDED_STORAGE_PSRS
    assert set(LIFECYCLE_G_PER_KWH) == generation_psrs
    assert set(DIRECT_G_PER_KWH) == generation_psrs


def test_storage_psrs_are_the_two_discharge_codes():
    assert EXCLUDED_STORAGE_PSRS == {"B10", "B25"}


# ─── the weighted mean ────────────────────────────────────────────────────────


def test_intensity_is_generation_weighted_mean(db_session):
    _seed(db_session, "gen.B04", {0: 300.0})   # gas: 490 / 370
    _seed(db_session, "gen.B19", {0: 700.0})   # wind onshore: 11 / 0
    compute_and_store_range(db_session, "DE_LU", _T0, _END)

    (ts, lifecycle), = read_hourly(db_session, SERIES_LIFECYCLE, "DE_LU")
    (_, direct), = read_hourly(db_session, SERIES_DIRECT, "DE_LU")
    assert ts == _T0
    assert lifecycle == (300 * 490 + 700 * 11) / 1000
    assert direct == (300 * 370) / 1000


def test_storage_discharge_moves_nothing(db_session):
    """Excluding B10/B25 ≡ pricing discharge at the hour's own average — the
    intensity must be identical with and without the storage series present."""
    _seed(db_session, "gen.B04", {0: 300.0})
    _seed(db_session, "gen.B19", {0: 700.0})
    _seed(db_session, "gen.B10", {0: 500.0})
    _seed(db_session, "gen.B25", {0: 100.0})
    compute_and_store_range(db_session, "DE_LU", _T0, _END)
    (_, lifecycle), = read_hourly(db_session, SERIES_LIFECYCLE, "DE_LU")
    assert lifecycle == (300 * 490 + 700 * 11) / 1000


def test_unrecognised_psr_is_skipped_not_guessed(db_session):
    _seed(db_session, "gen.B04", {0: 300.0})
    _seed(db_session, "gen.B23", {0: 1000.0})  # network element, not a fuel
    compute_and_store_range(db_session, "DE_LU", _T0, _END)
    (_, lifecycle), = read_hourly(db_session, SERIES_LIFECYCLE, "DE_LU")
    assert lifecycle == 490.0


def test_negative_generation_is_dropped_from_both_sums(db_session):
    _seed(db_session, "gen.B04", {0: -50.0})   # metering correction
    _seed(db_session, "gen.B19", {0: 700.0})
    compute_and_store_range(db_session, "DE_LU", _T0, _END)
    (_, lifecycle), = read_hourly(db_session, SERIES_LIFECYCLE, "DE_LU")
    assert lifecycle == 11.0


def test_near_empty_hour_is_not_a_reading(db_session):
    """An hour below the floor is publication-lag noise; publishing a 'grid
    intensity' computed from 10 MW of solar would be an invented claim."""
    assert 10.0 < MIN_TOTAL_MW
    _seed(db_session, "gen.B16", {0: 10.0})
    _seed(db_session, "gen.B16", {1: 10.0})
    _seed(db_session, "gen.B04", {1: 300.0})   # hour 1 clears the floor
    compute_and_store_range(db_session, "DE_LU", _T0, _END)
    rows = read_hourly(db_session, SERIES_LIFECYCLE, "DE_LU")
    assert [ts for ts, _ in rows] == [_T0 + 3600]


# ─── restatements and the ledger ──────────────────────────────────────────────


def test_recompute_absorbs_restatement_without_ledgering_itself(db_session):
    """The mix restates → the derived series restates on recompute, but writes
    no revision rows of its own (REVISION_EXCLUDED_PREFIXES): ledgering it would
    double-count the upstream revision the gen series already recorded."""
    assert any(SERIES_LIFECYCLE.startswith(p) for p in REVISION_EXCLUDED_PREFIXES)

    _seed(db_session, "gen.B04", {0: 300.0})
    _seed(db_session, "gen.B19", {0: 700.0})
    compute_and_store_range(db_session, "DE_LU", _T0, _END)
    _seed(db_session, "gen.B04", {0: 600.0})   # the restatement
    compute_and_store_range(db_session, "DE_LU", _T0, _END)

    (_, lifecycle), = read_hourly(db_session, SERIES_LIFECYCLE, "DE_LU")
    assert lifecycle == (600 * 490 + 700 * 11) / 1300

    co2_sids = [
        sid for (sid,) in db_session.query(SeriesDim.id).filter(SeriesDim.key.like("co2.%")).all()
    ]
    assert co2_sids  # both series exist as dims
    assert (
        db_session.query(PowerRevision).filter(PowerRevision.series_id.in_(co2_sids)).count() == 0
    )


# ─── wiring ───────────────────────────────────────────────────────────────────


def test_catalog_and_freshness_wiring():
    from backend.collectors.freshness import SPECS
    from backend.power.series_catalog import GROUP_LABELS, series_label

    assert series_label(SERIES_LIFECYCLE) != SERIES_LIFECYCLE  # labelled, not raw key
    assert "(est.)" in series_label(SERIES_LIFECYCLE)
    assert "(est.)" in series_label(SERIES_DIRECT)
    assert "co2" in GROUP_LABELS
    spec = next(s for s in SPECS if s.key == "co2_intensity")
    assert spec.hourly_series == SERIES_LIFECYCLE
