"""Capacity monitoring: live bus occupancy and route allocation utilisation.

Owns no tables; it reads boarding, allocation, trips and master data, and publishes
CapacityWarning / OverCapacity once per trip.
"""

import math

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.config import settings
from app.core.events import DomainEvent
from app.modules.allocation import service as alloc_service
from app.modules.boarding import service as boarding_service
from app.modules.capacity.schemas import Level, RouteUtilization, TripOccupancy
from app.modules.master_data import service as md_service
from app.modules.master_data.models import Bus
from app.modules.trips import service as trips_service
from app.modules.trips.models import Trip, TripSchedule


def level_for(count: int, capacity: int | None) -> Level:
    if not capacity:
        return Level.UNKNOWN
    if count > capacity:
        return Level.OVER
    if count == capacity:
        return Level.FULL
    if count * 100 >= capacity * settings.capacity_warn_pct:
        return Level.WARNING
    return Level.OK


def _pct(count: int, capacity: int) -> int:
    return round(count * 100 / capacity) if capacity else 0


def _occupancy(trip: Trip, bus: Bus, boarded: int) -> TripOccupancy:
    return TripOccupancy(trip_id=trip.id, route_id=trip.route_id, bus_id=bus.id, boarded=boarded,
                         capacity=bus.capacity, pct=_pct(boarded, bus.capacity),
                         level=level_for(boarded, bus.capacity))


async def trip_occupancy(session: AsyncSession, trip: Trip) -> TripOccupancy:
    bus = await md_service.get_bus(session, trip.bus_id)
    return _occupancy(trip, bus, await boarding_service.boarded_count(session, trip.id))


async def trip_occupancies(session: AsyncSession, trips: list[Trip]) -> dict[int, TripOccupancy]:
    """trip_occupancy for many trips in two queries (dashboards list every trip of the day)."""
    if not trips:
        return {}
    buses = {b.id: b for b in await session.scalars(select(Bus).where(Bus.id.in_({t.bus_id for t in trips})))}
    counts = await boarding_service.boarded_counts(session, [t.id for t in trips])
    return {t.id: _occupancy(t, buses[t.bus_id], counts.get(t.id, 0)) for t in trips}


async def active_occupancy(session: AsyncSession) -> list[TripOccupancy]:
    trips = await trips_service.active_trips(session)
    occ = await trip_occupancies(session, trips)
    return [occ[t.id] for t in trips]


async def route_utilization(session: AsyncSession) -> list[RouteUtilization]:
    allocated = await alloc_service.count_active_by_route(session)
    sched_counts = dict((await session.execute(
        select(TripSchedule.route_id, func.count())
        .where(TripSchedule.is_active.is_(True)).group_by(TripSchedule.route_id)
    )).all())
    capacities = await trips_service.route_seat_capacities(session)
    out = []
    for r in await md_service.list_routes(session, active_only=True):
        cap = capacities.get(r.id)
        n = allocated.get(r.id, 0)
        out.append(RouteUtilization(
            route_id=r.id, code=r.code, name=r.name, color=r.color, allocated=n, seat_capacity=cap,
            pct=_pct(n, cap) if cap else None, level=level_for(n, cap),
            active_schedules=sched_counts.get(r.id, 0),
        ))
    return out


async def _already_raised(session: AsyncSession, event_type: str, trip_id: int) -> bool:
    return bool(await session.scalar(
        select(DomainEvent.id).where(DomainEvent.type == event_type, DomainEvent.aggregate_type == "trip",
                                     DomainEvent.aggregate_id == trip_id).limit(1)
    ))


async def check_trip_capacity(session: AsyncSession, trip_id: int) -> str | None:
    """Raise CapacityWarning / OverCapacity for a trip, at most once each. Returns the event type raised.

    The trip row is locked first, so two students boarding at the same moment can't both see
    "not raised yet" and raise the alert twice."""
    trip = await trips_service.get_trip_for_update(session, trip_id)
    occ = await trip_occupancy(session, trip)
    payload = {"trip_id": trip.id, "route_id": trip.route_id, "bus_id": trip.bus_id,
               "driver_id": trip.driver_id, "boarded": occ.boarded, "capacity": occ.capacity, "pct": occ.pct}
    if occ.level == Level.OVER and not await _already_raised(session, "OverCapacity", trip.id):
        await events.publish(session, "OverCapacity", payload, aggregate=("trip", trip.id))
        return "OverCapacity"
    warn_at = math.ceil(occ.capacity * settings.capacity_warn_pct / 100)
    if occ.boarded >= warn_at and occ.level in (Level.WARNING, Level.FULL) \
            and not await _already_raised(session, "CapacityWarning", trip.id):
        await events.publish(session, "CapacityWarning", payload, aggregate=("trip", trip.id))
        return "CapacityWarning"
    return None
