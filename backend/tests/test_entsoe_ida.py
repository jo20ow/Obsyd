"""Intraday-auction parser (backend/power/entsoe_ida.py): sequence attribution,
the A03-curve position arithmetic, and the guards — a series without an auction
number is unattributable, an hourly series would fake the wrong granularity."""
from __future__ import annotations

from backend.power.entsoe_ida import IDA_ZONES, parse_ida_prices

_NS = 'xmlns="urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3"'


def _doc(series: str) -> str:
    return f'<?xml version="1.0"?><Publication_MarketDocument {_NS}>{series}</Publication_MarketDocument>'


def _ts(seq: str | None, resolution: str = "PT15M", points: str = "") -> str:
    seq_el = (
        f"<classificationSequence_AttributeInstanceComponent.position>{seq}"
        "</classificationSequence_AttributeInstanceComponent.position>" if seq else ""
    )
    return (
        f"<TimeSeries>{seq_el}<Period><timeInterval>"
        "<start>2026-09-09T22:00Z</start><end>2026-09-10T22:00Z</end></timeInterval>"
        f"<resolution>{resolution}</resolution>{points}</Period></TimeSeries>"
    )


def _pt(pos: int, price: float) -> str:
    return f"<Point><position>{pos}</position><price.amount>{price}</price.amount></Point>"


def test_sequences_are_split_and_positions_resolved():
    xml = _doc(
        _ts("1", points=_pt(1, 100.0) + _pt(3, 120.0))   # position 2 omitted (A03 curve)
        + _ts("2", points=_pt(1, 90.0))
    )
    out = parse_ida_prices(xml)
    base = 1788991200  # 2026-09-09T22:00Z
    assert out[1] == [(base, 100.0), (base + 1800, 120.0)]  # pos 3 = +2×15min
    assert out[2] == [(base, 90.0)]
    assert 3 not in out


def test_unattributed_and_hourly_series_are_skipped():
    xml = _doc(
        _ts(None, points=_pt(1, 55.0))            # no auction number → unattributable
        + _ts("1", "PT60M", _pt(1, 77.0))          # hourly would fake the granularity
    )
    assert parse_ida_prices(xml) == {}


def test_duplicate_points_average():
    xml = _doc(_ts("1", points=_pt(1, 100.0)) + _ts("1", points=_pt(1, 200.0)))
    assert parse_ida_prices(xml)[1] == [(1788991200, 150.0)]


def test_zone_list_is_probe_backed():
    """IDA_ZONES is a probe result, not an aspiration — see the finding doc.
    If this grows, the finding must grow with it."""
    assert IDA_ZONES == {"ES": "10YES-REE------0"}


def test_freshness_and_catalog_wiring():
    from backend.collectors.freshness import SPECS
    from backend.power.series_catalog import series_label

    assert any(s.key == "ida_prices" for s in SPECS)
    assert "IDA1" in series_label("price.ida1.qh")
