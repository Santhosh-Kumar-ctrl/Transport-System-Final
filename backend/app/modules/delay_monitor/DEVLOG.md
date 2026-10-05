# delay_monitor: dev log

## 2026-10-04: Review fixes (Review.md)
**Changed**
- **Stepped alerts replace "every threshold"** (B2). Measured before: a trip that never started sent
  every rider and admin an alert every 5 minutes (24 each in 2 h, ~150 a day). Now 5 → 15 → 30 for
  riders and the driver, one office-only alert at 60, then silence. Manual reports always go out.

**Decisions (and why)**
- The first alert of a delay always reaches riders, even if it is already past 30 min (a bus first
  seen 70 min late): they must hear it once.
- `previous_delay_min` is now the largest delay announced since recovery, not the last row's.

## 2026-09-24: Flutter screens
**Built**
- "Running late" bottom sheet for drivers: pick minutes and a reason (or type one), then *Tell riders*.

**Decisions (and why)**
- Two taps, big targets. Drivers report delays while stopped, and a text form would go unused.

## 2026-09-24: P0 delay monitor
**Built**
- Evaluation rules, event handlers for start/arrival, background watcher (overdue + not-started),
  manual reports, recovery detection.

**Decisions (and why)**
- **Schedule + check-ins rather than GPS in P0** (product decision). The planned stop times from
  trips are the baseline, and the driver's "ARRIVED" tap is the observation.
- **The watcher catches the silent case.** If the driver never taps "arrived", check-ins alone
  would never detect the delay. The watcher treats "scheduled time + threshold passed" as late.
- **Escalate only by whole thresholds** (5 → 10 → 15 min). Every extra minute re-notifying 40
  students would train them to ignore alerts.
- **Recovery only from real check-ins**, never from the watcher, which can only see things getting
  worse.
- **`affected_stops` computed here, recipients resolved in notifications.** Delay logic is about the
  bus, recipient logic is about people, and Team B's agent needs both separately.
- The DelayReport row is kept even when a later alert supersedes it: the timeline shows how the
  delay developed.

**Issues / next**
- Delay projects the same minutes to every remaining stop (no catch-up model). ETA prediction (P1)
  replaces this.
