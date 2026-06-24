"""
Trial Onboarding Drip — three nudges over the 14-day in-app trial.

Schedule (per Subscription that started an in-app trial):
  day 0  — Welcome (fired synchronously from POST /api/auth/start-trial)
  day 2  — "Read your first chokepoint anomaly"
  day 5  — "Upgrade to keep your alerts" (with days_remaining)

State is tracked on Subscription.drip_stage:
  None  → not in drip
  0     → welcome sent
  1     → day-2 sent
  2     → day-5 sent
  3     → drip complete

Idempotency: each stage advance is gated by a strict equality check on
the previous stage and a "trial age in days" floor. Re-running the
scheduler hourly cannot duplicate a send.

Resend tier limit (100/day) is shared with the daily briefing email,
so drip-stage advancement is bounded per run (DRIP_SEND_BUDGET).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import SessionLocal
from backend.models.subscription import Subscription

logger = logging.getLogger(__name__)

# Cap one scheduler run from sending too many drip emails at once (Resend
# free tier is 100/day shared with the daily briefing).
DRIP_SEND_BUDGET = 30

# Drip stage thresholds (days since Subscription.created_at)
DRIP_DAYS = {0: 0, 1: 2, 2: 5}


def _resend_api_key() -> str | None:
    key = settings.resend_api_key
    if not key:
        return None
    return key.get_secret_value() if hasattr(key, "get_secret_value") else key


def _send_html(client: httpx.Client, api_key: str, email: str, subject: str, html: str) -> bool:
    try:
        resp = client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": "OBSYD <briefing@obsyd.dev>",
                "to": [email],
                "subject": subject,
                "html": html,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning("Drip send failed for %s: %s", email, e)
        return False


def _shell(body_html: str) -> str:
    """Common branded HTML shell — matches the magic-link template."""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#09090b;font-family:'Courier New',monospace;color:#d4d4d4">
<div style="max-width:520px;margin:0 auto;padding:40px 20px">
<div style="border:1px solid #27272a;padding:30px;background:#0a0a12">
<div style="font-size:14px;color:#22d3ee;font-weight:bold;letter-spacing:3px;margin-bottom:24px">OBSYD</div>
{body_html}
<div style="margin-top:32px;padding-top:16px;border-top:1px solid #27272a;font-size:10px;color:#525252;line-height:1.6">
Open-source market observation tool. Not investment advice. AIS data is self-reported and unverified.
<br>You're receiving this because you started a Pro trial on obsyd.dev. <a href="https://obsyd.dev/api/auth/logout" style="color:#525252">Sign out</a> to stop.
</div>
</div>
</div>
</body></html>"""


def render_welcome(email: str) -> tuple[str, str]:
    """Day 0 — fired right after the user starts the trial."""
    subject = "OBSYD — your 14-day Pro trial is live"
    body = """
<div style="font-size:13px;color:#a3a3a3;margin-bottom:20px;line-height:1.6">
Your Pro trial is active. No card, no commitment — you have 14 days to decide whether the
daily briefing is worth your <span style="color:#22d3ee">€15/month</span>.
</div>
<div style="font-size:12px;color:#d4d4d4;line-height:1.7;margin-bottom:20px">
<b style="color:#22d3ee">What's unlocked today</b><br>
· Daily briefing email, Mon–Fri 07:00 UTC (you get tomorrow's)<br>
· Floating storage & STS transfer alerts<br>
· Crack spreads + related energy equities overlay<br>
· Market Intelligence Report (5-section narrative)<br>
· Custom flow-anomaly alerts via email
</div>
<div style="font-size:12px;color:#a3a3a3;line-height:1.6;margin-bottom:20px">
The fastest way to feel the difference: open the dashboard and skim the
<i>Market Intelligence Report</i> at the top — it's the same content
you'll get in your inbox tomorrow morning, but for today.
</div>
<a href="https://obsyd.dev/app" style="display:inline-block;padding:10px 24px;background:#22d3ee;color:#09090b;text-decoration:none;font-family:'Courier New',monospace;font-size:12px;font-weight:bold;letter-spacing:1px">
OPEN DASHBOARD →
</a>
"""
    return subject, _shell(body)


def render_day2(email: str) -> tuple[str, str]:
    """Day 2 — pull the user back in with a concrete recent anomaly."""
    subject = "OBSYD — read your first chokepoint anomaly"
    body = """
<div style="font-size:13px;color:#a3a3a3;margin-bottom:20px;line-height:1.6">
48 hours in. If you've opened the dashboard once, you've seen the AIS map.
The actual value lives one click deeper — in the <b style="color:#22d3ee">signals tab</b>.
</div>
<div style="font-size:12px;color:#d4d4d4;line-height:1.7;margin-bottom:20px">
<b style="color:#22d3ee">Try this in 30 seconds</b><br>
1. Open <a href="https://obsyd.dev/app#signals" style="color:#22d3ee">obsyd.dev/app#signals</a><br>
2. Scroll to the chokepoint card with the largest % deviation<br>
3. Click the chokepoint name — the historical chart shows you whether
   today's traffic is unusual against the same week last year, not just
   the 30-day average
</div>
<div style="font-size:12px;color:#a3a3a3;line-height:1.6;margin-bottom:20px">
Every alert OBSYD fires is computed in code you can read on
<a href="https://github.com/jo20ow/Obsyd" style="color:#22d3ee">GitHub</a>.
No black-box ML — Pearson with lag optimisation, transparent thresholds.
</div>
<a href="https://obsyd.dev/app#signals" style="display:inline-block;padding:10px 24px;background:#22d3ee;color:#09090b;text-decoration:none;font-family:'Courier New',monospace;font-size:12px;font-weight:bold;letter-spacing:1px">
OPEN SIGNALS TAB →
</a>
"""
    return subject, _shell(body)


def render_day5(email: str, days_remaining: int) -> tuple[str, str]:
    """Day 5 — reminder + upgrade nudge."""
    subject = f"OBSYD — {days_remaining} days left in your Pro trial"
    body = f"""
<div style="font-size:13px;color:#a3a3a3;margin-bottom:20px;line-height:1.6">
Your trial ends in <b style="color:#22d3ee">{days_remaining} days</b>. After that, the daily briefing
stops and the deep-dive panels (crack spreads, related equities, STS detection,
market report) lock back behind the paywall.
</div>
<div style="font-size:12px;color:#d4d4d4;line-height:1.7;margin-bottom:20px">
<b style="color:#22d3ee">€15/month</b> · or €149/year (−17%)<br>
Lemon Squeezy handles EU-VAT. Cancel any time from the customer portal.
</div>
<div style="font-size:12px;color:#a3a3a3;line-height:1.6;margin-bottom:20px">
If you've been getting value out of the briefing, the cheapest path is now
— before the trial expires the conversion is seamless (no double-billing).
</div>
<a href="https://obsyd.dev/app" style="display:inline-block;padding:10px 24px;background:#22d3ee;color:#09090b;text-decoration:none;font-family:'Courier New',monospace;font-size:12px;font-weight:bold;letter-spacing:1px">
SUBSCRIBE → KEEP PRO
</a>
"""
    return subject, _shell(body)


def _render_for_stage(stage: int, email: str, sub: Subscription) -> Optional[tuple[str, str]]:
    """Pick the right template for a stage transition (0→1→2→3)."""
    if stage == 0:
        return render_welcome(email)
    if stage == 1:
        return render_day2(email)
    if stage == 2:
        days_left = 14
        if sub.trial_ends_at:
            days_left = max(0, (sub.trial_ends_at - datetime.utcnow()).days)
        return render_day5(email, days_left)
    return None


def send_welcome_now(email: str) -> bool:
    """Synchronous send used by POST /api/auth/start-trial. Returns success."""
    api_key = _resend_api_key()
    if not api_key:
        logger.warning("Drip welcome: RESEND_API_KEY not configured, skipping")
        return False
    subject, html = render_welcome(email)
    with httpx.Client() as client:
        return _send_html(client, api_key, email, subject, html)


def process_trial_drip(db_factory=SessionLocal, now: datetime | None = None, budget: int = DRIP_SEND_BUDGET) -> dict:
    """Scheduler entry point — advance one drip stage per eligible trial.

    Idempotent: a Subscription only advances if its current drip_stage is
    exactly one below the next stage AND its trial age has crossed the
    threshold. Cap per run by `budget` so a backlog can't blow through
    the daily Resend quota.

    Args:
        db_factory: sessionmaker (overridable for tests).
        now: override "now" (testing).
        budget: max number of sends in one run.

    Returns:
        Dict with counts per stage and total sends.
    """
    api_key = _resend_api_key()
    if not api_key:
        logger.warning("Drip processor: RESEND_API_KEY not configured, skipping")
        return {"skipped": "no_api_key"}

    now = now or datetime.utcnow()
    sent: dict[str, int] = {"day0": 0, "day2": 0, "day5": 0}
    db: Session = db_factory()
    try:
        # Pull trial subs that still have a stage to advance.
        candidates = (
            db.query(Subscription)
            .filter(
                Subscription.status == "trialing",
                Subscription.drip_stage.isnot(None),
                Subscription.drip_stage < 3,
            )
            .order_by(Subscription.id.asc())
            .all()
        )

        remaining = budget
        with httpx.Client() as client:
            for sub in candidates:
                if remaining <= 0:
                    break
                next_stage = (sub.drip_stage or 0) + 1
                if next_stage not in DRIP_DAYS:
                    sub.drip_stage = 3
                    continue
                threshold_days = DRIP_DAYS[next_stage]
                age = (now - (sub.created_at or now)).total_seconds() / 86400.0
                if age < threshold_days:
                    continue
                rendered = _render_for_stage(next_stage, sub.email, sub)
                if not rendered:
                    sub.drip_stage = 3
                    continue
                subject, html = rendered
                ok = _send_html(client, api_key, sub.email, subject, html)
                if ok:
                    sub.drip_stage = next_stage
                    sent[f"day{threshold_days}"] += 1
                    remaining -= 1

        db.commit()
    finally:
        db.close()

    total = sum(sent.values())
    logger.info("Drip processor: sent %d emails (%s)", total, sent)
    return {"total": total, **sent}
