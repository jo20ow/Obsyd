"""
Per-user watchlist (Pro-tier): the materials and zones a user wants OBSYD
to monitor for them. Keyed on `email` like AlertRule — this is the per-user
primitive the "Personal Supply-Watch" product is built on (drives the
personalised daily brief and the wedge→watch conversion loop).
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class WatchlistItem(Base):
    """One saved item. `kind` ∈ {"material", "zone"}; `key` is the catalog
    key (e.g. "cobalt", "hormuz", "DE_LU"). `label` is denormalised for
    display so the brief/UI can render without re-deriving it from the
    catalog. (email, kind, key) is unique — saving the same thing twice
    is a no-op.
    """

    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("email", "kind", "key", name="uq_watch_email_kind_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # "material" | "zone"
    key: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
