import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import OtpCode, utcnow

MAX_ATTEMPTS = 5


async def issue_otp(db: AsyncSession, phone: str) -> str:
    """Generate an OTP. In production this would be handed to an SMS gateway
    (MSG91 / Twilio); in dev mode it is returned to the client directly."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    # invalidate previous codes for this phone
    await db.execute(update(OtpCode).where(OtpCode.phone == phone).values(used=True))
    db.add(
        OtpCode(
            phone=phone,
            code=code,
            expires_at=utcnow() + timedelta(minutes=settings.otp_expiry_minutes),
        )
    )
    await db.commit()
    return code


async def verify_otp(db: AsyncSession, phone: str, code: str) -> bool:
    row = (
        await db.execute(
            select(OtpCode)
            .where(OtpCode.phone == phone, OtpCode.used == False)  # noqa: E712
            .order_by(OtpCode.expires_at.desc())
        )
    ).scalars().first()
    if row is None or row.expires_at < utcnow() or row.attempts >= MAX_ATTEMPTS:
        return False
    row.attempts += 1
    if row.code != code:
        await db.commit()
        return False
    row.used = True
    await db.commit()
    return True
