# tracking: dev log

Newest entry at the top. Write one entry every time you finish a piece of work:
what you built, *why* you chose that way, and what's still open.

## 2026-10-04: Review fixes (Review.md)
**Changed**
- `recorded_at` must include a timezone (`…Z`); a naive time was a 500 (M1). The app already sends UTC.
- Ingest uses the shared `trips.get_trip_for_update` lock, which manual taps now take too (B5).
- `GET /trips/{id}/live` is limited to who may see the trip (L7). Route-topic subscriptions are
  access-controlled (B3).
- Retention job for `bus_positions` (L4): a bus sends ~720 fixes an hour.

## 2026-09-29: Live tracking from the driver's phone
**Built**
- New module that owns `bus_positions` (moved from trips, same table, plus `heading_deg`,
  `accuracy_m` and a `(trip_id, recorded_at)` index) and the new `approach_alerts`.
- `POST /trips/{id}/positions` batch ingest: stores fixes, checks the bus in at stops within
  100 m through `trips.service.arrive_at_stop(observed_at=…)`, and publishes `BusApproaching`
  once per trip and stop inside 2 km. `GET /trips/{id}/live`, `GET /tracking/live`, and a
  `position` WebSocket push on `route:{id}` plus admins.
- notifications: `BusApproaching` goes to waiting pickup riders of that stop and to drop riders on board.
- Flutter: the driver's phone reports (geolocator; foreground service on Android, buffered
  offline), with a sharing strip and a map on the run screen. The student home gets a live map
  with "You" and distance. There's a new admin/security **Live map** screen, and the new-stop
  form takes coordinates.
- `scripts/simulate_bus.py` drives a bus along its route. Seed data now sits around St. Joseph's
  College of Engineering (OMR, Chennai) with stop coordinates.

**Decisions (and why)**
- **Driver's phone, not hardware, for the first release.** It needs no purchase or second server,
  and it fits the existing Start trip flow. Everything enters through `ingest()`, so Traccar can
  be added later as another source.
- **Auto-arrival reuses `arrive_at_stop`.** Delay detection, notifications, dashboard and
  history keep working unchanged, and the ARRIVED button stays as the fallback.
- **Look-ahead of 2 stops** for auto-arrival (see README). Approach alerts have no look-ahead:
  2 km can span several stops.
- **Straight-line distance.** No PostGIS or OSRM to run; being slightly early on winding roads is
  acceptable for a heads-up.
- **In-app alerts first (inbox + WebSocket toast).** FCM needs a Firebase project and can be added
  in `notifications.deliver()` without touching tracking.
- **Row lock on the trip per ingest**, so two concurrent batches can't double check-in.
- **Only the newest fix of a batch is pushed** to keep the WebSocket light after an offline burst.

**Issues / next**
- Alerts only reach students with the app open. FCM is next.
- Drop alerts need riders to have scanned the QR; otherwise nobody is "on board".
- Phone tracking stops if the driver force-closes the app. The run screen shows sharing status.
- The map follows the bus but doesn't draw road geometry: the line joins stops straight.
- There's no screen yet to edit coordinates of existing stops (only when creating one); use `PATCH /stops/{id}`.
