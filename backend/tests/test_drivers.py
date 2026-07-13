"""The driver card: what co-occurs with today's price, ranked.

The tests exist mostly to hold the LANGUAGE honest. A driver card is one careless
sentence away from being a causal claim, and one careless mean away from being a
forecast. So: co-occurrence wording, a sample-size floor on the analogs, and no
driver invented where the data is absent.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.models.energy import PowerFlow, PowerGrid, PowerPriceDaily
from backend.power.drivers import (
    ANALOG_MIN_N,
    compute_drivers,
    net_position_by_day,
)

_TODAY = date(2026, 7, 12)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from backend.main import app

    app.dependency_overrides.clear()


def _client(db):
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


# Real baselines have variance; a constant one has no z at all (and that case is
# pinned separately below). The default seed therefore wobbles.
def _seed(db, zone="DE_LU", days=60, *, price=lambda i: 60.0 + (i % 7), load=50_000.0,
          wind=lambda i: 10_000.0 + (i % 5) * 400, solar=lambda i: 5_000.0 + (i % 3) * 200):
    for i in range(days):
        d = (_TODAY - timedelta(days=days - 1 - i)).isoformat()
        w, s = wind(i), solar(i)
        db.add(PowerGrid(date=d, zone=zone, load_mw=load, wind_mw=w, solar_mw=s,
                         residual_mw=load - w - s))
        db.add(PowerPriceDaily(date=d, zone=zone, mean_price=price(i),
                               min_price=0.0, max_price=200.0, negative_hours=0))
    db.commit()


def test_a_calm_day_says_nothing_is_far_from_its_norm(db_session):
    """Today lands ON each baseline's mean — the card must then say so plainly
    rather than dressing up noise as a driver."""
    _seed(db_session,
          price=lambda i: 63.0 if i == 59 else 60.0 + (i % 7),        # mean of 60..66
          wind=lambda i: 10_800.0 if i == 59 else 10_000.0 + (i % 5) * 400,
          solar=lambda i: 5_200.0 if i == 59 else 5_000.0 + (i % 3) * 200)
    out = compute_drivers(db_session, "DE_LU", today=_TODAY)
    assert out["available"] is True
    assert "nothing is far from its norm" in out["headline"], out["headline"]
    assert all(not d["notable"] for d in out["drivers"])


def test_the_headline_is_co_occurrence_never_causation(db_session):
    """The whole point of the card, and the one line where Posture B can die."""
    # Wind collapses on the last day; price spikes with it.
    _seed(db_session, wind=lambda i: 2_000.0 if i == 59 else 12_000.0 + (i % 5) * 300,
          price=lambda i: 190.0 if i == 59 else 55.0 + (i % 4))
    out = compute_drivers(db_session, "DE_LU", today=_TODAY)
    h = out["headline"]

    assert " WHILE " in h
    for forbidden in ("because", "caused", "due to", "will ", "expect"):
        assert forbidden not in h.lower(), f"causal/predictive wording leaked: {forbidden!r}"
    assert "wind generation is" in h.lower() and "below its norm" in h.lower()


def test_drivers_are_ranked_by_distance_from_their_own_norm(db_session):
    # Wind moves a lot on the last day, solar barely.
    _seed(db_session,
          wind=lambda i: 1_000.0 if i == 59 else 12_000.0 + (i % 5) * 300,
          solar=lambda i: 5_000.0 if i == 59 else 5_000.0 + (i % 3) * 100)
    out = compute_drivers(db_session, "DE_LU", today=_TODAY)
    zs = [abs(d["z"]) for d in out["drivers"]]
    assert zs == sorted(zs, reverse=True), "most-deviant driver first"
    assert out["drivers"][0]["key"] in ("wind", "residual")


def test_analogs_refuse_to_quote_a_norm_from_too_few_days(db_session):
    """A mean of four days dressed up as a norm is worse than no number."""
    # Every day has a different residual → today's ±1 GW band catches almost nothing.
    _seed(db_session, days=20, wind=lambda i: 1_000.0 * i)
    out = compute_drivers(db_session, "DE_LU", today=_TODAY)
    assert out["analogs"]["enough"] is False
    assert out["analogs"]["n"] < ANALOG_MIN_N
    assert "too few" in out["analogs"]["reason"]


def test_analogs_report_what_similar_days_cleared(db_session):
    """Past tense, with the sample size — a statement about the record."""
    _seed(db_session, days=60, price=lambda i: 100.0 if i < 59 else 142.0,
          wind=lambda i: 10_000.0, solar=lambda i: 5_000.0)  # same residual every day
    out = compute_drivers(db_session, "DE_LU", today=_TODAY)
    a = out["analogs"]
    assert a["enough"] is True and a["n"] >= ANALOG_MIN_N
    assert a["mean_price"] == 100.0, "what the similar days DID clear — past tense"
    assert a["p10"] == 100.0 and a["p90"] == 100.0
    assert out["price"]["value"] == 142.0


def test_no_flows_no_net_position_driver(db_session):
    """Country-level flows: a sub-zone gets no net-position driver rather than a
    fabricated zero."""
    _seed(db_session)
    out = compute_drivers(db_session, "DE_LU", today=_TODAY)
    assert all(d["key"] != "net_position" for d in out["drivers"])


def test_net_position_sums_both_border_directions(db_session):
    _seed(db_session)
    day = _TODAY.isoformat()
    # DE_LU exports 1 GW to FR, and imports 400 MW from BE (BE→DE_LU is +400 for BE)
    db_session.add(PowerFlow(date=day, from_zone="DE_LU", to_zone="FR", net_mw=1_000.0))
    db_session.add(PowerFlow(date=day, from_zone="BE", to_zone="DE_LU", net_mw=400.0))
    db_session.commit()

    net = net_position_by_day(db_session, "DE_LU", day)
    assert net[day] == 600.0, "1000 exported − 400 imported"


def test_unknown_zone_is_honest(db_session):
    out = compute_drivers(db_session, "ZZ", today=_TODAY)
    assert out["available"] is False and "Unknown zone" in out["reason"]


def test_empty_zone_is_honest(db_session):
    out = compute_drivers(db_session, "FR", today=_TODAY)
    assert out["available"] is False and "No price/grid history" in out["reason"]


def test_route(db_session):
    _seed(db_session)
    body = _client(db_session).get("/api/power/drivers?zone=DE_LU").json()
    assert body["available"] is True
    assert "no driver is claimed to have caused the price" in body["note"]


def test_a_flat_baseline_yields_a_value_but_no_deviation(db_session):
    """Zero variance → no z exists. The driver must still report its VALUE (a
    fact) while claiming no deviation (which would be invented), and it must sink
    below the drivers that do have a baseline."""
    _seed(db_session, days=60,
          wind=lambda i: 9_000.0,                       # perfectly flat → no z
          solar=lambda i: 5_000.0 + (i % 5) * 300)      # varies → has a z
    out = compute_drivers(db_session, "DE_LU", today=_TODAY)

    wind = next(d for d in out["drivers"] if d["key"] == "wind")
    assert wind["value"] == 9_000.0
    assert wind["z"] is None and wind["notable"] is False
    assert out["drivers"][-1]["key"] == "wind", "no baseline → ranked last, not first"


def test_net_position_is_worded_as_import_or_export_not_a_signed_number(db_session):
    """"net export position is -7.5 GW" makes the reader decode a sign. Say the
    word: the zone is importing 7.5 GW."""
    _seed(db_session)
    for i in range(40):
        d = (_TODAY - timedelta(days=39 - i)).isoformat()
        # normally a big exporter; today it flips to importing
        net = -7_500.0 if i == 39 else 3_000.0 + (i % 5) * 200
        db_session.add(PowerFlow(date=d, from_zone="DE_LU", to_zone="FR", net_mw=net))
    db_session.commit()

    out = compute_drivers(db_session, "DE_LU", today=_TODAY)
    assert "the zone is importing 7.5 GW" in out["headline"], out["headline"]
    assert "-7.5" not in out["headline"]


def test_an_immaterial_outage_stays_out_of_the_headline(db_session):
    """FR reported "0.1 GW is forced offline (0% of the fleet)" in prod — noise in
    a sentence that is supposed to be signal. It belongs in the table, not the
    headline."""
    from datetime import datetime, timezone
    from datetime import timedelta as td

    from backend.models.energy import InstalledCapacity, PowerOutage

    _seed(db_session)
    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%dT%H:%MZ"
    db_session.add(InstalledCapacity(zone="DE_LU", year=2026, psr_type="Solar",
                                     capacity_mw=100_000.0))
    db_session.add(PowerOutage(mrid="tiny", revision=1, doc_type="A77", zone="DE_LU",
                               business_type="A54", psr_type="B14", unit_name="U",
                               unit_eic="11W", location="DE",
                               nominal_mw=100.0, available_mw=0.0,
                               start_utc=(now - td(days=1)).strftime(fmt),
                               end_utc=(now + td(days=1)).strftime(fmt), status="active"))
    db_session.commit()

    out = compute_drivers(db_session, "DE_LU", today=_TODAY)
    assert out["outage"]["value"] == 100.0, "still reported as a level"
    assert "forced offline" not in out["headline"], out["headline"]
