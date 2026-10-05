"""Auth & users. Public API for other modules: get_user, get_users, briefs, admin_ids,
ensure_role."""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.db import flush_or_conflict
from app.core.deps import Principal
from app.core.errors import Conflict, InvalidState, NotFound, Unauthorized
from app.core.roles import Role
from app.core.security import (
    create_access_token,
    create_refresh_token,
    dummy_hash,
    hash_password_async,
    verify,
    verify_password_async,
)
from app.modules.auth.models import DriverProfile, RefreshSession, StudentProfile, User
from app.modules.auth.schemas import TokenPair, UserBrief, UserCreate, UserOut, UserUpdate


# Verified against when the email is unknown, so response time does not reveal which emails exist.
_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _tokens(session: AsyncSession, user: User) -> TokenPair:
    refresh_token = create_refresh_token(user.id, user.token_version)
    session.add(RefreshSession(token_digest=_digest(refresh_token), user_id=user.id,
                               expires_at=now_utc() + timedelta(days=settings.refresh_token_days)))
    return TokenPair(
        access_token=create_access_token(user.id, user.role.value, user.token_version),
        refresh_token=create_refresh_token(user.id, user.token_version),
        user=UserOut.model_validate(user),
    )


async def login(session: AsyncSession, email: str, password: str) -> TokenPair:
    user = await session.scalar(select(User).where(User.email == email.lower()))
    # End the read transaction first: the pooled DB connection goes back while bcrypt runs, so a
    # wave of logins doesn't starve other requests of connections.
    await session.commit()
    ok = await verify_password_async(password, user.password_hash if user else dummy_hash())
    if user is None or not ok:
        raise Unauthorized("Incorrect email or password", code="bad_credentials")
    if not user.is_active:
        raise Unauthorized("Account is deactivated", code="inactive")
    return _tokens(session, user)


async def refresh(session: AsyncSession, refresh_token: str) -> TokenPair:
    claims = verify(refresh_token, "refresh")
    try:
        user_id = int(claims["sub"])
    except (KeyError, ValueError, TypeError) as exc:
        raise Unauthorized("Invalid token", code="token_invalid") from exc
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.is_active or claims.get("ver") != user.token_version:
        raise Unauthorized("Account unavailable", code="inactive")
    if int(claims.get("ver", 0)) != user.token_version:
        raise Unauthorized("This session has ended. Sign in again.", code="session_revoked")
    return _tokens(user)


async def session_valid(session: AsyncSession, p: Principal) -> bool:
    """The account behind an access token is still active and the token wasn't revoked."""
    user = await session.get(User, p.id)
    return user is not None and user.is_active and user.token_version == p.ver


async def get_user(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound(f"User {user_id} not found")
    return user


async def ensure_role(session: AsyncSession, user_id: int, role: Role) -> User:
    user = await get_user(session, user_id)
    if user.role != role:
        raise InvalidState(f"User {user_id} is not a {role.value}", code="wrong_role")
    if not user.is_active:
        raise InvalidState(f"User {user_id} is deactivated", code="inactive")
    return user


async def get_users(session: AsyncSession, user_ids: list[int]) -> dict[int, User]:
    if not user_ids:
        return {}
    rows = await session.scalars(select(User).where(User.id.in_(set(user_ids))))
    return {u.id: u for u in rows}


def brief(user: User) -> UserBrief:
    return UserBrief(
        id=user.id,
        full_name=user.full_name,
        role=user.role,
        phone=user.phone,
        roll_no=user.student.roll_no if user.student else None,
    )


async def briefs(session: AsyncSession, user_ids: list[int]) -> dict[int, UserBrief]:
    return {uid: brief(u) for uid, u in (await get_users(session, user_ids)).items()}


async def admin_ids(session: AsyncSession) -> list[int]:
    rows = await session.scalars(
        select(User.id).where(User.role == Role.ADMIN, User.is_active.is_(True))
    )
    return list(rows)


async def find_student_by_roll_no(session: AsyncSession, roll_no: str) -> User:
    user = await session.scalar(
        select(User).join(StudentProfile, StudentProfile.user_id == User.id)
        .where(StudentProfile.roll_no == roll_no)
    )
    if user is None:
        raise NotFound(f"No student with roll no {roll_no}")
    return user


async def list_users(
    session: AsyncSession,
    *,
    role: Role | None = None,
    q: str | None = None,
    active: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[User]:
    stmt = select(User).outerjoin(StudentProfile, StudentProfile.user_id == User.id)
    if role:
        stmt = stmt.where(User.role == role)
    if active is not None:
        stmt = stmt.where(User.is_active.is_(active))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                User.full_name.ilike(like),
                User.email.ilike(like),
                StudentProfile.roll_no.ilike(like),
            )
        )
    stmt = stmt.order_by(User.full_name).limit(limit).offset(offset)
    return list(await session.scalars(stmt))


async def create_user(session: AsyncSession, data: UserCreate, *, actor_id: int | None) -> User:
    email = data.email.lower()
    if await session.scalar(select(User.id).where(User.email == email)):
        raise Conflict(f"Email {email} already registered", code="email_taken")
    if data.student and await session.scalar(
        select(StudentProfile.user_id).where(StudentProfile.roll_no == data.student.roll_no)
    ):
        raise Conflict(f"Roll no {data.student.roll_no} already exists", code="roll_no_taken")
    if data.driver and await session.scalar(
        select(DriverProfile.user_id).where(DriverProfile.license_no == data.driver.license_no)
    ):
        raise Conflict(f"License {data.driver.license_no} already exists", code="license_taken")

    user = User(
        email=email,
        password_hash=await hash_password_async(data.password),
        full_name=data.full_name,
        phone=data.phone,
        role=data.role,
    )
    # Always assign both so the attributes count as loaded (no async lazy-load later).
    user.student = StudentProfile(**data.student.model_dump()) if data.student else None
    user.driver = DriverProfile(**data.driver.model_dump()) if data.driver else None
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:  # concurrent create slipped past the checks above; DB constraints win
        raise Conflict("Email, roll no or license already registered", code="duplicate") from exc
    await events.publish(
        session, "UserCreated", {"user_id": user.id, "role": user.role.value},
        aggregate=("user", user.id), actor_id=actor_id,
    )
    return user


async def update_user(
    session: AsyncSession, user_id: int, data: UserUpdate, *, actor_id: int | None
) -> User:
    user = await get_user(session, user_id)
    revoke = False
    if data.full_name is not None:
        user.full_name = data.full_name
    if data.phone is not None:
        user.phone = data.phone
    if data.password is not None:
        user.password_hash = await hash_password_async(data.password)
        revoke = True
    if data.student is not None:
        if user.role != Role.STUDENT:
            raise InvalidState("Not a student", code="wrong_role")
        for k, v in data.student.model_dump().items():
            setattr(user.student, k, v)
    if data.driver is not None:
        if user.role != Role.DRIVER:
            raise InvalidState("Not a driver", code="wrong_role")
        for k, v in data.driver.model_dump().items():
            setattr(user.driver, k, v)
    if data.is_active is not None and data.is_active != user.is_active:
        if not data.is_active and user.role == Role.ADMIN and await _active_admin_count(session) <= 1:
            raise InvalidState("Can't deactivate the last active admin", code="last_admin")
        user.is_active = data.is_active
        revoke = revoke or not data.is_active
        await events.publish(
            session, "UserActivated" if data.is_active else "UserDeactivated",
            {"user_id": user.id, "role": user.role.value},
            aggregate=("user", user.id), actor_id=actor_id,
        )
    if revoke:
        user.token_version += 1
        await events.publish(session, "UserSessionsRevoked", {"user_id": user.id},
                             aggregate=("user", user.id), actor_id=actor_id)
    await flush_or_conflict(session, "That roll no or licence no is already used by another user",
                            code="profile_taken")
    return user


async def _active_admin_count(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count()).select_from(User).where(User.role == Role.ADMIN, User.is_active.is_(True))
    )
