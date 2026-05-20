"""
User-defined alert rules (Pro-tier feature).

Distinct from `backend.models.alerts.Alert`, which is the system-wide
signal-engine output. AlertRule + UserAlertEvent are per-user: each
Pro subscriber can configure their own triggers and gets their own
notification inbox.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class AlertRule(Base):
    """A user-configured rule. `params` is a JSON blob whose schema
    depends on `rule_type` (see backend.signals.user_alert_rules).
    """

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    rule_type: Mapped[str] = mapped_column(String, nullable=False)
    # Human-friendly label (defaulted by the template if omitted).
    name: Mapped[str] = mapped_column(String, default="")
    # JSON-encoded parameters specific to the rule_type.
    params: Mapped[str] = mapped_column(Text, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Rate-limit: after triggering, don't fire again until this passes.
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserAlertEvent(Base):
    """A trigger occurrence for a specific AlertRule. One row per fire,
    even if the same rule re-triggers later (each fires after cooldown).
    Powers the user's notification inbox.
    """

    __tablename__ = "user_alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id"), index=True)
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    title: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="")
    # JSON-encoded snapshot of the matched data (e.g. {zone, value, baseline}).
    payload: Mapped[str] = mapped_column(Text, default="{}")
    seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
