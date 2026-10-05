import asyncio
from datetime import timedelta
from functools import cache
from typing import Any

import bcrypt
import jwt

from app.core.config import settings
from app.core.errors import Unauthorized
from app.core.timeutil import now_utc

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    if len(password.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 UTF-8 bytes")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(settings.bcrypt_rounds)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    if len(password.encode("utf-8")) > 72:
        return False
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


# bcrypt takes ~250 ms of CPU at 12 rounds. Run it in a worker thread so a wave of logins
# doesn't stall every other request on the event loop.
async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(verify_password, password, password_hash)


@cache
def dummy_hash() -> str:
    """Checked when the email is unknown, so a login takes as long whether or not the account exists."""
    return hash_password("no-such-account")


def sign(claims: dict[str, Any], ttl: timedelta) -> str:
    now = now_utc()
    payload = {**claims, "iat": int(now.timestamp()), "exp": int((now + ttl).timestamp())}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def verify(token: str, expected_type: str) -> dict[str, Any]:
    """Decode a token we signed and check its `typ` claim. Raises Unauthorized."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM],
                             options={"require": ["exp", "iat", "typ"]})
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Token expired", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid token", code="token_invalid") from exc
    if payload.get("typ") != expected_type:
        raise Unauthorized("Wrong token type", code="token_invalid")
    return payload


# `ver` is the user's token_version: bumping it (password change, deactivation) ends every
# session at its next refresh or WebSocket connect.
def create_access_token(user_id: int, role: str, ver: int = 0) -> str:
    return sign(
        {"typ": "access", "sub": str(user_id), "role": role, "ver": ver},
        timedelta(minutes=settings.access_token_minutes),
    )


def create_refresh_token(user_id: int, ver: int = 0) -> str:
    return sign({"typ": "refresh", "sub": str(user_id), "ver": ver}, timedelta(days=settings.refresh_token_days))
