from uuid import UUID
import asyncio
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from autoposter.db import session_scope
from sqlalchemy.ext.asyncio import AsyncSession
from autoposter.db import get_db
from autoposter.deps import current_user, require_editor
from autoposter.models import User
from autoposter.services import top_posts, x_post_metrics

router = APIRouter(prefix='/analytics/top-posts-x', tags=['Top Posts X'])


async def _collect_in_background(days, token):
    try:
        async with session_scope() as db:
            await asyncio.wait_for(x_post_metrics.collect(db, days, token), timeout=1800)
    except Exception:
        # Keep error details out of the user-visible collection state.
        async with session_scope() as db:
            await x_post_metrics.fail(db, token)


@router.post('/collection/{scope}', status_code=202)
async def start_collection(scope: str, background: BackgroundTasks,
                           db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)):
    days = {'all': None, '180': 180, '30': 30}.get(scope, -1)
    if days == -1:
        raise HTTPException(422, 'Gültige Bereiche: all, 180, 30')
    token = await x_post_metrics.claim(db, days)
    background.add_task(_collect_in_background, days, token)
    return {'status': 'running', 'scope': scope}


@router.get('')
async def overview(db: AsyncSession = Depends(get_db), _: User = Depends(current_user)):
    return await top_posts.overview(db)


@router.post('/{post_id}/reuse')
async def release(post_id: UUID, channel_id: Optional[UUID] = None, db: AsyncSession = Depends(get_db), user: User = Depends(require_editor)):
    return await top_posts.release_post(db, post_id, user, target_channel_id=channel_id)
