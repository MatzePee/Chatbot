import pytest
from sqlalchemy import select
from autoposter import models
from autoposter.workspaces import WorkspaceAsyncSession,character_context,CharacterBoundaryError

@pytest.mark.asyncio
async def test_context_switch_cannot_commit_already_flushed_writes(db):
    a,b=models.Character(name='Alpha'),models.Character(name='Beta');db.add_all([a,b]);await db.commit()
    with character_context(a.id):
        async with WorkspaceAsyncSession(db.bind,expire_on_commit=False) as s:
            s.add(models.AppSetting(key='flushed-proof',value={}))
            await s.flush()
            with character_context(b.id):
                with pytest.raises(CharacterBoundaryError,match='context changed'):
                    await s.commit()
            await s.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['dirty','new','deleted','setting-new','setting-dirty','setting-deleted'])
@pytest.mark.parametrize('operation',['flush','commit'])
async def test_context_switch_rejects_all_orm_writes(db,kind,operation):
    a,b=models.Character(name='Alpha'),models.Character(name='Beta');db.add_all([a,b]);await db.commit()
    with character_context(a.id):
        async with WorkspaceAsyncSession(db.bind,expire_on_commit=False) as s:
            view=models.SavedView(name='Original');setting=models.AppSetting(key='proof',value={'original':True})
            s.add_all([view,setting]);await s.commit()
            vid=view.id
            if kind=='deleted':await s.delete(view)
            if kind=='setting-deleted':await s.delete(setting)
            with character_context(b.id):
                if kind=='dirty':view.name='must-not-persist'
                elif kind=='new':s.add(models.SavedView(name='must-not-persist'))
                elif kind=='setting-new':s.add(models.AppSetting(key='wrong-context-proof',value={}))
                elif kind=='setting-dirty':setting.value={'wrong':True}
                with pytest.raises(CharacterBoundaryError,match='context changed'):
                    await getattr(s,operation)()
            await s.rollback()
    assert (await db.get(models.SavedView,vid)).name=='Original'
    with character_context(a.id):
        async with WorkspaceAsyncSession(db.bind) as s:
            assert (await s.get(models.AppSetting,'proof')).value == {'original':True}
            assert await s.get(models.AppSetting,'wrong-context-proof') is None
    with character_context(b.id):
        async with WorkspaceAsyncSession(db.bind) as s:
            assert await s.get(models.AppSetting,'wrong-context-proof') is None
