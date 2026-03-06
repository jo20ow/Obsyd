from sqlalchemy import String, Float, Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class DailyFleetSummary(Base):
    """Daily aggregate of global vessel positions."""
    __tablename__ = "daily_fleet_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, unique=True, index=True)
    total_vessels: Mapped[int] = mapped_column(Integer, default=0)
    tanker_count: Mapped[int] = mapped_column(Integer, default=0)
    cargo_count: Mapped[int] = mapped_column(Integer, default=0)
    container_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_sog: Mapped[float] = mapped_column(Float, default=0.0)
    anchored_count: Mapped[int] = mapped_column(Integer, default=0)
    atlantic_count: Mapped[int] = mapped_column(Integer, default=0)
    pacific_count: Mapped[int] = mapped_column(Integer, default=0)
    indian_ocean_count: Mapped[int] = mapped_column(Integer, default=0)
    mediterranean_count: Mapped[int] = mapped_column(Integer, default=0)
