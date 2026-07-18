import pytest
import responses

from obsyd import Obsyd, ObsydBadRequest, ObsydRateLimited
from tests.conftest import API, BASE, HOURLY_CSV, ZONES_JSON


@pytest.fixture
def no_sleep(monkeypatch):
    delays = []
    monkeypatch.setattr("obsyd.time.sleep", lambda s: delays.append(s))
    return delays


@responses.activate
def test_retries_on_429_then_succeeds(no_sleep):
    client = Obsyd(base_url=BASE, max_retries=3)
    responses.get(f"{API}/zones", json=ZONES_JSON)
    responses.get(f"{API}/series", json={"detail": "slow down"}, status=429)
    responses.get(f"{API}/series", json={"detail": "slow down"}, status=429)
    responses.get(f"{API}/series", body=HOURLY_CSV, content_type="text/csv")
    df = client.series("price.dayahead", "DE_LU")
    assert len(df) == 2
    assert len(no_sleep) == 2
    assert no_sleep[1] > no_sleep[0]  # exponential: 2^0+j < 2^1+j


@responses.activate
def test_exhausted_retries_raise_rate_limited(no_sleep):
    client = Obsyd(base_url=BASE, max_retries=2)
    responses.get(f"{API}/zones", json=ZONES_JSON)
    for _ in range(3):
        responses.get(f"{API}/series", json={"detail": "slow down"}, status=429)
    with pytest.raises(ObsydRateLimited):
        client.series("price.dayahead", "DE_LU")
    assert len(no_sleep) == 2


@responses.activate
def test_no_retry_on_400(no_sleep):
    client = Obsyd(base_url=BASE, max_retries=3)
    responses.get(f"{API}/zones", json=ZONES_JSON)
    responses.get(f"{API}/series", json={"detail": "Invalid datetime"}, status=400)
    with pytest.raises(ObsydBadRequest):
        client.series("price.dayahead", "DE_LU", start="not-a-date")
    assert no_sleep == []


@responses.activate
def test_retry_honors_retry_after(no_sleep):
    client = Obsyd(base_url=BASE, max_retries=1)
    responses.get(f"{API}/zones", json=ZONES_JSON)
    responses.get(f"{API}/series", json={"detail": "slow"}, status=429, headers={"Retry-After": "7"})
    responses.get(f"{API}/series", body=HOURLY_CSV, content_type="text/csv")
    client.series("price.dayahead", "DE_LU")
    assert no_sleep[0] >= 7
