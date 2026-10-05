# master_data: dev log

## 2026-10-04: Review fixes (Review.md)
**Changed**
- PATCH bodies reject `null` for required fields with a 422 (`core.schemas.PatchModel`); before, a
  null registration no was a 500 and a null capacity a misleading 409 (M1).
- `GET /buses/{id}` is staff only (L7). `_flush_or_conflict` moved to `core.db.flush_or_conflict`.

## 2026-09-24: Admin assigns a driver to each bus
**Built**
- `buses.driver_id` (unique FK to users) + migration `master_data: bus assigned driver`.
- `PUT /buses/{id}/driver` with a `driver_taken` conflict and a `move` override; `BusOut` now
  carries `driver_id` / `driver_name`.
- Fleet screen: driver line per bus, *Assign / Change driver* dialog that lists which bus each
  driver already has and offers "Move driver here". The schedule dialog pre-selects the bus's driver.
- Seed gives buses 1–3 their regular drivers (bus 4 is a spare).

**Decisions (and why)**
- **The bus assignment drives the runs.** Changing it publishes `BusDriverAssigned`, and the trips
  module moves that bus's active schedules and not-yet-started trips to the new driver. Admins
  think "Murugan drives TN 09 AB 1401", not "Murugan does schedule #3". master_data never writes
  trips tables; the event keeps that boundary.
- **Running and finished trips keep their driver**, so history stays true and a trip isn't pulled
  from under someone mid-route.
- **One bus per driver (unique)**, but moving is one click: the conflict names the other bus, and
  `move: true` frees it. Silently moving would leave a bus driverless without anyone noticing.
- **Removing a driver doesn't touch existing runs.** A trip with no driver can't start, so runs
  keep their last driver until someone new is assigned.
- `Bus.driver` is a `viewonly` relationship to auth's `User`, for display only.

**Issues / next**
- When a driver is *moved*, the bus they left keeps its schedules pointing at them until the admin
  assigns that bus a new driver. The Fleet screen shows that bus as "No driver assigned".

## 2026-09-24: Flutter screens
**Built**
- Network screen (routes as horizontal strip maps), route editor (ordered stops, minutes, move
  up/down, add existing or new stop, live line-diagram preview), Fleet screen (number plates,
  status plates, change status).

**Decisions (and why)**
- **Curated route palette** that avoids red/amber/green, because those are reserved for status in the design system.
- The editor saves the whole ordered list in one PUT, matching the backend's replace semantics.

## 2026-09-24: P0 master data module
**Built**
- Buses, stops, routes, ordered route stops. CRUD + `PUT /routes/{id}/stops` (full ordered replace).

**Decisions (and why)**
- **Reorder keeps existing `route_stops` rows** (matched by `stop_id`) and only re-sequences them.
  Allocations point at `route_stops.id`, so a reorder must not orphan students.
- **`(route_id, sequence)` unique constraint is DEFERRABLE INITIALLY DEFERRED**, so swapping two
  stops' sequence numbers in one transaction doesn't trip the constraint mid-update.
- **Removing a stop students use is blocked by the database** (FK RESTRICT from active allocations)
  rather than by calling the allocation module. That avoids a master_data → allocation dependency
  cycle and can't be bypassed by a future code path.
- **Offsets must be non-decreasing**, which catches data-entry mistakes early. Every downstream time
  calculation assumes it.
- New `RouteStop` objects get their `Stop` object attached directly. Otherwise the response
  serialiser lazy-loads `rs.stop` outside the async context (MissingGreenlet).
- Route colour is **identity**, not status. Status colours (amber/red/green) are reserved in the
  design system, so seed route colours avoid them.

**Issues / next**
- No soft-delete for stops. A stop referenced by any past trip (`trip_stop_events.stop_id`,
  FK RESTRICT) can't be deleted: the API returns 409. Rename it instead, or add an
  `is_active` flag if this becomes a problem.
