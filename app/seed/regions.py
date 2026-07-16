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


async def import_regions(regions=NCR_REGIONS) -> dict:
    """Run both importers per region. Idempotent: external-id + 75 m proximity dedupe."""
    totals = {"ocm": 0, "osm": 0, "errors": []}
    for name, lat, lng, radius in regions:
        try:
            totals["ocm"] += await ocm_import.run(lat, lng, radius, max_results=500)
        except Exception as e:  # noqa: BLE001 — one source failing shouldn't kill the run
            totals["errors"].append(f"OCM {name}: {e}")
        try:
            south, west, north, east = bbox_around(lat, lng, radius)
            totals["osm"] += await overpass_import.run(south, west, north, east)
        except Exception as e:  # noqa: BLE001
            totals["errors"].append(f"OSM {name}: {e}")
    print(f"Region import done: +{totals['ocm']} OCM, +{totals['osm']} OSM, {len(totals['errors'])} errors")
    return totals
