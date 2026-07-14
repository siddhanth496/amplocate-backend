import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from typing import Optional


def uid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ConnectorType(str, enum.Enum):
    CCS2 = "CCS2"
    CHADEMO = "CHAdeMO"
    TYPE2_AC = "Type2_AC"
    BHARAT_AC = "Bharat_AC001"
    BHARAT_DC = "Bharat_DC001"
    GBT = "GB/T"
    WALL_3PIN = "Wall_3pin"


class ChargerStatus(str, enum.Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    BROKEN = "broken"
    UNKNOWN = "unknown"


class ReportType(str, enum.Enum):
    WORKING = "working"
    BROKEN = "broken"
    ICE_BLOCKED = "ice_blocked"
    QUEUE = "queue"
    CHECK_IN = "check_in"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    phone: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="user")


class OtpCode(Base):
    __tablename__ = "otp_codes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    phone: Mapped[str] = mapped_column(String, index=True)
    code: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    make: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String, default="4W")  # 2W / 3W / 4W
    battery_kwh: Mapped[float] = mapped_column(Float)
    efficiency_wh_per_km: Mapped[float] = mapped_column(Float)
    connector_types: Mapped[list] = mapped_column(JSON)  # list[str]
    max_dc_power_kw: Mapped[float] = mapped_column(Float, default=0.0)
    battery_soc: Mapped[float] = mapped_column(Float, default=100.0)  # user-entered %
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped["User"] = relationship(back_populates="vehicles")


class Charger(Base):
    __tablename__ = "chargers"
    __table_args__ = (Index("ix_chargers_lat_lng", "lat", "lng"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    external_id: Mapped[Optional[str]] = mapped_column(String, unique=True, nullable=True)  # OCM id etc.
    name: Mapped[str] = mapped_column(String)
    operator: Mapped[str] = mapped_column(String, default="Unknown")
    address: Mapped[str] = mapped_column(String, default="")
    city: Mapped[str] = mapped_column(String, default="", index=True)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    connectors: Mapped[list] = mapped_column(JSON)  # [{"type": "CCS2", "power_kw": 50, "count": 2}]
    price_per_kwh: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default=ChargerStatus.UNKNOWN.value)
    is_p2p: Mapped[bool] = mapped_column(Boolean, default=False)
    amenities: Mapped[list] = mapped_column(JSON, default=list)  # ["cafe", "restroom", ...]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    reliability: Mapped["Optional[ReliabilityScore]"] = relationship(
        back_populates="charger", uselist=False
    )


class ReliabilityScore(Base):
    __tablename__ = "reliability_scores"

    charger_id: Mapped[str] = mapped_column(ForeignKey("chargers.id"), primary_key=True)
    score: Mapped[float] = mapped_column(Float, default=0.5)  # 0..1
    # baseline evidence (operator history / seed) so fresh reports adjust rather than replace
    baseline_pos: Mapped[float] = mapped_column(Float, default=0.0)
    baseline_neg: Mapped[float] = mapped_column(Float, default=0.0)
    positive_signals: Mapped[int] = mapped_column(Integer, default=0)
    negative_signals: Mapped[int] = mapped_column(Integer, default=0)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    charger: Mapped["Charger"] = relationship(back_populates="reliability")


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (Index("ix_reports_charger_created", "charger_id", "created_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    charger_id: Mapped[str] = mapped_column(ForeignKey("chargers.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    report_type: Mapped[str] = mapped_column(String)
    comment: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    photo_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChargeSession(Base):
    __tablename__ = "charge_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    charger_id: Mapped[str] = mapped_column(ForeignKey("chargers.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    vehicle_id: Mapped[Optional[str]] = mapped_column(ForeignKey("vehicles.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    energy_kwh: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    successful: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
