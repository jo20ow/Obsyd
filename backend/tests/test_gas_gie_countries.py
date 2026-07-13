"""`data[]` has two roots, and we only ever read one.

The GIE payload has always been a tree. `_eu_row` took the single row whose code is "eu" and
threw the rest away — which meant Ukraine (77 TWh of storage) and the real post-Brexit GB
were parsed and deleted, every day, since 2023.

The obvious fix is to walk `data[]` instead. That fix is ALSO wrong, and these tests exist
because it is: ALSI carries a third root, `ai`, holding a DUPLICATE of Spain.
"""
from __future__ import annotations

import pytest

from backend.gas.gie import (
    COUNTRY_ROOTS,
    country_rows,
    upsert_lng_countries,
    upsert_storage_countries,
)
from backend.models.gas import GasLngCountry, GasStorage, GasStorageCountry


def _agsi_payload() -> dict:
    """The real shape, trimmed: an `eu` root AND a `ne` root. Verified on disk 2026-06-20."""
    return {
        "data": [
            {
                "code": "eu", "name": "EU", "gasInStorage": "580.1", "full": "51.2",
                "injection": "3100.0", "withdrawal": "40.0",
                "children": [
                    {"code": "DE", "name": "Germany", "gasInStorage": "94.7003",
                     "full": "38.37", "injection": "900.1", "withdrawal": "0",
                     "workingGasVolume": "246.8", "injectionCapacity": "2500.0",
                     "withdrawalCapacity": "3100.0", "trend": "0.36", "status": "E"},
                    # Pre-Brexit GB: present under `eu`, and nothing but dashes.
                    {"code": "GB", "name": "United Kingdom (Pre-Brexit)",
                     "gasInStorage": "-", "full": "-", "injection": "-", "withdrawal": "-"},
                ],
            },
            {
                "code": "ne", "name": "Non-EU",
                "children": [
                    {"code": "UA", "name": "Ukraine", "gasInStorage": "77.0359",
                     "full": "24.07", "injection": "120.0", "withdrawal": "0"},
                    {"code": "GB*", "name": "United Kingdom (Post-Brexit)",
                     "gasInStorage": "1.7912", "full": "18.16"},
                ],
            },
        ]
    }


def _alsi_payload() -> dict:
    """ALSI's THIRD root: `ai` → ES*, a duplicate of a country already under `eu`."""
    return {
        "data": [
            {
                "code": "eu", "name": "EU", "sendOut": "2100.0",
                "children": [
                    {"code": "ES", "name": "Spain", "sendOut": "343.8",
                     "inventory": {"gwh": "12000"}, "dtmi": {"gwh": "35000"}},
                ],
            },
            {"code": "ne", "name": "Non-EU",
             "children": [{"code": "GB*", "name": "United Kingdom (Post-Brexit)",
                           "sendOut": "-", "inventory": {"gwh": "-"}}]},
            {"code": "ai", "name": "Additional Information",
             "children": [{"code": "ES*", "name": "Spain (1)", "sendOut": "395.7"}]},
        ]
    }


# ─── the roots ────────────────────────────────────────────────────────────────


def test_non_eu_root_is_not_dropped():
    """THE bug. Ukraine holds 77 TWh and lives under `ne`. Reading only the `eu` root
    deleted it — and the real GB — from every day of the record since 2023.

    A fixture with only the `eu` root cannot express this bug; it would be no test at all."""
    by_code = {row["code"]: (region, row) for region, row in country_rows(_agsi_payload())}

    assert "UA" in by_code, "Ukraine is under `ne` and must survive"
    region, ua = by_code["UA"]
    assert region == "ne"
    assert ua["gasInStorage"] == "77.0359"


def test_alsi_additional_information_root_is_excluded():
    """The trap in the OBVIOUS fix. "Just walk data[] instead of the eu row" pulls in ALSI's
    `ai` root, which holds ES* "Spain (1)" — a duplicate of the Spain already under `eu`.
    Walking every root double-counts Spain in every LNG total."""
    codes = [row["code"] for _region, row in country_rows(_alsi_payload())]

    assert codes.count("ES") == 1
    assert "ES*" not in codes, "`ai` is a duplicate of Spain, not a country"
    assert "ai" not in COUNTRY_ROOTS


def test_pre_brexit_and_post_brexit_gb_are_different_countries():
    """`eu` carries a hollowed-out "GB (Pre-Brexit)" of pure dashes; the live UK is `GB*`
    under `ne`. Confuse them and Britain reads as an empty country."""
    by_code = {row["code"]: row for _r, row in country_rows(_agsi_payload())}

    assert by_code["GB"]["gasInStorage"] == "-", "the pre-Brexit row is a placeholder"
    assert by_code["GB*"]["gasInStorage"] == "1.7912"


# ─── the writes ───────────────────────────────────────────────────────────────


def test_country_rows_carry_the_capacity_the_eu_schema_never_had(db_session):
    upsert_storage_countries(db_session, "2026-06-20", _agsi_payload())
    db_session.commit()

    de = db_session.get(GasStorageCountry, ("2026-06-20", "DE"))
    assert de.stock_twh == pytest.approx(94.7003)
    assert de.fill_pct == pytest.approx(38.37)
    assert de.working_gas_twh == pytest.approx(246.8), "the denominator that makes TWh readable"
    assert de.withdrawal_capacity_gwh == pytest.approx(3100.0)
    assert de.region == "eu"


def test_the_dash_marker_becomes_none_not_zero(db_session):
    """A country reporting "-" has no data. Storing it as 0.0 would say it is empty."""
    upsert_storage_countries(db_session, "2026-06-20", _agsi_payload())
    db_session.commit()

    gb = db_session.get(GasStorageCountry, ("2026-06-20", "GB"))
    assert gb.stock_twh is None and gb.fill_pct is None


def test_lng_countries_land_without_the_duplicate(db_session):
    upsert_lng_countries(db_session, "2026-06-20", _alsi_payload())
    db_session.commit()

    rows = db_session.query(GasLngCountry).filter_by(date="2026-06-20").all()
    assert {r.country for r in rows} == {"ES", "GB*"}
    es = db_session.get(GasLngCountry, ("2026-06-20", "ES"))
    assert es.send_out_gwh == pytest.approx(343.8), "the eu figure, not the ai duplicate"
    assert es.inventory_twh == pytest.approx(12.0), "GWh → TWh"


def test_reprocessing_the_same_day_twice_does_not_duplicate(db_session):
    for _ in range(2):
        upsert_storage_countries(db_session, "2026-06-20", _agsi_payload())
    db_session.commit()

    assert db_session.query(GasStorageCountry).filter_by(date="2026-06-20").count() == 4


# ─── the thing that must NOT change ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_eu_aggregate_is_untouched(db_session, monkeypatch):
    """The residual engine (`gas/balance.py::compute_balance`) reads GasStorage.injection and
    .withdrawal as its `actual_delta` — the number the code itself calls "this is the
    product". Re-basing it while adding a country layer would move the product silently."""
    import backend.gas.gie as gie

    async def _fake_fetch(base, source, day, *, overwrite=False):
        return _agsi_payload()

    monkeypatch.setattr(gie, "_fetch_day", _fake_fetch)
    await gie.ingest_storage(db_session, ["2026-06-20"])

    eu = db_session.get(GasStorage, "2026-06-20")
    assert eu.stock_twh == pytest.approx(580.1)
    assert eu.injection_gwh == pytest.approx(3100.0)
    assert eu.fill_pct == pytest.approx(51.2)
    # …and the countries arrived alongside it, from the same payload, in the same pass.
    assert db_session.query(GasStorageCountry).count() == 4


# ─── the reprocessor ──────────────────────────────────────────────────────────


def test_the_reprocessor_counts_missing_days_instead_of_fetching(db_session, monkeypatch):
    """Zero API calls is the whole premise: the payloads are already on disk. A cache miss
    must be COUNTED and reported, never quietly turned into three years of GIE requests."""
    from backend.gas import raw_cache
    from backend.scripts import backfill_gie_countries as bf

    monkeypatch.setattr(raw_cache, "read_cached", lambda *a, **k: None)

    def _explode(*a, **k):
        raise AssertionError("the reprocessor must never hit the network")

    monkeypatch.setattr("httpx.AsyncClient", _explode)

    from datetime import date

    out = bf.run(db_session, date(2026, 6, 1), date(2026, 6, 3))
    assert out["days"] == 3
    assert out["missing_cache"] == 6, "3 days × 2 sources, all counted"
    assert out["storage_rows"] == 0


def test_the_reprocessor_writes_the_countries_it_finds(db_session, monkeypatch):
    from datetime import date

    from backend.gas import raw_cache
    from backend.scripts import backfill_gie_countries as bf

    monkeypatch.setattr(
        raw_cache, "read_cached",
        lambda source, key, day: _agsi_payload() if source == "agsi" else _alsi_payload(),
    )
    out = bf.run(db_session, date(2026, 6, 20), date(2026, 6, 20))

    assert out["missing_cache"] == 0
    assert out["storage_rows"] == 4
    assert out["lng_rows"] == 2, "ES + GB*, never the ai duplicate"
    assert db_session.get(GasStorageCountry, ("2026-06-20", "UA")).stock_twh == pytest.approx(77.0359)
