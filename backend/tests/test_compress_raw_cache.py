"""Tests for the one-off data/raw compression migration.

It rewrites 22k production files, so its contract is narrow and paranoid: never
destroy a payload it could not read back, never leave a half-written blob, and
be safe to run twice.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from backend.scripts.compress_raw_cache import compress_tree


def _json_file(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


#: Real ENTSO-E/ENTSOG payloads are hundreds of kilobytes of repetitive JSON.
#: A handful of bytes would *grow* under gzip's header, which is its own case.
BIG = {"series": [{"ts": f"2025-01-{d:02d}", "mw": 1234.5} for d in range(1, 29)]}


@pytest.fixture
def tree(tmp_path):
    _json_file(tmp_path / "entsoe_genmix" / "2025-01" / "a.json", BIG)
    _json_file(tmp_path / "entsog" / "2026-06" / "b.json", {"v": list(range(500))})
    return tmp_path


def test_compresses_every_json_blob(tree):
    compress_tree(tree)
    assert (tree / "entsoe_genmix" / "2025-01" / "a.json.gz").exists()
    assert (tree / "entsog" / "2026-06" / "b.json.gz").exists()


def test_payloads_survive_the_round_trip(tree):
    compress_tree(tree)
    gz = tree / "entsog" / "2026-06" / "b.json.gz"
    assert json.loads(gzip.decompress(gz.read_bytes())) == {"v": list(range(500))}


def test_a_blob_that_gzip_would_grow_is_left_uncompressed(tmp_path):
    """gzip's header costs ~20 bytes. Tiny payloads must not be inflated —
    raw_cache reads the plain form anyway."""
    tiny = _json_file(tmp_path / "eurostat" / "2026-07" / "t.json", {"v": 1})

    stats = compress_tree(tmp_path)

    assert tiny.exists()
    assert not tiny.with_name("t.json.gz").exists()
    assert stats.compressed == 0
    assert stats.not_worth_it == 1


def test_originals_are_removed(tree):
    compress_tree(tree)
    assert list(tree.rglob("*.json")) == []


def test_no_temporary_files_survive(tree):
    compress_tree(tree)
    assert list(tree.rglob("*.tmp")) == []


def test_reports_the_bytes_it_saved(tree):
    stats = compress_tree(tree)
    assert stats.compressed == 2
    assert stats.bytes_before > 0
    assert stats.bytes_after < stats.bytes_before


# ── paranoia ─────────────────────────────────────────────────────────────────


def test_dry_run_changes_nothing(tree):
    before = {p: p.read_bytes() for p in tree.rglob("*") if p.is_file()}
    stats = compress_tree(tree, dry_run=True)
    after = {p: p.read_bytes() for p in tree.rglob("*") if p.is_file()}
    assert after == before
    assert stats.compressed == 2  # still reports what it would do


def test_an_unreadable_blob_is_left_alone(tree):
    bad = tree / "entsog" / "2026-06" / "corrupt.json"
    bad.write_text("{ this is not json")

    stats = compress_tree(tree)

    assert bad.exists(), "a payload we cannot parse must never be destroyed"
    assert not (tree / "entsog" / "2026-06" / "corrupt.json.gz").exists()
    assert stats.skipped == 1


def test_a_pre_existing_archive_wins_and_the_plain_copy_goes(tree):
    target = tree / "entsog" / "2026-06" / "b.json"
    gz = target.with_name("b.json.gz")
    gz.write_bytes(gzip.compress(json.dumps({"v": "already-archived"}).encode()))

    compress_tree(tree)

    assert not target.exists()
    assert json.loads(gzip.decompress(gz.read_bytes())) == {"v": "already-archived"}


def test_a_pre_existing_but_corrupt_archive_is_rebuilt(tree):
    target = tree / "entsog" / "2026-06" / "b.json"
    gz = target.with_name("b.json.gz")
    gz.write_bytes(b"not gzip at all")

    compress_tree(tree)

    assert not target.exists()
    assert json.loads(gzip.decompress(gz.read_bytes())) == {"v": list(range(500))}


def test_running_twice_is_a_no_op(tree):
    compress_tree(tree)
    snapshot = {p: p.read_bytes() for p in tree.rglob("*") if p.is_file()}

    stats = compress_tree(tree)

    assert stats.compressed == 0
    assert {p: p.read_bytes() for p in tree.rglob("*") if p.is_file()} == snapshot


def test_a_blob_that_vanishes_mid_run_is_tolerated(tree, monkeypatch):
    """obsyd's scheduler keeps writing to this cache while we migrate it. An
    entry it overwrites (and whose plain copy it unlinks) must not abort the run.
    """
    ghost = tree / "entsog" / "2026-06" / "ghost.json"
    real_rglob = Path.rglob
    monkeypatch.setattr(
        Path, "rglob", lambda self, pat: [*real_rglob(self, pat), ghost]
    )

    stats = compress_tree(tree)  # must not raise

    assert stats.compressed == 2


def test_non_json_files_are_untouched(tree):
    """usgs_copper caches raw XLSX bytes as .bin — not our business."""
    binary = tree / "usgs_copper" / "2025-06" / "mis-202506-coppe.bin"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"\x50\x4b\x03\x04binary")

    compress_tree(tree)

    assert binary.read_bytes() == b"\x50\x4b\x03\x04binary"
    assert not binary.with_suffix(".bin.gz").exists()
