"""ATLAS — per-country economic data for the map/globe layer.

Node 1: per-country energy from EIA International Energy Statistics (public domain).
`countryRegionId` is the EIA native key and is ISO-3166-1 alpha-3, so we store it as
`iso3` directly (no code→ISO map needed); only true countries (countryRegionTypeId='c')
are persisted — regional aggregates (World, OPEC, OECD, …) are excluded at ingest.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class CountryEnergy(Base):
    __tablename__ = "country_energy"
    __table_args__ = (
        UniqueConstraint("iso3", "product", "activity", "period", name="uq_country_energy"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    iso3: Mapped[str] = mapped_column(String, index=True)          # ISO-3166-1 alpha-3
    country_name: Mapped[str] = mapped_column(String, default="")
    product: Mapped[str] = mapped_column(String, index=True)       # e.g. "petroleum"
    activity: Mapped[str] = mapped_column(String)                  # "production" | "consumption"
    period: Mapped[str] = mapped_column(String)                    # year "YYYY" (EIA international is annual)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String, default="")          # pinned per product (e.g. "TBPD")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
