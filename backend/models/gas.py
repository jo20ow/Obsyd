"""EU gas balance schema — 8 tables.

Phase 1 populates points/flows/storage/lng. power_burn/weather/demand_model/
balance are created now (auto via Base.metadata.create_all) but stay empty —
clean seams for Phase 2-4. All flow/stock values are GWh (canonical), except
stock and inventory which keep TWh for display per the spec.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class GasPoint(Base):
    """ENTSOG point registry, classified by counterparty into a flow class."""

    __tablename__ = "gas_points"

    point_id: Mapped[str] = mapped_column(String, primary_key=True)  # operator|point|direction
    name: Mapped[str] = mapped_column(String, default="")
    operator: Mapped[str] = mapped_column(String, default="")
    point_class: Mapped[str | None] = mapped_column("class", String, nullable=True, index=True)
    counterparty: Mapped[str | None] = mapped_column(String, nullable=True)
    active: Mapped[int] = mapped_column(Integer, default=1)


class GasFlow(Base):
    """Daily physical flow at a point (GWh/day)."""

    __tablename__ = "gas_flows"

    date: Mapped[str] = mapped_column(String, primary_key=True)           # YYYY-MM-DD (gas day)
    point_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    direction: Mapped[str] = mapped_column(String, primary_key=True)      # entry / exit
    value_gwh: Mapped[float] = mapped_column(Float)
    provisional: Mapped[int] = mapped_column(Integer, default=1)          # 1 until confirmed
    interpolated: Mapped[int] = mapped_column(Integer, default=0)         # 1 if forward-filled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GasStorage(Base):
    """AGSI EU-aggregate storage actuals."""

    __tablename__ = "gas_storage"

    date: Mapped[str] = mapped_column(String, primary_key=True)
    stock_twh: Mapped[float | None] = mapped_column(Float, nullable=True)        # gasInStorage (TWh)
    injection_gwh: Mapped[float | None] = mapped_column(Float, nullable=True)    # GWh/d
    withdrawal_gwh: Mapped[float | None] = mapped_column(Float, nullable=True)   # GWh/d
    fill_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GasLng(Base):
    """ALSI EU-aggregate LNG send-out + inventory."""

    __tablename__ = "gas_lng"

    date: Mapped[str] = mapped_column(String, primary_key=True)
    send_out_gwh: Mapped[float | None] = mapped_column(Float, nullable=True)     # GWh/d (primary LNG supply)
    inventory_twh: Mapped[float | None] = mapped_column(Float, nullable=True)    # TWh (context)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GasStorageCountry(Base):
    """AGSI storage PER COUNTRY — the level a power desk actually needs.

    "EU storage is 51% full" is nearly useless to anyone reading a bidding zone: gas is not
    fungible across borders, and the EU number averages a full Germany with an empty
    Ukraine. The per-country rows have been inside every payload we fetched since 2023 and
    were thrown away at read time (`gie._eu_row` kept only `code == "eu"`).

    `region` records WHICH root of the payload the row came from, because that is a fact
    about the data and not an implementation detail: `eu` is the EU aggregate's children,
    `ne` is Non-EU — and `ne` is where Ukraine (77 TWh) and the real post-Brexit GB live.
    Dropping it, as the old code did, silently deleted the two most trade-relevant
    non-member countries in the file.
    """

    __tablename__ = "gas_storage_country"

    date: Mapped[str] = mapped_column(String, primary_key=True)
    country: Mapped[str] = mapped_column(String, primary_key=True)   # "DE", "UA", "GB*"
    region: Mapped[str] = mapped_column(String)                      # "eu" | "ne"
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    stock_twh: Mapped[float | None] = mapped_column(Float, nullable=True)
    injection_gwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    withdrawal_gwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The capacity columns the EU-only schema never had — what makes a fill % readable as a
    # volume, and the only honest way to show TWh (beside the country's OWN working gas).
    working_gas_twh: Mapped[float | None] = mapped_column(Float, nullable=True)
    injection_capacity_gwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    withdrawal_capacity_gwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    trend: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GasLngCountry(Base):
    """ALSI LNG send-out + inventory PER COUNTRY. Same story as GasStorageCountry."""

    __tablename__ = "gas_lng_country"

    date: Mapped[str] = mapped_column(String, primary_key=True)
    country: Mapped[str] = mapped_column(String, primary_key=True)
    region: Mapped[str] = mapped_column(String)                      # "eu" | "ne" — never "ai"
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    send_out_gwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    inventory_twh: Mapped[float | None] = mapped_column(Float, nullable=True)
    dtmi_twh: Mapped[float | None] = mapped_column(Float, nullable=True)   # declared max inventory
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ─── Phase 2-4 seams: created, unpopulated in Phase 1 ────────────────────────


class GasPowerBurn(Base):
    """ENTSO-E gas power burn (Phase 2)."""

    __tablename__ = "gas_power_burn"

    date: Mapped[str] = mapped_column(String, primary_key=True)
    gen_gwh_el: Mapped[float | None] = mapped_column(Float, nullable=True)
    implied_gas_gwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    efficiency: Mapped[float | None] = mapped_column(Float, nullable=True)


class GasWeather(Base):
    """Open-Meteo HDD per country (Phase 3)."""

    __tablename__ = "gas_weather"

    date: Mapped[str] = mapped_column(String, primary_key=True)
    country: Mapped[str] = mapped_column(String, primary_key=True)
    t_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    hdd: Mapped[float | None] = mapped_column(Float, nullable=True)


class GasDemandModel(Base):
    """Modeled heating + industrial demand (Phase 3)."""

    __tablename__ = "gas_demand_model"

    date: Mapped[str] = mapped_column(String, primary_key=True)
    heat_gwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    industrial_gwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)


class GasBalance(Base):
    """Daily balance + residual signal (Phase 4)."""

    __tablename__ = "gas_balance"

    date: Mapped[str] = mapped_column(String, primary_key=True)
    supply_gwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    demand_gwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    exports_gwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    implied_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    residual: Mapped[float | None] = mapped_column(Float, nullable=True)
    residual_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    z_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    flag: Mapped[str | None] = mapped_column(String, nullable=True)
