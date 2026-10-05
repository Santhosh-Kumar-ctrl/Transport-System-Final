# allocation: student → route + stop

> Which bus route each student rides, and where they get on.

| | |
|---|---|
| **Owner** | Team A, member 2 |
| **Backend** | `backend/app/modules/allocation/` |
| **Frontend** | `frontend/lib/modules/allocation/` |
| **Status** | P0 done |

## Rules
- **One active allocation per student** (partial unique index). Re-assigning ends the old one
  (`status=ended`, `end_reason=reassigned`) and creates a new one, so history is kept.
- The stop must be on the route (`stop_not_on_route`), the user must be an active student
  (`wrong_role`), and the route must be active.
- **Seat guard:** route capacity = smallest bus among the route's active schedules
  (`trips_service.route_seat_capacity`). Going over returns **409 `route_full`** with
  `{allocated, capacity}` unless the admin sends `force: true`, which succeeds with a warning.
  Routes with no schedule yet succeed with a "capacity unknown" warning.
- Assigning a student to the allocation they already have is a no-op (`changed: false`, no event).

## Live channel policy
Registers the WebSocket `route:{id}` topic policy: admins any route, students only the route they
are allocated to, drivers routes they have a trip on today.

## Data model
| Table | Key columns |
|---|---|
| `allocations` | `student_id`, `route_id`, `route_stop_id` (FK RESTRICT, **set only while active**), `stop_id` (history), `status`, `valid_from`, `ended_at`, `end_reason`, `created_by` |

`route_stop_id` is cleared when an allocation ends. That way only *active* allocations block
an admin from deleting a stop, and past allocations don't lock the route forever.

## API
| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/allocations?route_id&stop_id&student_id&include_ended` | admin | list |
| POST | `/allocations` `{student_id, route_id, stop_id, valid_from?, force?}` | admin | assign / reassign → `{allocation, changed, warnings}` |
| POST | `/allocations/bulk` `{items:[…]}` | admin | many at once; per-row result (each row in its own savepoint) |
| DELETE | `/allocations/students/{student_id}` | admin | end the student's allocation |
| GET | `/allocations/me` | student | my allocation + full route with ordered stops |

## Events
| Emits | When |
|---|---|
| `StudentAllocated` | first allocation |
| `AllocationChanged` | moved route/stop (`previous` in payload) |
| `AllocationEnded` | unassigned |

Notifications tells the student in each case.

## Public service API
`get_active(student_id)`, `active_on_route(route_id)`, `students_at_stops(route_id, stop_ids)`,
`count_active_by_route()`, `to_out(allocations)`, `allocation_valid_on(student, route, date)`.

## Frontend screens
| Screen | Role | File |
|---|---|---|
| Allocation board (route → stops → students) | admin | `frontend/lib/modules/allocation/screens/admin_allocation_screen.dart` |

## How to test
```bash
cd backend && .venv/Scripts/python -m pytest app/modules/allocation -q
```

## Extension notes (Team B)
- **Smart reallocation / consolidation (P2):** call `service.assign` (or `bulk_assign`) from the
  agent. It already enforces capacity and emits events, so notifications come free.
- The capacity recommendation can read `capacity_service.route_utilization()`.
