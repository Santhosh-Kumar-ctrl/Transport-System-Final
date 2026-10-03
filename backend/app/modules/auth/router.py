from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import throttle
from app.core.db import get_session
from app.core.errors import Unauthorized
from app.core.deps import Principal, current_principal, require_roles
from app.core.roles import Role
from app.modules.auth import service
from app.modules.auth.schemas import LoginIn, RefreshIn, TokenPair, UserCreate, UserOut, UserUpdate

router = APIRouter(tags=["auth"])
admin_only = require_roles(Role.ADMIN)


@router.post("/auth/login", response_model=TokenPair)
async def login(request: Request, body: LoginIn, session: AsyncSession = Depends(get_session)):
    ip = request.client.host if request.client else "unknown"
    throttle.check(body.email, ip)
    try:
        result = await service.login(session, body.email, body.password)
    except Unauthorized:
        throttle.record_failure(body.email, ip)
        raise
    await session.commit()
    throttle.record_success(body.email)
    return result


@router.post("/auth/refresh", response_model=TokenPair)
async def refresh(body: RefreshIn, session: AsyncSession = Depends(get_session)):
    result = await service.refresh(session, body.refresh_token)
    await session.commit()
    return result


@router.get("/auth/me", response_model=UserOut)
async def me(p: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    return await service.get_user(session, p.id)


@router.get("/users", response_model=list[UserOut])
async def list_users(
    role: Role | None = None,
    q: str | None = None,
    active: bool | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    _: Principal = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_users(session, role=role, q=q, active=active, limit=limit, offset=offset)


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreate, p: Principal = Depends(admin_only), session: AsyncSession = Depends(get_session)
):
    user = await service.create_user(session, body, actor_id=p.id)
    await session.commit()
    return user


@router.get("/users/{user_id}", response_model=UserOut)
async def get_user(
    user_id: int, _: Principal = Depends(admin_only), session: AsyncSession = Depends(get_session)
):
    return await service.get_user(session, user_id)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UserUpdate,
    p: Principal = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
):
    user = await service.update_user(session, user_id, body, actor_id=p.id)
    await session.commit()
    return user
