from datetime import datetime

from sqlalchemy import String, Float, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class PortActivity(Base):
    """Daily port activity and chokepoint transit data from IMF PortWatch."""
    __tablename__ = "port_activity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    port_id: Mapped[str] = mapped_column(String, index=True)  # e.g. "port1114", "chokepoint1"
    port_name: Mapped[str] = mapped_column(String)
    date: Mapped[str] = mapped_column(String, index=True)  # YYYY-MM-DD
    kind: Mapped[str] = mapped_column(String)  # "port" or "chokepoint"

    # Vessel counts
    vessel_count: Mapped[int] = mapped_column(Integer, default=0)
    vessel_count_tanker: Mapped[int] = mapped_column(Integer, default=0)

    # Trade volumes (tonnes) — ports only
    import_total: Mapped[int] = mapped_column(Integer, default=0)
    export_total: Mapped[int] = mapped_column(Integer, default=0)
    import_tanker: Mapped[int] = mapped_column(Integer, default=0)
    export_tanker: Mapped[int] = mapped_column(Integer, default=0)

    # Capacity (DWT) — chokepoints only
    capacity: Mapped[float] = mapped_column(Float, default=0.0)
    capacity_tanker: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Disruption(Base):
    """Port/chokepoint disruption events from IMF PortWatch."""
    __tablename__ = "disruptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String, index=True)
    event_name: Mapped[str] = mapped_column(String)
    event_type: Mapped[str] = mapped_column(String)  # e.g. "conflict", "weather"
    alertlevel: Mapped[int] = mapped_column(Integer, default=0)
    start_date: Mapped[str] = mapped_column(String)  # YYYY-MM-DD
    end_date: Mapped[str] = mapped_column(String, nullable=True)
    affected_port_id: Mapped[str] = mapped_column(String, nullable=True)
    affected_port_name: Mapped[str] = mapped_column(String, nullable=True)
    country: Mapped[str] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
