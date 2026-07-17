"""Data-import admin endpoints. Available only in dev mode (demo deployments);
lock behind a proper admin role before production hardening."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..config import settings
from ..models import User
from ..seed.regions import NCR_REGIONS, import_regions

router = APIRouter(prefix="/admin", tags=["admin"])


def _dev_only():
    if not settings.dev_mode:
        raise HTTPException(403, "Admin imports are disabled outside dev mode")


class RegionImport(BaseModel):
    lat: float
    lng: float
    radius_km: float = Field(default=20, le=50)
    name: str = "custom"


@router.post("/import/ncr", status_code=202)
async def import_ncr(user: User = Depends(get_current_user)):
    """Kick off a full Delhi-NCR import (OCM + OSM) in the background."""
    _dev_only()
    asyncio.create_task(import_regions(NCR_REGIONS))
    return {"status": "started", "regions": [r[0] for r in NCR_REGIONS]}


@router.post("/import/region", status_code=202)
async def import_region(body: RegionImport, user: User = Depends(get_current_user)):
    _dev_only()
    asyncio.create_task(import_regions([(body.name, body.lat, body.lng, body.radius_km)]))
    return {"status": "started", "region": body.name}


@router.post("/import/google", status_code=202)
async def import_google(body: RegionImport, user: User = Depends(get_current_user)):
    """Import/refresh EV chargers from the official Google Places API for a region.
    Requires GOOGLE_MAPS_API_KEY (Places API New enabled). Run at least monthly —
    Google's caching policy caps stored place data at ~30 days."""
    _dev_only()
    if not settings.google_maps_api_key:
        raise HTTPException(400, "GOOGLE_MAPS_API_KEY is not configured on the server")
    from ..seed.google_places_import import run as google_run
    asyncio.create_task(google_run(body.lat, body.lng, body.radius_km))
    return {"status": "started", "region": body.name, "source": "google_places"}


@router.get("/import/status")
async def import_status(user: User = Depends(get_current_user)):
    """Charger count + result of the last import run (incl. per-region errors)."""
    _dev_only()
    from sqlalchemy import func, select
    from ..database import SessionLocal
    from ..models import Charger
    from ..seed.regions import STATUS

    async with SessionLocal() as db:
        total = (await db.execute(select(func.count(Charger.id)))).scalar()
        imported = (
            await db.execute(select(func.count(Charger.id)).where(Charger.external_id.isnot(None)))
        ).scalar()
    return {
        "total_chargers": total,
        "imported_chargers": imported,
        "import_running": STATUS["running"],
        "last_run": STATUS["last_run"],
        "ocm_api_key_configured": bool(settings.ocm_api_key),
        "google_api_key_configured": bool(settings.google_maps_api_key),
    }
