"""API keys + metered usage — Phase 2 of the premium build.

An ApiKey is a bearer credential for the data API, owned by an email (the
desk's identity unit — there is deliberately no users table). The key's TIER
is never stored on the key: every request resolves the owner's newest
Subscription exactly like the cookie path (require_pro's doctrine — a stored
tier would outlive a refund). The raw key is shown ONCE at creation; only a
SHA-256 hash is stored (API keys are high-entropy random tokens, so a fast
hash is the correct primitive — bcrypt exists for low-entropy passwords).

ApiUsageDaily is the metering ledger: ONE row per (day, key), upserted by the
scheduler's aggregate flush (backend/metering.py) — never written per request
(single uvicorn worker + SQLite WAL: per-request writes on the read path
would serialize reads behind the write lock).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: SHA-256 hex of the full raw key ("obsyd_…"). Unique — the lookup index.
    key_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    #: First characters of the raw key ("obsyd_ab12cd34") — display/identify
    #: only, never enough to reconstruct the credential.
    prefix: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    #: Set = revoked. Rows are never deleted — the usage ledger keys on id.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: Maintained by the metering flush (aggregate, minutes-granular), not per
    #: request.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ApiUsageDaily(Base):
    __tablename__ = "api_usage_daily"

    #: UTC day "YYYY-MM-DD".
    day: Mapped[str] = mapped_column(String, primary_key=True)
    key_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Data points served (the volume axis every comparable vendor bills on).
    #: Counted where the endpoint knows its row count (/api/v1/series); a
    #: request without a count still increments `requests`.
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
