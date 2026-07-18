import pandas as pd
import pytest
import responses

import obsyd as obsyd_mod
from tests.conftest import API, DAILY_CSV, GENMIX_CSV, HOURLY_CSV, SNAPSHOT_JSON


@responses.activate
def test_series_hourly_frame(client, zones_mock):
    zones_mock(responses)
    responses.get(
        f"{API}/series",
        body=HOURLY_CSV,
        content_type="text/csv",
        headers={"X-Attribution": "ENTSO-E; Energy-Charts CC BY 4.0; GIE"},
    )
    df = client.series("price.dayahead", "DE_LU", start="2026-07-01")
    assert list(df.columns) == ["value"]
    assert isinstance(df.index, pd.DatetimeIndex) and str(df.index.tz) == "UTC"
    assert df.attrs["series"] == "price.dayahead" and df.attrs["zone"] == "DE_LU"
    assert "ENTSO-E" in df.attrs["attribution"]


@responses.activate
def test_series_daily_keeps_hours_column(client, zones_mock):
    zones_mock(responses)
    responses.get(f"{API}/series", body=DAILY_CSV, content_type="text/csv")
    df = client.series("price.dayahead", "DE_LU", resolution="daily")
    assert list(df.columns) == ["value", "hours"]
    assert df["hours"].tolist() == [24, 12]


@responses.activate
def test_series_sends_useragent_and_iso_dates(client, zones_mock):
    import datetime as dt

    zones_mock(responses)
    responses.get(f"{API}/series", body=HOURLY_CSV, content_type="text/csv")
    client.series("price.dayahead", "DE_LU", start=dt.date(2026, 7, 1))
    req = responses.calls[-1].request
    assert f"obsyd-python/{obsyd_mod.__version__}" in req.headers["User-Agent"]
    assert "start=2026-07-01" in req.url


@responses.activate
def test_unknown_zone_raises_with_suggestion(client, zones_mock):
    zones_mock(responses)
    with pytest.raises(ValueError, match="DE_LU"):
        client.series("price.dayahead", "DE_LV")


@responses.activate
def test_snapshot_wide_frame(client):
    responses.get(f"{API}/snapshot", json=SNAPSHOT_JSON)
    df = client.snapshot()
    assert set(df.columns) == {"DE_LU", "FR"}
    assert df.index.name == "datetime_utc" and str(df.index.tz) == "UTC"
    assert pd.isna(df["FR"].iloc[1])
    assert df.attrs["unit"] == "EUR/MWh"


@responses.activate
def test_genmix_wide_frame(client, zones_mock):
    zones_mock(responses)
    responses.get(f"{API}/genmix", body=GENMIX_CSV, content_type="text/csv")
    df = client.genmix("DE_LU")
    assert set(df.columns) == {"Solar", "Fossil Gas"}
    assert df.attrs["unit"] == "MW"


@responses.activate
def test_capacity_and_units_attrs(client, zones_mock):
    zones_mock(responses)
    responses.get(
        f"{API}/capacity",
        json={"available": True, "zone": "DE_LU", "year": 2026, "unit": "MW",
              "total_mw": 1000.0, "data": [{"psr_type": "Solar", "capacity_mw": 600.0}]},
    )
    cap = client.capacity("DE_LU")
    assert cap.attrs["total_mw"] == 1000.0 and len(cap) == 1

    responses.get(
        f"{API}/units",
        json={"available": True, "zone": "DE_LU", "year": 2026, "count": 1,
              "published_capacity_mw": 500.0, "note": "not the installed fleet",
              "units": [{"unit_eic": "X", "name": "Plant", "psr_type": "B04",
                         "fuel": "Fossil Gas", "nominal_mw": 500.0}]},
    )
    units = client.units("DE_LU")
    assert units.attrs["note"] == "not the installed fleet" and len(units) == 1


@responses.activate
def test_series_multi_skips_empty_zone_with_warning(client, zones_mock, monkeypatch):
    zones_mock(responses)
    monkeypatch.setattr("time.sleep", lambda s: None)
    responses.get(f"{API}/series", body=HOURLY_CSV, content_type="text/csv")
    responses.get(f"{API}/series", body="datetime_utc,value\n", content_type="text/csv")
    with pytest.warns(UserWarning, match="FR"):
        df = client.series_multi("price.dayahead", ["DE_LU", "FR"])
    assert list(df.columns) == ["DE_LU"]
