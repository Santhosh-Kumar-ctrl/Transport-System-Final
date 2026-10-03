from datetime import timedelta
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


def create_access_token(user_id: int, role: str, version: int = 0) -> str:
    return sign(
        {"typ": "access", "sub": str(user_id), "role": role, "ver": version},
        timedelta(minutes=settings.access_token_minutes),
    )


def create_refresh_token(user_id: int, version: int = 0) -> str:
    import secrets
    return sign({"typ": "refresh", "sub": str(user_id), "ver": version,
                 "jti": secrets.token_urlsafe(32)}, timedelta(days=settings.refresh_token_days))
