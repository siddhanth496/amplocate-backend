# Amplocate backend — data & caching architecture

No more demo/seed data in production. Chargers come from live open-data APIs into
Postgres, and hot reads are served through a Redis cache.

```
 OpenStreetMap / OCM / Google Places      ← live sources (importers)
                │
                ▼
        Postgres  (chargers, reports, reliability, …)
                │
          /chargers/nearby
                │
             Redis cache  (read-through, 90s TTL, invalidated on reports)
                │
                ▼
             Frontend
```

## Data ingestion

- **OpenStreetMap / Overpass** — free, no key. The default source.
- **Open Charge Map** — needs a free `OCM_API_KEY`; skipped when unset.
- **Google Places** — needs a paid `GOOGLE_MAPS_API_KEY`; skipped when unset.
- **Statiq** — importer exists but is **disabled/not wired for prod** because
  scraping violates their ToS (see `STATIQ_IMPORT.md`). Statiq stations still
  arrive via OSM/OCM where the community has tagged `operator=Statiq`.

**First boot:** if the `chargers` table is empty and `IMPORT_ON_EMPTY=true`
(default), the app imports Bengaluru + Delhi from OSM in the background — so the
map has real data on a fresh database with zero keys. For full Delhi-NCR coverage
add the keys and either set `IMPORT_NCR_ON_START=true` or `POST /admin/import/ncr`.

## Caching

`app/cache.py` is a read-through Redis layer:

- Caches `GET /chargers/nearby` (the expensive geo scan), keyed by ~100 m
  quantised coordinates + filters, `CACHE_TTL_SECONDS` (default 90).
- Invalidated immediately when a report or charge session changes a reliability
  score (`delete_prefix`).
- **Graceful:** if `REDIS_URL` is empty or Redis is unreachable, every cache call
  is a no-op and the app runs normally (this is how the tests run).

## Database

DB-agnostic via SQLAlchemy async. `postgres://` / `postgresql://` URLs (Render,
Neon, Supabase, Heroku style) are auto-normalised to `postgresql+asyncpg://` and
`?sslmode=require` is handled. No PostGIS needed — proximity is a bounding-box
prefilter + haversine, which runs on both SQLite (tests) and Postgres.

## Running

**Local dev (free): Postgres + Redis via Docker**

```bash
docker compose up --build          # from the repo root
# API on :8000, auto-imports OSM data for Bengaluru + Delhi on first boot
```

**Local without Docker (SQLite, no cache)**

```bash
cd amplocate-backend
cp .env.example .env                # DATABASE_URL=sqlite…, REDIS_URL empty
uvicorn app.main:app --reload
```

**Production**

`render.yaml` provisions a managed Postgres + managed Redis (Key Value), wires
`DATABASE_URL` / `REDIS_URL`, and disables seeding. Any managed Postgres
(Neon/Supabase free tier) + Redis (Upstash free tier) works — just set the two
env vars. Add `OCM_API_KEY` / `GOOGLE_MAPS_API_KEY` in the dashboard to widen
coverage.

## Tests

```bash
cd amplocate-backend && python -m pytest -q      # 35 passing
```

Tests run on SQLite with caching disabled. `seed_data.py` remains **only** as a
test fixture — it is never loaded by the app in normal operation.

## Note on the legacy `backend/` folder

The old `backend/` service (SQLite + hard-coded demo rows) is superseded by
`amplocate-backend`. `docker-compose.yml` and `render.yaml` now target
`amplocate-backend`; the demo `.db` files have been removed. `backend/` can be
deleted once you've confirmed nothing else depends on it.
