# delay_monitor: schedule-based delay detection

> Notices when a bus is late and announces it (once), so notifications can tell the right people.

| | |
|---|---|
| **Owner** | Team A, member 5 |
| **Backend** | `backend/app/modules/delay_monitor/` |
| **Frontend** | `frontend/lib/modules/delay_monitor/` (report-delay sheet) |
| **Status** | P0 done |

## Detection sources (P0: no GPS)
| Source | Trigger |
|---|---|
| `start` | `TripStarted`: departure delay |
| `stop_arrival` | `StopArrived`: late at a stop |
| `overdue` | watcher: next stop's scheduled time + threshold passed with no check-in |
| `not_started` | watcher: departure + threshold passed, trip still `scheduled` |
| `manual` | driver/admin `POST /trips/{id}/delay {delay_min, reason}` |
| `recovered` | a stop check-in shows the bus back under threshold |

## Alerting rules
- Alerts go out in **steps** (`DELAY_ALERT_STEPS`, default 5, 15, 30, 60 min late): `TripDelayed`
  is raised when the delay reaches a step not yet announced since the trip was last on time. After
  the last step nothing more is raised, however long the trip stays late: a trip that never starts
  gives at most 4 alerts, not one every 5 minutes all day.
- Steps above `DELAY_STUDENT_ALERT_MAX_MIN` (30) go to the transport office only
  (`notify_students: false`), except the first alert of a delay, which riders always get.
- Raise `TripDelayResolved` when a stop check-in comes in under the threshold after a delay.
- Manual reports always raise (a human decided it matters).
- The payload includes `affected_stops`: every stop not yet reached, with `scheduled_at` and
  `expected_at = scheduled_at + delay`. Notifications uses it to pick students and word messages.

## Data model
| Table | Key columns |
|---|---|
| `delay_reports` | `trip_id`, `source`, `delay_min`, `at_sequence`, `reason`, `reported_by`, `created_at` |

The latest row per trip is that trip's alert state.

## API
| Method | Path | Role |
|---|---|---|
| POST | `/trips/{id}/delay` `{delay_min, reason}` | driver (own), admin |
| GET | `/trips/{id}/delays` | admin; the trip's driver; students on its route |
| GET | `/delays?service_date=` | admin |
| POST | `/delays/watch` | admin: run one watcher pass now |

## Events
| Emits | Payload highlights |
|---|---|
| `TripDelayed` | `trip_id, route_id, driver_id, direction, delay_min, previous_delay_min, source, reason, at_sequence, at_stop_name, alert_step, notify_students, affected_stops[]` |
| `TripDelayResolved` | `delay_min, previous_delay_min, remaining_stops[]` |

| Consumes | Why |
|---|---|
| `TripStarted`, `StopArrived` | evaluate |

## Background job
`delay-watcher` every `DELAY_WATCH_INTERVAL_SECONDS` (60s).

## Public service API
`evaluate(trip, observed_delay, source, …)`, `current_delays(trip_ids)`, `latest_report(trip_id)`,
`affected_stops(trip, delay)`, `watch_once()`.

## Frontend
| Screen | Role | File |
|---|---|---|
| Report delay sheet | driver | `frontend/lib/modules/delay_monitor/widgets/report_delay_sheet.dart` |

## How to test
```bash
cd backend && .venv/Scripts/python -m pytest app/modules/delay_monitor -q
```

## Extension notes (Team B: this is your main entry point)
- **ETA prediction / GPS:** compute a predicted delay and call `service.evaluate(trip, predicted,
  DelaySource.GPS)` after adding `GPS` / `ETA_MODEL` to `DelaySource`. Everything downstream
  (notifications, dashboard, history) works unchanged.
- **Route deviation:** publish a new `RouteDeviation` event in the same style.
- **Agent:** subscribe to `TripDelayed`. It already carries the affected stops, and
  `notifications.service.delay_student_targets()` gives the affected students.
