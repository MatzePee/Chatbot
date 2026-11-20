"""Synthetic multi-character boundaries. No lifespan, providers or real data."""
import uuid
import pytest
from sqlalchemy import select
from autoposter import models


def test_character_is_workspace_not_persona():
    assert hasattr(models, 'Character'), 'Missing independent Character workspace root'
    assert models.Character.__table__ is not models.Persona.__table__
    assert models.Character.__table__.c.name.nullable is False
    for name in ('Persona','Channel','MediaAsset','MediaSet','SavedView','Post',
                 'ChannelCredential','MediaAssignment','MediaSetItem','Notification','OAuthState'):
        col = getattr(models,name).__table__.c.get('character_id')
        assert col is not None, name
        assert col.nullable is False, name
        assert any(fk.target_fullname == 'character.id' for fk in col.foreign_keys), name


@pytest.mark.asyncio
async def test_scoped_crud_hides_foreign_ids_and_new_rows_inherit_owner(db):
    from autoposter.workspaces import character_session
    a, b = models.Character(name='Alpha'), models.Character(name='Beta')
    db.add_all([a,b]); await db.commit()
    async with character_session(db.bind, a.id) as first:
        view = models.SavedView(name='Only Alpha')
        first.add(view); await first.commit()
        view_id = view.id
        assert view.character_id == a.id
    async with character_session(db.bind, b.id) as second:
        assert await second.get(models.SavedView, view_id) is None
        assert (await second.scalars(select(models.SavedView))).all() == []
        other = models.SavedView(name='Only Beta')
        second.add(other); await second.commit()
    async with character_session(db.bind, a.id) as first:
        assert [v.name for v in await first.scalars(select(models.SavedView))] == ['Only Alpha']


@pytest.mark.asyncio
async def test_scope_rejects_cross_character_references_and_bulk_reassignment(db):
    from autoposter.workspaces import character_session, CharacterBoundaryError
    from sqlalchemy import update, delete, text
    a,b = models.Character(name='Alpha'), models.Character(name='Beta')
    db.add_all([a,b]); await db.commit()
    async with character_session(db.bind, a.id) as first:
        channel=models.Channel(platform='x',display_name='Alpha')
        first.add(channel);await first.commit()
        cid=channel.id
    async with character_session(db.bind, b.id) as second:
        with pytest.raises(CharacterBoundaryError):
            second.add(models.Post(channel_id=cid, type='text_only'))
            await second.flush()
        await second.rollback()
        result=await second.execute(update(models.Channel).values(display_name='hacked'))
        assert result.rowcount == 0
        result=await second.execute(delete(models.Channel).where(models.Channel.id==cid))
        assert result.rowcount == 0
        with pytest.raises(CharacterBoundaryError):
            await second.execute(text('select * from channel'))
        with pytest.raises(CharacterBoundaryError):
            await second.execute(update(models.Channel).values(character_id=b.id))


@pytest.mark.asyncio
async def test_same_media_bytes_allowed_per_character_and_paths_namespaced(db, tmp_path, monkeypatch):
    from autoposter.workspaces import character_session
    from autoposter.services import media
    monkeypatch.setattr(media.settings, 'media_root', tmp_path)
    a,b=models.Character(name='Alpha'),models.Character(name='Beta')
    db.add_all([a,b]);await db.commit()
    shared_id=uuid.uuid4()
    paths=[]
    for char in (a,b):
        async with character_session(db.bind,char.id) as session:
            asset=models.MediaAsset(filename='same.jpg',sha256='f'*64,storage_path='synthetic')
            session.add(asset);await session.flush()
            paths.append(media.asset_dir(shared_id))
            assert str(char.id) in str(paths[-1])
            thumb,file=media.public_urls(asset)
            assert f'character_id={char.id}' in file
            assert f'character_id={char.id}' in thumb
    assert paths[0] != paths[1]


@pytest.mark.asyncio
async def test_character_settings_and_memory_cache_do_not_cross(db):
    from autoposter.workspaces import character_session
    from autoposter.services import appconfig
    a,b=models.Character(name='Alpha'),models.Character(name='Beta')
    db.add_all([a,b]);await db.commit()
    async with character_session(db.bind,a.id) as first:
        first.add(models.AppSetting(key='private',value={'owner':'Alpha'}))
        await appconfig.set_many(first,{'auto_plan_enabled':True})
        await appconfig.refresh_snapshot(first)
        assert appconfig.current('auto_plan_enabled') is True
    async with character_session(db.bind,b.id) as second:
        assert await second.get(models.AppSetting,'private') is None
        assert appconfig.current('auto_plan_enabled') is False
        assert (await appconfig.get_all(second))['auto_plan_enabled'] is False
        second.add(models.AppSetting(key='private',value={'owner':'Beta'}))
        await second.commit()
    async with character_session(db.bind,a.id) as first:
        assert (await first.get(models.AppSetting,'private')).value=={'owner':'Alpha'}
        assert appconfig.current('auto_plan_enabled') is True


@pytest.mark.asyncio
async def test_fresh_database_supports_legacy_ownership_with_foreign_keys(tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from autoposter.db import Base
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'fresh.db'))
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql('PRAGMA foreign_keys=ON')
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as session:
            channel=models.Channel(platform='x',display_name='Existing workspace')
            session.add(channel);await session.flush()
            assert channel.character_id==models.LEGACY_CHARACTER_ID
            legacy=await session.get(models.Character,models.LEGACY_CHARACTER_ID)
            assert legacy and legacy.name=='Existing workspace'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_library_counts_cache_is_partitioned_by_character(db):
    from autoposter.workspaces import character_session
    from autoposter.services import library
    a,b=models.Character(name='Alpha'),models.Character(name='Beta');db.add_all([a,b]);await db.commit()
    async with character_session(db.bind,a.id) as session:
        session.add(models.MediaAsset(filename='Alpha.jpg',storage_path='a',sha256='d'*64))
        await session.flush()
        assert (await library.lifecycle_counts(session)).total==1
    async with character_session(db.bind,b.id) as session:
        assert (await library.lifecycle_counts(session)).total==0


@pytest.mark.asyncio
async def test_scoped_session_cannot_be_reused_after_context_switch(db):
    from autoposter.workspaces import character_session,character_context,CharacterBoundaryError
    a,b=models.Character(name='Alpha'),models.Character(name='Beta');db.add_all([a,b]);await db.commit()
    async with character_session(db.bind,a.id) as session:
        view=models.SavedView(name='Alpha private');session.add(view);await session.flush()
        assert await session.get(models.SavedView,view.id) is view
        with character_context(b.id):
            with pytest.raises(CharacterBoundaryError):await session.get(models.SavedView,view.id)
            with pytest.raises(CharacterBoundaryError):await session.execute(select(models.SavedView))


@pytest.mark.asyncio
async def test_ordered_bulk_dml_cannot_reassign_owner(db):
    from autoposter.workspaces import character_session,CharacterBoundaryError
    from sqlalchemy import update
    a,b=models.Character(name='Alpha'),models.Character(name='Beta');db.add_all([a,b]);await db.commit()
    async with character_session(db.bind,a.id) as session:
        view=models.SavedView(name='Alpha private');session.add(view);await session.flush()
        with pytest.raises(CharacterBoundaryError):
            await session.execute(update(models.SavedView).ordered_values((models.SavedView.character_id,b.id)))
