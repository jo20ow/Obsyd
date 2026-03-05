from datetime import datetime

from sqlalchemy import String, Float, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class ThermalHotspot(Base):
    """NASA FIRMS thermal hotspot detections (VIIRS satellite)."""
    __tablename__ = "thermal_hotspots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    brightness: Mapped[float] = mapped_column(Float)  # brightness temperature (Kelvin)
    confidence: Mapped[str] = mapped_column(String, default="")  # low/nominal/high
    area_name: Mapped[str] = mapped_column(String, index=True)
    satellite: Mapped[str] = mapped_column(String, default="VIIRS")
    acq_date: Mapped[str] = mapped_column(String, default="")  # YYYY-MM-DD
    acq_time: Mapped[str] = mapped_column(String, default="")  # HHMM
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
