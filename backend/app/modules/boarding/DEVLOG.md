# boarding: dev log

## 2026-10-04: Review fixes (Review.md)
**Built**
- Attendance for running trips that get cancelled (M5). Measured before: 0 rows.
- `reconcile_attendance` + `attendance-reconciler` job (H2): events are in-memory, so a restart
  between a trip ending and its handler used to lose that trip's attendance for good.
- The trip topic gets counts only; names go to the driver alone (B3). `boarded_counts()` batch helper (H3).

## 2026-09-24: Flutter screens
**Built**
- Driver QR screen: large high-contrast QR, auto-refresh 4s before expiry, countdown bar,
  wakelock, live "N on board" plus a green bar naming the last student who boarded (WebSocket).
- Student scan screen (mobile_scanner) with plain-language errors per code (`qr_expired`,
  `already_boarded`...), and the ticket-stub receipt with the stamp animation.
- Driver roster with manual Board, and the student "My trips" attendance list.

**Decisions (and why)**
- **Refresh the code before expiry** so a student mid-scan never gets a dead code.
- **Paste-code fallback** (web / debug) makes the whole boarding flow testable on a laptop without a camera.
- Wakelock failures are swallowed: unsupported platforms still work, just without keeping the screen awake.

## 2026-09-24: QR direction flipped: driver shows, student scans
**Built**
- `GET /boarding/trips/{id}/qr` (driver) + `POST /boarding/check-in` (student) replace the original
  plan of the student showing a personal QR.

**Decisions (and why)**
- Requested by the product owner: **the driver has the QR and students scan it.** One screen per bus
  instead of 40 students fumbling for a pass, and the driver's hands stay free.
- The weak point of "student scans" is *remote* check-in (a friend forwards a photo of the QR).
  Mitigated by a **30-second token lifetime**, binding to a running trip, and flagging off-route
  riders. P1 GPS adds location matching.

## 2026-09-24: P0 boarding module
**Built**
- Boardings, attendance records, roster, manual override, attendance finalisation on `TripEnded`,
  live WS push to the driver.

**Decisions (and why)**
- **QR token = our own short-lived JWT (`typ=board`)**: stateless, no QR table to clean up, and
  signature + expiry checks are the same code path as login tokens.
- **Map token errors to 422** (`qr_expired` / `qr_invalid`). The Flutter API client treats 401 as
  "refresh the login", which would be the wrong reaction to a stale QR.
- **Record unallocated riders instead of refusing them.** Leaving a student at the roadside is worse
  than a data-quality alert.
- **Attendance is written once at trip end** (idempotent: skipped if rows exist), not
  incrementally. "Absent" is only knowable when the trip is over.
- Duplicate check-in is caught by the unique constraint inside a savepoint (race-safe) rather than
  a read-then-insert check.

**Issues / next**
- Alighting isn't tracked (P0 scope is boarding). Add `alighted_at` if drop-trip occupancy
  per stop is needed.
