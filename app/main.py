from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import init_db
from .routers import auth_router, chargers_router, trips_router, vehicles_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if settings.seed_on_start:
        from .seed.seed_data import seed
        await seed()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Voltara — EV charging discovery, reliability intelligence, and trip planning.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(vehicles_router.router)
app.include_router(chargers_router.router)
app.include_router(trips_router.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.app_name}
