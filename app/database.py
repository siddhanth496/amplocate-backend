from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


def _normalize_url(url: str) -> tuple[str, dict]:
    """Accept Heroku/Render/Neon-style postgres URLs and map them to asyncpg.

    - postgres:// or postgresql://  ->  postgresql+asyncpg://
    - strips ?sslmode=require (asyncpg uses connect_args ssl instead)
    """
    connect_args: dict = {}
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if "sslmode=" in url:
        base, _, query = url.partition("?")
        params = [p for p in query.split("&") if p and not p.startswith("sslmode=")]
        if "sslmode=require" in query or "sslmode=verify" in query:
            connect_args["ssl"] = True
        url = base + ("?" + "&".join(params) if params else "")
    return url, connect_args


_url, _connect_args = _normalize_url(settings.database_url)
engine = create_async_engine(_url, echo=False, connect_args=_connect_args)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def init_db():
    from . import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
