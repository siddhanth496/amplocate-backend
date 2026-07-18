"""Region presets + orchestrator that pulls charger data from all open sources
(Open Charge Map + OpenStreetMap/Overpass) with cross-source dedupe."""
import math

from . import ocm_import, overpass_import

# Delhi NCR coverage grid: (name, lat, lng, radius_km)
NCR_REGIONS = [
    ("Delhi Central", 28.6139, 77.2090, 18),
    ("Delhi South", 28.5245, 77.2066, 12),
    ("Delhi West", 28.6663, 77.0469, 12),
    ("Delhi East/Shahdara", 28.6692, 77.2887, 10),
    ("Gurugram", 28.4595, 77.0266, 18),
    ("Noida", 28.5355, 77.3910, 14),
    ("Greater Noida", 28.4744, 77.5040, 14),
    ("Ghaziabad", 28.6692, 77.4538, 12),
    ("Faridabad", 28.4089, 77.3178, 14),
]


def bbox_around(lat: float, lng: float, radius_km: float) -> tuple[float, float, float, float]:
    dlat = radius_km / 110.574
    dlng = radius_km / (111.320 * max(math.cos(math.radians(lat)), 0.01))
    return lat - dlat, lng - dlng, lat + dlat, lng + dlng  # south, west, north, east


# Regions auto-imported on first boot when the DB is empty (keyless OSM only).
# Kept small so a cold start populates quickly; run /admin/import/ncr for full
# coverage. Covers the app's two default map centres (Bengaluru + Delhi).
BOOTSTRAP_REGIONS = [
    ("Bengaluru", 12.9716, 77.5946, 18),
    ("Delhi Central", 28.6139, 77.2090, 18),
]

# In-memory status for the /admin/import/status endpoint
STATUS: dict = {"running": False, "last_run": None}


async def bootstrap_if_empty() -> dict:
    """If there are no chargers yet, import default regions from OSM (no API key).

    Safe to call on every startup — it's a no-op once data exists.
    """
    from sqlalchemy import func, select

    from ..database import SessionLocal
    from ..models import Charger

    async with SessionLocal() as db:
        count = (await db.execute(select(func.count(Charger.id)))).scalar()
    if count:
        return {"skipped": True, "existing": count}
    print(f"Bootstrap: DB empty — importing {len(BOOTSTRAP_REGIONS)} region(s) from OSM.", flush=True)
    return await import_regions(BOOTSTRAP_REGIONS, osm_only=True)


async def import_regions(regions=NCR_REGIONS, osm_only: bool = False) -> dict:
    """Run the importers per region. Idempotent: external-id + 75 m proximity dedupe.

    ``osm_only`` skips OCM/Google (which need API keys) so a keyless bootstrap can
    still populate data from OpenStreetMap alone.
    """
    import traceback
    from datetime import datetime, timezone

    from ..config import settings
    from . import google_places_import

    totals = {"ocm": 0, "osm": 0, "google": 0, "errors": [], "started_at": datetime.now(timezone.utc).isoformat()}
    STATUS["running"] = True
    try:
        for name, lat, lng, radius in regions:
            if not osm_only:
                try:
                    totals["ocm"] += await ocm_import.run(lat, lng, radius, max_results=500)
                except Exception as e:  # noqa: BLE001 — one source failing shouldn't kill the run
                    totals["errors"].append(f"OCM {name}: {type(e).__name__}: {e}")
                    print(f"OCM {name} failed: {e}", flush=True)
            try:
                south, west, north, east = bbox_around(lat, lng, radius)
                totals["osm"] += await overpass_import.run(south, west, north, east)
            except Exception as e:  # noqa: BLE001
                totals["errors"].append(f"OSM {name}: {type(e).__name__}: {e}")
                print(f"OSM {name} failed: {e}", flush=True)
            if not osm_only and settings.google_maps_api_key:
                try:
                    totals["google"] += await google_places_import.run(lat, lng, radius)
                except Exception as e:  # noqa: BLE001
                    totals["errors"].append(f"Google {name}: {type(e).__name__}: {e}")
                    print(f"Google {name} failed: {e}", flush=True)
            # be polite to the free Overpass/OCM servers — rapid back-to-back
            # region queries are what trigger 429 rate limits
            import asyncio as _asyncio
            await _asyncio.sleep(5)
    except Exception:  # noqa: BLE001 — never lose the traceback silently
        totals["errors"].append(traceback.format_exc())
        print(traceback.format_exc(), flush=True)
    finally:
        totals["finished_at"] = datetime.now(timezone.utc).isoformat()
        STATUS["running"] = False
        STATUS["last_run"] = totals
    print(f"Region import done: +{totals['ocm']} OCM, +{totals['osm']} OSM, {len(totals['errors'])} errors", flush=True)
    return totals
