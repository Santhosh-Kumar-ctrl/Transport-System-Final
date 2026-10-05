"""Shared pytest fixtures. Tests run against the `transit_test` database
(created by docker-compose's init script) with background jobs disabled."""

import os

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://transit:transit@localhost:5433/transit_test"
)
os.environ["ENABLE_BACKGROUND_TASKS"] = "false"
os.environ["BCRYPT_ROUNDS"] = "4"
os.environ["REPORT_AI"] = "rules"  # never call a real model from tests
os.environ["ALLOW_SIMULATION"] = "true"  # tests set explicit arrival times
os.environ["ENVIRONMENT"] = "development"

import asyncio  # noqa: E402
import json  # noqa: E402
from datetime import datetime, time, timedelta  # noqa: E402
from itertools import count  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core import events, ratelimit  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.core.roles import Role  # noqa: E402
from app.core.timeutil import local_tz, now_utc  # noqa: E402
from app.db_models import metadata  # noqa: E402
from app.main import create_app  # noqa: E402
from app.modules.auth import service as auth_service  # noqa: E402
from app.modules.auth.schemas import DriverProfileIn, StudentProfileIn, UserCreate  # noqa: E402

PASSWORD = "password123"


@pytest.fixture(scope="session", autouse=True)
async def _schema():
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_db():
    ratelimit.reset_all()
    yield
    await events.drain()
    tables = ", ".join(t.name for t in metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def app():
    return create_app()


class WSClient:
    """Drives the app's /ws endpoint in the test's own event loop (Starlette's TestClient runs the
    app in another loop, which the async database pool can't share)."""

    def __init__(self, app):
        self._to_app: asyncio.Queue = asyncio.Queue()
        self._from_app: asyncio.Queue = asyncio.Queue()
        scope = {"type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws", "path": "/ws", "raw_path": b"/ws",
                 "root_path": "", "query_string": b"", "headers": [], "client": ("127.0.0.1", 5000),
                 "server": ("test", 80), "subprotocols": []}
        self._task = asyncio.create_task(app(scope, self._to_app.get, self._from_app.put))
        self.closed_with: int | None = None

    async def connect(self, token: str | None = None) -> "WSClient":
        await self._to_app.put({"type": "websocket.connect"})
        assert (await self._next())["type"] == "websocket.accept"
        if token is not None:
            await self.send({"action": "auth", "token": token})
        return self

    async def send(self, data) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": json.dumps(data)})

    async def _next(self, timeout: float = 2.0) -> dict:
        return await asyncio.wait_for(self._from_app.get(), timeout)

    async def receive(self, timeout: float = 2.0) -> dict | None:
        """Next JSON frame, or None once the server closed the socket (see `closed_with`)."""
        msg = await self._next(timeout)
        if msg["type"] == "websocket.close":
            self.closed_with = msg.get("code")
            return None
        return json.loads(msg["text"])

    async def drain(self, timeout: float = 0.3) -> list[dict]:
        """Every frame that arrives within `timeout`."""
        frames = []
        while True:
            try:
                frame = await self.receive(timeout)
            except asyncio.TimeoutError:
                return frames
            if frame is None:
                return frames
            frames.append(frame)

    async def close(self) -> None:
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(self._task, 2)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            self._task.cancel()


@pytest.fixture
async def ws(app):
    """Factory: `sock = await ws(token)` opens an authenticated socket; closed after the test."""
    opened: list[WSClient] = []

    async def open_socket(token: str | None = None) -> WSClient:
        sock = await WSClient(app).connect(token)
        opened.append(sock)
        return sock

    yield open_socket
    for sock in opened:
        await sock.close()


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


class World:
    """Builds test data through the real API (and the auth service for bootstrapping)."""

    def __init__(self, client: httpx.AsyncClient):
        self.c = client
        self._n = count(1)
        self.admin: dict = {}

    async def user(self, role: Role, name: str | None = None) -> dict:
        """Create a user and return {"id", "headers", "email"}."""
        i = next(self._n)
        email = f"{role.value}{i}@college.edu"
        data = UserCreate(
            email=email, password=PASSWORD, full_name=name or f"{role.value.title()} {i}", role=role,
            student=StudentProfileIn(roll_no=f"R{i:04d}", department="CSE", year=2) if role == Role.STUDENT else None,
            driver=DriverProfileIn(license_no=f"DL{i:05d}") if role == Role.DRIVER else None,
        )
        async with SessionLocal() as s:
            u = await auth_service.create_user(s, data, actor_id=None)
            await s.commit()
        r = await self.c.post("/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens = r.json()
        return {"id": u.id, "email": email, "token": tokens["access_token"], "refresh": tokens["refresh_token"],
                "headers": {"Authorization": f"Bearer {tokens['access_token']}"}}

    async def setup_admin(self) -> dict:
        self.admin = await self.user(Role.ADMIN, "Ada Admin")
        return self.admin

    async def post(self, url: str, body: dict | list | None = None, who: dict | None = None, expect: int | None = 200):
        r = await self.c.post(url, json=body, headers=(who or self.admin)["headers"])
        if expect is not None:
            assert r.status_code == expect, f"{url} -> {r.status_code}: {r.text}"
        await events.drain()
        return r

    async def delete(self, url: str, who: dict | None = None, expect: int | None = 204):
        r = await self.c.delete(url, headers=(who or self.admin)["headers"])
        if expect is not None:
            assert r.status_code == expect, f"{url} -> {r.status_code}: {r.text}"
        await events.drain()
        return r

    async def patch(self, url: str, body: dict, who: dict | None = None, expect: int | None = 200):
        r = await self.c.patch(url, json=body, headers=(who or self.admin)["headers"])
        if expect is not None:
            assert r.status_code == expect, f"{url} -> {r.status_code}: {r.text}"
        await events.drain()
        return r

    async def put(self, url: str, body: dict | list, who: dict | None = None, expect: int | None = 200):
        r = await self.c.put(url, json=body, headers=(who or self.admin)["headers"])
        if expect is not None:
            assert r.status_code == expect, f"{url} -> {r.status_code}: {r.text}"
        await events.drain()
        return r

    async def get(self, url: str, who: dict | None = None, expect: int | None = 200, **params):
        r = await self.c.get(url, params=params, headers=(who or self.admin)["headers"])
        if expect is not None:
            assert r.status_code == expect, f"{url} -> {r.status_code}: {r.text}"
        return r

    async def route(self, n_stops: int = 4, gap_min: int = 10, code: str | None = None) -> dict:
        i = next(self._n)
        route = (await self.post("/routes", {"code": code or f"R{i}", "name": f"Line {i}", "color": "#1F6FEB"},
                                 expect=201)).json()
        stops = []
        for k in range(n_stops):
            name = "Campus" if k == n_stops - 1 else f"Stop {i}-{k + 1}"
            stops.append((await self.post("/stops", {"name": name}, expect=201)).json())
        body = [{"stop_id": s["id"], "offset_min": k * gap_min} for k, s in enumerate(stops)]
        return (await self.c.put(f"/routes/{route['id']}/stops", json=body, headers=self.admin["headers"])).json()

    async def bus(self, capacity: int = 40) -> dict:
        i = next(self._n)
        return (await self.post("/buses", {"registration_no": f"TN09AB{i:04d}", "capacity": capacity},
                                expect=201)).json()

    async def schedule(self, route: dict, bus: dict, driver: dict, *, direction: str = "pickup",
                       departure: time | None = None) -> dict:
        departure = departure or now_utc().astimezone(local_tz()).time().replace(second=0, microsecond=0)
        body = {"route_id": route["id"], "bus_id": bus["id"], "driver_id": driver["id"], "direction": direction,
                "departure_time": departure.isoformat(), "days_of_week": [1, 2, 3, 4, 5, 6, 7]}
        return (await self.post("/schedules", body, expect=201)).json()

    async def todays_trip(self, schedule: dict) -> dict:
        await self.post("/trips/generate", {})
        trips = (await self.get("/trips")).json()
        return next(t for t in trips if t["schedule_id"] == schedule["id"])

    async def allocate(self, student: dict, route: dict, stop_index: int, force: bool = False) -> dict:
        stop_id = route["stops"][stop_index]["stop_id"]
        return (await self.post("/allocations", {"student_id": student["id"], "route_id": route["id"],
                                                 "stop_id": stop_id, "force": force})).json()

    async def running_trip(self, *, capacity: int = 40, n_stops: int = 4, direction: str = "pickup",
                           departure: time | None = None):
        """Route + bus + driver + schedule + started trip. Returns a dict of everything."""
        route = await self.route(n_stops)
        bus = await self.bus(capacity)
        driver = await self.user(Role.DRIVER, "Dev Driver")
        sched = await self.schedule(route, bus, driver, direction=direction, departure=departure)
        trip = await self.todays_trip(sched)
        trip = (await self.post(f"/trips/{trip['id']}/start", who=driver)).json()
        return {"route": route, "bus": bus, "driver": driver, "schedule": sched, "trip": trip}

    async def board(self, student: dict, trip_id: int, driver: dict, expect: int = 201):
        qr = (await self.get(f"/boarding/trips/{trip_id}/qr", who=driver)).json()
        return await self.post("/boarding/check-in", {"token": qr["token"]}, who=student, expect=expect)

    async def inbox(self, who: dict) -> list[dict]:
        return (await self.get("/notifications", who=who)).json()

    @staticmethod
    def at(iso: str, minutes: int) -> str:
        """ISO timestamp `minutes` after the given ISO timestamp."""
        return (datetime.fromisoformat(iso) + timedelta(minutes=minutes)).isoformat()


@pytest.fixture
async def world(client) -> World:
    w = World(client)
    await w.setup_admin()
    return w
