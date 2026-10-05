# master_data: buses, stops, routes

> The fleet and the network: what runs, where it stops, and in what order.

| | |
|---|---|
| **Owner** | Team A, member 1 |
| **Backend** | `backend/app/modules/master_data/` |
| **Frontend** | `frontend/lib/modules/master_data/` |
| **Status** | P0 done |

## Responsibilities
- Buses (registration, seat capacity, status `active / maintenance / retired`).
- Stops (name, landmark, optional lat/lng, ready for GPS in P1).
- Routes (code shown on the route badge, name, **line colour**) and their ordered stops.

Not here: when buses run (trips), who rides (allocation).

## Route convention (important for everyone)
- `route_stops.sequence` 1 = first pickup point. **The last stop is the campus.**
- `offset_min` = minutes after departure from stop 1, in the pickup direction. It must not decrease.
- Drop trips run the list in reverse. The trips module computes those times.

## Data model
| Table | Key columns |
|---|---|
| `buses` | `registration_no` (unique, upper-cased), `capacity`, `model`, `status`, `driver_id` (the bus's regular driver, **unique**: one bus per driver) |
| `stops` | `name`, `landmark`, `latitude`, `longitude` |
| `routes` | `code` (unique), `name`, `color` (`#RRGGBB`), `description`, `is_active` |
| `route_stops` | `route_id`, `stop_id`, `sequence`, `offset_min`; unique (route, sequence) **deferred**, unique (route, stop) |

## API
| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/buses` | admin, driver | list |
| GET | `/buses/{id}` | admin, driver | one bus |
| POST / PATCH / DELETE | `/buses[/{id}]` | admin | manage (delete refused if referenced → retire instead) |
| PUT | `/buses/{id}/driver` `{driver_id \| null, move?}` | admin | assign / change / remove the bus's driver. 409 `driver_taken` (with the other bus) unless `move: true` |
| GET | `/stops?q=` | any | list/search |
| POST / PATCH / DELETE | `/stops[/{id}]` | admin | manage |
| GET | `/routes`, `/routes/{id}` | any | routes **with ordered stops** (students need the line diagram) |
| POST / PATCH | `/routes[/{id}]` | admin | manage |
| PUT | `/routes/{id}/stops` | admin | replace ordered list `[{stop_id, offset_min}]` |

Error codes: `duplicate_stop`, `bad_offsets`, `stop_not_on_route`, 409 conflicts for duplicates / in-use deletes.

## Events
| Emits | When |
|---|---|
| `BusCreated`, `BusStatusChanged` | fleet changes (status change is what Team B's maintenance work hooks into) |
| `BusDriverAssigned` | `bus_id, registration_no, driver_id, previous_driver_id, moved_from_bus_id`. The trips module hands the bus's runs to the new driver; notifications tells both drivers |
| `RouteCreated`, `RouteUpdated`, `RouteStopsChanged` | network changes |

## Public service API
`get_bus`, `list_buses`, `assign_driver`, `get_route` (stops eager-loaded), `list_routes`, `get_route_stop`,
`find_route_stop(route_id, stop_id)`.

## Frontend screens
| Screen | Role | File |
|---|---|---|
| Network (routes list + line preview) | admin | `frontend/lib/modules/master_data/screens/admin_network_screen.dart` |
| Route editor (ordered stops) | admin | `frontend/lib/modules/master_data/screens/route_editor_screen.dart` |
| Fleet (buses) | admin | `frontend/lib/modules/master_data/screens/admin_fleet_screen.dart` |

## How to test
```bash
cd backend && .venv/Scripts/python -m pytest app/modules/master_data -q
```

## Extension notes
- P1 GPS: stops already have lat/lng; route polylines can live in a new `route_shapes` table here.
- P2 route optimisation writes new `route_stops` through `set_route_stops`, which already preserves
  allocations for stops that stay.
