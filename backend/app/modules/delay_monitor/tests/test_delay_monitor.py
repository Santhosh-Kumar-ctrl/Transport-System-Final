from datetime import timedelta

import pytest

from app.core.roles import Role
from app.core.timeutil import local_tz, now_utc


def _types(inbox: list[dict]) -> list[str]:
    return [n["type"] for n in inbox]


def _minutes_ago(m: int):
    local = now_utc().astimezone(local_tz())
    earlier = local - timedelta(minutes=m)
    if earlier.date() != local.date():
        pytest.skip("too close to midnight for a same-day schedule")
    return earlier.time().replace(second=0, microsecond=0)


async def test_late_arrival_notifies_only_downstream_waiting_students(world):
    w = await world.running_trip(n_stops=4)
    trip, route = w["trip"], w["route"]
    on_board = await world.user(Role.STUDENT, "On Board")
    at_stop2 = await world.user(Role.STUDENT, "At Stop Two")
    at_stop3 = await world.user(Role.STUDENT, "At Stop Three")
    await world.allocate(on_board, route, 0)
    await world.allocate(at_stop2, route, 1)
    await world.allocate(at_stop3, route, 2)
    await world.board(on_board, trip["id"], w["driver"])

    stop2 = trip["stops"][1]
    await world.post(f"/trips/{trip['id']}/stops/2/arrive", {"arrived_at": world.at(stop2["scheduled_at"], 8)})

    s3 = await world.inbox(at_stop3)
    delayed = [n for n in s3 if n["type"] == "TripDelayed"]
    assert len(delayed) == 1
    assert "8 min late" in delayed[0]["title"]
    assert route["stops"][2]["stop"]["name"] in delayed[0]["body"]
    assert "TripDelayed" not in _types(await world.inbox(at_stop2))  # bus already at their stop
    assert "TripDelayed" not in _types(await world.inbox(on_board))  # already riding
    admin_alert = next(n for n in await world.inbox(world.admin) if n["type"] == "TripDelayed")
    assert admin_alert["payload"]["affected_students"] == 1
    assert "TripDelayed" in _types(await world.inbox(w["driver"]))


async def test_small_delays_and_repeats_dont_spam(world):
    w = await world.running_trip(n_stops=6)
    trip = w["trip"]
    stops = trip["stops"]
    await world.post(f"/trips/{trip['id']}/stops/2/arrive", {"arrived_at": world.at(stops[1]["scheduled_at"], 3)})
    assert (await world.get(f"/trips/{trip['id']}/delays")).json() == []  # under threshold

    await world.post(f"/trips/{trip['id']}/stops/3/arrive", {"arrived_at": world.at(stops[2]["scheduled_at"], 7)})
    await world.post(f"/trips/{trip['id']}/stops/4/arrive", {"arrived_at": world.at(stops[3]["scheduled_at"], 13)})
    reports = (await world.get(f"/trips/{trip['id']}/delays")).json()
    assert [r["delay_min"] for r in reports] == [7]  # still the 5-minute step: no new alert

    await world.post(f"/trips/{trip['id']}/stops/5/arrive", {"arrived_at": world.at(stops[4]["scheduled_at"], 16)})
    reports = (await world.get(f"/trips/{trip['id']}/delays")).json()
    assert [r["delay_min"] for r in reports] == [16, 7]  # reached the 15-minute step -> escalation


async def test_recovery_sends_back_on_schedule(world):
    w = await world.running_trip(n_stops=5)
    trip, route = w["trip"], w["route"]
    far = await world.user(Role.STUDENT)
    await world.allocate(far, route, 3)
    stops = trip["stops"]
    await world.post(f"/trips/{trip['id']}/stops/2/arrive", {"arrived_at": world.at(stops[1]["scheduled_at"], 9)})
    await world.post(f"/trips/{trip['id']}/stops/3/arrive", {"arrived_at": world.at(stops[2]["scheduled_at"], 1)})
    types = _types(await world.inbox(far))
    assert types[:2] == ["TripDelayResolved", "TripDelayed"]  # newest first
    reports = (await world.get(f"/trips/{trip['id']}/delays")).json()
    assert reports[0]["source"] == "recovered"


async def test_manual_report_by_driver(world):
    w = await world.running_trip(n_stops=4)
    trip, route, driver = w["trip"], w["route"], w["driver"]
    waiting = await world.user(Role.STUDENT)
    await world.allocate(waiting, route, 2)
    r = (await world.post(f"/trips/{trip['id']}/delay", {"delay_min": 15, "reason": "Road closed near market"},
                          who=driver, expect=201)).json()
    assert r["source"] == "manual"
    note = next(n for n in await world.inbox(waiting) if n["type"] == "TripDelayed")
    assert "Road closed near market" in note["body"]
    assert "TripDelayed" not in _types(await world.inbox(driver))  # they reported it themselves


async def test_watcher_flags_overdue_stop(world):
    route = await world.route(n_stops=4, gap_min=10)
    driver = await world.user(Role.DRIVER)
    sched = await world.schedule(route, await world.bus(), driver, departure=_minutes_ago(30))
    trip = await world.todays_trip(sched)
    await world.post(f"/trips/{trip['id']}/start", {"started_at": trip["scheduled_departure"]})  # on time

    first = (await world.post("/delays/watch")).json()
    again = (await world.post("/delays/watch")).json()
    assert first["raised"] == 1 and again["raised"] == 0
    report = (await world.get(f"/trips/{trip['id']}/delays")).json()[0]
    assert report["source"] == "overdue" and report["at_sequence"] == 2 and report["delay_min"] >= 19


async def test_watcher_flags_trip_not_started(world):
    route = await world.route(n_stops=3)
    driver = await world.user(Role.DRIVER)
    sched = await world.schedule(route, await world.bus(), driver, departure=_minutes_ago(20))
    trip = await world.todays_trip(sched)
    student = await world.user(Role.STUDENT)
    await world.allocate(student, route, 0)
    assert (await world.post("/delays/watch")).json()["raised"] == 1
    report = (await world.get(f"/trips/{trip['id']}/delays")).json()[0]
    assert report["source"] == "not_started"
    assert "TripDelayed" in _types(await world.inbox(student))


# ---------------- Review fixes: B2 (stepped alerts) ----------------
async def test_a_trip_that_never_starts_is_announced_in_steps_then_stops(world, monkeypatch):
    from sqlalchemy import func, select

    from app.core import events
    from app.core.db import SessionLocal
    from app.modules.delay_monitor import service as delay_service
    from app.modules.notifications.models import Notification

    route, bus, driver = await world.route(), await world.bus(), await world.user(Role.DRIVER)
    student = await world.user(Role.STUDENT)
    await world.allocate(student, route, 1)
    start = (now_utc() - timedelta(minutes=1)).astimezone(local_tz())
    if start.date() != now_utc().astimezone(local_tz()).date():
        pytest.skip("near midnight")
    sched = await world.schedule(route, bus, driver, departure=start.time().replace(second=0, microsecond=0))
    await world.todays_trip(sched)
    t0 = now_utc()
    for minute in range(0, 181):  # the watcher runs every minute for three hours; nobody starts the trip
        monkeypatch.setattr(delay_service, "now_utc", lambda m=minute: t0 + timedelta(minutes=m))
        async with SessionLocal() as s:
            await delay_service.watch_once(s)
            await s.commit()
        await events.drain()

    async def delayed_alerts(user_id: int) -> int:
        async with SessionLocal() as s:
            return await s.scalar(select(func.count()).select_from(Notification).where(
                Notification.user_id == user_id, Notification.type == "TripDelayed"))

    assert await delayed_alerts(student["id"]) == 3  # +5, +15, +30
    assert await delayed_alerts(driver["id"]) == 3
    assert await delayed_alerts(world.admin["id"]) == 4  # and one office-only alert at +60
    last = (await world.inbox(world.admin))[0]
    assert "No further alerts" in last["body"]


async def test_manual_reports_still_reach_riders_after_the_last_step(world):
    w = await world.running_trip(n_stops=4)
    trip, rider = w["trip"], await world.user(Role.STUDENT)
    await world.allocate(rider, w["route"], 2)
    stops = trip["stops"]
    await world.post(f"/trips/{trip['id']}/stops/2/arrive", {"arrived_at": world.at(stops[1]["scheduled_at"], 70)})
    before = len([n for n in await world.inbox(rider) if n["type"] == "TripDelayed"])
    await world.post(f"/trips/{trip['id']}/delay", {"delay_min": 75, "reason": "Road closed"}, who=w["driver"],
                     expect=201)
    after = [n for n in await world.inbox(rider) if n["type"] == "TripDelayed"]
    assert len(after) == before + 1 and "Road closed" in after[0]["body"]


async def test_first_alert_of_a_delay_always_reaches_riders(world):
    w = await world.running_trip(n_stops=4)
    trip, rider = w["trip"], await world.user(Role.STUDENT)
    await world.allocate(rider, w["route"], 2)
    stop2 = trip["stops"][1]
    await world.post(f"/trips/{trip['id']}/stops/2/arrive", {"arrived_at": world.at(stop2["scheduled_at"], 70)})
    assert [n["type"] for n in await world.inbox(rider)][:1] == ["TripDelayed"]
