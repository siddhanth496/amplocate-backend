"""Import charging stations from the Statiq public website (https://www.statiq.in).

Statiq is India's largest EV charging network. They do not publish a third-party
API, but their per-station pages are server-rendered and their robots.txt permits
crawling (``Allow: /``; it even sets ``Content-Signal: ai-input=yes, ai-train=yes``)
and advertises a sitemap. This importer discovers station URLs from the sitemap
(or a city page), fetches each station page, extracts the embedded structured data
(Next.js ``__NEXT_DATA__`` JSON, with JSON-LD + maps-link fallbacks for
coordinates), and normalises it into an Amplocate ``Charger``.

    Discovery + import (all cities in the sitemap):
        python -m app.seed.statiq_import --sitemap [--max 500]

    One city page:
        python -m app.seed.statiq_import --city bengaluru-ev-charging-station

    A single station (also the fastest way to sanity-check parsing):
        python -m app.seed.statiq_import --url https://www.statiq.in/...-id-4741

    Probe mode — fetch one page and DUMP the extracted station object so you can
    confirm the field mapping against the live JSON on first run:
        python -m app.seed.statiq_import --probe https://www.statiq.in/...-id-4741

Compliance: crawling is permitted by robots.txt, but review Statiq's Terms of
Service before commercial reuse, keep the crawl polite (this module rate-limits
itself), cache results, and attribute Statiq as the source in the UI. See
STATIQ_IMPORT.md.
"""
import argparse
import asyncio
import gzip
import json
import re
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..database import SessionLocal, init_db
from ..models import Charger, ReliabilityScore
from ..services.geo import haversine_km

BASE = "https://www.statiq.in"
SITEMAP_URL = f"{BASE}/sitemap.xml"
USER_AGENT = "Amplocate/0.1 (+EV charging discovery; contact: support@amplocate.app)"

# Only station detail pages carry the data we want.
STATION_URL_RE = re.compile(r"-ev-charging-station-id-(\d+)\b")
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)
LDJSON_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL
)
# e.g. .../maps/dir/?...destination=12.84,77.66  or  maps?q=12.84,77.66
MAPS_COORD_RE = re.compile(r"(?:destination|daddr|q|query|ll)=(-?\d{1,2}\.\d+),(-?\d{2,3}\.\d+)")

PROXIMITY_DEDUPE_KM = 0.075  # skip if an existing charger sits within 75 m

# Statiq connector labels → our ConnectorType enum. Statiq writes these as
# "CCS-2", "Type 2", "CHAdeMO", "GB/T", "Wall" (3-pin smart plug), etc.
CONNECTOR_MAP = [
    (("ccs2", "ccs-2", "ccs 2", "ccs combo"), "CCS2"),
    (("chademo",), "CHAdeMO"),
    (("type2", "type 2", "type-2", "iec 62196", "mennekes"), "Type2_AC"),
    (("gb/t", "gbt", "gb t"), "GB/T"),
    (("wall", "3-pin", "3 pin", "three pin", "smart plug", "socket", "domestic"), "Wall_3pin"),
    (("bharat dc", "bharat_dc"), "Bharat_DC001"),
    (("bharat ac", "bharat_ac"), "Bharat_AC001"),
]


def map_connector(label: Optional[str]) -> Optional[str]:
    if not label:
        return None
    s = str(label).lower()
    for needles, enum in CONNECTOR_MAP:
        if any(n in s for n in needles):
            return enum
    return None


# ── Normalised-station → Charger ──────────────────────────────────────────────
# `normalize_station()` produces this shape; `to_charger()` maps it to the model.
# Both are pure functions so they're unit-testable without network access.
#   {
#     "id": "4741", "name": "...", "operator": "Statiq",
#     "address": "...", "city": "Bengaluru", "lat": 12.84, "lng": 77.66,
#     "amenities": ["restroom", "cafe"],
#     "available": True,          # station open / reachable
#     "chargers": [
#       {"current": "DC", "power_kw": 120, "price": 24.15,
#        "connectors": [{"type": "CCS-2", "status": "available"}, ...]},
#     ],
#   }

_STATUS_ONLINE = {"available", "charging", "in_use", "busy", "occupied", "preparing"}


def to_charger(station: dict) -> Optional[Charger]:
    sid = station.get("id")
    lat, lng = station.get("lat"), station.get("lng")
    if sid is None or lat is None or lng is None:
        return None

    # Aggregate identical (type, power) connectors into count buckets, and derive
    # a station-level status + price from the per-connector detail.
    buckets: dict[tuple, dict] = {}
    prices: list[float] = []
    any_online = False
    for ch in station.get("chargers") or []:
        power = _num(ch.get("power_kw")) or 0.0
        price = _num(ch.get("price"))
        if price:
            prices.append(price)
        conns = ch.get("connectors") or [{}]
        for conn in conns:
            ctype = map_connector(conn.get("type") or ch.get("current"))
            if ctype is None:
                # AC/DC hint without an explicit connector label
                ctype = "CCS2" if str(ch.get("current")).upper() == "DC" else "Type2_AC"
            if str(conn.get("status", "")).lower() in _STATUS_ONLINE:
                any_online = True
            key = (ctype, round(power, 1))
            b = buckets.setdefault(key, {"type": ctype, "power_kw": power, "count": 0})
            b["count"] += 1

    connectors = list(buckets.values())
    if not connectors:
        connectors = [{"type": "Type2_AC", "power_kw": 7.4, "count": 1}]

    if station.get("available") is False:
        status = "offline"
    elif any_online or station.get("available"):
        status = "online"
    else:
        status = "unknown"

    return Charger(
        external_id=f"statiq-{sid}",
        name=station.get("name") or f"Statiq Station {sid}",
        operator=station.get("operator") or "Statiq",
        address=station.get("address") or "",
        city=station.get("city") or "",
        lat=float(lat),
        lng=float(lng),
        connectors=connectors,
        price_per_kwh=min(prices) if prices else None,
        status=status,
        amenities=[a.lower() for a in (station.get("amenities") or [])],
    )


# ── Parsing the station page ──────────────────────────────────────────────────
def _num(v) -> Optional[float]:
    """Pull a float out of things like '₹ 24.15', '120 kW', 22.99, None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group()) if m else None


def _iter_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_dicts(v)


def _get(d: dict, *keys):
    """First present, non-empty value among candidate keys (case-insensitive)."""
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, "", [], {}):
            return v
    return None


_CHARGER_KEYS = ("chargers", "connectors", "points", "evses", "chargerlist")


def _find_station_node(data: dict, want_id: Optional[str] = None) -> Optional[dict]:
    """Locate the dict in __NEXT_DATA__ that describes the station.

    Prefers a node that (a) matches the URL's station id, (b) has coordinates,
    and (c) carries charger/connector detail — but will still return an
    identity-bearing node without coordinates so a maps-link fallback can supply
    them.
    """
    best = None
    for node in _iter_dicts(data):
        lat = _num(_get(node, "latitude", "lat"))
        lng = _num(_get(node, "longitude", "lng", "lon", "long"))
        has_coords = lat is not None and lng is not None
        node_id = _get(node, "id", "stationId", "station_id", "slug")
        has_name = _get(node, "name", "stationName", "title") is not None
        keys = {k.lower() for k in node}
        has_chargers = any(k in keys for k in _CHARGER_KEYS)
        # a node qualifies as "the station" if it has coords, or an id + (name/chargers)
        if not (has_coords or (node_id is not None and (has_name or has_chargers))):
            continue
        score = (
            (8 if want_id and str(node_id) == str(want_id) else 0)
            + (4 if has_coords else 0)
            + (2 if has_chargers else 0)
            + (1 if has_name else 0)
        )
        if best is None or score > best[0]:
            best = (score, node)
    return best[1] if best else None


def normalize_station(node: dict, url: str = "", fallback_coords: Optional[tuple] = None) -> Optional[dict]:
    """Map a raw Statiq station node into our normalised shape.

    NOTE: this is the one site-shape-specific function. Statiq's exact JSON keys
    are read defensively; run ``--probe <url>`` on first use to confirm the
    mapping against the live payload and adjust the key lists below if needed.
    """
    lat = _num(_get(node, "latitude", "lat"))
    lng = _num(_get(node, "longitude", "lng", "lon", "long"))
    if (lat is None or lng is None) and fallback_coords:
        lat, lng = fallback_coords
    if lat is None or lng is None:
        return None

    sid = _get(node, "id", "stationId", "station_id", "slug")
    if sid is None:
        m = STATION_URL_RE.search(url)
        sid = m.group(1) if m else None

    amenities = []
    for a in (_get(node, "amenities", "amenityList") or []):
        amenities.append(a.get("name") if isinstance(a, dict) else str(a))

    chargers = []
    raw_chargers = _get(node, "chargers", "points", "evses", "chargerList", "connectors") or []
    for rc in raw_chargers:
        if not isinstance(rc, dict):
            continue
        conns = []
        for conn in (_get(rc, "connectors", "connectorList", "guns") or []):
            if isinstance(conn, dict):
                conns.append({
                    "type": _get(conn, "type", "connectorType", "name"),
                    "status": _get(conn, "status", "state", "availability"),
                })
        chargers.append({
            "current": _get(rc, "current", "currentType", "chargerType", "type"),
            "power_kw": _num(_get(rc, "power_kw", "powerKw", "power", "maxPower", "capacity")),
            "price": _num(_get(rc, "price", "pricePerUnit", "tariff", "rate", "unitPrice")),
            "connectors": conns,
        })

    avail = _get(node, "available", "isAvailable", "isOpen", "open")
    if isinstance(avail, str):
        avail = avail.strip().lower() in ("true", "open", "available", "online", "yes")

    return {
        "id": str(sid) if sid is not None else None,
        "name": _get(node, "name", "stationName", "title"),
        "operator": _get(node, "operator", "cpo", "brand") or "Statiq",
        "address": _get(node, "address", "fullAddress", "formattedAddress", "addressLine"),
        "city": _get(node, "city", "cityName", "town"),
        "lat": lat, "lng": lng,
        "amenities": amenities,
        "available": avail,
        "chargers": chargers,
    }


def parse_station_page(html: str, url: str = "") -> Optional[dict]:
    """Extract a normalised station dict from a station page's HTML."""
    fallback = None
    m = MAPS_COORD_RE.search(html)
    if m:
        fallback = (float(m.group(1)), float(m.group(2)))

    url_id_m = STATION_URL_RE.search(url)
    url_id = url_id_m.group(1) if url_id_m else None

    m = NEXT_DATA_RE.search(html)
    if m:
        try:
            data = json.loads(m.group(1))
            node = _find_station_node(data, want_id=url_id)
            if node:
                norm = normalize_station(node, url, fallback)
                if norm:
                    return norm
        except json.JSONDecodeError:
            pass

    # JSON-LD fallback (coords + name/address only; no connector detail)
    for block in LDJSON_RE.findall(html):
        try:
            ld = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in _iter_dicts(ld):
            geo = _get(node, "geo") or node
            lat = _num(_get(geo, "latitude", "lat"))
            lng = _num(_get(geo, "longitude", "lng"))
            if lat is not None and lng is not None:
                sid = STATION_URL_RE.search(url)
                return {
                    "id": sid.group(1) if sid else None,
                    "name": _get(node, "name"), "operator": "Statiq",
                    "address": _get(node, "address") if isinstance(_get(node, "address"), str) else None,
                    "city": None, "lat": lat, "lng": lng,
                    "amenities": [], "available": None, "chargers": [],
                }
    return None


# ── Network: discovery + fetch ────────────────────────────────────────────────
async def _get_text(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    if url.endswith(".gz") or resp.headers.get("content-type", "").startswith("application/x-gzip"):
        return gzip.decompress(resp.content).decode("utf-8", "replace")
    return resp.text


async def discover_station_urls(client: httpx.AsyncClient, sitemap_url: str = SITEMAP_URL) -> list[str]:
    """Walk the sitemap (index → child sitemaps) and return station page URLs."""
    seen: set[str] = set()
    queue = [sitemap_url]
    stations: list[str] = []
    while queue:
        sm = queue.pop()
        if sm in seen:
            continue
        seen.add(sm)
        try:
            xml = await _get_text(client, sm)
        except Exception as e:  # noqa: BLE001
            print(f"sitemap {sm} failed: {e}", flush=True)
            continue
        locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", xml, re.DOTALL)
        for loc in locs:
            loc = loc.strip()
            if loc.endswith(".xml") or loc.endswith(".xml.gz"):
                queue.append(loc)
            elif STATION_URL_RE.search(loc):
                stations.append(loc)
    # de-dup keeping order
    return list(dict.fromkeys(stations))


async def fetch_station(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore) -> Optional[dict]:
    async with sem:
        try:
            html = await _get_text(client, url)
        except Exception as e:  # noqa: BLE001
            print(f"station {url} failed: {e}", flush=True)
            return None
        await asyncio.sleep(0.4)  # be polite even though robots.txt allows all
        return parse_station_page(html, url)


# ── Upsert ────────────────────────────────────────────────────────────────────
async def _upsert(stations: list[dict]) -> tuple[int, int]:
    added = updated = 0
    async with SessionLocal() as db:
        existing_pos = (
            await db.execute(select(Charger.lat, Charger.lng).where(Charger.external_id.is_(None)))
        ).all()
        for norm in stations:
            c = to_charger(norm)
            if c is None:
                continue
            row = (
                await db.execute(
                    select(Charger).options(selectinload(Charger.reliability))
                    .where(Charger.external_id == c.external_id)
                )
            ).scalar_one_or_none()
            if row is not None:
                # refresh live-ish fields on re-run
                row.name, row.address, row.city = c.name, c.address, c.city
                row.lat, row.lng = c.lat, c.lng
                row.connectors, row.price_per_kwh = c.connectors, c.price_per_kwh
                row.status, row.amenities = c.status, c.amenities
                updated += 1
                continue
            if any(haversine_km(c.lat, c.lng, la, ln) < PROXIMITY_DEDUPE_KM for la, ln in existing_pos):
                continue  # already covered by OCM/OSM/Google/seed
            db.add(c)
            await db.flush()
            # Statiq is first-party operator data (live status) → slightly positive baseline
            db.add(ReliabilityScore(charger_id=c.id, score=0.6, baseline_pos=1.0))
            existing_pos.append((c.lat, c.lng))
            added += 1
        await db.commit()
    return added, updated


async def run(
    *, sitemap: bool = False, city: Optional[str] = None, url: Optional[str] = None,
    max_results: int = 0, concurrency: int = 4,
) -> int:
    await init_db()
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        if url:
            urls = [url]
        elif city:
            page = await _get_text(client, f"{BASE}/{city.strip('/')}")
            urls = list(dict.fromkeys(
                (u if u.startswith("http") else BASE + u)
                for u in re.findall(r'href="([^"]*-ev-charging-station-id-\d+)"', page)
            ))
        elif sitemap:
            urls = await discover_station_urls(client)
        else:
            raise ValueError("Pass one of sitemap=True, city=..., or url=...")

        if max_results:
            urls = urls[:max_results]
        print(f"Statiq: {len(urls)} station page(s) to fetch.", flush=True)

        results = await asyncio.gather(*(fetch_station(client, u, sem) for u in urls))
        stations = [s for s in results if s and s.get("id")]

    added, updated = await _upsert(stations)
    print(f"Statiq: +{added} new, {updated} refreshed ({len(stations)}/{len(urls)} parsed).", flush=True)
    return added


async def _probe(url: str) -> None:
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        html = await _get_text(client, url)
    norm = parse_station_page(html, url)
    print("── Extracted station (confirm these fields map correctly) ──")
    print(json.dumps(norm, indent=2, ensure_ascii=False))
    if norm:
        c = to_charger(norm)
        print("── Would import as Charger ──")
        print(json.dumps({
            "external_id": c.external_id, "name": c.name, "operator": c.operator,
            "city": c.city, "lat": c.lat, "lng": c.lng, "status": c.status,
            "price_per_kwh": c.price_per_kwh, "connectors": c.connectors,
            "amenities": c.amenities,
        }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Import Statiq stations from statiq.in")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--sitemap", action="store_true", help="discover all stations via sitemap.xml")
    g.add_argument("--city", help="city page slug, e.g. bengaluru-ev-charging-station")
    g.add_argument("--url", help="single station page URL")
    g.add_argument("--probe", help="fetch one station URL and dump the parsed data (no DB write)")
    p.add_argument("--max", type=int, default=0, help="cap number of stations (0 = no cap)")
    p.add_argument("--concurrency", type=int, default=4)
    a = p.parse_args()
    if a.probe:
        asyncio.run(_probe(a.probe))
    else:
        asyncio.run(run(sitemap=a.sitemap, city=a.city, url=a.url,
                        max_results=a.max, concurrency=a.concurrency))
