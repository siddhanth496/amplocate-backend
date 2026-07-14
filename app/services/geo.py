"""Geospatial helpers.

MVP uses a bounding-box SQL prefilter + haversine in Python, which works on both
SQLite (dev/tests) and PostgreSQL. Production swap: replace `nearby_chargers`
with a PostGIS query —
    SELECT * FROM chargers
    WHERE ST_DWithin(location::geography, ST_MakePoint(:lng,:lat)::geography, :radius_m)
"""
import math

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bounding_box(lat: float, lng: float, radius_km: float) -> tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lng, max_lng) box containing the radius circle."""
    dlat = radius_km / 110.574
    dlng = radius_km / (111.320 * max(math.cos(math.radians(lat)), 0.01))
    return lat - dlat, lat + dlat, lng - dlng, lng + dlng


def interpolate(p1: tuple[float, float], p2: tuple[float, float], frac: float) -> tuple[float, float]:
    return (p1[0] + (p2[0] - p1[0]) * frac, p1[1] + (p2[1] - p1[1]) * frac)


def sample_polyline(points: list[tuple[float, float]], step_km: float = 2.0) -> list[tuple[float, float, float]]:
    """Return (lat, lng, cumulative_km) samples every `step_km` along a polyline."""
    if not points:
        return []
    samples = [(points[0][0], points[0][1], 0.0)]
    cum = 0.0            # distance walked so far
    next_at = step_km    # cumulative distance of the next sample to emit
    for i in range(1, len(points)):
        seg = haversine_km(*points[i - 1], *points[i])
        if seg <= 0:
            continue
        while next_at <= cum + seg:
            frac = (next_at - cum) / seg
            lat, lng = interpolate(points[i - 1], points[i], frac)
            samples.append((lat, lng, next_at))
            next_at += step_km
        cum += seg
    if samples[-1][2] < cum:
        samples.append((points[-1][0], points[-1][1], cum))
    return samples
