from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ---------- Auth ----------

class OtpRequest(BaseModel):
    phone: str = Field(pattern=r"^\+?[0-9]{10,13}$")


class OtpRequestResponse(BaseModel):
    message: str
    dev_otp: Optional[str] = None  # only populated in dev mode


class OtpVerify(BaseModel):
    phone: str
    code: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    is_new_user: bool


class ProfileUpdate(BaseModel):
    name: Optional[str] = None


class UserOut(BaseModel):
    id: str
    phone: str
    name: Optional[str]

    class Config:
        from_attributes = True


# ---------- Vehicles ----------

class VehicleCreate(BaseModel):
    catalog_id: Optional[str] = None  # pick from catalog, or provide fields manually
    make: Optional[str] = None
    model: Optional[str] = None
    category: str = "4W"
    battery_kwh: Optional[float] = None
    efficiency_wh_per_km: Optional[float] = None
    connector_types: Optional[list[str]] = None
    max_dc_power_kw: Optional[float] = None
    battery_soc: float = 100.0
    is_default: bool = True


class VehicleUpdate(BaseModel):
    battery_soc: Optional[float] = Field(default=None, ge=0, le=100)
    is_default: Optional[bool] = None


class VehicleOut(BaseModel):
    id: str
    make: str
    model: str
    category: str
    battery_kwh: float
    efficiency_wh_per_km: float
    connector_types: list[str]
    max_dc_power_kw: float
    battery_soc: float
    is_default: bool

    class Config:
        from_attributes = True


class CatalogEntry(BaseModel):
    id: str
    make: str
    model: str
    category: str
    battery_kwh: float
    efficiency_wh_per_km: float
    connector_types: list[str]
    max_dc_power_kw: float


# ---------- Chargers ----------

class ConnectorOut(BaseModel):
    type: str
    power_kw: float
    count: int = 1


class ChargerOut(BaseModel):
    id: str
    name: str
    operator: str
    address: str
    city: str
    lat: float
    lng: float
    connectors: list[dict[str, Any]]
    price_per_kwh: Optional[float]
    status: str
    is_p2p: bool
    amenities: list[str]
    reliability_score: float
    last_verified_at: Optional[datetime]
    distance_km: Optional[float] = None
    compatible: Optional[bool] = None


class ChargerDetail(ChargerOut):
    recent_reports: list["ReportOut"] = []


# ---------- Reports ----------

class ReportCreate(BaseModel):
    report_type: str
    comment: Optional[str] = None
    photo_url: Optional[str] = None

    @field_validator("report_type")
    @classmethod
    def valid_type(cls, v: str) -> str:
        allowed = {"working", "broken", "ice_blocked", "queue", "check_in"}
        if v not in allowed:
            raise ValueError(f"report_type must be one of {sorted(allowed)}")
        return v


class ReportOut(BaseModel):
    id: str
    charger_id: str
    report_type: str
    comment: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Sessions ----------

class SessionCreate(BaseModel):
    charger_id: str
    vehicle_id: Optional[str] = None


class SessionEnd(BaseModel):
    successful: bool
    energy_kwh: Optional[float] = None


# ---------- Trip planning ----------

class LatLng(BaseModel):
    lat: float
    lng: float


class TripPlanRequest(BaseModel):
    origin: LatLng
    destination: LatLng
    vehicle_id: str
    departure_soc: Optional[float] = Field(default=None, ge=0, le=100)
    waypoints: list[LatLng] = []
    # leg_index -> charger_id: force a specific (viable) charger for that leg
    pinned_chargers: dict[str, str] = {}


class TripStop(BaseModel):
    charger: ChargerOut
    arrival_soc: float
    target_soc: float
    dwell_minutes: float
    energy_to_add_kwh: float
    estimated_cost: Optional[float]
    backup_charger: Optional[ChargerOut]
    leg_index: int = 0
    alternatives: list["TripStop"] = []


class TripPlan(BaseModel):
    feasible: bool
    stops: list[TripStop]
    destination_arrival_soc: Optional[float]
    total_distance_km: float
    drive_minutes: float
    total_trip_minutes: Optional[float]
    confidence: str  # high / medium / low
    warnings: list[str] = []
    note: Optional[str] = None
