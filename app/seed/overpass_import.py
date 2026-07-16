"""Import charging stations from OpenStreetMap via the Overpass API.

Open data (ODbL); no API key required. Maps OSM `amenity=charging_station`
nodes/ways with socket:* tags to Amplocate chargers.

Usage:
    python -m app.seed.overpass_import --south 28.30 --west 76.80 --north 28.90 --east 77.70
"""
import argparse
import asyncio
from typing import Optional

import httpx
from sqlalchemy import select

from ..database import SessionLocal, init_db
from ..models import Charger, ReliabilityScore
from ..services.geo import haversine_km

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",  # mirror — main server rate-limits cloud IPs
    "https://overpass.osm.jp/api/interpreter",
]

# OSM socket:* keys → our connector enum
OSM_SOCKET_MAP = {
    "type2": ("Type2_AC", 22.0),
    "type2_combo": ("CCS2", 50.0),
    "ccs": ("CCS2", 50.0),
    "chademo": ("CHAdeMO", 50.0),
    "type2_cable": ("Type2_AC", 22.0),
    "schuko": ("Wall_3pin", 3.3),
    "cee_blue": ("Wall_3pin", 3.3),
    "gb_dc": ("GB/T", 60.0),
    "gb_ac": ("GB/T", 7.0),
}

PROXIMITY_DEDUPE_KM = 0.075  # skip if an existing charger sits within 75 m


def _power_kw(tags: dict, socket_key: str, default: float) -> float:
    """socket:<key>:output like '22 kW' / '22000 W' / '22'."""
    raw = tags.get(f"socket:{socket_key}:output") or tags.get("charging_station:output")
    if not raw:
        return default
    try:
        val = float(str(raw).lower().replace("kw", "").replace("w", "").strip())
        return val / 1000 if val > 1000 else val
    except ValueError:
        return default


def to_charger(el: dict) -> Optional[Charger]:
    tags = el.get("tags") or {}
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    if lat is None or lon is None:
        return None

    connectors = []
    for key, (ctype, default_kw) in OSM_SOCKET_MAP.items():
        raw = tags.get(f"socket:{key}")
        if raw in (None, "no", "0"):
            continue
        try:
            count = max(int(raw), 1)
        except ValueError:
            count = 1
        connectors.append({"type": ctype, "power_kw": _power_kw(tags, key, default_kw), "count": count})
    if not connectors:
        # untyped station: assume one Type 2 AC point so it's still discoverable
        connectors = [{"type": "Type2_AC", "power_kw": 7.4, "count": 1}]

    operator = tags.get("operator") or tags.get("brand") or "Unknown"
    name = tags.get("name") or (
        f"{operator} Charging Station" if operator != "Unknown" else "EV Charging Station"
    )
    return Charger(
        external_id=f"osm-{el['type']}-{el['id']}",
        name=name,
        operator=operator,
        address=", ".join(filter(None, [tags.get("addr:street"), tags.get("addr:suburb")])),
        city=tags.get("addr:city") or "",
        lat=float(lat),
        lng=float(lon),
        connectors=connectors,
        price_per_kwh=None,
        status="unknown",
        amenities=[],
    )


async def fetch(south: float, west: float, north: float, east: float) -> list[dict]:
    query = f"""
    [out:json][timeout:90];
    ( node["amenity"="charging_station"]({south},{west},{north},{east});
      way["amenity"="charging_station"]({south},{west},{north},{east}); );
    out body center;
    """
    last_err: Optional[Exception] = None
    async with httpx.AsyncClient(headers={"User-Agent": "Amplocate/0.1 (EV charging discovery)"}) as client:
        for url in OVERPASS_URLS:
            try:
                resp = await client.post(url, data={"data": query}, timeout=120)
                resp.raise_for_status()
                return resp.json().get("elements", [])
            except Exception as e:  # noqa: BLE001 — try the next mirror
                print(f"Overpass {url} failed: {e}", flush=True)
                last_err = e
    raise last_err if last_err else RuntimeError("No Overpass endpoint available")


async def run(south: float, west: float, north: float, east: float) -> int:
    await init_db()
    elements = await fetch(south, west, north, east)
    async with SessionLocal() as db:
        existing_ids = set(
            (await db.execute(select(Charger.external_id).where(Charger.external_id.isnot(None))))
            .scalars().all()
        )
        existing_pos = (await db.execute(select(Charger.lat, Charger.lng))).all()
        added = 0
        for el in elements:
            c = to_charger(el)
            if c is None or c.external_id in existing_ids:
                continue
            if any(haversine_km(c.lat, c.lng, lat, lng) < PROXIMITY_DEDUPE_KM for lat, lng in existing_pos):
                continue  # already covered by another source
            db.add(c)
            await db.flush()
            db.add(ReliabilityScore(charger_id=c.id, score=0.5))  # neutral until verified
            existing_pos.append((c.lat, c.lng))
            added += 1
        await db.commit()
    print(f"Overpass: imported {added} chargers ({len(elements)} elements fetched).")
    return added


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--south", type=float, required=True)
    p.add_argument("--west", type=float, required=True)
    p.add_argument("--north", type=float, required=True)
    p.add_argument("--east", type=float, required=True)
    a = p.parse_args()
    asyncio.run(run(a.south, a.west, a.north, a.east))
