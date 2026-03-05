from datetime import datetime

from sqlalchemy import String, Float, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class WeatherAlert(Base):
    """Active weather alerts from NOAA NWS API."""
    __tablename__ = "weather_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    event: Mapped[str] = mapped_column(String)  # e.g. "Hurricane Warning"
    severity: Mapped[str] = mapped_column(String)  # Extreme, Severe, Moderate, Minor
    headline: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    area: Mapped[str] = mapped_column(String, default="")
    latitude: Mapped[float] = mapped_column(Float, nullable=True)
    longitude: Mapped[float] = mapped_column(Float, nullable=True)
    onset: Mapped[str] = mapped_column(String, default="")
    expires_at: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
