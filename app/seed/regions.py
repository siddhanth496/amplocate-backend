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


# In-memory status for the /admin/import/status endpoint
STATUS: dict = {"running": False, "last_run": None}


async def import_regions(regions=NCR_REGIONS) -> dict:
    """Run both importers per region. Idempotent: external-id + 75 m proximity dedupe."""
    import traceback
    from datetime import datetime, timezone

    from ..config import settings
    from . import google_places_import

    totals = {"ocm": 0, "osm": 0, "google": 0, "errors": [], "started_at": datetime.now(timezone.utc).isoformat()}
    STATUS["running"] = True
    try:
        for name, lat, lng, radius in regions:
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
            if settings.google_maps_api_key:
                try:
                    totals["google"] += await google_places_import.run(lat, lng, radius)
                except Exception as e:  # noqa: BLE001
                    totals["errors"].append(f"Google {name}: {type(e).__name__}: {e}")
                    print(f"Google {name} failed: {e}", flush=True)
    except Exception:  # noqa: BLE001 — never lose the traceback silently
        totals["errors"].append(traceback.format_exc())
        print(traceback.format_exc(), flush=True)
    finally:
        totals["finished_at"] = datetime.now(timezone.utc).isoformat()
        STATUS["running"] = False
        STATUS["last_run"] = totals
    print(f"Region import done: +{totals['ocm']} OCM, +{totals['osm']} OSM, {len(totals['errors'])} errors", flush=True)
    return totals
