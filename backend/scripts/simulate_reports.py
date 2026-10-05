"""Check the report agent end to end: build a trip with known facts, file reports, compare verdicts.

Needs a RUNNING API and seeded data (`python -m scripts.seed`). Uses whatever model the API is
configured with (Ollama by default; the rule-based fallback if Ollama isn't running).

    python -m scripts.simulate_reports                 # driver1, route 14
    python -m scripts.simulate_reports --driver driver2@college.edu

What it sets up, as a one-off trip departing now:
  - stop 2 reached 12 min late (admin timestamp),
  - stop 3 skipped (no arrival recorded),
  - GPS readings at 85 km/h,
  - a "Blue steel water bottle" logged as found by the driver.
Then students on that route file reports and the script checks each verdict the agent gives.
Exits non-zero if any verdict is wrong. Refuses to run if the driver already has a trip running.
"""

import argparse
import sys
import time
from datetime import datetime, timedelta

from scripts.simulate_bus import TZ, Api

# (who, kind, text template, check, expected verdict, extra expectation)
CASES = [
    ("late", "lateness", "The bus was 12 minutes late at {stop2} this morning.", "lateness", "confirmed", None),
    ("late", "lateness", "Bus came 45 minutes late, I missed my first class!", "lateness", "partly", None),
    ("skipped", "lateness", "The bus didn't stop at {stop3}, it just drove past.", "stop_reached", "confirmed", None),
    ("crowd", "overcrowding", "So crowded, no seats at all, people standing at the door.", "crowding", "not_supported", None),
    ("safety", "safety", "The driver was going really fast and braking hard, it felt unsafe.", "speed", "confirmed",
     ("severity", "critical")),
    ("lost", "lost_item", "I left my blue water bottle near the back seat.", "lost_item", "partly", ("candidates", 1)),
]


def one_off_trip(api: Api, admin: dict, driver: dict, driver_email: str) -> dict:
    if any(t["status"] == "in_progress" for t in api.call("GET", "/trips/mine", driver)):
        sys.exit(f"{driver_email} has a trip running. End it first (this script won't touch it).")
    me = api.call("GET", "/users", admin, params={"role": "driver", "q": driver_email})[0]
    base = api.call("GET", "/schedules", admin, params={"driver_id": me["id"]})[0]
    now = datetime.now(TZ)
    sched = api.call("POST", "/schedules", admin, json={
        "route_id": base["route_id"], "bus_id": base["bus_id"], "driver_id": me["id"], "direction": "pickup",
        "departure_time": now.strftime("%H:%M:00"), "days_of_week": [now.isoweekday()]})
    api.call("POST", "/trips/generate", admin, json={})
    api.call("PATCH", f"/schedules/{sched['id']}", admin, json={"is_active": False})
    trip = next(t for t in api.call("GET", "/trips", admin) if t["schedule_id"] == sched["id"])
    return api.call("POST", f"/trips/{trip['id']}/start", driver)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--driver", default="driver1@college.edu")
    ap.add_argument("--timeout", type=float, default=240, help="seconds to wait for each analysis")
    args = ap.parse_args()

    api = Api(args.api)
    admin, driver = api.login("admin@college.edu"), api.login(args.driver)
    trip = one_off_trip(api, admin, driver, args.driver)
    tid, stops = trip["id"], trip["stops"]
    if len(stops) < 4:
        sys.exit("The route needs at least 4 stops.")
    print(f"Trip #{tid} on route {trip['route']['code']} started.")

    # Riders: who is allocated where on this route.
    roster = api.call("GET", f"/boarding/trips/{tid}/roster", admin)["entries"]
    emails = {u["id"]: u["email"] for u in api.call("GET", "/users", admin, params={"role": "student"})}
    at_stop = {}
    for e in roster:
        at_stop.setdefault(e["stop_id"], []).append(e)
    stop2, stop3 = stops[1], stops[2]
    riders_2 = at_stop.get(stop2["stop_id"], [])
    riders_3 = at_stop.get(stop3["stop_id"], [])
    others = [e for e in roster if e["stop_id"] not in (stop2["stop_id"], stop3["stop_id"])]
    if not riders_2 or not riders_3 or len(riders_2) + len(others) < 3:
        sys.exit("Not enough students allocated on this route; re-seed (python -m scripts.seed --reset).")
    pool = riders_2 + others
    who = {"late": riders_2[0], "skipped": riders_3[0], "crowd": pool[1 % len(pool)],
           "safety": pool[2 % len(pool)], "lost": pool[-1]}

    # Everyone except the "skipped" rider boards (they were left at the stop).
    for e in {id(x): x for x in who.values()}.values():
        if e is who["skipped"]:
            continue
        qr = api.call("GET", f"/boarding/trips/{tid}/qr", driver)
        api.call("POST", "/boarding/check-in", api.login(emails[e["student_id"]]), json={"token": qr["token"]})

    late_at = datetime.fromisoformat(stop2["scheduled_at"]) + timedelta(minutes=12)
    api.call("POST", f"/trips/{tid}/stops/2/arrive", admin, json={"arrived_at": late_at.isoformat()})
    # Fast GPS readings, placed well away from any stop so they don't trigger arrivals or alerts.
    fixes = [{"latitude": 13.40 + i * 0.002, "longitude": 80.60, "speed_kmph": s} for i, s in enumerate([62, 85, 88, 70])]
    api.call("POST", f"/trips/{tid}/positions", driver, json={"positions": fixes})
    api.call("POST", "/found-items", driver, json={"trip_id": tid, "description": "Blue steel water bottle"})
    api.call("POST", f"/trips/{tid}/end", driver)
    print(f"Set up: {stop2['stop_name']} 12 min late, {stop3['stop_name']} skipped, GPS up to 88 km/h, "
          f"water bottle found. Trip ended.\n")

    failures = 0
    for key, kind, template, check, expected, extra in CASES:
        student = api.login(emails[who[key]["student_id"]])
        text = template.format(stop2=stop2["stop_name"], stop3=stop3["stop_name"])
        created = api.call("POST", "/reports", student, json={"kind": kind, "description": text, "trip_id": tid})
        started = time.time()
        while True:
            r = api.call("GET", f"/reports/{created['id']}", admin)
            if r["analysis_status"] != "pending" or time.time() - started > args.timeout:
                break
            time.sleep(1)
        took = time.time() - started
        print(f"[{kind}] \"{text}\"")
        if r["analysis_status"] != "done":
            print(f"  ✗ analysis {r['analysis_status']} after {took:.0f}s\n")
            failures += 1
            continue
        a = r["analysis"]
        print(f"  by {r['analysed_by']} in {took:.1f}s, severity {r['severity']}, subtype {a['claims']['subtype']}")
        for f in a["findings"]:
            print(f"    [{f['verdict']}] {f['check']}: {f['detail']}")
        print(f"  summary: {a['summary']}")
        print(f"  draft:   {a['draft_reply']}")
        got = next((f["verdict"] for f in a["findings"] if f["check"] == check), "missing")
        problems = [] if got == expected else [f"{check} was {got}, expected {expected}"]
        if extra and extra[0] == "severity" and r["severity"] != extra[1]:
            problems.append(f"severity was {r['severity']}, expected {extra[1]}")
        if extra and extra[0] == "candidates" and len(a["match_candidates"]) < extra[1]:
            problems.append("no found-item match suggested")
        print(f"  {'✓ as expected' if not problems else '✗ ' + '; '.join(problems)}\n")
        failures += bool(problems)

    if failures:
        sys.exit(f"✗ {failures} of {len(CASES)} reports didn't get the expected verdict.")
    print(f"✓ All {len(CASES)} reports got the expected verdict.")


if __name__ == "__main__":
    main()
