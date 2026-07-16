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
