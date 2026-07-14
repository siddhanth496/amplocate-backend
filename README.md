# Voltara Backend

FastAPI backend for the Voltara EV charging platform: OTP auth, vehicle management, charger discovery, reliability engine, community reports, and the single-stop risk-free trip planner.

## Quick start

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m app.seed.seed_data          # seed Bengaluru/Mysuru-corridor/Delhi chargers
uvicorn app.main:app --reload         # http://localhost:8000/docs
```

Runs on SQLite by default (zero setup). For Postgres/PostGIS use docker-compose from the repo root, or set `DATABASE_URL` in `.env`.

## Import real chargers (Open Charge Map)

```bash
python -m app.seed.ocm_import --lat 12.97 --lng 77.59 --radius-km 50
```

## Tests

```bash
pytest
```

## API overview

| Endpoint | Purpose |
|---|---|
| `POST /auth/otp/request` → `POST /auth/otp/verify` | Phone OTP login (dev mode returns the OTP in the response) |
| `GET/POST/PATCH /vehicles`, `GET /vehicles/catalog` | Vehicle management + Indian EV catalog |
| `GET /chargers/nearby` | Geo search with connector / power / reliability filters, compatibility-first sort |
| `GET /chargers/{id}` | Details + reliability score + recent reports |
| `POST /chargers/{id}/reports` | Community reports (working / broken / ice_blocked / queue / check_in) |
| `POST /chargers/sessions/start`, `.../end` | Session logging → reliability signal |
| `POST /trips/plan` | Single-stop risk-free trip planner |

## Architecture notes

- **Reliability engine** (`services/reliability.py`): Beta-prior evidence model with exponential time decay (14-day half-life). Session outcomes weigh more than reports; scores drift toward neutral 0.5 when stale, so broken chargers "heal" into uncertainty rather than staying condemned.
- **Trip planner** (`services/trip_planner.py`): implements the risk-free spec — 20% pessimism factor, 15% reserve floor, charging window, corridor search, six hard filters including the backup-charger rule, weighted scoring, and the fallback ladder (widen detour → lower reliability bar → report unplannable). Never relaxes the reserve floor.
- **Geo queries**: bounding-box prefilter + haversine (works on SQLite and Postgres). Production swap-in for PostGIS `ST_DWithin` is documented in `services/geo.py`.
- **Routing**: straight-line router with 1.25 circuity factor by default; switches to Google Directions automatically when `GOOGLE_MAPS_API_KEY` is set.
- **P2P marketplace**: not yet implemented (Phase 3); `Charger.is_p2p` and the trip-planner fallback ladder already account for it.
