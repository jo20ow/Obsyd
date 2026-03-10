from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class VesselPosition(Base):
    """AIS vessel position within a geofence zone."""

    __tablename__ = "vessel_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mmsi: Mapped[str] = mapped_column(String, index=True)
    ship_name: Mapped[str] = mapped_column(String, default="")
    ship_type: Mapped[int] = mapped_column(Integer)  # AIS type 80-89 = tanker
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    sog: Mapped[float] = mapped_column(Float)  # Speed Over Ground (knots)
    cog: Mapped[float] = mapped_column(Float)  # Course Over Ground
    heading: Mapped[float] = mapped_column(Float, default=0.0)
    zone: Mapped[str] = mapped_column(String, index=True)  # geofence zone name
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GlobalVesselPosition(Base):
    """All AIS vessel positions from AISHub (global, not zone-filtered)."""

    __tablename__ = "global_vessel_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mmsi: Mapped[str] = mapped_column(String, index=True)
    ship_name: Mapped[str] = mapped_column(String, default="")
    ship_type: Mapped[int] = mapped_column(Integer)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    sog: Mapped[float] = mapped_column(Float)
    cog: Mapped[float] = mapped_column(Float)
    is_tanker: Mapped[bool] = mapped_column(Integer, default=False)  # SQLite: 0/1
    zone: Mapped[str | None] = mapped_column(String, nullable=True, default=None)


class GeofenceEvent(Base):
    """Aggregated geofence events (tanker counts, dwell times, anomalies)."""

    __tablename__ = "geofence_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zone: Mapped[str] = mapped_column(String, index=True)
    date: Mapped[str] = mapped_column(String)
    tanker_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_dwell_hours: Mapped[float] = mapped_column(Float, default=0.0)
    slow_movers: Mapped[int] = mapped_column(Integer, default=0)  # SOG < 0.5 kn
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FloatingStorageEvent(Base):
    """Tanker stationary for 7+ days — potential floating storage."""

    __tablename__ = "floating_storage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mmsi: Mapped[str] = mapped_column(String, index=True)
    ship_name: Mapped[str] = mapped_column(String, default="")
    ship_type: Mapped[int] = mapped_column(Integer, default=80)
    zone: Mapped[str] = mapped_column(String, default="")
    latitude: Mapped[float] = mapped_column(Float, default=0.0)
    longitude: Mapped[float] = mapped_column(Float, default=0.0)
    first_seen: Mapped[datetime] = mapped_column(DateTime)
    last_seen: Mapped[datetime] = mapped_column(DateTime)
    duration_days: Mapped[float] = mapped_column(Float, default=0.0)
    avg_sog: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String, default="active")  # active | resolved
