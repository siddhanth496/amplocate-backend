from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import create_token, get_current_user
from ..config import settings
from ..database import get_db
from ..models import User
from ..schemas import OtpRequest, OtpRequestResponse, OtpVerify, ProfileUpdate, TokenResponse, UserOut
from ..services import otp as otp_service

router = APIRouter(prefix="/auth", tags=["auth"])


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
