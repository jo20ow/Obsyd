from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    lemon_squeezy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # status values:
    #   "active"      — paid LS subscription (Pro)
    #   "trialing"    — in-app 14-day trial without card (Pro until trial_ends_at)
    #   "past_due"    — payment failed, grace period (treated as Pro for now)
    #   "cancelled"   — user cancelled, still Pro until period end
    #   "expired"     — past period end, no Pro
    status: Mapped[str] = mapped_column(String, default="active")
    plan: Mapped[str] = mapped_column(String, default="pro")
    customer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    variant_id: Mapped[str | None] = mapped_column(String, nullable=True)
    update_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Set when a Pro trial is started (without LS). When trial_ends_at < now AND
    # status == "trialing", the subscription is effectively expired. Cleared
    # when the user converts to a paid LS subscription.
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Onboarding drip progress for in-app trials (and any subscriber we want
    # to nudge): 0=welcome sent, 1=day-2 sent, 2=day-5 sent, 3=done.
    # NULL means the user is not (yet) in the drip flow.
    drip_stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
