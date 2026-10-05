from app.core.roles import Role


async def test_login_me_and_refresh(world, client):
    student = await world.user(Role.STUDENT, "Sam Student")
    me = (await world.get("/auth/me", who=student)).json()
    assert me["full_name"] == "Sam Student"
    assert me["student"]["roll_no"].startswith("R")

    login = (await client.post("/auth/login", json={"email": student["email"], "password": "password123"})).json()
    refreshed = await client.post("/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert refreshed.status_code == 200
    assert refreshed.json()["user"]["id"] == student["id"]


async def test_bad_password_and_wrong_token_type(world, client):
    student = await world.user(Role.STUDENT)
    r = await client.post("/auth/login", json={"email": student["email"], "password": "nope-nope"})
    assert r.status_code == 401 and r.json()["code"] == "bad_credentials"
    # a refresh token must not work as an access token
    login = (await client.post("/auth/login", json={"email": student["email"], "password": "password123"})).json()
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {login['refresh_token']}"})
    assert r.status_code == 401


async def test_admin_creates_users_with_profiles(world):
    r = await world.post("/users", {"email": "new@college.edu", "password": "password123",
                                    "full_name": "New Student", "role": "student"}, expect=422)
    assert "student profile is required" in r.text

    created = (await world.post("/users", {
        "email": "New@College.edu", "password": "password123", "full_name": "New Student",
        "role": "student", "student": {"roll_no": "21CS001", "department": "CSE", "year": 3},
    }, expect=201)).json()
    assert created["email"] == "new@college.edu"

    dup = await world.post("/users", {
        "email": "other@college.edu", "password": "password123", "full_name": "Dup",
        "role": "student", "student": {"roll_no": "21CS001"},
    }, expect=409)
    assert dup.json()["code"] == "roll_no_taken"

    students = (await world.get("/users", role="student", q="21cs")).json()
    assert [u["id"] for u in students] == [created["id"]]


async def test_only_admin_manages_users(world):
    driver = await world.user(Role.DRIVER)
    await world.get("/users", who=driver, expect=403)


async def test_deactivated_user_cannot_log_in(world, client):
    student = await world.user(Role.STUDENT)
    r = await client.patch(f"/users/{student['id']}", json={"is_active": False}, headers=world.admin["headers"])
    assert r.status_code == 200
    r = await client.post("/auth/login", json={"email": student["email"], "password": "password123"})
    assert r.status_code == 401 and r.json()["code"] == "inactive"


# ---------------- Review fixes: B1, M1, M2, M3, M6 ----------------
async def test_password_hashing_runs_off_the_event_loop(monkeypatch):
    import asyncio
    import time as clock

    from app.core import security

    def slow_check(password, password_hash):
        clock.sleep(0.3)  # a CPU-bound bcrypt check
        return True

    monkeypatch.setattr(security, "verify_password", slow_check)
    loop = asyncio.get_running_loop()
    started = loop.time()
    check = asyncio.create_task(security.verify_password_async("x", "y"))
    await asyncio.sleep(0.05)
    assert loop.time() - started < 0.2  # the loop kept serving while the check ran
    assert await check


async def test_login_is_rate_limited_per_account(world):
    s = await world.user(Role.STUDENT)  # one successful login already
    codes = [(await world.c.post("/auth/login", json={"email": s["email"], "password": "wrong-pass"})).status_code
             for _ in range(10)]
    assert codes[:9] == [401] * 9 and codes[9] == 429
    r = await world.c.post("/auth/login", json={"email": s["email"], "password": "wrong-pass"})
    assert r.json()["code"] == "rate_limited" and int(r.headers["Retry-After"]) > 0
    other = await world.user(Role.STUDENT)  # other accounts from the same address still sign in
    assert other["token"]


async def test_password_change_ends_existing_sessions(world):
    s = await world.user(Role.STUDENT)
    await world.patch(f"/users/{s['id']}", {"password": "a-new-password"})
    r = await world.c.post("/auth/refresh", json={"refresh_token": s["refresh"]})
    assert r.status_code == 401 and r.json()["code"] == "session_revoked"
    fresh = await world.c.post("/auth/login", json={"email": s["email"], "password": "a-new-password"})
    again = await world.c.post("/auth/refresh", json={"refresh_token": fresh.json()["refresh_token"]})
    assert again.status_code == 200


async def test_deactivation_ends_existing_sessions(world):
    s = await world.user(Role.DRIVER)
    await world.patch(f"/users/{s['id']}", {"is_active": False})
    r = await world.c.post("/auth/refresh", json={"refresh_token": s["refresh"]})
    assert r.status_code == 401


async def test_last_active_admin_cannot_be_deactivated(world):
    r = await world.patch(f"/users/{world.admin['id']}", {"is_active": False}, expect=422)
    assert r.json()["code"] == "last_admin"
    await world.user(Role.ADMIN)
    await world.patch(f"/users/{world.admin['id']}", {"is_active": False})


async def test_duplicate_roll_no_on_update_is_a_conflict(world):
    a, b = await world.user(Role.STUDENT), await world.user(Role.STUDENT)
    roll = (await world.get(f"/users/{a['id']}")).json()["student"]["roll_no"]
    r = await world.patch(f"/users/{b['id']}", {"student": {"roll_no": roll}}, expect=409)
    assert r.json()["code"] == "profile_taken"


async def test_negative_paging_is_a_validation_error(world):
    await world.get("/users", expect=422, offset=-1)
    await world.get("/users", expect=422, limit=0)
    await world.get("/history/events", expect=422, offset=-1)
    student = await world.user(Role.STUDENT)
    await world.get("/notifications", who=student, expect=422, limit=-1)
    await world.get("/boarding/me/attendance", who=student, expect=422, limit=-1)
