from uuid import UUID
from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from autoposter.db import get_db
from autoposter.deps import current_user, require_editor
from autoposter.models import User
from autoposter.services import top_posts

router = APIRouter(prefix='/analytics/top-posts-x', tags=['Top Posts X'])


@router.get('')
async def overview(db: AsyncSession = Depends(get_db), _: User = Depends(current_user)):
    return await top_posts.overview(db)


@router.post('/{post_id}/reuse')
async def release(post_id: UUID, channel_id: Optional[UUID] = None, db: AsyncSession = Depends(get_db), user: User = Depends(require_editor)):
    return await top_posts.release_post(db, post_id, user, target_channel_id=channel_id)
