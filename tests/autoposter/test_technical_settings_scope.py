"""Global infrastructure is shared deliberately, never account/content state."""
import uuid
import pytest
import pytest_asyncio


@pytest_asyncio.fixture(name='db')
async def atomic_settings_db():
    # Canonical and ORM settings in one file: exercises the supported atomic
    # API rather than the old independently committing :memory: + bot.db pair.
    from app import db as chat
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from autoposter.db import Base
    engine = create_async_engine('sqlite+aiosqlite:///' + chat.DB_PATH)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_control_plane_uses_same_canonical_technical_settings(db, monkeypatch):
    from app import db as chat
    from autoposter.services import appconfig
    from autoposter.workspaces import control_context
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    chat.set_setting('openrouter_api_key', 'synthetic-control-original')
    with control_context():
        assert (await appconfig.get_all(db, use_cache=False))['openrouter_api_key'] == 'synthetic-control-original'
        await appconfig.set_many(db, {'openrouter_api_key': 'synthetic-control-new'})
        await db.commit()
        assert chat.get_setting('openrouter_api_key') == 'synthetic-control-new'
        assert appconfig.current('openrouter_api_key') == 'synthetic-control-new'
        await appconfig.clear_secret(db, 'openrouter_api_key')
        await db.commit()
        assert chat.technical_setting('openrouter_api_key') == (True, '')
from app import db
from autoposter.workspaces import character_context


@pytest.mark.asyncio
async def test_control_plane_technical_config_never_reads_character_accounts(db, monkeypatch):
    from autoposter.services import appconfig, chatbot_fanvue
    from autoposter.workspaces import control_context
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    def forbidden():
        raise AssertionError('Control plane attempted account configuration read')
    monkeypatch.setattr(chatbot_fanvue, 'configuration', forbidden)
    with control_context():
        for cached in (False, True):
            values = await appconfig.get_all(db, use_cache=cached)
            assert values['fanvue_client_secret'] == ''
            assert values['x_client_secret'] == ''
        assert appconfig.current('fanvue_client_secret') is None
        assert appconfig.current('x_client_secret') is None


@pytest.mark.parametrize('key', ['openrouter_api_key', 'vision_api_key', 'github_token', 'telegram_bot_token'])
def test_technical_secrets_encrypted_at_rest_and_readable_after_reopen(key):
    import json
    from autoposter.crypto import decrypt
    secret = 'synthetic-at-rest-' + key
    db.set_setting(key, secret)
    raw = db._technical_settings_conn().execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()[0]
    assert secret not in raw
    envelope = json.loads(raw)
    assert envelope['format'] == 'technical-secret-v1'
    assert decrypt(envelope['ciphertext']) == secret
    db._conn.close()
    db._conn = None
    assert db.get_setting(key) == secret
    assert db.technical_setting(key) == (True, secret)
    assert db.all_settings()[key] == secret
    db.set_setting(key, '')
    assert db.technical_setting(key) == (True, '')


def test_technical_overlay_includes_keys_absent_from_autochat_defaults():
    db.set_setting('openrouter_model_caption', 'synthetic/shared-caption-model')
    with character_context(uuid.uuid4()):
        assert db.all_settings()['openrouter_model_caption'] == 'synthetic/shared-caption-model'


def test_autochat_technical_secrets_are_global_but_accounts_and_content_are_owned():
    a,b=uuid.uuid4(),uuid.uuid4()
    db.set_setting('openrouter_api_key','synthetic-global-llm')
    db.set_setting('git_remote_url','https://example.invalid/project')
    with character_context(a):
        assert db.get_setting('openrouter_api_key')=='synthetic-global-llm'
        db.set_setting('fanvue_client_secret','synthetic-alpha-account')
        db.set_setting('system_prompt','Alpha private')
        db.set_setting('openrouter_api_key','synthetic-updated-llm')
    with character_context(b):
        assert db.get_setting('openrouter_api_key')=='synthetic-updated-llm'
        assert db.get_setting('git_remote_url')=='https://example.invalid/project'
        assert db.get_setting('fanvue_client_secret')==''
        assert db.get_setting('system_prompt')!='Alpha private'
        assert db.all_settings()['openrouter_api_key']=='synthetic-updated-llm'
    assert db.get_setting('openrouter_api_key')=='synthetic-updated-llm'


@pytest.mark.asyncio
async def test_autopost_and_autochat_share_canonical_technical_settings_and_clear(db):
    from app import db as chat
    from autoposter.models import Character
    from autoposter.workspaces import character_session
    from autoposter.services import appconfig
    a,b=Character(name='Alpha'),Character(name='Beta');db.add_all([a,b]);await db.commit()
    chat.set_setting('openrouter_api_key','synthetic-shared')
    async with character_session(db.bind,a.id) as first:
        assert (await appconfig.get_all(first))['openrouter_api_key']=='synthetic-shared'
        await appconfig.set_many(first,{'openrouter_api_key':'synthetic-updated','auto_plan_enabled':True})
    async with character_session(db.bind,b.id) as second:
        values=await appconfig.get_all(second)
        assert values['openrouter_api_key']=='synthetic-updated'
        assert values['auto_plan_enabled'] is False
        assert chat.get_setting('openrouter_api_key')=='synthetic-updated'
        await appconfig.set_many(second,{'openrouter_api_key':'********'})
        assert chat.get_setting('openrouter_api_key')=='synthetic-updated'
        # Independent AutoChat writes are separate transactions, not part of
        # the caller's still-pending AutoPost save.
        await second.commit()
        chat.set_setting('openrouter_api_key','synthetic-new-global')
        assert appconfig.current('openrouter_api_key')=='synthetic-new-global'
        await appconfig.clear_secret(second,'openrouter_api_key')
    async with character_session(db.bind,a.id) as first:
        assert (await appconfig.get_all(first))['openrouter_api_key']==''
        assert chat.get_setting('openrouter_api_key')==''


@pytest.mark.asyncio
async def test_global_technical_shells_do_not_read_character_account_or_drafts(monkeypatch):
    from app.main import app
    from app import fanvue
    from httpx import AsyncClient,ASGITransport
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    def forbidden():
        raise AssertionError('A global technical page must not read any character account')
    monkeypatch.setattr(fanvue,'is_connected',forbidden)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        for path in ['/system','/upload']:
            result=await client.get(path)
            assert result.status_code==200,result.text


@pytest.mark.asyncio
async def test_existing_autopost_global_settings_are_not_lost_or_account_credentials_inherited(db):
    from autoposter.models import Character,AppSetting
    from autoposter.workspaces import character_session
    from autoposter.services import appconfig
    from autoposter.crypto import encrypt
    a,b=Character(name='Alpha'),Character(name='Beta')
    db.add_all([a,b,AppSetting(key='integrations',value={
        'openrouter_api_key':encrypt('synthetic-existing-global'),
        'x_client_secret':encrypt('synthetic-private-legacy-account'),
    })]);await db.commit()
    async with character_session(db.bind,b.id) as session:
        values=await appconfig.get_all(session,use_cache=False)
        assert values['openrouter_api_key']=='synthetic-existing-global'
        assert not values['x_client_secret']
        await appconfig.clear_secret(session,'openrouter_api_key')
        assert (await appconfig.get_all(session,use_cache=False))['openrouter_api_key']==''


@pytest.mark.asyncio
async def test_shared_form_explicit_clear_does_not_restore_legacy_secret(db):
    from app import db as chat
    from autoposter.models import Character,AppSetting
    from autoposter.workspaces import character_session
    from autoposter.services import appconfig
    from autoposter.crypto import encrypt
    owner=Character(name='Alpha')
    db.add_all([owner,AppSetting(key='integrations',value={
        'openrouter_api_key':encrypt('synthetic-preserved-old')})]);await db.commit()
    chat.set_setting('openrouter_api_key','synthetic-replacement')
    chat.set_setting('openrouter_api_key','')
    async with character_session(db.bind,owner.id) as session:
        assert (await appconfig.get_all(session,use_cache=False))['openrouter_api_key']==''
        assert not any(key.startswith('__technical_cleared__:') for key in chat.all_settings())
