# notifications: dev log

## 2026-10-04: Review fixes (Review.md)
**Changed**
- `TripDelayed` with `notify_students: false` (the office-only last alert step) goes to admins only,
  saying no further alerts will follow (B2).
- New: `ScheduleSkipped` → admins ("won't run today: bus is in maintenance").
- Retention job for read notifications (L4). `limit` must be ≥ 1 (M1).

## 2026-09-29: "Bus is near" alerts
**Built**
- `BusApproaching` → "Route 14 bus is 1.9 km from your stop" to waiting pickup riders, and
  "<stop> is coming up" to drop riders on board. `approach_student_targets()` is public for the agent.

**Decisions (and why)**
- Same targeting as delay alerts (boarded or not, direction-aware), so a student already on the
  bus isn't told to walk to their stop.
- In-app only for now. FCM goes in `deliver()` later.

## 2026-09-24: Flutter screens
**Built**
- Alerts inbox (severity edge, route badge, mark read / all), unread badge in bottom bars and
  admin rail, live toast in house style via `LiveNotificationListener` around every role shell.

## 2026-09-24: P0 notifications module
**Built**
- Inbox table, WebSocket push, recipient resolution for delays, recoveries, trip start/cancel,
  capacity, unallocated riders and allocation changes.

**Decisions (and why)**
- **Personalised delay messages.** Each student gets *their* stop and its new expected time
  ("Expected at Koyambedu Market around 11:32 (scheduled 11:20)"). "Bus is late" alone isn't actionable.
- **Only students who are still waiting.** Pickup delays skip students already on board and stops
  the bus has passed. Over-notifying is the fastest way to get alerts ignored.
- **`messages_for` is side-effect free.** It returns messages and `deliver` persists and pushes them,
  so recipient logic is unit-testable and reusable by Team B's agent.
- **Commit before push.** A client that receives a push and immediately fetches the inbox must find
  the row.
- **Event payloads are normalised to JSON in `core.events.publish`.** Before that fix, `affected_stops`
  carried Python datetimes into the JSONB column and inserts failed.

**Issues / next**
- One notification row per user per event. At college scale that's fine; batch inserts if a route
  ever has thousands of riders.
