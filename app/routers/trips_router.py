from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..database import get_db
from ..models import User, Vehicle
from ..schemas import TripPlan, TripPlanRequest
from ..services.trip_planner import plan_trip
from .chargers_router import charger_to_out

router = APIRouter(prefix="/trips", tags=["trips"])


@router.post("/plan", response_model=TripPlan)
async def plan(
    body: TripPlanRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vehicle = (
        await db.execute(
            select(Vehicle).where(Vehicle.id == body.vehicle_id, Vehicle.user_id == user.id)
        )
    ).scalar_one_or_none()
    if vehicle is None:
        raise HTTPException(404, "Vehicle not found")

    departure_soc = body.departure_soc if body.departure_soc is not None else vehicle.battery_soc

    result = await plan_trip(
        db,
        (body.origin.lat, body.origin.lng),
        (body.destination.lat, body.destination.lng),
        vehicle,
        departure_soc,
    )

    # serialize ORM chargers in stops
    stops = []
    for s in result["stops"]:
        stops.append(dict(
            charger=charger_to_out(s["charger"]),
            backup_charger=charger_to_out(s["backup_charger"]) if s["backup_charger"] else None,
            arrival_soc=s["arrival_soc"],
            target_soc=s["target_soc"],
            dwell_minutes=s["dwell_minutes"],
            energy_to_add_kwh=s["energy_to_add_kwh"],
            estimated_cost=s["estimated_cost"],
        ))
    result["stops"] = stops
    return result
