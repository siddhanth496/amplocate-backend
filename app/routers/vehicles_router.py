from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..database import get_db
from ..models import User, Vehicle
from ..schemas import CatalogEntry, VehicleCreate, VehicleOut, VehicleUpdate
from ..services.vehicle_catalog import CATALOG, get_entry

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.get("/catalog", response_model=list[CatalogEntry])
async def catalog():
    return CATALOG


@router.post("", response_model=VehicleOut, status_code=201)
async def add_vehicle(
    body: VehicleCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.catalog_id:
        entry = get_entry(body.catalog_id)
        if entry is None:
            raise HTTPException(404, "Unknown catalog_id")
        fields = {k: entry[k] for k in
                  ("make", "model", "category", "battery_kwh", "efficiency_wh_per_km",
                   "connector_types", "max_dc_power_kw")}
    else:
        required = ("make", "model", "battery_kwh", "efficiency_wh_per_km", "connector_types")
        if any(getattr(body, f) is None for f in required):
            raise HTTPException(422, f"Provide catalog_id or all of: {required}")
        fields = dict(
            make=body.make, model=body.model, category=body.category,
            battery_kwh=body.battery_kwh, efficiency_wh_per_km=body.efficiency_wh_per_km,
            connector_types=body.connector_types, max_dc_power_kw=body.max_dc_power_kw or 0.0,
        )

    if body.is_default:
        await db.execute(update(Vehicle).where(Vehicle.user_id == user.id).values(is_default=False))

    vehicle = Vehicle(user_id=user.id, battery_soc=body.battery_soc, is_default=body.is_default, **fields)
    db.add(vehicle)
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


@router.get("", response_model=list[VehicleOut])
async def list_vehicles(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Vehicle).where(Vehicle.user_id == user.id))).scalars().all()
    return rows


@router.patch("/{vehicle_id}", response_model=VehicleOut)
async def update_vehicle(
    vehicle_id: str,
    body: VehicleUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vehicle = (
        await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.user_id == user.id))
    ).scalar_one_or_none()
    if vehicle is None:
        raise HTTPException(404, "Vehicle not found")
    if body.battery_soc is not None:
        vehicle.battery_soc = body.battery_soc
    if body.is_default:
        await db.execute(update(Vehicle).where(Vehicle.user_id == user.id).values(is_default=False))
        vehicle.is_default = True
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


@router.delete("/{vehicle_id}", status_code=204)
async def delete_vehicle(
    vehicle_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vehicle = (
        await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.user_id == user.id))
    ).scalar_one_or_none()
    if vehicle is None:
        raise HTTPException(404, "Vehicle not found")
    await db.delete(vehicle)
    await db.commit()
