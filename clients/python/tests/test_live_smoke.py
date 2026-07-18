"""Live smoke against https://obsyd.dev — excluded by default (addopts -m 'not live').

Run manually before a release:  pytest -m live
"""
import datetime as dt

import pytest

from obsyd import Obsyd

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live():
    return Obsyd()


def test_meta_and_zones(live):
    meta = live.meta()
    assert meta["version"] == "v1" and meta["attribution"]
    assert "DE_LU" in live.zones()["enabled_keys"]


def test_small_series_pull(live):
    start = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    df = live.series("price.dayahead", "DE_LU", start=start)
    assert len(df) > 24 and df["value"].notna().any()
    assert str(df.index.tz) == "UTC"
