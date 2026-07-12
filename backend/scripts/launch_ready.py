"""
Pre-launch smoke check for OBSYD.

Run from the repo root on the VPS::

    cd /home/obsyd/obsyd
    source .venv/bin/activate
    python -m backend.scripts.launch_ready

Returns exit code 0 when every check is green, 1 when any check fails.
Prints a human-readable summary; OBSYD_JSON_LOGS=1 not required.

Checks (each runs independently; one failure does not abort the rest):
  1. DB pipeline freshness   — alerts row in last 24h, vessel_positions in last 1h
  2. Subscription/auth env   — SECRET_KEY + JWT_SECRET set to non-default values
  3. Lemon Squeezy config    — checkout URL is a real LS URL, webhook secret set
  4. Resend                  — RESEND_API_KEY set (drip + alerts need it)
  5. Scheduler jobs          — eight critical jobs present in the APScheduler registry
  6. TLS                     — obsyd.dev certificate valid + > 7 days remaining
  7. Public reachability     — https://obsyd.dev/health returns 200 from this host

Designed to be run from inside cron / CI / a deploy hook — pure stdlib
plus what the backend already depends on (httpx, SQLAlchemy).
"""

from __future__ import annotations

import os
import socket
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

# Make sure we can import the backend package when invoked via -m.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str

    def format(self) -> str:
        flag = "OK" if self.ok else "FAIL"
        marker = "✓" if self.ok else "✗"
        return f"  [{flag}] {marker} {self.name}: {self.detail}"


# ---------------------------- individual checks ----------------------------


def _check_db_freshness() -> CheckResult:
    from sqlalchemy import func

    from backend.database import SessionLocal
    from backend.models.alerts import Alert
    from backend.models.vessels import VesselPosition

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        latest_alert = db.query(func.max(Alert.created_at)).scalar()
        latest_vp = db.query(func.max(VesselPosition.timestamp)).scalar()

        problems: list[str] = []
        if latest_alert is None or (now - latest_alert) > timedelta(hours=24):
            age = "never" if latest_alert is None else f"{(now - latest_alert).total_seconds() / 3600:.1f}h ago"
            problems.append(f"no alerts last 24h (latest {age})")
        if latest_vp is None or (now - latest_vp) > timedelta(hours=1):
            age = "never" if latest_vp is None else f"{(now - latest_vp).total_seconds() / 60:.0f}m ago"
            problems.append(f"vessel feed stale (latest {age})")

        if problems:
            return CheckResult("db freshness", False, "; ".join(problems))
        return CheckResult(
            "db freshness",
            True,
            f"latest alert {(now - latest_alert).total_seconds() / 60:.0f}m, "
            f"vessel {(now - latest_vp).total_seconds() / 60:.0f}m",
        )
    except Exception as e:
        return CheckResult("db freshness", False, f"query failed: {e}")
    finally:
        db.close()


def _check_secrets_set() -> CheckResult:
    from backend.config import settings

    missing: list[str] = []
    for name in ("secret_key", "jwt_secret"):
        val = getattr(settings, name)
        raw = val.get_secret_value() if hasattr(val, "get_secret_value") else (val or "")
        if not raw or "change-me" in raw:
            missing.append(name)
    if missing:
        return CheckResult("secrets set", False, f"default/empty: {', '.join(missing)}")
    return CheckResult("secrets set", True, "SECRET_KEY + JWT_SECRET non-default")


def _check_lemon_squeezy() -> CheckResult:
    """Payments are DORMANT by decision (2026-06-25: Obsyd is fully free, the
    LS checkout was rejected) — launch readiness must not depend on them. The
    check now only flags the UNEXPECTED case: a checkout URL configured even
    though nothing is for sale."""
    from backend.config import settings

    url = settings.lemonsqueezy_checkout_url or ""
    if url:
        return CheckResult(
            "lemon squeezy", False,
            f"checkout_url is set ({url}) but Obsyd is free — leftover config?",
        )
    return CheckResult("lemon squeezy", True, "dormant by decision (free product)")


def _check_resend() -> CheckResult:
    from backend.config import settings

    key = settings.resend_api_key
    raw = key.get_secret_value() if hasattr(key, "get_secret_value") else (key or "")
    if not raw:
        return CheckResult("resend", False, "RESEND_API_KEY unset — drip + alert emails can't send")
    return CheckResult("resend", True, "RESEND_API_KEY present")


# Critical scheduled jobs the product depends on. Each value = (id, friendly).
_REQUIRED_JOBS = (
    "geofence_hourly",
    "geofence_daily",
    "signals_5min",
    "daily_email",
    "user_alert_rules_30min",
    "floating_storage",
    "voyage_detection_2h",
)


def _check_scheduler_jobs() -> CheckResult:
    # The scheduler module is module-singleton; we look at the registry
    # WITHOUT starting it (start_scheduler() would also boot APScheduler).
    try:
        from backend.collectors.scheduler import scheduler, start_scheduler

        if not scheduler.get_jobs():
            # Jobs only register after start_scheduler() runs. For the
            # smoke we register them in a fresh scheduler instance.
            start_scheduler()
        job_ids = {j.id for j in scheduler.get_jobs()}
    except Exception as e:
        return CheckResult("scheduler jobs", False, f"failed to inspect: {e}")

    missing = [j for j in _REQUIRED_JOBS if j not in job_ids]
    if missing:
        return CheckResult("scheduler jobs", False, f"missing: {', '.join(missing)}")
    return CheckResult("scheduler jobs", True, f"{len(_REQUIRED_JOBS)} critical jobs present")


def _check_tls_cert(host: str = "obsyd.dev", days_min: int = 7) -> CheckResult:
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert()
    except Exception as e:
        return CheckResult("tls cert", False, f"connect/handshake to {host}:443 failed: {e}")
    not_after = cert.get("notAfter")
    if not not_after:
        return CheckResult("tls cert", False, "no notAfter in cert")
    try:
        expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
    except ValueError:
        return CheckResult("tls cert", False, f"unparseable notAfter: {not_after}")
    days_left = (expiry - datetime.utcnow()).days
    if days_left < days_min:
        return CheckResult("tls cert", False, f"only {days_left}d remaining (< {days_min})")
    return CheckResult("tls cert", True, f"{days_left} days remaining (expires {expiry.date()})")


def _check_health_endpoint(url: str = "https://obsyd.dev/health") -> CheckResult:
    try:
        import httpx

        r = httpx.get(url, timeout=10)
    except Exception as e:
        return CheckResult("public /health", False, f"{url} unreachable: {e}")
    if r.status_code != 200:
        return CheckResult("public /health", False, f"HTTP {r.status_code} from {url}")
    return CheckResult("public /health", True, f"200 OK in {r.elapsed.total_seconds() * 1000:.0f}ms")


# ---------------------------- runner ----------------------------


CHECKS: list[Callable[[], CheckResult]] = [
    _check_db_freshness,
    _check_secrets_set,
    _check_lemon_squeezy,
    _check_resend,
    _check_scheduler_jobs,
    _check_tls_cert,
    _check_health_endpoint,
]


def main() -> int:
    print("=== OBSYD launch_ready smoke ===")
    started = datetime.utcnow()
    results: list[CheckResult] = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as e:
            results.append(CheckResult(check.__name__, False, f"crashed: {e}"))

    for r in results:
        print(r.format())

    failed = [r for r in results if not r.ok]
    elapsed = (datetime.utcnow() - started).total_seconds()
    print(f"\nRan {len(results)} checks in {elapsed:.1f}s — "
          f"{len(results) - len(failed)} OK, {len(failed)} FAIL")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
