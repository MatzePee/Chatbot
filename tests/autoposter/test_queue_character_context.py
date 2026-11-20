"""Real queue reject handler + middleware + isolated per-character SQLite stores."""
import pytest
from httpx import ASGITransport,AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from autoposter import db as postdb,models
from autoposter.workspaces import WorkspaceAsyncSession,character_context
from app import db as chatdb,main
from urllib.parse import urlsplit,parse_qs


@pytest.mark.asyncio
async def test_queue_reject_redirect_preserves_owner_and_other_drafts(db,monkeypatch):
    owners=[models.Character(name=f'Synthetic {i}') for i in range(6)]
    db.add_all(owners);await db.commit()
    monkeypatch.setattr(postdb,'SessionLocal',async_sessionmaker(db.bind,class_=WorkspaceAsyncSession,expire_on_commit=False))
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    monkeypatch.setattr(main.fanvue,'is_connected',lambda:False)
    draft_ids={}
    for owner in owners:
        with character_context(owner.id):
            draft_ids[owner.id]=chatdb.create_draft(user_uuid='synthetic-fan',status='pending',generated_text='Synthetic draft')
    assert len(set(draft_ids.values()))==1,'Exercise colliding draft IDs in distinct owner DBs'
    async with AsyncClient(transport=ASGITransport(app=main.app),base_url='http://test') as client:
        missing=await client.post(f'/queue/{draft_ids[owners[0].id]}/reject')
        assert missing.status_code==409
        for i,owner in enumerate(owners):
            response=await client.post(f'/queue/{draft_ids[owner.id]}/reject?character_id={owner.id}')
            assert response.status_code==303
            location=response.headers['location'];assert urlsplit(location).path=='/queue'
            assert parse_qs(urlsplit(location).query)['character_id']==[str(owner.id)]
            returned=await client.get(location)
            assert returned.status_code==200 and 'Character URL context required' not in returned.text
            for j,peer in enumerate(owners):
                with character_context(peer.id):
                    assert chatdb.get_draft(draft_ids[peer.id])['status']==('rejected' if j<=i else 'pending')
