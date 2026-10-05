from datetime import datetime

from app.core.roles import Role
from app.modules.tracking.geo import distance_m

# Stops 3 km apart heading north along one meridian: 0.009° of latitude ≈ 1 km.
BASE_LAT, LNG = 12.80, 80.22
KM = 0.009


def _stop_lat(k: int) -> float:
    return BASE_LAT + 3 * KM * k


async def _geo_run(world, *, n_stops: int = 4, direction: str = "pickup") -> dict:
    """A running trip whose route stops have coordinates (stop k at 3k km north of the first)."""
    route = (await world.post("/routes", {"code": "G1", "name": "Geo line", "color": "#1F6FEB"}, expect=201)).json()
    stops = []
    for k in range(n_stops):
        stops.append((await world.post("/stops", {"name": "Campus" if k == n_stops - 1 else f"Geo {k + 1}",
                                                  "latitude": _stop_lat(k), "longitude": LNG}, expect=201)).json())
    route = (await world.put(f"/routes/{route['id']}/stops",
                             [{"stop_id": s["id"], "offset_min": k * 10} for k, s in enumerate(stops)])).json()
    bus = await world.bus()
    driver = await world.user(Role.DRIVER, "Geo Driver")
    sched = await world.schedule(route, bus, driver, direction=direction)
    trip = await world.todays_trip(sched)
    trip = (await world.post(f"/trips/{trip['id']}/start", who=driver)).json()
    return {"route": route, "driver": driver, "trip": trip}


async def _report(world, w, *points, who=None, expect=200, **extra):
    body = {"positions": [{"latitude": lat, "longitude": LNG, **extra} for lat in points]}
    return await world.post(f"/trips/{w['trip']['id']}/positions", body, who=who or w["driver"], expect=expect)


def _types(inbox):
    return [n["type"] for n in inbox]


def test_haversine_matches_known_distance():
    # One degree of latitude is ~111.2 km everywhere.
    assert abs(distance_m(12.0, 80.0, 13.0, 80.0) - 111_195) < 50
    assert distance_m(12.9, 80.2, 12.9, 80.2) == 0


async def test_fix_near_next_stop_marks_it_arrived(world):
    w = await _geo_run(world)
    tid = w["trip"]["id"]
    out = (await _report(world, w, _stop_lat(1) - 0.5 * KM)).json()  # 500 m short of stop 2
    assert out["arrived"] == []
    assert (await world.get(f"/trips/{tid}")).json()["stops"][1]["arrived_at"] is None

    out = (await _report(world, w, _stop_lat(1) - 0.05 * KM)).json()  # 50 m short
    assert out["arrived"] == [2]
    trip = (await world.get(f"/trips/{tid}")).json()
    assert trip["stops"][1]["arrived_at"] is not None
    assert trip["next_stop"]["sequence"] == 3

    arrived = (await world.get("/history/events", type="StopArrived")).json()
    assert arrived[0]["payload"]["source"] == "gps"

    # Standing at the stop for a while doesn't check in again.
    assert (await _report(world, w, _stop_lat(1))).json()["arrived"] == []


async def test_inaccurate_fix_is_stored_but_triggers_nothing(world):
    w = await _geo_run(world)
    out = (await _report(world, w, _stop_lat(1), accuracy_m=400)).json()
    assert out["accepted"] == 1
    assert out["arrived"] == [] and out["approaching"] == []
    live = (await world.get(f"/trips/{w['trip']['id']}/live")).json()
    assert live["position"]["accuracy_m"] == 400


async def test_far_later_stop_is_not_checked_in(world):
    """Only the next couple of stops can be reached by GPS, so a stray fix can't skip half the route."""
    w = await _geo_run(world, n_stops=5)
    out = (await _report(world, w, _stop_lat(3))).json()  # at stop 4 while stop 2 is next
    assert out["arrived"] == []


async def test_missed_stop_is_skipped_when_bus_reaches_the_one_after(world):
    w = await _geo_run(world, n_stops=5)
    out = (await _report(world, w, _stop_lat(2))).json()  # stop 2 had no fix nearby
    assert out["arrived"] == [3]
    trip = (await world.get(f"/trips/{w['trip']['id']}")).json()
    assert trip["stops"][1]["arrived_at"] is None
    assert trip["next_stop"]["sequence"] == 4


async def test_pickup_riders_hear_once_when_bus_is_2km_away(world):
    w = await _geo_run(world, n_stops=4)
    route, tid = w["route"], w["trip"]["id"]
    waiting3 = await world.user(Role.STUDENT, "Waiting at 3")
    boarded3 = await world.user(Role.STUDENT, "Boarded early")
    waiting4 = await world.user(Role.STUDENT, "Waiting at 4")
    await world.allocate(waiting3, route, 2)
    await world.allocate(boarded3, route, 2)
    await world.allocate(waiting4, route, 3)
    await world.board(boarded3, tid, w["driver"])

    # 1.5 km before stop 3 (and 4.5 km before stop 4). Stop 2 is 1.5 km behind: also within 2 km.
    out = (await _report(world, w, _stop_lat(2) - 1.5 * KM)).json()
    assert out["approaching"] == [2, 3]

    inbox = await world.inbox(waiting3)
    near = [n for n in inbox if n["type"] == "BusApproaching"]
    assert len(near) == 1
    assert "1.5 km" in near[0]["title"]
    assert "Geo 3" in near[0]["body"]
    assert "BusApproaching" not in _types(await world.inbox(boarded3))
    assert "BusApproaching" not in _types(await world.inbox(waiting4))

    # Closer still: no repeat for stop 3.
    out = (await _report(world, w, _stop_lat(2) - 0.8 * KM)).json()
    assert out["approaching"] == []
    assert _types(await world.inbox(waiting3)).count("BusApproaching") == 1


async def test_drop_riders_on_board_hear_their_stop_is_coming(world):
    # Drop runs campus-first: sequence 1 is the campus (northmost), then Geo 3, Geo 2, Geo 1.
    w = await _geo_run(world, n_stops=4, direction="drop")
    route, tid = w["route"], w["trip"]["id"]
    rider = await world.user(Role.STUDENT, "Getting off at Geo 2")
    stayed_home = await world.user(Role.STUDENT, "Not on this bus")
    await world.allocate(rider, route, 1)
    await world.allocate(stayed_home, route, 1)
    await world.board(rider, tid, w["driver"])

    await _report(world, w, _stop_lat(1) + 1.2 * KM)
    near = [n for n in await world.inbox(rider) if n["type"] == "BusApproaching"]
    assert len(near) == 1 and near[0]["title"] == "Geo 2 is coming up"
    assert "BusApproaching" not in _types(await world.inbox(stayed_home))


async def test_only_the_trips_driver_reports_and_only_while_running(world):
    w = await _geo_run(world)
    other = await world.user(Role.DRIVER, "Other Driver")
    student = await world.user(Role.STUDENT)
    await _report(world, w, BASE_LAT, who=other, expect=403)
    await _report(world, w, BASE_LAT, who=student, expect=403)

    await world.post(f"/trips/{w['trip']['id']}/end", who=w["driver"])
    r = await _report(world, w, BASE_LAT, expect=422)
    assert r.json()["code"] == "bad_trip_state"


async def test_buffered_fixes_keep_order_and_future_times_are_clamped(world):
    w = await _geo_run(world)
    tid = w["trip"]["id"]
    started = w["trip"]["started_at"]
    body = {"positions": [  # sent out of order after a network drop, plus one from a fast phone clock
        {"latitude": _stop_lat(0) + 1.5 * KM, "longitude": LNG, "recorded_at": world.at(started, 600)},
        {"latitude": _stop_lat(0) + 0.9 * KM, "longitude": LNG, "recorded_at": world.at(started, -1)},
        {"latitude": _stop_lat(0) + 0.3 * KM, "longitude": LNG, "recorded_at": world.at(started, -2)},
    ]}
    out = (await world.post(f"/trips/{tid}/positions", body, who=w["driver"])).json()
    assert out["accepted"] == 3
    assert abs(out["position"]["latitude"] - (_stop_lat(0) + 1.5 * KM)) < 1e-9  # latest = the clamped one
    latest = datetime.fromisoformat(out["position"]["recorded_at"])
    assert latest < datetime.fromisoformat(world.at(started, 600))


async def test_live_views_carry_stops_and_latest_position(world):
    w = await _geo_run(world)
    tid = w["trip"]["id"]
    before = (await world.get(f"/trips/{tid}/live", who=w["driver"])).json()
    assert before["position"] is None
    assert [s["latitude"] for s in before["stops"]] == [_stop_lat(k) for k in range(4)]

    await _report(world, w, BASE_LAT + 0.2 * KM, speed_kmph=32, heading_deg=0)
    live = (await world.get("/tracking/live")).json()
    assert [t["trip_id"] for t in live] == [tid]
    assert live[0]["position"]["speed_kmph"] == 32
    assert live[0]["next_stop_sequence"] == 2
    student = await world.user(Role.STUDENT)
    await world.get("/tracking/live", who=student, expect=403)
    await world.get(f"/trips/{tid}/live", who=student, expect=404)  # not their route
    await world.allocate(student, w["route"], 1)
    assert (await world.get(f"/trips/{tid}/live", who=student)).json()["position"] is not None


async def test_manual_arrival_still_works_and_is_labelled(world):
    w = await _geo_run(world)
    await world.post(f"/trips/{w['trip']['id']}/stops/2/arrive", who=w["driver"])
    arrived = (await world.get("/history/events", type="StopArrived")).json()
    assert arrived[0]["payload"]["source"] == "manual"


# ---------------- Review fixes: M1, L4 ----------------
async def test_fix_times_need_a_timezone(world):
    w = await _geo_run(world)
    await _report(world, w, BASE_LAT, recorded_at="2026-10-04T10:00:00", expect=422)
    await _report(world, w, BASE_LAT, recorded_at="2026-10-04T10:00:00Z")


async def test_old_positions_are_deleted(world):
    from datetime import timedelta

    from sqlalchemy import func, select, update

    from app.core.db import SessionLocal
    from app.core.timeutil import now_utc
    from app.modules.tracking import service
    from app.modules.tracking.models import BusPosition

    w = await _geo_run(world)
    await _report(world, w, BASE_LAT, BASE_LAT + 0.1 * KM)
    async with SessionLocal() as s:
        oldest = await s.scalar(select(func.min(BusPosition.id)))
        await s.execute(update(BusPosition).where(BusPosition.id == oldest)
                        .values(recorded_at=now_utc() - timedelta(days=100)))
        assert await service.delete_old_positions(s, 90) == 1
        await s.commit()
        assert await s.scalar(select(func.count()).select_from(BusPosition)) == 1
