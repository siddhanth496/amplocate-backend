from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import cache
from ..auth import get_current_user
from ..database import get_db
from ..models import ChargeSession, Charger, Report, User, Vehicle, utcnow
from ..schemas import (
    ChargerDetail, ChargerOut, ReportCreate, ReportOut, SessionCreate, SessionEnd,
)
from ..services import reliability
from ..services.geo import bounding_box, haversine_km
from typing import Optional

router = APIRouter(prefix="/chargers", tags=["chargers"])


def charger_to_out(c: Charger, distance_km: Optional[float] = None,
                   vehicle_connectors: Optional[set[str]] = None) -> dict:
    rel = c.reliability
    compatible = None
    if vehicle_connectors is not None:
        compatible = bool(vehicle_connectors & {conn["type"] for conn in c.connectors})
    return dict(
        id=c.id, name=c.name, operator=c.operator, address=c.address, city=c.city,
        lat=c.lat, lng=c.lng, connectors=c.connectors, price_per_kwh=c.price_per_kwh,
        status=c.status, is_p2p=c.is_p2p, amenities=c.amenities,
        reliability_score=reliability.effective_score(rel),
        last_verified_at=rel.last_verified_at if rel else None,
        distance_km=round(distance_km, 2) if distance_km is not None else None,
        compatible=compatible,
    )


@router.get("/nearby", response_model=list[ChargerOut])
async def nearby(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = Query(10, le=100),
    connector_type: Optional[str] = None,
    min_power_kw: Optional[float] = None,
    min_reliability: Optional[float] = Query(None, ge=0, le=1),
    vehicle_id: Optional[str] = None,
    limit: int = Query(50, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Validate vehicle ownership up front so a cache hit can't leak a 404 path.
    vehicle_connectors = None
    if vehicle_id:
        v = (
            await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.user_id == user.id))
        ).scalar_one_or_none()
        if v is None:
            raise HTTPException(404, "Vehicle not found")
        vehicle_connectors = set(v.connector_types)

    # Cache the expensive geo scan. Coords are quantised to ~100 m so small map
    # nudges reuse the same entry. Reliability decays slowly, so a short TTL is
    # fine; writes (reports/sessions) invalidate the whole prefix immediately.
    cache_key = (
        f"{cache.NEARBY_PREFIX}{round(lat, 3)}:{round(lng, 3)}:{radius_km}:"
        f"{connector_type}:{min_power_kw}:{min_reliability}:{vehicle_id}:{limit}"
    )
    cached = await cache.get_json(cache_key)
    if cached is not None:
        return cached

    min_lat, max_lat, min_lng, max_lng = bounding_box(lat, lng, radius_km)
    rows = (
        await db.execute(
            select(Charger)
            .options(selectinload(Charger.reliability))
            .where(Charger.lat.between(min_lat, max_lat), Charger.lng.between(min_lng, max_lng))
        )
    ).scalars().all()

    results = []
    for c in rows:
        d = haversine_km(lat, lng, c.lat, c.lng)
        if d > radius_km:
            continue
        if connector_type and connector_type not in {conn["type"] for conn in c.connectors}:
            continue
        if min_power_kw and not any(conn["power_kw"] >= min_power_kw for conn in c.connectors):
            continue
        out = charger_to_out(c, d, vehicle_connectors)
        if min_reliability is not None and out["reliability_score"] < min_reliability:
            continue
        results.append(out)

    # compatible chargers first, then by distance (PRD 7.3)
    results.sort(key=lambda r: (not r["compatible"] if r["compatible"] is not None else False,
                                r["distance_km"]))
    results = results[:limit]
    await cache.set_json(cache_key, results)
    return results


@router.get("/{charger_id}", response_model=ChargerDetail)
async def detail(
    charger_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    c = (
        await db.execute(
            select(Charger).options(selectinload(Charger.reliability)).where(Charger.id == charger_id)
        )
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "Charger not found")
    reports = (
        await db.execute(
            select(Report).where(Report.charger_id == charger_id)
            .order_by(Report.created_at.desc()).limit(10)
        )
    ).scalars().all()
    out = charger_to_out(c)
    out["recent_reports"] = [ReportOut.model_validate(r) for r in reports]
    return out


@router.post("/{charger_id}/reports", response_model=ReportOut, status_code=201)
async def create_report(
    charger_id: str,
    body: ReportCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    c = (await db.execute(select(Charger).where(Charger.id == charger_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "Charger not found")

    # basic spam guard: one report per user per charger per 10 minutes
    from datetime import timedelta
    recent = (
        await db.execute(
            select(Report).where(
                Report.charger_id == charger_id,
                Report.user_id == user.id,
                Report.created_at > utcnow() - timedelta(minutes=10),
            )
        )
    ).scalars().first()
    if recent is not None:
        raise HTTPException(429, "You already reported this charger recently")

    report = Report(charger_id=charger_id, user_id=user.id, report_type=body.report_type,
                    comment=body.comment, photo_url=body.photo_url)
    db.add(report)
    await db.commit()
    await reliability.recompute(db, charger_id)
    await cache.delete_prefix(cache.NEARBY_PREFIX)  # reliability changed
    await db.refresh(report)
    return report


@router.post("/sessions/start", status_code=201)
async def start_session(
    body: SessionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    c = (await db.execute(select(Charger).where(Charger.id == body.charger_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "Charger not found")
    s = ChargeSession(charger_id=body.charger_id, user_id=user.id, vehicle_id=body.vehicle_id)
    db.add(s)
    await db.commit()
    return {"session_id": s.id}


@router.post("/sessions/{session_id}/end")
async def end_session(
    session_id: str,
    body: SessionEnd,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    s = (
        await db.execute(
            select(ChargeSession).where(ChargeSession.id == session_id, ChargeSession.user_id == user.id)
        )
    ).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, "Session not found")
    s.ended_at = utcnow()
    s.successful = body.successful
    s.energy_kwh = body.energy_kwh
    # session outcome is a reliability signal (stored as a synthetic report)
    db.add(Report(
        charger_id=s.charger_id, user_id=user.id,
        report_type="working" if body.successful else "broken",
        comment="auto: charge session " + ("succeeded" if body.successful else "failed"),
    ))
    await db.commit()
    await reliability.recompute(db, s.charger_id)
    await cache.delete_prefix(cache.NEARBY_PREFIX)  # reliability changed
    return {"ok": True}
