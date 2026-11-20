"""Control-plane regression on real fresh/copy workspace output, no lifespan."""
import sqlite3
from types import SimpleNamespace
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware

@pytest.mark.asyncio
@pytest.mark.parametrize('fresh',[True,False])
async def test_control_handlers_real_workspace(tmp_path,monkeypatch,fresh):
    from tools.character_migrate import initialize_empty,build_workspace_copy,LEGACY_ID
    from app import db,main,publisher,updater
    from app.character_http import CharacterMiddleware
    from autoposter.workspaces import control_context
    initialize_empty(tmp_path/'seed',name='Cherry')
    target=tmp_path/'seed'
    if not fresh:
        # Genuine copy entry point, from a legacy DB with the actual full schema.
        source=tmp_path/'legacy.db'
        with sqlite3.connect(source) as c: c.executescript(db.SCHEMA)
        import subprocess,sys,os
        script=tmp_path/'baseline.py'
        script.write_text("import sys\nsys.path.insert(0, '/root/multi-character-activation/fresh-baseline')\nfrom sqlalchemy import create_engine\nfrom autoposter.db import Base\nfrom autoposter import models\ne=create_engine('sqlite:///'+sys.argv[1]);Base.metadata.create_all(e);e.dispose()\n")
        subprocess.run([sys.executable,'-B',str(script),str(tmp_path/'post.db')],cwd=tmp_path,check=True)
        (tmp_path/'media').mkdir()
        target=tmp_path/'copy'
        build_workspace_copy(tmp_path/'post.db',source,tmp_path/'media',target,name='Cherry')
    monkeypatch.setattr(db,'_conn',None)
    monkeypatch.setattr(db,'DATA_DIR',str(target));monkeypatch.setattr(db,'DB_PATH',str(target/'bot.db'))
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    from autoposter.config import settings
    from autoposter import db as post_db,models
    from autoposter.workspaces import WorkspaceAsyncSession
    from autoposter.security import create_token
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    engine=create_async_engine('sqlite+aiosqlite:///'+str(target/'autoposter.db'))
    factory=async_sessionmaker(engine,class_=WorkspaceAsyncSession,expire_on_commit=False)
    monkeypatch.setattr(post_db,'SessionLocal',factory)
    with control_context():
        async with factory(info={'control_plane':True}) as s:
            admin=models.User(email='admin@example.invalid',password_hash='synthetic-unused',role='admin')
            s.add(admin);await s.commit()
            token=create_token(str(admin.id))
    monkeypatch.setattr(settings,'auth_enabled',True)
    with control_context():
        db.log('info','upload','control proof')
        tables={r[0] for r in db.get_conn().execute("select name from sqlite_master where type='table'")}
        assert 'logs' in tables and not {'tokens','chats','drafts'} & tables
    app=FastAPI()
    for path,handler in [('/upload/settings',main.upload_settings),('/upload/publish',main.upload_publish),('/system/update',main.system_update),('/system/restart-service',main.system_restart)]:
        app.add_api_route(path,handler,methods=['POST'])
    @app.get('/upload/csrf')
    def csrf(request:Request):
        request.session['upload_csrf']='proof';return {'ok':True}
    app.add_middleware(CharacterMiddleware);app.add_middleware(SessionMiddleware,secret_key='synthetic')
    monkeypatch.setattr(main,'_run_admin',lambda action: None)
    monkeypatch.setattr(updater.subprocess,'run',lambda *a,**kw: SimpleNamespace(stdout=b'ok',stderr=b'',returncode=0))
    monkeypatch.setattr(publisher.updater,'is_git_checkout',lambda:True)
    monkeypatch.setattr(publisher,'guard',lambda:[])
    monkeypatch.setattr(publisher,'_askpass_env',lambda:({},None))
    monkeypatch.setattr(publisher,'_git',lambda *a,**kw:(True,'https://github.com/synthetic/repo.git' if a[:2]==('remote','get-url') else ''))
    async with AsyncClient(transport=ASGITransport(app=app,raise_app_exceptions=False),base_url='http://test') as c:
        assert (await c.post('/upload/settings',data={'upload_csrf':'proof'})).status_code == 401
        c.headers['authorization']='Bearer '+token
        await c.get('/upload/csrf')
        assert (await c.post('/upload/settings',data={'upload_csrf':'bad','git_branch':'bad'})).status_code == 403
        assert (await c.post('/upload/settings',data={'upload_csrf':'proof','git_branch':'proof-branch'})).status_code == 303
        for path in ('/upload/publish','/system/update','/system/restart-service'):
            response=await c.post(path,data={'upload_csrf':'proof'})
            assert response.status_code == 303, response.text
    with control_context():
        assert db.get_setting('git_branch') == 'proof-branch'
        assert len(db.list_logs()) >= 4
    db._conn.close()
    await engine.dispose()

@pytest.mark.asyncio
async def test_upload_audit_failure_rolls_back_all_settings(tmp_path,monkeypatch):
    from tools.character_migrate import initialize_empty
    from app import db,main
    from autoposter.workspaces import control_context
    initialize_empty(tmp_path/'workspace',name='Cherry')
    monkeypatch.setattr(db,'_conn',None);monkeypatch.setattr(db,'DATA_DIR',str(tmp_path/'workspace'));monkeypatch.setattr(db,'DB_PATH',str(tmp_path/'workspace/bot.db'))
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    class Request:
        session={'upload_csrf':'proof'}
        async def form(self):return {'upload_csrf':'proof','git_branch':'must-rollback','git_user_name':'also-rollback'}
    with control_context():
        c=db.get_conn()
        c.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON logs BEGIN SELECT RAISE(ABORT,'audit failure'); END")
        c.commit()
        with pytest.raises(sqlite3.IntegrityError):await main.upload_settings(Request())
        assert db.get_setting('git_branch') != 'must-rollback'
        assert db.get_setting('git_user_name') != 'also-rollback'
    db._conn.close()
