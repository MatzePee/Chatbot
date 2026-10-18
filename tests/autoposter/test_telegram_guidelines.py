import json
import pytest
from unittest.mock import AsyncMock
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from autoposter.db import Base
from autoposter.models import PostingPolicy
from autoposter.schemas import ChannelUpdate, PolicyBase
from autoposter.api.channels import update_channel
from autoposter.services.llm import llm
from autoposter import schema_sync
from .test_telegram_pipeline import setup
from .test_telegram import no_network

@pytest.mark.asyncio
async def test_telegram_own_text_guidelines_are_saved_and_used_for_generation(db,monkeypatch):
    channel,_,_=await setup(db)
    policy=PolicyBase(text_guidelines='Nur hier: stelle am Ende eine konkrete Kanalfrage.')
    out=await update_channel(channel.id,ChannelUpdate(policy=policy),db,None)
    assert out.policy.text_guidelines=='Nur hier: stelle am Ende eine konkrete Kanalfrage.'
    request=AsyncMock(return_value={'choices':[{'message':{'content':json.dumps({'variants':[{'text':'Ein eigener Text.','translation_de':'Ein eigener Text.','hashtags':[]}]})},'finish_reason':'stop'}]})
    monkeypatch.setattr(llm,'_call',request)
    await llm.generate_post_text(db,persona=None,platform='telegram',channel_id=channel.id,image_descriptions=[],has_image=False,max_chars=4096)
    assert channel.policy.text_guidelines in request.await_args.kwargs['messages'][1]['content']
    await llm.generate_post_text(db,persona=None,platform='x',channel_id=channel.id,image_descriptions=[],has_image=False,max_chars=280)
    assert channel.policy.text_guidelines not in request.await_args.kwargs['messages'][1]['content']

@pytest.mark.asyncio
async def test_additive_guidelines_migration_keeps_every_existing_policy_value(tmp_path):
    assert 'text_guidelines' in PostingPolicy.__table__.columns
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'old.db'))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from sqlalchemy.ext.asyncio import async_sessionmaker
    async with async_sessionmaker(engine)() as session:
        session.add(PostingPolicy(posts_per_day=7,auto_approve=False,allowed_time_windows=[],text_guidelines=''))
        await session.commit()
    async with engine.begin() as conn:
        await conn.execute(text('ALTER TABLE posting_policy DROP COLUMN text_guidelines'))
        before=dict((await conn.execute(text('SELECT * FROM posting_policy'))).mappings().one())
    report=await schema_sync.sync(engine)
    assert report['failed']==[] and 'posting_policy.text_guidelines' in report['added_columns']
    async with engine.connect() as conn:
        after=dict((await conn.execute(text('SELECT * FROM posting_policy'))).mappings().one())
    assert after.pop('text_guidelines')=='' and after==before
    await engine.dispose()
