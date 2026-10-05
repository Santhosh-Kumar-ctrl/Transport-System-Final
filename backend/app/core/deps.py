from dataclasses import dataclass

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session

from app.core.errors import Forbidden, Unauthorized
from app.core.roles import Role
from app.core.security import verify

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, decoded from the access token (no DB hit).

    A deactivated user (or one whose password changed) keeps REST access until their access
    token expires (ACCESS_TOKEN_MINUTES); refresh and WebSocket connects check the account.
    """

    id: int
    role: Role
    ver: int = 0  # the user's token_version when the token was issued
    expires_at: int | None = None  # unix time the access token expires

    def is_(self, *roles: Role) -> bool:
        return self.role in roles


def principal_from_token(token: str) -> Principal:
    claims = verify(token, "access")
    return Principal(id=int(claims["sub"]), role=Role(claims["role"]),
                     ver=int(claims.get("ver", 0)), expires_at=claims.get("exp"))


async def current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    if creds is None:
        raise Unauthorized("Not authenticated")
    return await authenticated_principal(creds.credentials, session)


def require_roles(*roles: Role):
    """Dependency factory: `Depends(require_roles(Role.ADMIN))`."""

    async def dep(p: Principal = Depends(current_principal)) -> Principal:
        if p.role not in roles:
            raise Forbidden(f"Requires role: {', '.join(r.value for r in roles)}")
        return p

    return dep
