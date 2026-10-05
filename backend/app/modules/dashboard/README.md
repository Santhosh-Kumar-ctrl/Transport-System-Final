# dashboard: per-role home screens

> One call per role that returns everything its home screen shows, plus live "refresh" pushes.

| | |
|---|---|
| **Owner** | Team A, member 5 |
| **Backend** | `backend/app/modules/dashboard/` |
| **Frontend** | `frontend/lib/modules/dashboard/` |
| **Status** | P0 done |

## API
| Method | Path | Role | Returns |
|---|---|---|---|
| GET | `/dashboard/admin` | admin | today's **departure board** (one row per trip: route, bus, driver, status, delay, next stop + expected time, stops done, boarded/capacity, occupancy level, allocated), counts, today's alerts, route utilisation |
| GET | `/dashboard/driver` | driver | today's trips with occupancy + the active trip |
| GET | `/dashboard/student` | student | allocation + route, today's trips on my route with **my stop's expected time**, boarded?, unread count |

"Delay" shown = the worse of the last check-in delay and the latest delay alert, so a bus
flagged overdue by the watcher shows as late even before the driver checks in.

## Live updates
`register()` forwards operational events as `{"type":"ops","data":{event, payload}}`:
- to all **admin** sockets
- to topic **`route:{id}`** (student apps subscribe to their route), **without** `student_id`,
  `student_name` or `allocated_route_id`, and never `UnallocatedBoarding`: every rider of the route
  follows this topic
- to the trip's **driver**

Clients treat `ops` as a hint to re-fetch their dashboard.

Owns no tables. It composes the other modules' services.

## Frontend screens
| Screen | Role | File |
|---|---|---|
| Live departure board | admin | `frontend/lib/modules/dashboard/screens/admin_board_screen.dart` |
| My line (home) | student | `frontend/lib/modules/dashboard/screens/student_home_screen.dart` |

## How to test
```bash
cd backend && .venv/Scripts/python -m pytest app/modules/dashboard -q
```
