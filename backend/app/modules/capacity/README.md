# capacity: occupancy and utilisation

> How full each running bus is, how full each route's allocation is, and alerts when it matters.

| | |
|---|---|
| **Owner** | Team A, member 4 |
| **Backend** | `backend/app/modules/capacity/` |
| **Frontend** | `frontend/lib/modules/capacity/` (widgets used on dashboards) |
| **Status** | P0 done |

## What it computes
| Level | Rule |
|---|---|
| `ok` | below `CAPACITY_WARN_PCT` (90%) |
| `warning` | ≥ 90% |
| `full` | exactly at capacity |
| `over` | more riders (or allocations) than seats |
| `unknown` | no capacity known (route without schedule) |

- **Trip occupancy** = boarded students / bus capacity.
- **Route utilisation** = active allocations / route seat capacity (smallest scheduled bus).

Owns no tables. It reads boarding, allocation, trips and master data through their services
(and `domain_events` to de-duplicate alerts).

## Alerts
On every `StudentBoarded`: publish `CapacityWarning` when the trip first reaches the warn level
(or full), and `OverCapacity` when it first exceeds seats. **Each is raised at most once per trip**
(checked against `domain_events`), so the driver doesn't get a buzz for every extra rider.

## API
| Method | Path | Role |
|---|---|---|
| GET | `/capacity/trips/{id}` | admin; the trip's driver; students on its route |
| GET | `/capacity/active` | admin |
| GET | `/capacity/routes` | admin |

## Events
| Emits | Payload |
|---|---|
| `CapacityWarning`, `OverCapacity` | `trip_id, route_id, bus_id, driver_id, boarded, capacity, pct` |

| Consumes | Why |
|---|---|
| `StudentBoarded` | re-check the trip |

## Public service API
`level_for(count, capacity)`, `trip_occupancy(trip)`, `active_occupancy()`, `route_utilization()`,
`check_trip_capacity(trip_id)`.

## Frontend
- `SeatBlocks` (`frontend/lib/design/widgets/seat_blocks.dart`) renders occupancy as a top-down
  bus seat plan: on the departure board, driver screens and route utilisation.
- `UtilizationList` (`frontend/lib/modules/capacity/widgets/utilization_list.dart`): seats allocated
  per route, shown on the admin live board.

## How to test
```bash
cd backend && .venv/Scripts/python -m pytest app/modules/capacity -q
```

## Extension notes (Team B)
Capacity recommendations / demand prediction can start from `route_utilization()` + attendance
history (actual riders vs allocated).
