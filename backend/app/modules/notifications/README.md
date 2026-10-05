# notifications: in-app inbox and live push

> Decides **who** hears about **what**, stores it in their inbox and pushes it over WebSocket.

| | |
|---|---|
| **Owner** | Team A, member 5 |
| **Backend** | `backend/app/modules/notifications/` |
| **Frontend** | `frontend/lib/modules/notifications/` |
| **Status** | P0 done (in-app only; push/email/SMS are P1) |

## Who gets what
| Event | Students | Driver | Admins |
|---|---|---|---|
| `TripDelayed` | pickup: allocated to a **not-yet-reached** stop and **not boarded**; drop: on board (or all riders if not started). Each gets *their* stop's new expected time | ✓ (unless they reported it) | ✓ with affected count |
| `TripDelayResolved` | same targeting | | ✓ |
| `TripStarted` | all allocated on the route, with their stop's scheduled time | | |
| `BusApproaching` (tracking, once per trip and stop, bus within 2 km) | pickup: allocated to that stop and **not boarded**; drop: **on board** and getting off there | | |
| `TripCancelled` | all allocated | | ✓ |
| `CapacityWarning` / `OverCapacity` | | ✓ | ✓ |
| `UnallocatedBoarding` | | ✓ | ✓ |
| `StudentAllocated` / `AllocationChanged` / `AllocationEnded` | that student | | |
| `BusDriverAssigned` | | new driver ("You're now driving bus …") and previous driver | |
| `ReportAnalysed` (reports, first analysis only) | | | ✓ severity from the report: critical / high → warning / info |
| `ReportFollowUp` | | | ✓ |
| `ReportReplied` / `ReportClosed` / `LostItemMatched` | the student who sent the report (looked up via `reports.service.recipient_id`; payloads carry no student id) | | |

Severity: `info` / `warning` / `critical` (delays ≥ 15 min and over-capacity are critical).

## Data model
| Table | Key columns |
|---|---|
| `notifications` | `user_id`, `type`, `title`, `body`, `severity`, `payload` jsonb (`trip_id`, `route_code`, `route_color`, `delay_min`, `stop`…), `read_at`, `created_at` |

## API
| Method | Path | Role |
|---|---|---|
| GET | `/notifications?unread_only&limit&before_id` | any (own only) |
| GET | `/notifications/unread-count` | any |
| POST | `/notifications/{id}/read` | any (404 if not yours) |
| POST | `/notifications/read-all` | any |

WebSocket: each stored notification is pushed as `{"type":"notification","data":{…}}` to the user.

Housekeeping: read notifications older than `NOTIFICATION_RETENTION_DAYS` (180) are deleted
(job `notification-retention`, every 6 h). Unread ones are kept.

## Consumes
All events in `service.HANDLED_EVENTS`.

## Public service API
`messages_for(event)` (recipients + wording, writes nothing), `deliver(messages)`,
`delay_student_targets(payload)`, `create`, `list_for_user`, `unread_count`, `mark_read`.

## Frontend screens
| Screen | Role | File |
|---|---|---|
| Alerts inbox | all | `frontend/lib/modules/notifications/screens/inbox_screen.dart` |
| Live toast / badge | all | `frontend/lib/modules/notifications/state/notifications_controller.dart` |

## How to test
```bash
cd backend && .venv/Scripts/python -m pytest app/modules/notifications app/modules/delay_monitor -q
```

## Extension notes (Team B: "intelligent notifications")
- Add channels by extending `deliver()` (FCM push, email) while keeping `messages_for` as the
  single source of recipients and wording.
- Quiet hours / preferences: filter in `deliver()` per user.
