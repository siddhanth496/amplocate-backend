import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import cache
from .config import settings
from .database import init_db
from .routers import admin_router, auth_router, chargers_router, trips_router, vehicles_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await cache.init_cache()

    # seed_on_start is DEV/TEST only — production never loads demo rows.
    if settings.seed_on_start:
        from .seed.seed_data import seed
        await seed()

    # Populate live data in the background so the API serves immediately.
    if settings.import_ncr_on_start:
        from .seed.regions import import_regions
        asyncio.create_task(import_regions())
    elif settings.import_on_empty:
        # keyless first-boot bootstrap from OpenStreetMap
        from .seed.regions import bootstrap_if_empty
        asyncio.create_task(bootstrap_if_empty())

    try:
        yield
    finally:
        await cache.close_cache()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Amplocate — EV charging discovery, reliability intelligence, and trip planning.",
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
app.include_router(admin_router.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.app_name}
