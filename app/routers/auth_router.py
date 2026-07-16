from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import create_token, get_current_user
from ..config import settings
from ..database import get_db
from ..models import ChargeSession, Charger, Report, User, Vehicle
from ..schemas import OtpRequest, OtpRequestResponse, OtpVerify, ProfileUpdate, TokenResponse, UserOut
from ..services import otp as otp_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me/stats")
async def my_stats(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Aggregate EV stats for the dashboard."""
    sessions = (
        await db.execute(
            select(ChargeSession).where(ChargeSession.user_id == user.id)
            .order_by(ChargeSession.started_at.desc())
        )
    ).scalars().all()
    total = len(sessions)
    successful = sum(1 for s in sessions if s.successful)
    energy = sum(s.energy_kwh or 0 for s in sessions)
    charger_ids = {s.charger_id for s in sessions}

    reports_count = (
        await db.execute(select(func.count(Report.id)).where(Report.user_id == user.id))
    ).scalar()
    vehicles_count = (
        await db.execute(select(func.count(Vehicle.id)).where(Vehicle.user_id == user.id))
    ).scalar()

    # estimated spend: energy * avg price of visited chargers (fallback ₹20/kWh)
    avg_price = None
    if charger_ids:
        avg_price = (
            await db.execute(
                select(func.avg(Charger.price_per_kwh)).where(Charger.id.in_(charger_ids))
            )
        ).scalar()
    est_cost = round(energy * (avg_price or 20.0), 0) if energy else 0

    recent = []
    for s in sessions[:6]:
        charger = (await db.execute(select(Charger).where(Charger.id == s.charger_id))).scalar_one_or_none()
        recent.append(dict(
            charger_id=s.charger_id,
            charger_name=charger.name if charger else "Unknown charger",
            started_at=s.started_at,
            energy_kwh=s.energy_kwh,
            successful=s.successful,
        ))

    return dict(
        sessions_total=total,
        sessions_successful=successful,
        success_rate=round(successful / total, 3) if total else None,
        energy_kwh=round(energy, 2),
        est_cost_inr=est_cost,
        co2_saved_kg=round(energy * 0.62, 1),  # vs petrol equivalent, rough factor
        reports_count=reports_count,
        chargers_visited=len(charger_ids),
        vehicles_count=vehicles_count,
        recent_sessions=recent,
    )


@router.post("/otp/request", response_model=OtpRequestResponse)
async def request_otp(body: OtpRequest, db: AsyncSession = Depends(get_db)):
    code = await otp_service.issue_otp(db, body.phone)
    return OtpRequestResponse(
        message="OTP sent",
        dev_otp=code if settings.dev_mode else None,
    )


@router.post("/otp/verify", response_model=TokenResponse)
async def verify_otp(body: OtpVerify, db: AsyncSession = Depends(get_db)):
    if not await otp_service.verify_otp(db, body.phone, body.code):
        raise HTTPException(400, "Invalid or expired OTP")
    user = (await db.execute(select(User).where(User.phone == body.phone))).scalar_one_or_none()
    is_new = user is None
    if is_new:
        user = User(phone=body.phone)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return TokenResponse(access_token=create_token(user.id), is_new_user=is_new)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserOut)
async def update_profile(
    body: ProfileUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.name is not None:
        user.name = body.name
    await db.commit()
    await db.refresh(user)
    return user
