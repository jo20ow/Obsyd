"""Bulk Parquet archive (backend/power/archive.py): file/manifest correctness
(read the parquet back and compare to the store), the hot-window rebuild
policy, and the premium-gated serving with manifest-validated filenames."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from backend import metering
from backend.power.archive import build_archive, build_year_file, read_manifest
from backend.power.hourly_store import upsert_hourly


@pytest.fixture(autouse=True)
def _clean():
    metering._reset()
    yield
    metering._reset()
    from backend.main import app

    app.dependency_overrides.clear()


def _seed(db):
    ts24 = int(datetime(2024, 6, 1, tzinfo=UTC).timestamp())
    ts_now = int(datetime(datetime.now(UTC).year, 2, 1, tzinfo=UTC).timestamp())
    upsert_hourly(db, "price.dayahead", "DE_LU",
                  [(ts24 + i * 3600, 50.0 + i) for i in range(24)], unit="EUR/MWh")
    upsert_hourly(db, "price.dayahead", "FR",
                  [(ts24, 40.0)], unit="EUR/MWh")
    upsert_hourly(db, "price.dayahead", "DE_LU",
                  [(ts_now, 99.0)], unit="EUR/MWh")


def test_year_file_round_trips_exactly(db_session, tmp_path):
    import pyarrow.parquet as pq

    _seed(db_session)
    entry = build_year_file(db_session, "price.dayahead", 2024, root=tmp_path)
    assert entry["rows"] == 25 and entry["zones"] == 2
    table = pq.read_table(tmp_path / entry["file"])
    assert table.column_names == ["zone", "datetime_utc", "value"]
    zones = table.column("zone").to_pylist()
    assert zones == sorted(zones)  # (zone, ts) ordering
    # FR's single point survives with its exact value and timestamp.
    fr_idx = zones.index("FR")
    assert table.column("value")[fr_idx].as_py() == 40.0
    assert table.column("datetime_utc")[fr_idx].as_py().year == 2024
    # Checksum in the entry matches the file on disk.
    import hashlib
    assert entry["sha256"] == hashlib.sha256((tmp_path / entry["file"]).read_bytes()).hexdigest()


def test_build_archive_hot_window_policy(db_session, tmp_path):
    _seed(db_session)
    out = build_archive(db_session, root=tmp_path)
    assert out["built"] >= 2  # 2024 + current year
    m1 = read_manifest(root=tmp_path)
    assert {e["year"] for e in m1["files"]} >= {2024, datetime.now(UTC).year}

    # Second run: 2024 is outside the hot window (assuming now > 2025) and its
    # file exists → skipped; the current year rebuilds.
    out2 = build_archive(db_session, root=tmp_path)
    assert out2["skipped"] >= 1
    # force rebuilds everything.
    out3 = build_archive(db_session, root=tmp_path, force=True)
    assert out3["skipped"] == 0 and out3["built"] >= out["built"]


def test_empty_year_writes_nothing(db_session, tmp_path):
    _seed(db_session)
    assert build_year_file(db_session, "price.dayahead", 1999, root=tmp_path) is None
    assert build_year_file(db_session, "no.such.series", 2024, root=tmp_path) is None


def _client(db, *, pro=False) -> TestClient:
    from backend.auth.dependencies import require_pro
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    if pro:
        app.dependency_overrides[require_pro] = lambda: {"email": "owner@test"}
    return TestClient(app, raise_server_exceptions=True)


def test_archive_endpoints_are_premium_and_traversal_safe(db_session, tmp_path, monkeypatch):
    import backend.power.archive as archive_mod

    monkeypatch.setattr(archive_mod, "ARCHIVE_ROOT", tmp_path)
    _seed(db_session)
    build_archive(db_session, root=tmp_path)

    assert _client(db_session).get("/api/v1/archive").status_code == 401

    c = _client(db_session, pro=True)
    m = c.get("/api/v1/archive").json()
    assert m["available"] is True and m["files"]
    fname = m["files"][0]["file"]

    got = c.get(f"/api/v1/archive/{fname}")
    assert got.status_code == 200
    assert got.content == (tmp_path / fname).read_bytes()

    # Unknown names and traversal shapes 404 via the manifest check — the
    # filesystem is never consulted for names the manifest doesn't carry.
    assert c.get("/api/v1/archive/nope.parquet").status_code == 404
    assert c.get("/api/v1/archive/..%2Fmanifest.json").status_code == 404


def test_keyed_download_is_metered_with_row_points(db_session, tmp_path, monkeypatch):
    import backend.power.archive as archive_mod
    from backend.auth.api_keys import create_key
    from backend.models.api_key import ApiKey
    from backend.models.subscription import Subscription

    monkeypatch.setattr(archive_mod, "ARCHIVE_ROOT", tmp_path)
    _seed(db_session)
    build_archive(db_session, root=tmp_path)
    db_session.add(Subscription(email="k@t.co", status="active", plan="pro"))
    db_session.commit()
    _, raw = create_key(db_session, "k@t.co")
    key_id = db_session.query(ApiKey.id).scalar()

    c = _client(db_session)
    m = c.get("/api/v1/archive", headers={"X-Api-Key": raw}).json()
    entry = next(e for e in m["files"] if e["year"] == 2024)
    assert c.get(f"/api/v1/archive/{entry['file']}",
                 headers={"X-Api-Key": raw}).status_code == 200
    requests, points = metering.usage_today(key_id)
    assert requests == 2 and points == entry["rows"]


def test_manifest_survives_and_prunes(db_session, tmp_path):
    _seed(db_session)
    build_archive(db_session, root=tmp_path)
    m = read_manifest(root=tmp_path)
    victim = m["files"][0]["file"]
    (tmp_path / victim).unlink()  # manual cleanup on disk
    build_archive(db_session, root=tmp_path)
    m2 = read_manifest(root=tmp_path)
    files = {e["file"] for e in m2["files"]}
    # Either rebuilt (hot year) or pruned (old year) — never a manifest entry
    # pointing at a missing file.
    assert all((tmp_path / f).exists() for f in files)
    assert json.loads((tmp_path / "manifest.json").read_text())["schema"]
