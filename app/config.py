from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Amplocate API"
    database_url: str = "sqlite+aiosqlite:///./voltara.db"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiry_days: int = 30

    # Dev mode: OTP is not sent via SMS, it's returned in the API response.
    dev_mode: bool = True
    otp_expiry_minutes: int = 5

    # DEV/TEST ONLY: load demo chargers on startup. Off by default — production
    # data comes from the live importers, never from hard-coded demo rows.
    seed_on_start: bool = False

    # Import real charger data for Delhi NCR (OCM + OSM) in the background on startup
    import_ncr_on_start: bool = False

    # On startup, if the chargers table is empty, auto-populate the default
    # regions from OpenStreetMap/Overpass (no API key required). This is what
    # replaces the old demo seed as the "there's data on first boot" mechanism.
    import_on_empty: bool = True

    # Redis response cache (leave empty to disable — the app still works).
    redis_url: str = ""
    cache_ttl_seconds: int = 90

    google_maps_api_key: str = ""
    ocm_api_key: str = ""

    # Statiq website importer (public pages; robots.txt permits crawling)
    statiq_import_concurrency: int = 4

    # Trip planner constants (see trip planning spec)
    reserve_soc: float = 15.0
    target_arrival_soc: float = 20.0
    max_charge_soc: float = 80.0
    pessimism_factor: float = 1.20
    min_reliability: float = 0.80
    max_detour_km: float = 5.0

    # Reliability engine
    reliability_half_life_days: float = 14.0  # decay toward neutral
    verified_within_days: int = 7

    class Config:
        env_file = ".env"


settings = Settings()
