"""Single-stop, risk-free trip planner (see trip planning spec).

Guarantees:
- never plans the battery below RESERVE_SOC (pessimistic model),
- consumption inflated by PESSIMISM_FACTOR,
- every proposed stop has a reachable backup charger.
"""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..models import Charger, ChargerStatus, Vehicle
from . import reliability
from .geo import bounding_box, haversine_km, sample_polyline
from .routing import Route, get_router
from typing import Optional

CHARGE_EFFICIENCY = 0.85  # taper/loss factor
AC_FALLBACK_KW = 7.0      # assumed AC rate for vehicles without DC charging


@dataclass
class EnergyModel:
    vehicle: Vehicle
    effective_efficiency: float  # Wh/km, before pessimism

    @classmethod
    def build(cls, vehicle: Vehicle, route: Route) -> "EnergyModel":
        terrain_factor = 1.0          # TODO: elevation from route (up to 1.15)
        speed_factor = 1.15 if route.is_highway else 1.0
        climate_factor = 1.05         # AC season default for India
        eff = vehicle.efficiency_wh_per_km * terrain_factor * speed_factor * climate_factor
        return cls(vehicle=vehicle, effective_efficiency=eff)

    def energy_needed_kwh(self, distance_km: float) -> float:
        return distance_km * self.effective_efficiency * settings.pessimism_factor / 1000

    def soc_drop(self, distance_km: float) -> float:
        return self.energy_needed_kwh(distance_km) / self.vehicle.battery_kwh * 100

    def safe_range_km(self, soc: float) -> float:
        usable_kwh = self.vehicle.battery_kwh * max(soc - settings.reserve_soc, 0) / 100
        return usable_kwh * 1000 / (self.effective_efficiency * settings.pessimism_factor)


@dataclass
class Candidate:
    charger: Charger
    rel_score: float
    route_km: float          # position along route of nearest sample
    detour_km: float
    arrival_soc: float
    dist_from_origin_km: float
    dist_to_dest_km: float
    backup: Optional[Charger] = None
    score: float = 0.0
    warnings: list = field(default_factory=list)


def _connector_match(charger: Charger, vehicle: Vehicle) -> bool:
    return bool(set(vehicle.connector_types) & {c["type"] for c in charger.connectors})


def _best_power_kw(charger: Charger, vehicle: Vehicle) -> float:
    compatible = [c["power_kw"] for c in charger.connectors if c["type"] in vehicle.connector_types]
    if not compatible:
        return 0.0
    vehicle_max = vehicle.max_dc_power_kw or AC_FALLBACK_KW
    return min(max(compatible), vehicle_max)


async def _corridor_chargers(db: AsyncSession, samples: list, detour_km: float) -> list[Charger]:
    """Chargers within detour_km of any polyline sample. MVP: bounding box over
    the window + per-sample distance check. Production: PostGIS ST_DWithin on
    the route linestring."""
    if not samples:
        return []
    lats = [s[0] for s in samples]
    lngs = [s[1] for s in samples]
    pad = detour_km / 100.0  # ~degrees
    rows = (
        await db.execute(
            select(Charger)
            .options(selectinload(Charger.reliability))
            .where(
                Charger.lat.between(min(lats) - pad, max(lats) + pad),
                Charger.lng.between(min(lngs) - pad, max(lngs) + pad),
            )
        )
    ).scalars().all()
    result = []
    for c in rows:
        best = min(
            (haversine_km(c.lat, c.lng, s[0], s[1]), s[2]) for s in samples
        )
        if best[0] <= detour_km:
            c._detour_km = best[0]     # annotate
            c._route_km = best[1]
            result.append(c)
    return result


async def _has_backup(db: AsyncSession, primary: Candidate, vehicle: Vehicle,
                      model: EnergyModel, post_charge_soc: float,
                      min_reliability: float) -> Optional[Charger]:
    """Rule 6: another compatible, reliable charger reachable from the primary
    with the SoC the vehicle arrives with (if primary is dead, you must escape)."""
    reach_km = model.safe_range_km(primary.arrival_soc)
    if reach_km <= 0:
        return None
    min_lat, max_lat, min_lng, max_lng = bounding_box(
        primary.charger.lat, primary.charger.lng, reach_km
    )
    rows = (
        await db.execute(
            select(Charger)
            .options(selectinload(Charger.reliability))
            .where(
                Charger.id != primary.charger.id,
                Charger.lat.between(min_lat, max_lat),
                Charger.lng.between(min_lng, max_lng),
            )
        )
    ).scalars().all()
    best: Optional[tuple[float, Charger]] = None
    for c in rows:
        if not _connector_match(c, vehicle):
            continue
        if c.status == ChargerStatus.BROKEN.value:
            continue
        if reliability.effective_score(c.reliability) < min_reliability:
            continue
        d = haversine_km(primary.charger.lat, primary.charger.lng, c.lat, c.lng) * 1.25
        if d <= reach_km and (best is None or d < best[0]):
            best = (d, c)
    return best[1] if best else None


def _score(cand: Candidate, vehicle: Vehicle, max_price: float, detour_limit: float) -> float:
    power = _best_power_kw(cand.charger, vehicle)
    vehicle_max = vehicle.max_dc_power_kw or AC_FALLBACK_KW
    detour_score = 1 - min(cand.detour_km / detour_limit, 1)
    speed_score = power / vehicle_max if vehicle_max else 0
    price = cand.charger.price_per_kwh
    price_score = 1 - (price / max_price) if (price and max_price) else 0.5
    poi_score = min(len(cand.charger.amenities) / 4, 1.0)
    buffer_score = min(max(cand.arrival_soc - settings.reserve_soc, 0) / 30, 1.0)
    return (
        0.30 * cand.rel_score
        + 0.20 * detour_score
        + 0.20 * speed_score
        + 0.10 * price_score
        + 0.10 * poi_score
        + 0.10 * buffer_score
    )


async def plan_trip(db: AsyncSession, origin: tuple[float, float], dest: tuple[float, float],
                    vehicle: Vehicle, departure_soc: float) -> dict:
    route = await get_router().route(origin, dest)
    model = EnergyModel.build(vehicle, route)
    warnings: list[str] = []

    # Step 1: feasibility without charging
    trip_drop = model.soc_drop(route.distance_km)
    if departure_soc >= trip_drop + settings.target_arrival_soc:
        return _plan(feasible=True, stops=[], route=route,
                     arrival_soc=departure_soc - trip_drop,
                     confidence="high", note="No charging needed", warnings=warnings)

    # Vehicle can't reach any charger at all?
    if model.safe_range_km(departure_soc) <= 0:
        return _plan(feasible=False, stops=[], route=route, arrival_soc=None,
                     confidence="low",
                     note="Battery below safe reserve — charge before departing", warnings=warnings)

    # Step 2: charging window along the route
    soc_to_50 = max(departure_soc - 50.0, 0.0)
    earliest_km = soc_to_50 / trip_drop * route.distance_km if trip_drop > 0 else 0.0
    latest_km = model.safe_range_km(departure_soc)
    if latest_km < earliest_km:
        earliest_km = 0.0
    latest_km = min(latest_km, route.distance_km)

    # sample the polyline; scale cumulative distances so they match the (road)
    # route distance rather than raw polyline geometry
    all_samples = sample_polyline(route.points, step_km=2.0)
    polyline_km = all_samples[-1][2] if all_samples else 0.0
    scale = route.distance_km / polyline_km if polyline_km > 0 else 1.0
    all_samples = [(s[0], s[1], s[2] * scale) for s in all_samples]

    samples = [s for s in all_samples if earliest_km <= s[2] <= latest_km]
    if not samples:
        samples = [s for s in all_samples if s[2] <= latest_km]

    # Fallback ladder (spec §4): widen detour → lower reliability → 2-stop hint → give up
    ladder = [
        (settings.max_detour_km, settings.min_reliability, None),
        (8.0, settings.min_reliability, "Widened search corridor to 8 km"),
        (8.0, 0.65, "Included lower-reliability chargers — verify before relying on them"),
    ]

    for detour_limit, min_rel, note in ladder:
        candidates = await _find_viable(db, samples, route, model, vehicle,
                                        departure_soc, detour_limit, min_rel)
        if candidates:
            if note:
                warnings.append(note)
            max_price = max((c.charger.price_per_kwh or 0) for c in candidates) or 0
            for c in candidates:
                c.score = _score(c, vehicle, max_price, detour_limit)
            best = max(candidates, key=lambda c: c.score)
            stop = _build_stop(best, model, vehicle, route)
            confidence = "high" if best.rel_score >= 0.85 and not warnings else (
                "medium" if best.rel_score >= settings.min_reliability else "low")
            return _plan(feasible=True, stops=[stop], route=route,
                         arrival_soc=stop["destination_arrival_soc"],
                         confidence=confidence, note=None, warnings=warnings,
                         extra_minutes=stop["dwell_minutes"] + stop["detour_km"] / 30 * 60 * 2)

    return _plan(
        feasible=False, stops=[], route=route, arrival_soc=None, confidence="low",
        note=("No safe single-stop plan found. Consider charging before departure "
              "or splitting the trip into two shorter charge stops."),
        warnings=warnings,
    )


async def _find_viable(db, samples, route, model, vehicle, departure_soc,
                       detour_limit, min_rel) -> list[Candidate]:
    chargers = await _corridor_chargers(db, samples, detour_limit)
    # Dense corridor: pre-rank by reliability, cap scoring set (spec §6)
    if len(chargers) > 50:
        chargers.sort(key=lambda c: reliability.effective_score(c.reliability), reverse=True)
        chargers = chargers[:20]

    viable = []
    for c in chargers:
        if not _connector_match(c, vehicle):                                   # filter 1
            continue
        rel_score = reliability.effective_score(c.reliability)
        if rel_score < min_rel or c.status == ChargerStatus.BROKEN.value:      # filters 2–3
            continue
        dist_to_charger = c._route_km + c._detour_km
        arrival = departure_soc - model.soc_drop(dist_to_charger)
        if arrival < settings.reserve_soc:                                     # filter 4
            continue
        remaining = (route.distance_km - c._route_km) + c._detour_km
        drop_after = model.soc_drop(remaining)
        if settings.max_charge_soc - drop_after < settings.target_arrival_soc:  # filter 5
            continue
        cand = Candidate(
            charger=c, rel_score=rel_score, route_km=c._route_km, detour_km=c._detour_km,
            arrival_soc=round(arrival, 1), dist_from_origin_km=dist_to_charger,
            dist_to_dest_km=remaining,
        )
        backup = await _has_backup(db, cand, vehicle, model,
                                   settings.max_charge_soc, min_rel)           # filter 6
        if backup is None:
            continue
        cand.backup = backup
        viable.append(cand)
    return viable


def _build_stop(cand: Candidate, model: EnergyModel, vehicle: Vehicle, route: Route) -> dict:
    soc_needed = model.soc_drop(cand.dist_to_dest_km) + settings.target_arrival_soc
    target_soc = min(soc_needed + 5, settings.max_charge_soc)
    energy_to_add = vehicle.battery_kwh * max(target_soc - cand.arrival_soc, 0) / 100
    power = _best_power_kw(cand.charger, vehicle) * CHARGE_EFFICIENCY
    dwell_minutes = energy_to_add / power * 60 if power > 0 else 0
    price = cand.charger.price_per_kwh
    dest_arrival = target_soc - model.soc_drop(cand.dist_to_dest_km)
    return dict(
        charger=cand.charger,
        backup_charger=cand.backup,
        arrival_soc=cand.arrival_soc,
        target_soc=round(target_soc, 1),
        energy_to_add_kwh=round(energy_to_add, 2),
        dwell_minutes=round(dwell_minutes, 1),
        estimated_cost=round(energy_to_add * price, 2) if price else None,
        detour_km=round(cand.detour_km, 2),
        destination_arrival_soc=round(dest_arrival, 1),
    )


def _plan(feasible, stops, route, arrival_soc, confidence, note, warnings, extra_minutes=0.0):
    return dict(
        feasible=feasible,
        stops=stops,
        destination_arrival_soc=round(arrival_soc, 1) if arrival_soc is not None else None,
        total_distance_km=round(route.distance_km, 1),
        drive_minutes=round(route.duration_minutes, 1),
        total_trip_minutes=round(route.duration_minutes + extra_minutes, 1) if feasible else None,
        confidence=confidence,
        note=note,
        warnings=warnings,
    )
