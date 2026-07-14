from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Voltara API"
    database_url: str = "sqlite+aiosqlite:///./voltara.db"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiry_days: int = 30

    # Dev mode: OTP is not sent via SMS, it's returned in the API response.
    dev_mode: bool = True
    otp_expiry_minutes: int = 5

    # Seed demo chargers on startup if the table is empty (useful on fresh deploys)
    seed_on_start: bool = False

    google_maps_api_key: str = ""
    ocm_api_key: str = ""

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
