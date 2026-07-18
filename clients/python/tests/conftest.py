import pytest

from obsyd import Obsyd

BASE = "https://test.invalid"
API = f"{BASE}/api/v1"

HOURLY_CSV = (
    "datetime_utc,value\n"
    "2026-07-01T00:00:00+00:00,80.5\n"
    "2026-07-01T01:00:00+00:00,75.0\n"
)
DAILY_CSV = "date,value,hours\n2026-07-01,80.5,24\n2026-07-02,90.0,12\n"
GENMIX_CSV = "t,Solar,Fossil Gas\n2026-06,1200.5,800.0\n2026-07,1400.0,700.0\n"
SNAPSHOT_JSON = {
    "available": True,
    "series": "price.dayahead",
    "unit": "EUR/MWh",
    "timestamps": ["2026-07-01T00:00:00+00:00", "2026-07-01T01:00:00+00:00"],
    "zones": {"DE_LU": [80.5, 75.0], "FR": [90.0, None]},
}
ZONES_JSON = {
    "default": "DE_LU",
    "enabled_keys": ["DE_LU", "FR", "NL"],
    "zones": [{"key": "DE_LU", "label": "DE-LU"}],
}
NO_DATA_JSON = {"available": False, "series": "price.dayahead", "zone": "NL", "reason": "no rows yet"}


@pytest.fixture
def client():
    """Client against a non-routable base; every test registers its own mocks.

    max_retries=0 keeps error tests instant; the retry test builds its own client.
    """
    return Obsyd(base_url=BASE, max_retries=0)


@pytest.fixture
def zones_mock():
    """Register the /zones payload the zone validator fetches lazily."""

    def _register(rsps):
        rsps.get(f"{API}/zones", json=ZONES_JSON)

    return _register
