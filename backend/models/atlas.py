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


class CountryMacro(Base):
    """Per-country macro indicators from World Bank Open Data (CC BY 4.0, no key).

    ISO-3 keyed (matches CountryEnergy → clean cross-source join). Only real countries are
    stored; World Bank regional/income aggregates (region == 'Aggregates') are excluded at
    ingest. `metric` is a friendly key (e.g. "gdp_usd"); `indicator_code` is the WB code.
    """

    __tablename__ = "country_macro"
    __table_args__ = (
        UniqueConstraint("iso3", "metric", "period", name="uq_country_macro"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    iso3: Mapped[str] = mapped_column(String, index=True)
    country_name: Mapped[str] = mapped_column(String, default="")
    metric: Mapped[str] = mapped_column(String, index=True)        # e.g. "gdp_usd", "trade_pct_gdp"
    indicator_code: Mapped[str] = mapped_column(String, default="")  # World Bank indicator code
    period: Mapped[str] = mapped_column(String)                    # year "YYYY" (WB is annual)
    value: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CountryResource(Base):
    """Per-country mineral/metal mine production from USGS Mineral Commodity Summaries.

    Public domain. ISO-3 keyed (USGS country names mapped via usgs_country_map; aggregates
    excluded). `commodity` is a friendly key (e.g. "lithium", "rare_earths").
    """

    __tablename__ = "country_resource"
    __table_args__ = (
        UniqueConstraint("iso3", "commodity", "period", name="uq_country_resource"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    iso3: Mapped[str] = mapped_column(String, index=True)
    country_name: Mapped[str] = mapped_column(String, default="")
    commodity: Mapped[str] = mapped_column(String, index=True)     # e.g. "lithium", "cobalt"
    period: Mapped[str] = mapped_column(String)                    # year "YYYY"
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
