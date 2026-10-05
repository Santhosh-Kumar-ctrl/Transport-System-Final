"""Cross-cutting fixes from Review.md: WebSocket auth and topic access (B3, M2), the hub's
broadcast bookkeeping (H1), production settings (B6) and the health check."""

import asyncio
import json
from datetime import timedelta

import pytest

from app.core import security
from app.core.config import Settings
from app.core.deps import Principal
from app.core.realtime import Hub
from app.core.roles import Role


# ---------------- WebSocket auth ----------------
async def test_socket_needs_a_token_in_the_first_frame(ws, world):
    silent = await ws()
    await silent.send({"action": "subscribe", "topic": "route:1"})  # not an auth frame
    assert await silent.receive() is None and silent.closed_with == 4401

    bad = await ws("not-a-token")
    assert await bad.receive() is None and bad.closed_with == 4401

    good = await ws(world.admin["token"])
    assert (await good.receive())["type"] == "authenticated"


async def test_socket_closes_when_its_token_expires(ws, world):
    token = security.sign({"typ": "access", "sub": str(world.admin["id"]), "role": "admin", "ver": 0},
                          timedelta(seconds=2))
    sock = await ws(token)
    assert (await sock.receive())["type"] == "authenticated"
    assert await sock.receive(timeout=4) is None and sock.closed_with == 4401


async def test_revoking_a_users_sessions_closes_their_sockets(ws, world):
    student = await world.user(Role.STUDENT)
    sock = await ws(student["token"])
    assert (await sock.receive())["type"] == "authenticated"
    await world.patch(f"/users/{student['id']}", {"password": "brand-new-password"})
    assert await sock.receive() is None and sock.closed_with == 4401
    again = await ws(student["token"])  # the old token can't reconnect either
    assert await again.receive() is None and again.closed_with == 4401


# ---------------- Topic access and payload privacy ----------------
async def test_students_follow_only_their_own_route_and_its_trips(ws, world):
    mine, other = await world.running_trip(), await world.running_trip()
    student = await world.user(Role.STUDENT)
    await world.allocate(student, mine["route"], 1)
    sock = await ws(student["token"])
    await sock.receive()
    for topic in (f"route:{mine['route']['id']}", f"trip:{mine['trip']['id']}",
                  f"route:{other['route']['id']}", f"trip:{other['trip']['id']}", "anything:1", "route:x"):
        await sock.send({"action": "subscribe", "topic": topic})
    errors = {f["data"]["topic"] for f in await sock.drain() if f["type"] == "error"}
    assert errors == {f"route:{other['route']['id']}", f"trip:{other['trip']['id']}", "anything:1", "route:x"}


async def test_riders_never_see_who_boarded(ws, world):
    w = await world.running_trip()
    watcher, rider = await world.user(Role.STUDENT), await world.user(Role.STUDENT, "Priya Rider")
    await world.allocate(watcher, w["route"], 1)
    await world.allocate(rider, w["route"], 1)
    sock = await ws(watcher["token"])
    await sock.receive()
    await sock.send({"action": "subscribe", "topic": f"route:{w['route']['id']}"})
    await sock.send({"action": "subscribe", "topic": f"trip:{w['trip']['id']}"})
    driver_sock = await ws(w["driver"]["token"])
    await driver_sock.receive()
    await asyncio.sleep(0.1)

    await world.board(rider, w["trip"]["id"], w["driver"])
    frames = await sock.drain()
    assert {f["type"] for f in frames} >= {"ops", "boarding"}
    assert "Priya Rider" not in json.dumps(frames)
    assert all("student_id" not in f["data"].get("payload", f["data"]) for f in frames)
    assert any(f["type"] == "boarding" and f["data"] == {"trip_id": w["trip"]["id"], "boarded_count": 1}
               for f in frames)
    # the driver still gets the name on their own channel
    assert any(f["type"] == "boarding" and f["data"].get("student_name") == "Priya Rider"
               for f in await driver_sock.drain())


# ---------------- Hub bookkeeping ----------------
class _FakeSocket:
    def __init__(self, hub: Hub, *, fail: bool = False, joiner: "_FakeSocket | None" = None):
        self.hub, self.fail, self.joiner = hub, fail, joiner

    async def send_json(self, frame):
        await asyncio.sleep(0.001)
        if self.joiner:  # another admin connects while this broadcast is in flight
            self.hub.add(self.joiner, Principal(id=99, role=Role.ADMIN))
            self.joiner = None
        if self.fail:
            raise RuntimeError("socket closed")

    async def close(self, code: int = 1000):
        pass


async def test_a_broadcast_drops_only_dead_sockets():
    for _ in range(200):
        hub = Hub()
        newcomer = _FakeSocket(hub)
        dead = _FakeSocket(hub, fail=True, joiner=newcomer)
        hub.add(dead, Principal(id=1, role=Role.ADMIN))
        await hub.send_to_role(Role.ADMIN, "ops", {})
        assert hub._by_role[Role.ADMIN] == {newcomer}


async def test_a_stuck_socket_doesnt_hold_up_a_broadcast(monkeypatch):
    from app.core import realtime

    monkeypatch.setattr(realtime, "SEND_TIMEOUT_SECONDS", 0.05)

    class Stuck(_FakeSocket):
        async def send_json(self, frame):
            await asyncio.sleep(10)

    hub = Hub()
    stuck, fine = Stuck(hub), _FakeSocket(hub)
    hub.add(stuck, Principal(id=1, role=Role.ADMIN))
    hub.add(fine, Principal(id=2, role=Role.ADMIN))
    await asyncio.wait_for(hub.send_to_role(Role.ADMIN, "ops", {}), 1)
    assert hub._by_role[Role.ADMIN] == {fine}


# ---------------- Production settings and health ----------------
def test_production_refuses_unsafe_settings(monkeypatch):
    monkeypatch.delenv("ALLOW_SIMULATION", raising=False)
    unsafe = Settings(environment="production", allow_simulation=True, _env_file=None)
    problems = unsafe.production_problems()
    assert [p.split()[0] for p in problems] == ["JWT_SECRET", "ALLOW_SIMULATION", "CORS_ORIGINS"]
    safe = Settings(environment="production", jwt_secret="x" * 40, cors_origins="https://transit.example.edu",
                    _env_file=None)
    assert safe.production_problems() == []


def test_app_wont_start_in_production_with_unsafe_settings(monkeypatch):
    from app import main
    from app.core.config import settings

    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(RuntimeError, match="Refusing to start in production"):
        main.create_app()


def test_simulation_is_off_unless_configured(monkeypatch):
    monkeypatch.delenv("ALLOW_SIMULATION", raising=False)
    assert Settings(_env_file=None).allow_simulation is False


async def test_health_checks_the_database(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
