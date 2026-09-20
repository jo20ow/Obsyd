"""Drive one daily-post run: compose (event → weekday franchise), render the
card, publish (or dry-run). Dedup is a one-line-per-day ledger so a re-fire
never double-posts a LIVE tweet.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from backend.config import settings
from backend.social import poster
from backend.social.card import render
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
    tag = result.get("tweet_id") or "dry"
    with LEDGER.open("a") as f:
        f.write(f"{stamp}\t{tag}\t{dedup_key}\n")


def run_once(db: Session, *, now: datetime | None = None) -> dict:
    """Compose and post today's item. Never raises for a 'nothing to post'
    outcome — silence is a valid day."""
    now = now or datetime.now(timezone.utc)

    post = compose(db, now=now)
    if post is None:
        logger.info("social: nothing eligible today — no post")
        return {"posted": False, "reason": "no_eligible_content"}
    if len(post.text) > 280:
        logger.error("social: composed text %d chars > 280 (%s), refusing", len(post.text), post.kind)
        return {"posted": False, "reason": "too_long", "kind": post.kind}
    if _already_posted(post.dedup_key):
        logger.info("social: %s already posted — skip", post.dedup_key)
        return {"posted": False, "reason": "already_posted", "dedup": post.dedup_key}

    # The link self-reply carries X's $0.20 URL-post premium; off by default.
    reply = post.reply if settings.x_social_link_reply else None
    png = render(post.card)
    result = poster.post(post.text, post.alt_text, png, reply=reply, dedup=post.dedup_key)
    # Only a LIVE post consumes the day's key; a dry-run is an inspection.
    if not result.get("dry_run"):
        _record(post.dedup_key, result)
    return {"posted": True, "kind": post.kind, "dry_run": result.get("dry_run", False),
            "dedup": post.dedup_key,
            **({"tweet_id": result["tweet_id"]} if "tweet_id" in result else {})}


if __name__ == "__main__":  # manual: python -m backend.social.runner
    import logging as _l

    from backend.database import SessionLocal

    _l.basicConfig(level=_l.INFO, format="%(levelname)s %(message)s")
    _db = SessionLocal()
    try:
        print(run_once(_db))
    finally:
        _db.close()
