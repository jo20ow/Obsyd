"""Drive one daily-post run: fetch the desk's own data, compose, render, post.

Reads through the SAME in-process functions the API serves (no HTTP self-call),
so the numbers are exactly the desk's. Dedup is a one-line-per-day ledger file:
a dedup_key already recorded means today's post is done — the job is safe to run
more than once (idempotent), and a restart never double-posts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from backend.social import poster
from backend.social.card import render_card
from backend.social.composer import compose

logger = logging.getLogger(__name__)

LEDGER = Path("data/social/posted.log")


def _already_posted(dedup_key: str) -> bool:
    if not LEDGER.exists():
        return False
    return any(line.strip().endswith(dedup_key) for line in LEDGER.read_text().splitlines())


def _record(dedup_key: str, result: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tag = result.get("tweet_id") or ("dry" if result.get("dry_run") else "?")
    with LEDGER.open("a") as f:
        f.write(f"{stamp}\t{tag}\t{dedup_key}\n")


def _gather(db: Session) -> tuple[dict, list[dict]]:
    """Overview (all zones) + fresh price records, via the in-process builders —
    the same synthesis the API serves, no HTTP self-call."""
    from datetime import timedelta

    from backend.routes.power import build_power_overview

    overview = build_power_overview(db)

    records: list[dict] = []
    try:
        from backend.models.energy import PowerRecord
        fresh_cut = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
        # zone→label straight from the overview we just built (single source).
        labels = {z["zone"]: z.get("zone_label", z["zone"]) for z in overview.get("zones", [])}
        # Only the canonical hourly day-ahead series carries a meaningful
        # long-history extreme (the composer enforces this too; belt and braces).
        rows = (db.query(PowerRecord)
                .filter(PowerRecord.series_key == "price.dayahead",
                        PowerRecord.ts_utc >= fresh_cut)
                .all())
        for r in rows:
            records.append({
                "fresh": True, "series": r.series_key, "kind": r.kind,
                "label": "day-ahead price", "unit": r.unit, "value": r.value,
                "zone": r.zone, "zone_label": labels.get(r.zone, r.zone),
                "date": datetime.fromtimestamp(r.ts_utc, tz=timezone.utc).date().isoformat(),
            })
    except Exception as exc:  # records are a bonus tier, never a hard dep
        logger.debug("social: records unavailable (%s)", exc)
    return overview, records


def run_once(db: Session, *, now: datetime | None = None) -> dict:
    """Compose and post today's item. Returns a status dict; never raises for a
    'nothing to post' outcome — silence is a valid day."""
    now = now or datetime.now(timezone.utc)
    today = now.date()

    overview, records = _gather(db)
    post = compose(overview, records=records, today=today)
    if post is None:
        logger.info("social: gates failed / nothing eligible — no post today")
        return {"posted": False, "reason": "no_eligible_content"}

    if len(post.text) > 280:
        logger.error("social: composed text %d chars > 280, refusing", len(post.text))
        return {"posted": False, "reason": "too_long", "kind": post.kind}

    if _already_posted(post.dedup_key):
        logger.info("social: %s already posted — skip", post.dedup_key)
        return {"posted": False, "reason": "already_posted", "dedup": post.dedup_key}

    # The link self-reply carries X's $0.20 URL-post premium (vs $0.015 plain);
    # gated off by default so the account runs at ~cents/month — the image's
    # obsyd.dev wordmark + the bio carry the link instead.
    from backend.config import settings
    reply = post.reply if settings.x_social_link_reply else None

    png = render_card(post.headline, post.rows, highlight=post.highlight)
    result = poster.post(post.text, post.alt_text, png,
                         reply=reply, dedup=post.dedup_key)
    # Dedup guards against a double LIVE post, so only a live post consumes the
    # day's key. A dry-run is an inspection (writes a card to data/social/) and
    # must not block the real post of the same day when keys are later added.
    if not result.get("dry_run"):
        _record(post.dedup_key, result)
    return {"posted": True, "kind": post.kind, "dry_run": result.get("dry_run", False),
            "dedup": post.dedup_key, **({"tweet_id": result["tweet_id"]}
                                        if "tweet_id" in result else {})}


if __name__ == "__main__":  # manual: python -m backend.social.runner
    import logging as _l

    from backend.database import SessionLocal

    _l.basicConfig(level=_l.INFO, format="%(levelname)s %(message)s")
    _db = SessionLocal()
    try:
        print(run_once(_db))
    finally:
        _db.close()
