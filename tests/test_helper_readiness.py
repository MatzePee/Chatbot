"""Installed OLD-helper boundary: never execute application-owned root code."""
import json
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_normal_gui_blocks_missing_installed_helper_before_staging(tmp_path, monkeypatch):
    from app import character_upgrade
    from autoposter.api import system
    from autoposter.deps import require_admin
    monkeypatch.setattr(character_upgrade, 'ROOT', tmp_path)
    api = FastAPI(); api.include_router(system.router, prefix='/api/v1')
    api.dependency_overrides[require_admin] = lambda: object()
    with TestClient(api) as client:
        result = client.get('/api/v1/system/character-upgrade')
        assert result.status_code == 200
        assert result.json().get('helper', {}).get('ready') is False
        assert 'Administrator' in result.json()['helper']['message']
        refused = client.post('/api/v1/system/character-upgrade', json={'name':'Cherry','confirmed':True})
        assert refused.status_code == 409, refused.text
    assert not (tmp_path/'data').exists()


def test_reads_actual_installed_version_and_trusted_service_contract(tmp_path, monkeypatch):
    import os, shutil, tempfile
    from app import helper_readiness as gate
    from deploy import repair_update
    # Safe root-owned ancestors, unlike world-writable /tmp.
    with tempfile.TemporaryDirectory(prefix='helper-readiness-', dir='/run') as directory:
        system = Path(directory)
        wrapper=system/'fanvue-admin'; supervisor=system/'supervisor'
        installation=repair_update.Installation('fixture.service','nobody',tmp_path,8000)
        files=repair_update.rendered_files(installation)
        wrapper.write_text(files[repair_update.WRAPPER][0]);wrapper.chmod(0o755)
        supervisor.write_text(files[repair_update.SUPERVISOR][0]);supervisor.chmod(0o755)
        monkeypatch.setattr(gate,'WRAPPER',wrapper);monkeypatch.setattr(gate,'SUPERVISOR',supervisor)
        calls=[]
        def boundary(args,**kwargs):
            calls.append((args,kwargs))
            return __import__('subprocess').CompletedProcess(args,0,json.dumps({'ready':True}), '')
        monkeypatch.setattr(__import__('subprocess'),'run',boundary)
        ready=gate.readiness(tmp_path)
        assert ready['installed_version']==4,ready
        assert ready['ready'] is True,ready
        assert calls and '-I' in calls[0][0]
        assert calls[0][1]['pass_fds']
        assert not any(k.startswith(('MP_','PYTHON')) for k in calls[0][1]['env'])
        supervisor.write_text(supervisor.read_text().replace('HELPER_VERSION = 4','HELPER_VERSION = 3'))
        calls.clear()
        old=gate.readiness(tmp_path)
        assert old['ready'] is False and old['installed_version']==3,old
        assert not calls,'Old helper must not run a readiness command it does not implement'
        supervisor.chmod(0o777)
        assert gate.readiness(tmp_path)['ready'] is False
        supervisor.chmod(0o755);os.chown(supervisor,65534,65534)
        assert gate.readiness(tmp_path)['ready'] is False
        os.chown(supervisor,0,0);supervisor.rename(system/'real');supervisor.symlink_to(system/'real')
        assert gate.readiness(tmp_path)['ready'] is False
