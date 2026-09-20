"""Post one composed item to X — media upload + tweet + optional self-reply.

Write-only, own brand account, OAuth 1.0a user context. The default is SAFE:
with any key missing, or x_social_dry_run set, `post()` writes the text, alt
text and PNG to a dated file under data/social/ and returns a synthetic result
WITHOUT touching the network. Live posting happens only when all four keys are
present and dry-run is off.

Media upload uses the v1.1 endpoint (still the simplest path for a single image
+ alt text); the tweet and reply use API v2.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from backend.config import settings

logger = logging.getLogger(__name__)

MEDIA_UPLOAD = "https://upload.twitter.com/1.1/media/upload.json"
MEDIA_METADATA = "https://upload.twitter.com/1.1/media/metadata/create.json"
TWEETS = "https://api.twitter.com/2/tweets"
SOCIAL_DIR = Path("data/social")


def _keys() -> tuple[str, str, str, str] | None:
    vals = [settings.x_api_key, settings.x_api_secret,
            settings.x_access_token, settings.x_access_secret]
    if any(v is None for v in vals):
        return None
    return tuple(v.get_secret_value() for v in vals)  # type: ignore[return-value]


def is_live() -> bool:
    """True only when we would actually hit the network."""
    return not settings.x_social_disabled and not settings.x_social_dry_run and _keys() is not None


def _dry_run(text: str, alt: str, png: bytes, reply: str | None, dedup: str) -> dict:
    SOCIAL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = SOCIAL_DIR / f"{stamp}-{dedup}"
    base.with_suffix(".png").write_bytes(png)
    base.with_suffix(".json").write_text(json.dumps(
        {"text": text, "alt": alt, "reply": reply, "dedup": dedup,
         "chars": len(text)}, ensure_ascii=False, indent=2))
    logger.info("social DRY-RUN wrote %s (%d chars)", base, len(text))
    return {"dry_run": True, "path": str(base), "chars": len(text)}


def post(text: str, alt: str, png: bytes, *, reply: str | None = None,
         dedup: str = "post") -> dict:
    """Post text + image (+ optional self-reply). Returns a result dict; in
    dry-run it never touches the network. Raises only on a live API failure."""
    if not is_live():
        return _dry_run(text, alt, png, reply, dedup)

    from requests_oauthlib import OAuth1Session  # lazy: only live path needs it

    ck, cs, at, ats = _keys()  # type: ignore[misc]
    sess = OAuth1Session(ck, cs, at, ats)

    # 1) upload the image, 2) attach alt text
    up = sess.post(MEDIA_UPLOAD, files={"media": png})
    up.raise_for_status()
    media_id = str(up.json()["media_id"])
    meta = sess.post(MEDIA_METADATA, json={
        "media_id": media_id, "alt_text": {"text": alt[:1000]}})
    meta.raise_for_status()

    # 3) the tweet
    r = sess.post(TWEETS, json={"text": text, "media": {"media_ids": [media_id]}})
    r.raise_for_status()
    tweet_id = r.json()["data"]["id"]
    out = {"dry_run": False, "tweet_id": tweet_id}

    # 4) optional self-reply carrying the link (kept out of the main body so X
    #    does not down-rank the post for an external link)
    if reply:
        rr = sess.post(TWEETS, json={
            "text": reply, "reply": {"in_reply_to_tweet_id": tweet_id}})
        rr.raise_for_status()
        out["reply_id"] = rr.json()["data"]["id"]

    logger.info("social POSTED tweet %s (reply=%s)", tweet_id, out.get("reply_id"))
    return out
