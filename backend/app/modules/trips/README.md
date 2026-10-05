# trips: schedules and the driver workflow

> When buses run, and the driver's **start → arrive at stop → end** workflow.

| | |
|---|---|
| **Owner** | Team A, member 3 |
| **Backend** | `backend/app/modules/trips/` |
| **Frontend** | `frontend/lib/modules/trips/` |
| **Status** | P0 done |

## Concepts
- **Schedule**: recurring service: route + bus + driver + direction + departure time + weekdays.
- **Trip**: one run of a schedule on a service date. Generated from schedules (idempotent).
- **Trip stop event**: planned vs actual time at each stop, in the order the bus visits them.

### Stop times
Pickup: stop *i* at `departure + offset_i`. Drop (campus first, list reversed): stop *i* at
`departure + (campus_offset − offset_i)`. Sequence 1 is always the departure point.

### State machine
```
scheduled ──start──▶ in_progress ──end──▶ completed
    └──────cancel───────┴──────cancel────▶ cancelled
```
- **start**: assigned driver (or admin). Bus must be `active`; neither driver nor bus may have
  another trip in progress. Marks stop 1 as reached at the start time.
- **arrive** (`/stops/{sequence}/arrive`, or automatically by the tracking module when GPS puts the bus
  within 100 m): records `arrived_at` + `delay_min`. Stops may be skipped,
  but you can't go back to an earlier stop once a later one is reached.
- **end**: marks the terminus reached (if not already) and completes the trip.
- **auto-close**: a trip still `in_progress` after its service day, with no start/stop activity for
  `STALE_TRIP_GRACE_HOURS` (3 h), is completed at its last activity. Unreached stops stay unreached.
  Runs from the background job and before every start, so a forgotten End never blocks the next run.

## Concurrency
Every state change of a trip (start, arrive, end, cancel, and tracking's GPS ingest) first locks
the trip row (`get_trip_for_update`, `SELECT … FOR UPDATE`), so a double tap or a tap racing a GPS
arrival runs once. Partial unique indexes allow at most one `in_progress` trip per driver and per
bus, whatever the timing (409 `already_running`).

## Data model
| Table | Key columns |
|---|---|
| `trip_schedules` | `route_id`, `bus_id`, `driver_id`, `direction` (pickup/drop), `departure_time` (local), `days_of_week` int[] ISO 1–7, `is_active` |
| `trips` | `schedule_id`, `route_id`, `bus_id`, `driver_id`, `direction`, `service_date`, `scheduled_departure` (UTC), `status`, `started_at`, `ended_at`, `current_delay_min`, `cancel_reason`; unique (schedule, date) |
| `trip_stop_events` | `trip_id`, `route_stop_id` (SET NULL), `stop_id`, `stop_name` (snapshot), `sequence`, `scheduled_at`, `arrived_at`, `delay_min` |

`bus_positions` (GPS telemetry) now belongs to the [tracking](../tracking/README.md) module.

## API
| Method | Path | Role | Purpose |
|---|---|---|---|
| GET / POST | `/schedules` | admin | list / create (`driver_id` optional: defaults to the bus's assigned driver, else 422 `driver_required`) |
| PATCH | `/schedules/{id}` | admin | change bus, driver, time, days, active. Time/bus/driver changes reach the schedule's not-yet-started trips (today onwards); deactivating only stops future generation |
| POST | `/trips/generate` `{service_date?}` | admin | create the day's trips (also runs automatically); past dates → 422 `past_date`. Returns `{created, existing, skipped}` |
| GET | `/trips?service_date&status&route_id&driver_id` | admin | trips with route/bus/driver/stops |
| GET | `/trips/mine?service_date` | driver | today's trips for the driver |
| GET | `/trips/{id}` | admin; the trip's driver; students allocated to its route (others: 404) | trip detail incl. `next_stop` |
| POST | `/trips/{id}/start` | driver (own), admin | start (today's trips only: 422 `wrong_day`) |
| POST | `/trips/{id}/stops/{sequence}/arrive` | driver (own), admin | check in at a stop |
| POST | `/trips/{id}/end` | driver (own), admin | finish |
| POST | `/trips/{id}/cancel` `{reason}` | admin | cancel |

**Simulation:** with `ALLOW_SIMULATION=true`, admins may pass `started_at` / `arrived_at` to
replay late arrivals in demos and tests. Drivers can't (403).

Error codes: `bad_trip_state`, `bus_unavailable`, `already_running`, `already_arrived`,
`out_of_order`, `route_incomplete`, `bus_retired`, `wrong_role`.

## Events
| Emits | Payload highlights |
|---|---|
| `TripsGenerated` | `service_date`, `created` |
| `ScheduleSkipped` | once per schedule and day when its bus isn't `active` (no trip is generated): `schedule_id, route_id, bus_id, registration_no, bus_status, direction, departure_time, service_date` |
| `TripStarted` | `trip_id, route_id, bus_id, driver_id, direction, scheduled_departure, started_at, delay_min` |
| `StopArrived` | `sequence, stop_id, stop_name, scheduled_at, arrived_at, delay_min, is_last, source` (`manual` \| `gps`) |
| `TripEnded` | `ended_at, final_delay_min, skipped_stops`, `auto_closed` (only when closed automatically; `actor_id` is null) |
| `TripCancelled` | `reason` |
| `ScheduleCreated`, `ScheduleUpdated` | ids |

| `BusRunsReassigned` | `bus_id, driver_id, schedule_ids, trip_ids` |

Consumers: delay_monitor (TripStarted, StopArrived), boarding (TripEnded → attendance),
notifications, dashboard (live refresh).

`arrive_at_stop(..., observed_at=)` is for in-process callers that detected the arrival themselves
(tracking's geofence): the GPS fix time, clamped between trip start and now. The router never passes it.

| Consumes | Why |
|---|---|
| `BusDriverAssigned` (master_data) | `reassign_bus_driver`: the bus's active schedules and not-yet-started trips (today onwards) move to the new driver. Running/finished trips keep theirs |

## Background jobs
`trip-generator` runs every `TRIP_GENERATION_INTERVAL_SECONDS` (15 min) and at startup, so
today's trips always exist, including after midnight.

`stale-trip-closer` runs on the same interval and auto-closes trips left running on an earlier day
(see the state machine above).

## Public service API
`get_trip`, `list_trips`, `active_trips`, `driver_trips`, `trips_for_route_on`, `trip_detail(s)`,
`next_stop(trip)`, `stops_after(trip, seq)`, `route_seat_capacity(route_id)`, `close_stale_trips()`.

## Frontend screens
| Screen | Role | File |
|---|---|---|
| Today's runs | driver | `frontend/lib/modules/trips/screens/driver_home_screen.dart` |
| Run trip (line + ARRIVED) | driver | `frontend/lib/modules/trips/screens/driver_run_screen.dart` |
| Schedules | admin | `frontend/lib/modules/trips/screens/admin_schedules_screen.dart` |

## How to test
```bash
cd backend && .venv/Scripts/python -m pytest app/modules/trips -q
```

## Extension notes (Team B)
- **GPS:** done in the tracking module, which calls `service.arrive_at_stop(observed_at=…)`.
- **Temporary route reassignment:** `PATCH /schedules/{id}` changes bus/driver for future trips.
  A one-day swap = new trip row with `schedule_id = NULL` (supported by the schema).
