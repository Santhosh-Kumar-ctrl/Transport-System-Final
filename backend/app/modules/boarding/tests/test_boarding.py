from datetime import timedelta

from app.core import security
from app.core.roles import Role
from app.core.timeutil import now_utc


async def test_student_scans_driver_qr(world):
    w = await world.running_trip()
    student = await world.user(Role.STUDENT, "Meera")
    await world.allocate(student, w["route"], 1)

    qr = (await world.get(f"/boarding/trips/{w['trip']['id']}/qr", who=w["driver"])).json()
    assert qr["ttl_seconds"] == 30 and qr["route_code"] == w["route"]["code"]
    receipt = (await world.post("/boarding/check-in", {"token": qr["token"]}, who=student, expect=201)).json()
    assert receipt["allocation_match"] is True
    assert receipt["stop_name"] == w["route"]["stops"][1]["stop"]["name"]
    assert receipt["boarded_count"] == 1

    dup = await world.post("/boarding/check-in", {"token": qr["token"]}, who=student, expect=409)
    assert dup.json()["code"] == "already_boarded"


async def test_qr_older_than_ttl_is_rejected(world, monkeypatch):
    w = await world.running_trip()
    student = await world.user(Role.STUDENT)
    await world.allocate(student, w["route"], 0)
    # Issue the QR "31 seconds ago"
    monkeypatch.setattr(security, "now_utc", lambda: now_utc() - timedelta(seconds=31))
    qr = (await world.get(f"/boarding/trips/{w['trip']['id']}/qr", who=w["driver"])).json()
    monkeypatch.undo()
    r = await world.post("/boarding/check-in", {"token": qr["token"]}, who=student, expect=422)
    assert r.json()["code"] == "qr_expired"  # 422, not 401: the student's login is still fine


async def test_forged_or_wrong_type_token_rejected(world):
    w = await world.running_trip()
    student = await world.user(Role.STUDENT)
    login_token = student["headers"]["Authorization"].split()[1]
    r = await world.post("/boarding/check-in", {"token": login_token}, who=student, expect=422)
    assert r.json()["code"] == "qr_invalid"
    forged = security.jwt.encode({"typ": "board", "trip": w["trip"]["id"], "exp": 9999999999},
                                 "an-attacker-secret-that-is-not-ours-123", algorithm="HS256")
    r = await world.post("/boarding/check-in", {"token": forged}, who=student, expect=422)
    assert r.json()["code"] == "qr_invalid"


async def test_qr_only_while_trip_runs(world):
    route = await world.route()
    driver = await world.user(Role.DRIVER)
    sched = await world.schedule(route, await world.bus(), driver)
    trip = await world.todays_trip(sched)
    r = await world.get(f"/boarding/trips/{trip['id']}/qr", who=driver, expect=422)
    assert r.json()["code"] == "bad_trip_state"

    # a QR captured during the trip stops working once the trip has ended
    await world.post(f"/trips/{trip['id']}/start", who=driver)
    qr = (await world.get(f"/boarding/trips/{trip['id']}/qr", who=driver)).json()
    await world.post(f"/trips/{trip['id']}/end", who=driver)
    student = await world.user(Role.STUDENT)
    r = await world.post("/boarding/check-in", {"token": qr["token"]}, who=student, expect=422)
    assert r.json()["code"] == "bad_trip_state"


async def test_other_driver_cannot_show_qr(world):
    w = await world.running_trip()
    other = await world.user(Role.DRIVER)
    await world.get(f"/boarding/trips/{w['trip']['id']}/qr", who=other, expect=403)


async def test_unallocated_rider_is_flagged(world):
    w = await world.running_trip()
    stranger = await world.user(Role.STUDENT, "Ravi")
    receipt = (await world.board(stranger, w["trip"]["id"], w["driver"])).json()
    assert receipt["allocation_match"] is False
    driver_inbox = await world.inbox(w["driver"])
    assert any(n["type"] == "UnallocatedBoarding" and "Ravi" in n["body"] for n in driver_inbox)


async def test_manual_board_roster_and_attendance(world):
    w = await world.running_trip()
    tid, driver = w["trip"]["id"], w["driver"]
    rider, no_phone, absentee = [await world.user(Role.STUDENT) for _ in range(3)]
    for s, stop in ((rider, 0), (no_phone, 1), (absentee, 2)):
        await world.allocate(s, w["route"], stop)

    await world.board(rider, tid, driver)
    me = (await world.get("/auth/me", who=no_phone)).json()
    await world.post(f"/boarding/trips/{tid}/manual", {"roll_no": me["student"]["roll_no"]}, who=driver, expect=201)

    roster = (await world.get(f"/boarding/trips/{tid}/roster", who=driver)).json()
    assert roster["allocated_count"] == 3 and roster["boarded_count"] == 2
    assert [e["boarded"] for e in roster["entries"]] == [True, True, False]  # in stop order

    await world.post(f"/trips/{tid}/end", who=driver)
    att = (await world.get("/history/attendance")).json()
    by_student = {a["student_id"]: a["status"] for a in att}
    assert by_student == {rider["id"]: "present", no_phone["id"]: "present", absentee["id"]: "absent"}
    mine = (await world.get("/boarding/me/attendance", who=absentee)).json()
    assert mine[0]["status"] == "absent"


# ---------------- Review fixes: M5, H2 ----------------
async def test_cancelling_a_running_trip_records_who_was_on_board(world):
    w = await world.running_trip()
    on_board, waiting = await world.user(Role.STUDENT), await world.user(Role.STUDENT)
    await world.allocate(on_board, w["route"], 1)
    await world.allocate(waiting, w["route"], 2)
    await world.board(on_board, w["trip"]["id"], w["driver"])
    await world.post(f"/trips/{w['trip']['id']}/cancel", {"reason": "Breakdown"})
    rows = {r["student_id"]: r["status"] for r in (await world.get("/history/attendance")).json()}
    assert rows == {on_board["id"]: "present", waiting["id"]: "absent"}


async def test_cancelling_a_trip_that_never_ran_records_nothing(world):
    route, bus, driver = await world.route(), await world.bus(), await world.user(Role.DRIVER)
    student = await world.user(Role.STUDENT)
    await world.allocate(student, route, 1)
    trip = await world.todays_trip(await world.schedule(route, bus, driver))
    await world.post(f"/trips/{trip['id']}/cancel", {"reason": "No driver"})
    assert (await world.get("/history/attendance")).json() == []


async def test_reconciler_writes_attendance_a_crash_skipped(world):
    from sqlalchemy import update

    from app.core.db import SessionLocal
    from app.modules.boarding import service
    from app.modules.trips.models import Trip, TripStatus

    w = await world.running_trip()
    student = await world.user(Role.STUDENT)
    await world.allocate(student, w["route"], 1)
    await world.board(student, w["trip"]["id"], w["driver"])
    # The trip ended but the TripEnded handler never ran (server restarted in between).
    async with SessionLocal() as s:
        await s.execute(update(Trip).where(Trip.id == w["trip"]["id"]).values(
            status=TripStatus.COMPLETED, ended_at=now_utc() - timedelta(minutes=10)))
        await s.commit()
    async with SessionLocal() as s:
        assert await service.reconcile_attendance(s) == [w["trip"]["id"]]
        await s.commit()
    async with SessionLocal() as s:
        assert await service.reconcile_attendance(s) == []  # nothing left to do
    rows = (await world.get("/history/attendance")).json()
    assert [(r["student_id"], r["status"]) for r in rows] == [(student["id"], "present")]
