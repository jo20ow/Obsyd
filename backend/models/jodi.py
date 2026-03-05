from datetime import datetime

from sqlalchemy import String, Float, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class JODIProduction(Base):
    """JODI Oil World Database — monthly production, consumption, stocks by country."""
    __tablename__ = "jodi_production"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    country: Mapped[str] = mapped_column(String, index=True)  # ISO 2-letter code
    country_name: Mapped[str] = mapped_column(String, default="")
    date: Mapped[str] = mapped_column(String, index=True)  # YYYY-MM
    production: Mapped[float | None] = mapped_column(Float, nullable=True)  # KBBL
    consumption: Mapped[float | None] = mapped_column(Float, nullable=True)  # KBBL (refinery intake)
    stocks: Mapped[float | None] = mapped_column(Float, nullable=True)  # KBBL (closing stock level)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
