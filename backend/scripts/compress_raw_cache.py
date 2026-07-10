"""One-off migration: gzip the raw-response disk cache in place.

`backend/gas/raw_cache.py` writes `.json.gz` and reads either form, so this can
run at any time, twice, or not at all. On the VPS it turns ~9.1 GB of ENTSO-E /
ENTSOG JSON into roughly 0.4 GB — the uncompressed cache was a material part of
what filled the root filesystem on 2026-07-07.

    python -m backend.scripts.compress_raw_cache --dry-run
    python -m backend.scripts.compress_raw_cache

Rules it will not break:
  * a payload that cannot be parsed is left exactly where it is;
  * a blob is only unlinked after its archive has been read back successfully;
  * writes go to a temp file and are renamed into place, so an interrupted run
    (a full disk, say) leaves no half-written archive.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Stats:
    compressed: int = 0
    skipped: int = 0
    already_archived: int = 0
    not_worth_it: int = 0
    bytes_before: int = 0
    bytes_after: int = 0

    @property
    def saved(self) -> int:
        return self.bytes_before - self.bytes_after


def _is_readable_archive(gz: Path) -> bool:
    try:
        with gzip.open(gz, "rt", encoding="utf-8") as fh:
            json.load(fh)
    except (OSError, EOFError, json.JSONDecodeError):
        return False
    return True


def _compress(blob: Path, gz: Path) -> None:
    """Write `blob` to `gz` atomically, verifying the archive before returning."""
    tmp = gz.with_name(f"{gz.name}.tmp")
    try:
        payload = blob.read_bytes()
        with tmp.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as out:
            out.write(payload)
        if not _is_readable_archive(tmp):
            raise OSError(f"archive of {blob} did not read back")
        os.replace(tmp, gz)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def compress_tree(root: Path, *, dry_run: bool = False) -> Stats:
    stats = Stats()

    for blob in sorted(root.rglob("*.json")):
        if not blob.is_file():
            continue

        gz = blob.with_name(f"{blob.name}.gz")
        size = blob.stat().st_size

        # A payload we cannot read is not ours to throw away.
        try:
            with blob.open("r", encoding="utf-8") as fh:
                json.load(fh)
        except (OSError, json.JSONDecodeError):
            logger.warning("skipping unreadable blob: %s", blob)
            stats.skipped += 1
            continue

        # An intact archive already exists: drop the redundant plain copy.
        if gz.exists() and _is_readable_archive(gz):
            stats.already_archived += 1
            if not dry_run:
                blob.unlink()
            continue

        if dry_run:
            # Compress into a temp file purely to learn whether it is worth it.
            probe = len(gzip.compress(blob.read_bytes(), mtime=0))
            if probe >= size:
                stats.not_worth_it += 1
            else:
                stats.compressed += 1
                stats.bytes_before += size
                stats.bytes_after += probe
            continue

        _compress(blob, gz)
        archived = gz.stat().st_size

        # gzip's header costs ~20 bytes; a tiny payload would only grow. Leave it
        # as plain JSON — raw_cache reads that form too.
        if archived >= size:
            gz.unlink()
            stats.not_worth_it += 1
            continue

        stats.compressed += 1
        stats.bytes_before += size
        stats.bytes_after += archived
        blob.unlink()

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/raw"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not args.root.is_dir():
        logger.error("no such directory: %s", args.root)
        return 1

    stats = compress_tree(args.root, dry_run=args.dry_run)
    verb = "would compress" if args.dry_run else "compressed"
    logger.info(
        "%s %d blobs (%.1f MB -> %.1f MB, saved %.1f MB); "
        "%d already archived; %d too small to be worth it; %d skipped",
        verb,
        stats.compressed,
        stats.bytes_before / 1e6,
        stats.bytes_after / 1e6,
        stats.saved / 1e6,
        stats.already_archived,
        stats.not_worth_it,
        stats.skipped,
    )
    if stats.skipped:
        logger.warning("%d unreadable blobs were left in place", stats.skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
