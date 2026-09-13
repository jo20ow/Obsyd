"""Bulk Parquet archive — Phase 2, brick 3: the premium delivery layer.

The market research's recurring paid gate is history depth + bulk delivery
(Electricity Maps sells the archive separately; Balancing Services gives 30
days free, years paid; gridstatus ships CSV-on-S3). Obsyd's reading: the
live API and the desk stay free; the COMPLETE history as ready-made files is
the production feature — one wget instead of a request loop, zero DB load
per download, stable URLs for reproducible research.

Layout (data/archive/, regenerable — deliberately outside the DB backup):

    <series>_<year>.parquet   long format, one file per series per calendar
                              year across ALL zones; columns:
                                zone         string
                                datetime_utc timestamp[s, UTC]
                                value        float64
                              sorted (zone, datetime_utc); zstd-compressed
    manifest.json             every file with rows/zones/bytes/sha256 +
                              generated_at — the checksums make a download
                              verifiable, which is a credibility feature,
                              not bookkeeping

Rebuild policy (the records doctrine, sized for a file store): the nightly
job rebuilds the CURRENT and PREVIOUS calendar year (the horizon where
ingest + revisions still move data — including the day-ahead hours already
published into January of a new year); older years are built once and left
alone unless --force (backend/scripts/archive_backfill.py) — a restated
deep-history backfill is followed by a forced rebuild, not silently absorbed.

Serving (routes/api_v1.py): GET /api/v1/archive (manifest) and
/archive/{filename} (FileResponse), both require_pro — the archive IS the
premium product and the preview is tested hidden. Filenames are validated
against the manifest, never against the filesystem, which closes path
traversal by construction. Downloads by API key are metered with the file's
row count as points. If archive traffic ever outgrows the app process, the
files are already static — move serving to Caddy behind a forward_auth to
this app without touching the builder.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.energy import PowerHourly, SeriesDim, ZoneDim

logger = logging.getLogger(__name__)

ARCHIVE_ROOT = Path("data/archive")
MANIFEST = "manifest.json"

SCHEMA_NOTE = (
    "Long format: zone (string), datetime_utc (timestamp, UTC), value (float64); "
    "sorted by (zone, datetime_utc); zstd compression. One file per series per "
    "calendar year across all zones. Values are the canonical hourly store as of "
    "generated_at — current and previous year are rebuilt nightly."
)


def _year_bounds(year: int) -> tuple[int, int]:
    start = int(datetime(year, 1, 1, tzinfo=UTC).timestamp())
    end = int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp())
    return start, end


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_year_file(db: Session, series_key: str, year: int,
                    root: Path | None = None) -> dict | None:
    """Write <series>_<year>.parquet (atomically) and return its manifest
    entry, or None when the year holds no rows for the series."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    root = root or ARCHIVE_ROOT
    sid = db.query(SeriesDim.id).filter(SeriesDim.key == series_key).scalar()
    if sid is None:
        return None
    start, end = _year_bounds(year)
    zones = dict(db.query(ZoneDim.id, ZoneDim.key).all())
    rows = (
        db.query(PowerHourly.zone_id, PowerHourly.ts_utc, PowerHourly.value)
        .filter(PowerHourly.series_id == sid,
                PowerHourly.ts_utc >= start, PowerHourly.ts_utc < end)
        .all()
    )
    data = sorted(
        ((zones[zid], int(ts), float(v)) for zid, ts, v in rows if zid in zones),
        key=lambda r: (r[0], r[1]),
    )
    if not data:
        return None

    table = pa.table({
        "zone": pa.array([r[0] for r in data], pa.string()),
        "datetime_utc": pa.array([r[1] for r in data], pa.timestamp("s", tz="UTC")),
        "value": pa.array([r[2] for r in data], pa.float64()),
    })
    root.mkdir(parents=True, exist_ok=True)
    fname = f"{series_key}_{year}.parquet"
    tmp = root / (fname + ".part")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, root / fname)
    return {
        "file": fname,
        "series": series_key,
        "year": year,
        "rows": len(data),
        "zones": len({r[0] for r in data}),
        "bytes": (root / fname).stat().st_size,
        "sha256": _sha256(root / fname),
    }


def build_archive(db: Session, *, years: list[int] | None = None,
                  force: bool = False, root: Path | None = None,
                  now: datetime | None = None) -> dict:
    """Build/refresh the archive and rewrite the manifest.

    Default `years=None` = every year from the store's oldest hour to now,
    where a file older than the previous calendar year is SKIPPED if it
    already exists (see the module docstring); `force=True` rebuilds all.
    Returns {"built": n, "skipped": n, "files": total}.
    """
    root = root or ARCHIVE_ROOT
    now = now or datetime.now(UTC)
    if years is None:
        oldest = db.query(func.min(PowerHourly.ts_utc)).scalar()
        if oldest is None:
            return {"built": 0, "skipped": 0, "files": 0}
        years = list(range(datetime.fromtimestamp(oldest, UTC).year, now.year + 1))
    hot = {now.year, now.year - 1}

    series_keys = [k for (k,) in db.query(SeriesDim.key).order_by(SeriesDim.key).all()]
    entries: dict[str, dict] = {}
    # Carry forward existing manifest entries for files we skip.
    manifest_path = root / MANIFEST
    if manifest_path.exists():
        try:
            for e in json.loads(manifest_path.read_text()).get("files", []):
                entries[e["file"]] = e
        except (ValueError, KeyError):
            logger.warning("archive: unreadable manifest, rebuilding entries from scratch")

    built = skipped = 0
    for series_key in series_keys:
        for year in years:
            fname = f"{series_key}_{year}.parquet"
            if not force and year not in hot and (root / fname).exists():
                skipped += 1
                continue
            entry = build_year_file(db, series_key, year, root=root)
            if entry is not None:
                entries[fname] = entry
                built += 1

    # Entries whose file vanished (manual cleanup) drop out of the manifest.
    entries = {f: e for f, e in entries.items() if (root / f).exists()}
    manifest = {
        "generated_at": now.isoformat(),
        "schema": SCHEMA_NOTE,
        "files": sorted(entries.values(), key=lambda e: (e["series"], e["year"])),
    }
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / (MANIFEST + ".part")
    tmp.write_text(json.dumps(manifest, indent=1))
    os.replace(tmp, manifest_path)
    logger.info("archive: %d built, %d skipped, %d files in manifest",
                built, skipped, len(entries))
    return {"built": built, "skipped": skipped, "files": len(entries)}


def read_manifest(root: Path | None = None) -> dict | None:
    root = root or ARCHIVE_ROOT
    path = root / MANIFEST
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None
