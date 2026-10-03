from app.core.config import settings
from app.core.roles import Role


async def test_lockout_after_repeated_failures_then_blocks_even_correct_password(world, client):
    user = await world.user(Role.STUDENT)
    for _ in range(settings.login_max_failures):
        r = await client.post("/auth/login", json={"email": user["email"], "password": "wrong-password"})
        assert r.status_code == 401
    r = await client.post("/auth/login", json={"email": user["email"], "password": "password123"})
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0
    assert r.json()["code"] == "too_many_attempts"


async def test_success_resets_counter_and_other_users_unaffected(world, client):
    a = await world.user(Role.STUDENT)
    b = await world.user(Role.STUDENT)
    for _ in range(settings.login_max_failures - 1):
        await client.post("/auth/login", json={"email": a["email"], "password": "nope-nope"})
    ok = await client.post("/auth/login", json={"email": a["email"], "password": "password123"})
    assert ok.status_code == 200
    for _ in range(settings.login_max_failures - 1):
        r = await client.post("/auth/login", json={"email": a["email"], "password": "nope-nope"})
        assert r.status_code == 401
    assert (await client.post("/auth/login", json={"email": b["email"], "password": "password123"})).status_code == 200


async def test_admin_cannot_deactivate_self(world, client):
    r = await client.patch(f"/users/{world.admin['id']}", headers=world.admin["headers"], json={"is_active": False})
    assert r.status_code == 422
    assert r.json()["code"] == "self_deactivate"
