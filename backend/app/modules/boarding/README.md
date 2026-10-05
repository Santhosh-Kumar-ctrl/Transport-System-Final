# boarding: QR check-in and attendance

> The driver's phone shows a trip QR; students scan it to board. Attendance is written when the trip ends.

| | |
|---|---|
| **Owner** | Team A, member 4 |
| **Backend** | `backend/app/modules/boarding/` |
| **Frontend** | `frontend/lib/modules/boarding/` |
| **Status** | P0 done |

## Flow
```
Driver app                      API                              Student app
GET /boarding/trips/{id}/qr ──▶ signed token {typ:board, trip, n, exp=+30s}
render QR, refetch every 30s                                     scan QR
                                POST /boarding/check-in {token} ◀── token
                                verify → trip running? → allocated? → not boarded twice?
push "boarding" over WS ◀────── StudentBoarded                   ──▶ receipt (ticket stub)
```

## Anti-cheat
- The token **expires after `QR_TTL_SECONDS` (30s)**. A photo forwarded to a friend who isn't
  on the bus stops working almost immediately. The driver screen refreshes the code before expiry.
- The token is bound to one trip, and check-in is refused unless that trip is `in_progress`.
  A code captured during the trip is useless after it ends.
- Tokens are HMAC-signed with the server secret and carry `typ=board`, so login tokens and forged
  tokens are rejected (`qr_invalid`).
- One boarding per student per trip (unique constraint → `already_boarded`).
- A student boarding a bus that isn't their allocated route **is recorded, not refused**
  (turning a student away is a safety problem). It's flagged as `UnallocatedBoarding` to the
  driver and admin.
- QR errors return **422, not 401**, so the student's app doesn't think the *login* expired.
- P1 hook: compare the student's device location with the bus position once GPS exists.

## Data model
| Table | Key columns |
|---|---|
| `boardings` | `trip_id`, `student_id`, `stop_id` (their allocated stop if on-route), `method` (qr/manual), `allocation_match`, `boarded_at`, `recorded_by`; unique (trip, student) |
| `attendance_records` | `trip_id`, `student_id`, `route_id`, `service_date`, `status` (present / absent / present_unallocated), `boarding_id`; unique (trip, student) |

## API
| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/boarding/trips/{id}/qr` | driver (own), admin | current QR token `{token, expires_at, ttl_seconds, route_code, route_color, bus_registration_no}` |
| POST | `/boarding/check-in` `{token}` | student | board → receipt |
| POST | `/boarding/trips/{id}/manual` `{student_id \| roll_no}` | driver (own), admin | board a student without a phone |
| GET | `/boarding/trips/{id}/roster` | driver (own), admin | allocated riders + boarded status, in stop order |
| GET | `/boarding/me/attendance?limit` | student | my attendance history |

Error codes: `qr_expired`, `qr_invalid`, `already_boarded`, `bad_trip_state`.

## Events
| Emits | When |
|---|---|
| `StudentBoarded` | every boarding (`boarded_count`, `allocation_match`, `method`) |
| `UnallocatedBoarding` | rider not allocated to this route |
| `AttendanceFinalized` | after `TripEnded`: present / absent / present_unallocated counts |

| Consumes | Why |
|---|---|
| `TripEnded` | write attendance (idempotent) |

WebSocket: on `StudentBoarded`, pushes `{"type":"boarding"}` with the student's name to the driver,
and only `{trip_id, boarded_count}` to topic `trip:{id}` (riders of the trip follow it and never see
who boarded).

## Attendance safety net
Attendance is written on `TripEnded`, and on `TripCancelled` when the bus had set off (a breakdown):
boarded riders present, the others absent. The `attendance-reconciler` job (every 5 min) writes it
for any recent finished trip whose handler never ran (server restart, failure). It is idempotent:
a trip with rows or an `AttendanceFinalized` event is skipped.

## Public service API
`boarded_count(trip_id)`, `boarded_student_ids(trip_id)`, `finalize_attendance(trip_id)`,
`student_boardings_on(student_id, trip_ids)`, `student_attendance(student_id)`.

## Frontend screens
| Screen | Role | File |
|---|---|---|
| Boarding QR (auto-rotating, wakelock, live count) | driver | `frontend/lib/modules/boarding/screens/driver_qr_screen.dart` |
| Roster / manual board | driver | `frontend/lib/modules/boarding/screens/driver_roster_screen.dart` |
| Scan to board + ticket stub | student | `frontend/lib/modules/boarding/screens/student_scan_screen.dart` |
| My attendance | student | `frontend/lib/modules/boarding/screens/student_attendance_screen.dart` |

## How to test
```bash
cd backend && .venv/Scripts/python -m pytest app/modules/boarding -q
```
Covers: happy path, 31-second-old token rejected, forged / wrong-type token, QR only while running,
stale QR after trip end, other driver forbidden, unallocated flag, manual board, roster order,
attendance finalisation.
