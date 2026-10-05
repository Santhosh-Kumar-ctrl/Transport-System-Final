# dashboard: dev log

## 2026-10-04: Review fixes (Review.md)
**Changed**
- Route-topic `ops` messages carry no student identities (B3). Measured before: a student following
  a route received the name of every rider who boarded.
- Admin and driver dashboards use batched occupancy queries (H3); `ScheduleSkipped` is added to the
  live events and the admin alerts list.
- The admin board app ignores `position` messages and debounces refetches (2 s).

## 2026-09-24: Flutter screens
**Built**
- Admin live departure board (dark board, LED clock, counts, departure rows, trip detail dialog with
  line + timeline + cancel), alerts column, seats-per-route. Student "My line" home: next-bus panel
  with the expected time at *my* stop, Scan to board, live line diagram, other buses today.

**Decisions (and why)**
- The student's big number is **their stop's expected time**, not the bus's departure time,
  because that's the question a student actually has.
- Screens re-fetch on WebSocket `ops` hints (`listenLive`) instead of patching state.
- Visual review via rendered screenshots led to fixes: badge rim on dark panels, board filling
  the height, truncated driver header and buttons.

## 2026-09-24: P0 dashboard module
**Built**
- Admin departure board, driver and student aggregates, live `ops` forwarding over WebSocket.

**Decisions (and why)**
- **One endpoint per home screen.** Mobile clients on college Wi-Fi shouldn't make 6 round trips
  to paint a home screen.
- **Push "something changed" hints, not full state.** The client re-fetches the aggregate, so there's
  one code path for state and no risk of a half-applied delta.
- **Board sorting: running → upcoming → finished**, then by departure. That matches how a
  control room reads a departure board.

**Issues / next**
- `admin()` computes occupancy per trip (N small queries). Fine for tens of trips a day; batch if
  the fleet grows.
