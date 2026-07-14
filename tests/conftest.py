import asyncio
import os

import tempfile

_TEST_DB = os.path.join(tempfile.gettempdir(), "test_voltara.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB}"

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import engine, Base
from app.main import app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_client(client):
    """Client authenticated as a fresh user with a Nexon EV added."""
    r = await client.post("/auth/otp/request", json={"phone": "+919876543210"})
    otp = r.json()["dev_otp"]
    r = await client.post("/auth/otp/verify", json={"phone": "+919876543210", "code": otp})
    token = r.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client
