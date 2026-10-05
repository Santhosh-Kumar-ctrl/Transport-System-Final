from app.core.roles import Role


async def _route_with_capacity(world, capacity: int):
    route = await world.route(n_stops=3)
    await world.schedule(route, await world.bus(capacity), await world.user(Role.DRIVER))
    return route


async def test_assign_and_student_sees_it(world):
    route = await _route_with_capacity(world, 40)
    student = await world.user(Role.STUDENT, "Priya")
    res = await world.allocate(student, route, 1)
    assert res["changed"] and res["warnings"] == []
    assert res["allocation"]["stop"]["id"] == route["stops"][1]["stop_id"]

    mine = (await world.get("/allocations/me", who=student)).json()
    assert mine["route"]["id"] == route["id"]
    assert len(mine["route"]["stops"]) == 3

    inbox = await world.inbox(student)
    assert inbox[0]["type"] == "StudentAllocated"


async def test_reassign_ends_previous(world):
    r1 = await _route_with_capacity(world, 40)
    r2 = await _route_with_capacity(world, 40)
    student = await world.user(Role.STUDENT)
    await world.allocate(student, r1, 0)
    same = await world.allocate(student, r1, 0)
    assert same["changed"] is False
    await world.allocate(student, r2, 1)

    all_rows = (await world.get("/allocations", student_id=student["id"], include_ended=True)).json()
    statuses = sorted((a["route"]["id"], a["status"]) for a in all_rows)
    assert statuses == sorted([(r1["id"], "ended"), (r2["id"], "active")])
    assert (await world.inbox(student))[0]["type"] == "AllocationChanged"


async def test_capacity_guard_and_force(world):
    route = await _route_with_capacity(world, 2)
    s1, s2, s3 = [await world.user(Role.STUDENT) for _ in range(3)]
    await world.allocate(s1, route, 0)
    await world.allocate(s2, route, 0)
    r = await world.post("/allocations", {"student_id": s3["id"], "route_id": route["id"],
                                          "stop_id": route["stops"][0]["stop_id"]}, expect=409)
    assert r.json()["code"] == "route_full" and r.json()["capacity"] == 2
    forced = await world.allocate(s3, route, 0, force=True)
    assert forced["warnings"] and "Over capacity" in forced["warnings"][0]


async def test_route_without_schedule_warns(world):
    route = await world.route()
    student = await world.user(Role.STUDENT)
    res = await world.allocate(student, route, 0)
    assert "capacity is unknown" in res["warnings"][0]


async def test_cannot_remove_stop_with_allocated_students(world, client):
    route = await _route_with_capacity(world, 40)
    student = await world.user(Role.STUDENT)
    await world.allocate(student, route, 1)
    keep = [s for i, s in enumerate(route["stops"]) if i != 1]
    body = [{"stop_id": s["stop_id"], "offset_min": s["offset_min"]} for s in keep]
    r = await client.put(f"/routes/{route['id']}/stops", json=body, headers=world.admin["headers"])
    assert r.status_code == 409
    # after unassigning, the stop can go
    r = await client.delete(f"/allocations/students/{student['id']}", headers=world.admin["headers"])
    assert r.status_code == 204
    r = await client.put(f"/routes/{route['id']}/stops", json=body, headers=world.admin["headers"])
    assert r.status_code == 200


async def test_stop_must_be_on_route_and_student_role(world):
    r1, r2 = await _route_with_capacity(world, 40), await _route_with_capacity(world, 40)
    student = await world.user(Role.STUDENT)
    r = await world.post("/allocations", {"student_id": student["id"], "route_id": r1["id"],
                                          "stop_id": r2["stops"][0]["stop_id"]}, expect=422)
    assert r.json()["code"] == "stop_not_on_route"
    driver = await world.user(Role.DRIVER)
    r = await world.post("/allocations", {"student_id": driver["id"], "route_id": r1["id"],
                                          "stop_id": r1["stops"][0]["stop_id"]}, expect=422)
    assert r.json()["code"] == "wrong_role"


async def test_bulk_assign_reports_per_row(world):
    route = await _route_with_capacity(world, 1)
    s1, s2 = await world.user(Role.STUDENT), await world.user(Role.STUDENT)
    stop = route["stops"][0]["stop_id"]
    res = (await world.post("/allocations/bulk", {"items": [
        {"student_id": s1["id"], "route_id": route["id"], "stop_id": stop},
        {"student_id": s2["id"], "route_id": route["id"], "stop_id": stop},
    ]})).json()
    assert [x["ok"] for x in res] == [True, False]
    assert "full" in res[1]["error"]


# ---------------- Review fixes: M6 ----------------
async def test_deactivated_student_gives_their_seat_back(world):
    route, bus, driver = await world.route(), await world.bus(capacity=1), await world.user(Role.DRIVER)
    await world.schedule(route, bus, driver)
    leaving, joining = await world.user(Role.STUDENT), await world.user(Role.STUDENT)
    await world.allocate(leaving, route, 0)
    await world.patch(f"/users/{leaving['id']}", {"is_active": False})
    assert (await world.get("/allocations", student_id=leaving["id"])).json() == []
    await world.allocate(joining, route, 0)  # the seat is free again
