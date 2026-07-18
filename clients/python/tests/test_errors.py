import pytest
import responses

from obsyd import ObsydBadRequest, ObsydNoData, ObsydRateLimited, ObsydServerError
from tests.conftest import API, NO_DATA_JSON


@responses.activate
def test_400_raises_bad_request_with_detail(client, zones_mock):
    zones_mock(responses)
    responses.get(f"{API}/series", json={"detail": "start must be before end."}, status=400)
    with pytest.raises(ObsydBadRequest) as e:
        client.series("price.dayahead", "DE_LU", start="2026-02-01", end="2026-01-01")
    assert e.value.status == 400 and "before end" in e.value.detail


@responses.activate
def test_available_false_json_raises_no_data(client):
    responses.get(f"{API}/snapshot", json=NO_DATA_JSON)
    with pytest.raises(ObsydNoData) as e:
        client.snapshot()
    assert e.value.reason == "no rows yet"


@responses.activate
def test_empty_csv_raises_no_data(client, zones_mock):
    zones_mock(responses)
    responses.get(f"{API}/series", body="datetime_utc,value\n", content_type="text/csv")
    with pytest.raises(ObsydNoData, match="catalog"):
        client.series("price.dayahead", "NL")


@responses.activate
def test_genmix_json_body_despite_csv_format(client, zones_mock):
    # /genmix answers a format=csv request with a JSON available:false body when
    # the zone has no data — the client must detect it via content-type.
    zones_mock(responses)
    responses.get(f"{API}/genmix", json={"available": False, "zone": "NL", "reason": "No generation-mix data yet."})
    with pytest.raises(ObsydNoData, match="generation-mix"):
        client.genmix("NL")


@responses.activate
def test_501_parquet_maps_to_server_error_with_hint(client, zones_mock):
    zones_mock(responses)
    responses.get(f"{API}/series", json={"detail": "Parquet export is unavailable"}, status=501)
    with pytest.raises(ObsydServerError, match="csv"):
        client.series("price.dayahead", "DE_LU", format="parquet")


@responses.activate
def test_429_without_retries_raises_rate_limited(client, zones_mock):
    zones_mock(responses)
    responses.get(f"{API}/series", json={"detail": "Rate limit exceeded"}, status=429)
    with pytest.raises(ObsydRateLimited) as e:
        client.series("price.dayahead", "DE_LU")
    assert e.value.status == 429
