"""SMARD congestion costs (backend/power/smard.py): the German-format CSV
parser and its guards — header-order independence, the "-" not-yet-published
convention, and the wiring."""
from __future__ import annotations

import pytest

from backend.power.smard import parse_costs_csv, ingest_congestion_costs
from backend.power import smard
from backend.power.hourly_store import read_hourly

CSV = (
    "﻿Datum von;Datum bis;ÜNB-Netzsicherheit [€] Originalauflösungen;"
    "Countertrading [€] Originalauflösungen\n"
    "01.07.2022;01.08.2022;244.209.025,99;57.501.213,71\n"
    "01.05.2026;01.06.2026;70.527.695,93;2.439.879,48\n"
    "01.06.2026;01.07.2026;-;-\n"
)


def test_parser_handles_german_numbers_bom_and_unpublished_months():
    rows = parse_costs_csv(CSV)
    assert len(rows) == 3
    ts0, sec0, ct0 = rows[0]
    assert ts0 == 1656633600  # 2022-07-01T00:00Z
    assert sec0 == 244209025.99 and ct0 == 57501213.71
    assert rows[2][1] is None and rows[2][2] is None  # "-" = not published, never 0


def test_parser_reads_header_order_not_position():
    swapped = CSV.replace(
        "Datum von;Datum bis;ÜNB-Netzsicherheit [€] Originalauflösungen;Countertrading [€] Originalauflösungen",
        "Datum von;Datum bis;Countertrading [€] X;ÜNB-Netzsicherheit [€] X",
    )
    rows = parse_costs_csv(swapped)
    assert rows[0][1] == 57501213.71  # security column found by NAME
    assert rows[0][2] == 244209025.99


def test_unexpected_header_raises_instead_of_guessing():
    with pytest.raises(ValueError):
        parse_costs_csv("Datum von;Etwas anderes\n01.07.2022;1,0\n")


@pytest.mark.asyncio
async def test_ingest_writes_both_series_and_skips_unpublished(db_session, monkeypatch):
    async def fake_cache(source, key, dt, coro, *, overwrite=False):
        return {"csv": CSV}

    monkeypatch.setattr(smard.raw_cache, "fetch_or_cache", fake_cache)
    result = await ingest_congestion_costs(db_session)
    assert result["months"] == 2
    sec = read_hourly(db_session, "congestion.cost.security", "DE_LU")
    ct = read_hourly(db_session, "congestion.cost.countertrading", "DE_LU")
    assert len(sec) == 2 and len(ct) == 2  # the "-" month wrote nothing


def test_wiring():
    from backend.collectors.freshness import SPECS
    from backend.power.series_catalog import GROUP_LABELS, series_label

    assert "Congestion" in series_label("congestion.cost.security")
    assert "congestion" in GROUP_LABELS
    assert any(s.key == "smard_congestion" for s in SPECS)
