from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Principal, current_principal
from app.core.errors import NotFound
from app.modules.notifications import service
from app.modules.notifications.schemas import NotificationOut, UnreadCount

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationOut])
async def inbox(unread_only: bool = False, limit: int = Query(50, ge=1, le=200), before_id: int | None = None,
                p: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    return await service.list_for_user(session, p.id, unread_only=unread_only, limit=limit, before_id=before_id)


@router.get("/unread-count", response_model=UnreadCount)
async def unread(p: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    return UnreadCount(unread=await service.unread_count(session, p.id))


@router.post("/{notification_id}/read", response_model=UnreadCount)
async def mark_read(notification_id: int, p: Principal = Depends(current_principal),
                    session: AsyncSession = Depends(get_session)):
    if not await service.mark_read(session, p.id, notification_id):
        raise NotFound("Notification not found or already read")
    await session.commit()
    return UnreadCount(unread=await service.unread_count(session, p.id))


@router.post("/read-all", response_model=UnreadCount)
async def mark_all(p: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    await service.mark_read(session, p.id)
    await session.commit()
    return UnreadCount(unread=0)
