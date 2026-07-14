"""Seed realistic chargers for Bengaluru city, the Bengaluru–Mysuru corridor,
and Delhi. Run:  python -m app.seed.seed_data
"""
import asyncio
import random
from datetime import timedelta

from sqlalchemy import select

from ..database import SessionLocal, init_db
from ..models import Charger, ReliabilityScore, utcnow

CCS50 = [{"type": "CCS2", "power_kw": 50, "count": 2}, {"type": "Type2_AC", "power_kw": 22, "count": 1}]
CCS30 = [{"type": "CCS2", "power_kw": 30, "count": 1}, {"type": "Type2_AC", "power_kw": 7.4, "count": 1}]
CCS120 = [{"type": "CCS2", "power_kw": 120, "count": 2}, {"type": "Type2_AC", "power_kw": 22, "count": 2}]
AC_ONLY = [{"type": "Type2_AC", "power_kw": 7.4, "count": 2}]
BHARAT = [{"type": "Bharat_DC001", "power_kw": 15, "count": 1}, {"type": "Bharat_AC001", "power_kw": 3.3, "count": 2}]
WALL = [{"type": "Wall_3pin", "power_kw": 3.3, "count": 2}]

SEED = [
    # --- Bengaluru city ---
    ("Tata Power - Phoenix Marketcity", "Tata Power", "Whitefield Rd, Mahadevapura", "Bengaluru", 12.9969, 77.6959, CCS50, 19.5, ["cafe", "restroom", "mall", "food_court"], 0.92),
    ("ChargeZone - Orion Mall", "ChargeZone", "Dr Rajkumar Rd, Rajajinagar", "Bengaluru", 13.0110, 77.5550, CCS120, 21.0, ["cafe", "restroom", "mall"], 0.88),
    ("Ather Grid - Indiranagar", "Ather Energy", "100 Feet Rd, Indiranagar", "Bengaluru", 12.9784, 77.6408, BHARAT, 12.0, ["cafe"], 0.90),
    ("Statiq - Koramangala Forum", "Statiq", "Hosur Rd, Koramangala", "Bengaluru", 12.9346, 77.6112, CCS50, 20.0, ["mall", "restroom", "food_court"], 0.75),
    ("Relux - HSR Layout", "Relux Electric", "27th Main, HSR Layout", "Bengaluru", 12.9116, 77.6473, CCS30, 18.0, ["cafe", "restroom"], 0.83),
    ("BESCOM - Jayanagar", "BESCOM", "4th Block, Jayanagar", "Bengaluru", 12.9254, 77.5834, BHARAT, 8.5, [], 0.55),
    ("Zeon - Electronic City", "Zeon Charging", "Phase 1, Electronic City", "Bengaluru", 12.8452, 77.6602, CCS50, 19.0, ["cafe", "restroom"], 0.86),
    ("Ather Grid - Malleshwaram", "Ather Energy", "Sampige Rd, Malleshwaram", "Bengaluru", 13.0035, 77.5709, BHARAT, 12.0, ["cafe"], 0.89),
    ("Tata Power - Kempegowda Airport", "Tata Power", "KIAL Terminal Parking", "Bengaluru", 13.1986, 77.7066, CCS120, 22.0, ["restroom", "food_court"], 0.91),
    ("GLIDA - Cubbon Park Metro", "GLIDA", "Kasturba Rd", "Bengaluru", 12.9762, 77.5993, CCS30, 17.5, ["park"], 0.68),
    # --- Bengaluru → Mysuru corridor (NH-275) ---
    ("Zeon - Bidadi Highway", "Zeon Charging", "NH-275, Bidadi", "Ramanagara", 12.7970, 77.3830, CCS120, 21.5, ["restaurant", "restroom"], 0.90),
    ("Statiq - Ramanagara", "Statiq", "NH-275, Ramanagara", "Ramanagara", 12.7220, 77.2810, CCS50, 20.5, ["cafe", "restroom"], 0.88),
    ("Cafe Coffee Day - Channapatna", "ChargeZone", "NH-275, Channapatna", "Channapatna", 12.6510, 77.2060, CCS50, 20.0, ["cafe", "restroom", "restaurant"], 0.87),
    ("Zeon - Maddur", "Zeon Charging", "NH-275, Maddur", "Maddur", 12.5850, 77.0430, CCS120, 21.5, ["restaurant", "restroom", "cafe"], 0.93),
    ("Relux - Mandya", "Relux Electric", "NH-275, Mandya", "Mandya", 12.5240, 76.8960, CCS50, 19.5, ["restroom"], 0.79),
    ("Tata Power - Srirangapatna", "Tata Power", "NH-275, Srirangapatna", "Srirangapatna", 12.4220, 76.6930, CCS50, 20.0, ["restaurant", "restroom"], 0.85),
    ("ChargeZone - Mysuru Mall of Mysore", "ChargeZone", "MG Rd, Mysuru", "Mysuru", 12.3050, 76.6550, CCS120, 20.5, ["mall", "cafe", "restroom", "food_court"], 0.90),
    ("Ather Grid - Mysuru", "Ather Energy", "Vinoba Rd, Mysuru", "Mysuru", 12.3110, 76.6400, BHARAT, 12.0, ["cafe"], 0.88),
    # --- Delhi NCR ---
    ("Tata Power - Select Citywalk", "Tata Power", "Saket District Centre", "Delhi", 28.5286, 77.2192, CCS50, 21.0, ["mall", "cafe", "restroom", "food_court"], 0.91),
    ("Statiq - Connaught Place", "Statiq", "Block K, CP", "Delhi", 28.6315, 77.2196, CCS30, 19.0, ["cafe", "restaurant"], 0.77),
    ("EESL - Nehru Place", "EESL", "Nehru Place Metro Parking", "Delhi", 28.5494, 77.2519, BHARAT, 10.0, [], 0.52),
    ("ChargeZone - IGI Airport T3", "ChargeZone", "T3 Parking, IGI Airport", "Delhi", 28.5562, 77.0999, CCS120, 22.5, ["restroom", "food_court"], 0.89),
    ("Jio-bp - Gurugram Cyber City", "Jio-bp Pulse", "DLF Cyber City", "Gurugram", 28.4950, 77.0890, CCS120, 21.5, ["cafe", "restroom", "restaurant"], 0.92),
    ("Statiq - Noida Sector 18", "Statiq", "Atta Market, Sector 18", "Noida", 28.5700, 77.3210, CCS50, 20.0, ["mall", "cafe", "restroom"], 0.80),
]


async def seed():
    await init_db()
    async with SessionLocal() as db:
        existing = (await db.execute(select(Charger.name))).scalars().all()
        existing_names = set(existing)
        added = 0
        rng = random.Random(42)
        for name, op, addr, city, lat, lng, conns, price, amen, rel in SEED:
            if name in existing_names:
                continue
            charger = Charger(
                name=name, operator=op, address=addr, city=city, lat=lat, lng=lng,
                connectors=conns, price_per_kwh=price, amenities=amen, status="online",
            )
            db.add(charger)
            await db.flush()
            # convert seed score into baseline evidence (total weight 15) so future
            # reports adjust the score instead of replacing it
            weight = 15.0
            db.add(ReliabilityScore(
                charger_id=charger.id,
                score=rel,
                baseline_pos=max(weight * rel - 2.0, 0.0),
                baseline_neg=max(weight * (1 - rel) - 2.0, 0.0),
                positive_signals=rng.randint(5, 40),
                negative_signals=rng.randint(0, 5),
                last_verified_at=utcnow() - timedelta(hours=rng.randint(1, 48)),
            ))
            added += 1
        await db.commit()
    print(f"Seeded {added} chargers ({len(SEED) - added} already existed).")


if __name__ == "__main__":
    asyncio.run(seed())
