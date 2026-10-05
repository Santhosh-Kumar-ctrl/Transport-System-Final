"""WebSocket hub: pushes live updates to connected clients.

Channels a socket receives from:
  * its own user   -> hub.send_to_user(user_id, msg)
  * its role       -> hub.send_to_role(Role.ADMIN, msg)
  * topics it asked for, e.g. "route:3" or "trip:12" -> hub.send_to_topic("trip:12", msg)

Client protocol (JSON text frames):
  -> {"action": "auth", "token": "<access token>"}   first frame, within AUTH_TIMEOUT_SECONDS
  <- {"type": "authenticated", "data": {}}
  -> {"action": "subscribe", "topic": "route:3"}
  <- {"type": "error", "data": {"topic": "route:3", "code": "forbidden"}}   if not allowed
  -> {"action": "unsubscribe", "topic": "route:3"}
  -> {"action": "ping"}                      <- {"type": "pong"}
  <- {"type": "<message type>", "data": {...}}

The token is sent in a frame, not the URL, so it never ends up in access logs. The server closes
the socket with code 4401 when the token is missing, invalid, expired or revoked; the client then
refreshes its token and reconnects.

Topics are access-controlled: each prefix ("route", "trip") has a policy that the owning module
registers with `set_topic_policy` in its `register()`. Admins may subscribe to anything; a topic
without a policy is refused.

In-memory and single-process. To run several API workers, back this with Redis pub/sub.
"""

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.deps import Principal, authenticated_principal
from app.core.db import SessionLocal
from app.core.access import can_subscribe
from app.core.errors import DomainError
from app.core.events import jsonable
from app.core.roles import Role

log = logging.getLogger("transit.realtime")

AUTH_TIMEOUT_SECONDS = 5
SEND_TIMEOUT_SECONDS = 5
CLOSE_UNAUTHORIZED = 4401

TopicPolicy = Callable[[Principal, int], Awaitable[bool]]
SessionCheck = Callable[[Principal], Awaitable[bool]]

_topic_policies: dict[str, TopicPolicy] = {}
_session_check: SessionCheck | None = None


def set_topic_policy(prefix: str, policy: TopicPolicy) -> None:
    """Who may subscribe to "<prefix>:<id>" topics. Admins always may."""
    _topic_policies[prefix] = policy


def set_session_check(check: SessionCheck) -> None:
    """Extra check on connect: is the account behind the token still active and not revoked?"""
    global _session_check
    _session_check = check


def clear_policies() -> None:
    global _session_check
    _topic_policies.clear()
    _session_check = None


async def can_subscribe(p: Principal, topic: str) -> bool:
    prefix, _, raw = topic.partition(":")
    policy = _topic_policies.get(prefix)
    if policy is None or not raw.isdigit():
        return False
    if p.role == Role.ADMIN:
        return True
    try:
        return await policy(p, int(raw))
    except Exception:  # noqa: BLE001 - a failing policy denies, never crashes the socket
        log.exception("Topic policy for %s failed", topic)
        return False


class Hub:
    def __init__(self) -> None:
        self._by_user: dict[int, set[WebSocket]] = defaultdict(set)
        self._by_role: dict[Role, set[WebSocket]] = defaultdict(set)
        self._by_topic: dict[str, set[WebSocket]] = defaultdict(set)
        self._topics_of: dict[WebSocket, set[str]] = defaultdict(set)
        self._principal: dict[WebSocket, Principal] = {}
        self._tokens: dict[WebSocket, str] = {}

    def add(self, ws: WebSocket, p: Principal, token: str) -> None:
        self._principal[ws] = p
        self._tokens[ws] = token
        self._by_user[p.id].add(ws)
        self._by_role[p.role].add(ws)

    def remove(self, ws: WebSocket) -> None:
        self._tokens.pop(ws, None)
        p = self._principal.pop(ws, None)
        if p:
            self._by_user[p.id].discard(ws)
            self._by_role[p.role].discard(ws)
        for topic in self._topics_of.pop(ws, set()):
            self._by_topic[topic].discard(ws)

    def subscribe(self, ws: WebSocket, topic: str) -> None:
        self._by_topic[topic].add(ws)
        self._topics_of[ws].add(topic)

    def unsubscribe(self, ws: WebSocket, topic: str) -> None:
        self._by_topic[topic].discard(ws)
        self._topics_of[ws].discard(topic)

    async def _send(self, sockets: set[WebSocket], msg_type: str, data: Any) -> None:
        # Snapshot once: the live set can change while we await (someone connects or leaves),
        # and results must line up with the sockets they came from.
        targets = list(sockets)
        if not targets:
            return
        frame = {"type": msg_type, "data": jsonable(data)}
        live = []
        for ws in list(sockets):
            try:
                async with SessionLocal() as session:
                    await authenticated_principal(self._tokens[ws], session)
                live.append(ws)
            except (DomainError, KeyError):
                await ws.close(code=4401)
                self.remove(ws)
        results = await asyncio.gather(
            *(asyncio.wait_for(ws.send_json(frame), SEND_TIMEOUT_SECONDS) for ws in targets),
            return_exceptions=True,
        )
        for ws, res in zip(targets, results):
            if isinstance(res, BaseException):
                self.remove(ws)

    async def send_to_user(self, user_id: int, msg_type: str, data: Any) -> None:
        await self._send(self._by_user.get(user_id, set()), msg_type, data)

    async def send_to_users(self, user_ids: list[int], msg_type: str, data: Any) -> None:
        sockets: set[WebSocket] = set()
        for uid in user_ids:
            sockets |= self._by_user.get(uid, set())
        await self._send(sockets, msg_type, data)

    async def send_to_role(self, role: Role, msg_type: str, data: Any) -> None:
        await self._send(self._by_role.get(role, set()), msg_type, data)

    async def send_to_topic(self, topic: str, msg_type: str, data: Any) -> None:
        permitted = set()
        async with SessionLocal() as session:
            for ws in list(self._by_topic.get(topic, set())):
                # A socket can disconnect (and be removed) while we await below.
                principal = self._principal.get(ws)
                if principal is None:
                    continue
                if await can_subscribe(session, principal, topic):
                    permitted.add(ws)
                else:
                    self.unsubscribe(ws, topic)
        await self._send(permitted, msg_type, data)

    async def disconnect_user(self, user_id: int) -> None:
        """Close every socket of a user (sessions revoked); their app must sign in again."""
        for ws in list(self._by_user.get(user_id, ())):
            self.remove(ws)
            try:
                await ws.close(code=CLOSE_UNAUTHORIZED)
            except Exception:  # noqa: BLE001 - already gone
                pass

    def connection_count(self) -> int:
        return len(self._principal)


hub = Hub()
router = APIRouter(tags=["realtime"])


async def _authenticate(ws: WebSocket) -> Principal | None:
    try:
        msg = await asyncio.wait_for(ws.receive_json(), AUTH_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, ValueError):
        return None
    if not isinstance(msg, dict) or msg.get("action") != "auth" or not isinstance(msg.get("token"), str):
        return None
    try:
        principal = principal_from_token(msg["token"])
    except DomainError:
        return None
    if _session_check is not None and not await _session_check(principal):
        return None
    return principal


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    try:
        principal = await _authenticate(ws)
    except WebSocketDisconnect:
        return
    if principal is None:
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return
    hub.add(ws, principal)
    try:
        await ws.send_json({"type": "authenticated", "data": {}})
        while True:
            # The socket lives only as long as its token: then the client refreshes and reconnects.
            remaining = principal.expires_at - time.time() if principal.expires_at else None
            if remaining is not None and remaining <= 0:
                await ws.close(code=CLOSE_UNAUTHORIZED)
                break
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=remaining)
            except asyncio.TimeoutError:
                await ws.close(code=CLOSE_UNAUTHORIZED)
                break
            action = msg.get("action") if isinstance(msg, dict) else None
            topic = msg.get("topic") if isinstance(msg, dict) else None
            if action == "subscribe" and isinstance(topic, str):
                if await can_subscribe(principal, topic):
                    hub.subscribe(ws, topic)
                else:
                    await ws.send_json({"type": "error", "data": {"topic": topic, "code": "forbidden"}})
            elif action == "unsubscribe" and isinstance(topic, str):
                hub.unsubscribe(ws, topic)
            elif action == "ping":
                await ws.send_json({"type": "pong", "data": {}})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("WebSocket error")
        await ws.close(code=1011)
    finally:
        hub.remove(ws)
