"""Route provider abstraction.

- StraightLineRouter: dev/test fallback — interpolates a straight polyline and
  approximates road distance with a 1.25 circuity factor.
- GoogleRouter: used automatically when GOOGLE_MAPS_API_KEY is set.
"""
from dataclasses import dataclass

import httpx

from ..config import settings
from .geo import haversine_km, interpolate

CIRCUITY_FACTOR = 1.25
AVG_SPEED_KMH = 45.0  # blended city/highway; Google provides real durations


@dataclass
class Route:
    points: list[tuple[float, float]]   # polyline
    distance_km: float
    duration_minutes: float
    is_highway: bool = False


class StraightLineRouter:
    async def route(self, origin: tuple[float, float], dest: tuple[float, float]) -> Route:
        straight = haversine_km(*origin, *dest)
        distance = straight * CIRCUITY_FACTOR
        n = max(int(straight // 1), 2)
        points = [interpolate(origin, dest, i / n) for i in range(n + 1)]
        return Route(
            points=points,
            distance_km=distance,
            duration_minutes=distance / AVG_SPEED_KMH * 60,
            is_highway=straight > 40,
        )


class GoogleRouter:
    async def route(self, origin: tuple[float, float], dest: tuple[float, float]) -> Route:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/directions/json",
                params={
                    "origin": f"{origin[0]},{origin[1]}",
                    "destination": f"{dest[0]},{dest[1]}",
                    "key": settings.google_maps_api_key,
                },
                timeout=10,
            )
        data = resp.json()
        if data.get("status") != "OK":
            return await StraightLineRouter().route(origin, dest)
        leg = data["routes"][0]["legs"][0]
        points = _decode_polyline(data["routes"][0]["overview_polyline"]["points"])
        return Route(
            points=points,
            distance_km=leg["distance"]["value"] / 1000,
            duration_minutes=leg["duration"]["value"] / 60,
            is_highway=leg["distance"]["value"] / 1000 > 40,
        )


def _decode_polyline(encoded: str) -> list[tuple[float, float]]:
    points, index, lat, lng = [], 0, 0, 0
    while index < len(encoded):
        for coord in ("lat", "lng"):
            shift, result = 0, 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if coord == "lat":
                lat += delta
            else:
                lng += delta
        points.append((lat / 1e5, lng / 1e5))
    return points


def get_router():
    return GoogleRouter() if settings.google_maps_api_key else StraightLineRouter()
