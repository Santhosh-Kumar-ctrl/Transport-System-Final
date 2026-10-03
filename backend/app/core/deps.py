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
    """The authenticated caller; request authorization also checks current DB state."""

    id: int
    role: Role

    def is_(self, *roles: Role) -> bool:
        return self.role in roles


def principal_from_token(token: str) -> Principal:
    claims = verify(token, "access")
    try:
        return Principal(id=int(claims["sub"]), role=Role(claims["role"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise Unauthorized("Invalid token", code="token_invalid") from exc


async def authenticated_principal(token: str, session: AsyncSession) -> Principal:
    from app.modules.auth.models import User
    claims = verify(token, "access")
    p = principal_from_token(token)
    user = await session.get(User, p.id, populate_existing=True)
    if (user is None or not user.is_active or user.role != p.role
            or claims.get("ver") != user.token_version):
        raise Unauthorized("Session revoked", code="token_invalid")
    return p


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
