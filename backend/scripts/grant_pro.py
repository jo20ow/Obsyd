"""Grant (or refresh) unconditional Pro access for an email — idempotent.

    python -m backend.scripts.grant_pro obsyd.dev@pm.me
    python -m backend.scripts.grant_pro user@x.de --revoke    # set status=expired

Inserts (or updates) a Subscription row with status="active", which is in
PRO_STATUSES_UNCONDITIONAL — i.e. unlimited Pro, no expiry, no trial consumed.
This uses the existing single-source-of-truth (is_pro / Subscription) untouched,
so Pro applies consistently across login, /me, require_pro and alerts.

Intended for comp/owner access (e.g. self-testing). Run once locally against
obsyd.db and once on the VPS against the production DB.
"""

from __future__ import annotations

import argparse
import sys

from backend.auth.subscription_check import is_pro
from backend.database import SessionLocal
from backend.models.subscription import Subscription


def grant_pro(email: str, *, revoke: bool = False) -> str:
    """Create or update the newest Subscription for `email`.

    Returns a short human-readable summary of what happened.
    """
    email = email.strip().lower()
    target_status = "expired" if revoke else "active"

    db = SessionLocal()
    try:
        sub = (
            db.query(Subscription)
            .filter(Subscription.email == email)
            .order_by(Subscription.id.desc())  # newest first
            .first()
        )

        if sub is None:
            if revoke:
                return f"No subscription for {email} — nothing to revoke."
            sub = Subscription(
                email=email,
                status="active",
                plan="pro",
            )
            db.add(sub)
            action = "created"
        else:
            if sub.status == target_status:
                action = "unchanged"
            else:
                sub.status = target_status
                action = "updated"

        db.commit()
        db.refresh(sub)
        pro = is_pro(sub)
        return (
            f"{action}: {email} → status={sub.status}, plan={sub.plan}, "
            f"is_pro={pro} (subscription id={sub.id})"
        )
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Grant unconditional Pro access to an email.")
    parser.add_argument("email", help="Login email to grant Pro to.")
    parser.add_argument(
        "--revoke",
        action="store_true",
        help="Set status=expired instead of granting (removes Pro).",
    )
    args = parser.parse_args()

    print(grant_pro(args.email, revoke=args.revoke))
    return 0


if __name__ == "__main__":
    sys.exit(main())
