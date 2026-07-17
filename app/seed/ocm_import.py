"""Import real charger data from Open Charge Map (https://openchargemap.org).

Usage:
    python -m app.seed.ocm_import --lat 12.97 --lng 77.59 --radius-km 50 [--max 200]

Free API; set OCM_API_KEY in .env for higher rate limits.
"""
import argparse
import asyncio

import httpx
from sqlalchemy import select

from ..config import settings
from ..database import SessionLocal, init_db
from ..models import Charger, ReliabilityScore
from typing import Optional

# OCM connection type id → our connector enum
OCM_CONNECTOR_MAP = {
    33: "CCS2",        # CCS (Type 2)
    2: "CHAdeMO",
    25: "Type2_AC",    # Type 2 socket
    1036: "Type2_AC",  # Type 2 tethered
    28: "Wall_3pin",   # domestic
    29: "Wall_3pin",
    1029: "Bharat_DC001",
    1028: "Bharat_AC001",
    16: "GB/T",
}


async def fetch(lat: float, lng: float, radius_km: float, max_results: int) -> list[dict]:
    params = {
        "output": "json",
        "latitude": lat,
        "longitude": lng,
        "distance": radius_km,
        "distanceunit": "KM",
        "maxresults": max_results,
        "countrycode": "IN",
        "compact": "true",
        "verbose": "false",
    }
    if settings.ocm_api_key:
        params["key"] = settings.ocm_api_key
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://api.openchargemap.io/v3/poi", params=params, timeout=30)
        if resp.status_code == 403:
            raise RuntimeError(
                "Open Charge Map requires an API key. Get a free one at "
                "openchargemap.org (My Profile → My Applications) and set OCM_API_KEY."
            )
        resp.raise_for_status()
        return resp.json()


def to_charger(poi: dict) -> Optional[Charger]:
    addr = poi.get("AddressInfo") or {}
    if not addr.get("Latitude") or not addr.get("Longitude"):
        return None
    connectors = []
    for conn in poi.get("Connections") or []:
        ctype = OCM_CONNECTOR_MAP.get(conn.get("ConnectionTypeID"))
        if ctype is None:
            continue
        connectors.append({
            "type": ctype,
            "power_kw": conn.get("PowerKW") or 7.0,
            "count": conn.get("Quantity") or 1,
        })
    if not connectors:
        return None
    operator = (poi.get("OperatorInfo") or {}).get("Title") or "Unknown"
    return Charger(
        external_id=f"ocm-{poi['ID']}",
        name=addr.get("Title") or f"Charger {poi['ID']}",
        operator=operator,
        address=", ".join(filter(None, [addr.get("AddressLine1"), addr.get("Town")])),
        city=addr.get("Town") or "",
        lat=addr["Latitude"],
        lng=addr["Longitude"],
        connectors=connectors,
        price_per_kwh=None,
        status="unknown",
        amenities=[],
    )


PROXIMITY_DEDUPE_KM = 0.075  # skip if an existing charger sits within 75 m


async def run(lat: float, lng: float, radius_km: float, max_results: int = 500) -> int:
    from ..services.geo import haversine_km

    await init_db()
    pois = await fetch(lat, lng, radius_km, max_results)
    async with SessionLocal() as db:
        existing = set(
            (await db.execute(select(Charger.external_id).where(Charger.external_id.isnot(None))))
            .scalars().all()
        )
        existing_pos = (await db.execute(select(Charger.lat, Charger.lng))).all()
        added = 0
        for poi in pois:
            c = to_charger(poi)
            if c is None or c.external_id in existing:
                continue
            if any(haversine_km(c.lat, c.lng, la, ln) < PROXIMITY_DEDUPE_KM for la, ln in existing_pos):
                continue  # already covered by another source
            db.add(c)
            await db.flush()
            db.add(ReliabilityScore(charger_id=c.id, score=0.5))  # neutral until verified
            existing_pos.append((c.lat, c.lng))
            added += 1
        await db.commit()
    print(f"OCM: imported {added} chargers ({len(pois)} fetched).")
    return added


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lng", type=float, required=True)
    p.add_argument("--radius-km", type=float, default=50)
    p.add_argument("--max", type=int, default=200)
    a = p.parse_args()
    asyncio.run(run(a.lat, a.lng, a.radius_km, a.max))
