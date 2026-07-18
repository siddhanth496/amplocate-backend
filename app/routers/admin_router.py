"""Data-import admin endpoints. Available only in dev mode (demo deployments);
lock behind a proper admin role before production hardening."""
import asyncio
from typing import Optional

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


class StatiqImport(BaseModel):
    city: Optional[str] = Field(default=None, description="city page slug, e.g. bengaluru-ev-charging-station")
    sitemap: bool = Field(default=False, description="discover every station via sitemap.xml")
    max: int = Field(default=0, ge=0, description="cap number of stations (0 = no cap)")


@router.post("/import/statiq", status_code=202)
async def import_statiq(body: StatiqImport, user: User = Depends(get_current_user)):
    """Import Statiq stations from statiq.in (public pages).

    Pass a `city` slug for a single city, or `sitemap: true` for the whole
    network. Crawling is permitted by Statiq's robots.txt; keep it polite and
    review their Terms of Service before commercial reuse. See STATIQ_IMPORT.md."""
    _dev_only()
    if not body.city and not body.sitemap:
        raise HTTPException(400, "Provide a `city` slug or set `sitemap: true`")
    from ..seed.statiq_import import run as statiq_run
    asyncio.create_task(
        statiq_run(sitemap=body.sitemap, city=body.city, max_results=body.max,
                   concurrency=settings.statiq_import_concurrency)
    )
    return {"status": "started", "source": "statiq", "scope": body.city or "sitemap"}


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
        statiq = (
            await db.execute(select(func.count(Charger.id)).where(Charger.external_id.like("statiq-%")))
        ).scalar()
    return {
        "total_chargers": total,
        "imported_chargers": imported,
        "statiq_chargers": statiq,
        "import_running": STATUS["running"],
        "last_run": STATUS["last_run"],
        "ocm_api_key_configured": bool(settings.ocm_api_key),
        "google_api_key_configured": bool(settings.google_maps_api_key),
    }
