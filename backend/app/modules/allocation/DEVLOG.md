# allocation: dev log

## 2026-10-04: Review fixes (Review.md)
**Built**
- Consumes `UserDeactivated`: a deactivated student's allocation ends (`end_reason=deactivated`), so
  their seat is free again (M6). Measured before: the route stayed "full".
- `assign` locks the route row before counting seats, so two admins can't both take the last one (L3).
- Owns the `route:{id}` WebSocket topic policy (B3).

## 2026-09-24: Flutter screens
**Built**
- Allocation screen: pick a route, see its stops as a line diagram with the students at each stop,
  assign via a search dialog, remove per stop.

**Decisions (and why)**
- **409 `route_full` becomes an explicit "Allocate anyway?" step** (force=true), keeping the
  override a conscious choice in the UI too.
- Pickup stops exclude the campus terminus: nobody boards the morning bus at the college.

## 2026-09-24: P0 allocation module
**Built**
- Assign / reassign / unassign / bulk, `/allocations/me`, seat-capacity guard with admin override.

**Decisions (and why)**
- **End-and-insert instead of update on reassignment**, so we keep a full history of who rode what
  and when (attendance reports need it), and the partial unique index guarantees one active row.
- **Flush the "ended" row before inserting the new one.** Otherwise the unit of work may INSERT
  first and hit the one-active-per-student index.
- **`route_stop_id` only while active.** It gives DB-level protection against deleting a stop in use,
  without history rows blocking route edits forever.
- **Capacity guard is a 409 with an explicit `force` override.** Admins sometimes knowingly
  over-allocate (not everyone rides every day); the system makes that a conscious choice.
  Enforcing at *boarding* time instead would strand students at the stop.
- **Bulk assign uses a savepoint per row**, so one full route doesn't roll back the whole import.

**Issues / next**
- Allocation has no end date in the UI yet (`valid_from` exists; `valid_to` can be added for
  semester-based allocations).
- Events published inside a savepoint that later rolls back would still dispatch. Today every
  failure path raises *before* publishing, so this can't happen, but keep it that way.
