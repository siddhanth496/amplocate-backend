"""Import EV charging stations via the official Google Places API (New).

This is the ToS-compliant alternative to scraping Google Maps. Requires
GOOGLE_MAPS_API_KEY with the Places API (New) enabled.

Compliance note: Google permits indefinite storage of place IDs but caps
caching of other place data at ~30 days — so this importer UPSERTS
(refreshes existing rows) instead of skipping them. Re-run it at least
monthly (POST /admin/import/google or a scheduled job).

Usage:
    python -m app.seed.google_places_import --lat 28.61 --lng 77.20 --radius-km 18
"""
import argparse
import asyncio
import math
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..config import settings
from ..database import SessionLocal, init_db
from ..models import Charger, ReliabilityScore
from ..services.geo import haversine_km

PLACES_URL = "https://places.googleapis.com/v1/places:searchNearby"
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.businessStatus",
    "places.evChargeOptions",
])

# Google EV connector enum → Amplocate connector enum
GOOGLE_CONNECTOR_MAP = {
    "EV_CONNECTOR_TYPE_CCS_COMBO_2": "CCS2",
    "EV_CONNECTOR_TYPE_CCS_COMBO_1": "CCS2",
    "EV_CONNECTOR_TYPE_CHADEMO": "CHAdeMO",
    "EV_CONNECTOR_TYPE_TYPE_2": "Type2_AC",
    "EV_CONNECTOR_TYPE_J1772": "Type2_AC",
    "EV_CONNECTOR_TYPE_GB_T": "GB/T",
    "EV_CONNECTOR_TYPE_WALL_OUTLET": "Wall_3pin",
}

MAX_CIRCLE_RADIUS_KM = 10.0   # searchNearby returns ≤20 places; tile big regions
PROXIMITY_DEDUPE_KM = 0.075


def to_charger(place: dict) -> Optional[Charger]:
    loc = place.get("location") or {}
    if loc.get("latitude") is None or loc.get("longitude") is None:
        return None
    if place.get("businessStatus") == "CLOSED_PERMANENTLY":
        return None

    connectors = []
    ev = place.get("evChargeOptions") or {}
    for agg in ev.get("connectorAggregation") or []:
        ctype = GOOGLE_CONNECTOR_MAP.get(agg.get("type"))
        if ctype is None:
            continue
        connectors.append({
            "type": ctype,
            "power_kw": float(agg.get("maxChargeRateKw") or 7.0),
            "count": int(agg.get("count") or 1),
        })
    if not connectors:
        # station exists on Google but connector data is missing — keep it
        # discoverable with a conservative default
        connectors = [{"type": "Type2_AC", "power_kw": 7.4, "count": 1}]

    name = (place.get("displayName") or {}).get("text") or "EV Charging Station"
    return Charger(
        external_id=f"gplace-{place['id']}",
        name=name,
        operator="Unknown",
        address=place.get("formattedAddress") or "",
        city="",
        lat=float(loc["latitude"]),
        lng=float(loc["longitude"]),
        connectors=connectors,
        price_per_kwh=None,
        status="unknown",
        amenities=[],
    )


def tile_centers(lat: float, lng: float, radius_km: float) -> list[tuple[float, float, float]]:
    """Cover a large circle with ≤MAX_CIRCLE_RADIUS_KM circles (hex pattern)."""
    if radius_km <= MAX_CIRCLE_RADIUS_KM:
        return [(lat, lng, radius_km)]
    r = MAX_CIRCLE_RADIUS_KM
    step = r * 1.5
    centers = [(lat, lng, r)]
    rings = math.ceil(radius_km / step)
    for ring in range(1, rings + 1):
        for k in range(6 * ring):
            angle = 2 * math.pi * k / (6 * ring)
            dlat = (step * ring * math.sin(angle)) / 110.574
            dlng = (step * ring * math.cos(angle)) / (111.320 * max(math.cos(math.radians(lat)), 0.01))
            if haversine_km(lat, lng, lat + dlat, lng + dlng) <= radius_km:
                centers.append((lat + dlat, lng + dlng, r))
    return centers[:15]  # cost guard: ≤15 API calls per region


async def fetch_circle(client: httpx.AsyncClient, lat: float, lng: float, radius_km: float) -> list[dict]:
    resp = await client.post(
        PLACES_URL,
        headers={
            "X-Goog-Api-Key": settings.google_maps_api_key,
            "X-Goog-FieldMask": FIELD_MASK,
            "Content-Type": "application/json",
        },
        json={
            "includedTypes": ["electric_vehicle_charging_station"],
            "maxResultCount": 20,
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": min(radius_km * 1000, 50000),
                },
            },
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("places", [])


async def run(lat: float, lng: float, radius_km: float) -> int:
    if not settings.google_maps_api_key:
        raise RuntimeError("GOOGLE_MAPS_API_KEY is not configured")
    await init_db()

    places: dict[str, dict] = {}
    async with httpx.AsyncClient() as client:
        for clat, clng, cr in tile_centers(lat, lng, radius_km):
            for p in await fetch_circle(client, clat, clng, cr):
                places[p["id"]] = p

    added = updated = 0
    async with SessionLocal() as db:
        existing_pos = (
            await db.execute(select(Charger.lat, Charger.lng).where(Charger.external_id.is_(None)))
        ).all()
        for p in places.values():
            c = to_charger(p)
            if c is None:
                continue
            row = (
                await db.execute(
                    select(Charger).options(selectinload(Charger.reliability))
                    .where(Charger.external_id == c.external_id)
                )
            ).scalar_one_or_none()
            if row is not None:
                # refresh cached Google data (30-day caching policy)
                row.name, row.address = c.name, c.address
                row.lat, row.lng, row.connectors = c.lat, c.lng, c.connectors
                updated += 1
                continue
            # cross-source proximity dedupe against OCM/OSM/seed entries
            near_other = (
                await db.execute(
                    select(Charger).where(
                        Charger.lat.between(c.lat - 0.001, c.lat + 0.001),
                        Charger.lng.between(c.lng - 0.001, c.lng + 0.001),
                    )
                )
            ).scalars().first()
            if near_other is not None and haversine_km(c.lat, c.lng, near_other.lat, near_other.lng) < PROXIMITY_DEDUPE_KM:
                continue
            if any(haversine_km(c.lat, c.lng, la, ln) < PROXIMITY_DEDUPE_KM for la, ln in existing_pos):
                continue
            db.add(c)
            await db.flush()
            db.add(ReliabilityScore(charger_id=c.id, score=0.5))
            added += 1
        await db.commit()
    print(f"Google Places: +{added} new, {updated} refreshed ({len(places)} places fetched).", flush=True)
    return added


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lng", type=float, required=True)
    p.add_argument("--radius-km", type=float, default=15)
    a = p.parse_args()
    asyncio.run(run(a.lat, a.lng, a.radius_km))
