# capacity: dev log

## 2026-10-04: Review fixes (Review.md)
**Changed**
- `trip_occupancies(trips)` and batched seat capacities: dashboards no longer run queries per trip (H3).
- `check_trip_capacity` locks the trip row first, so simultaneous boardings raise an alert once (L3).
- Trip occupancy reads are limited to who may see the trip (L7).

## 2026-09-24: Flutter screens
**Built**
- `SeatBlocks` (in the design system) redrawn as a top-down seat plan (2 + aisle + 2) after reviewing
  the first render: the flat grid wrapped into ragged rows on the board.
  `frontend/lib/modules/capacity/widgets/utilization_list.dart` shows seats per route on the admin board.

**Decisions (and why)**
- Seat plan instead of a progress bar: it's specific to buses and shows "3 seats left" at a glance.

## 2026-09-24: P0 capacity module
**Built**
- Occupancy levels, trip occupancy, route utilisation, once-per-trip capacity alerts.

**Decisions (and why)**
- **Read-model module with no tables.** Occupancy is derivable from boardings + bus capacity, and
  storing it would create a second source of truth.
- **De-duplicate alerts by looking in `domain_events`** instead of keeping an "alert sent" flag.
  The event log already is the record of what was raised.
- **Alert on crossing, not on every boarding.** Drivers mute apps that nag.

**Issues / next**
- Two boardings processed at the same instant could both see "no warning yet" and raise twice.
  That's rare and harmless (one duplicate notification). Fix with an advisory lock if it shows up.
