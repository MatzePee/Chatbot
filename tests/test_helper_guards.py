"""Service-bound authority and destructive-phase guards."""
import os
from pathlib import Path
import pwd
import sqlite3

import pytest
from deploy import creatorstudio_update as helper


@pytest.mark.parametrize('fault',['running','wrong-user','wrong-repo','writer','symlink','root-owner'])
def test_stopped_authority_rejects_unsafe_source_before_child(tmp_path,monkeypatch,fault):
    user=pwd.getpwnam('nobody')
    # Only disposable fixture files; no real systemctl invocation.
    repo=tmp_path/'repo';source=repo/'data';source.mkdir(parents=True)
    os.chown(repo,user.pw_uid,user.pw_gid);os.chown(source,user.pw_uid,user.pw_gid)
    (source/'bot.db').write_bytes(b'original sentinel')
    original=(source/'bot.db').read_bytes()
    from tests.test_helper_environment import service_properties
    props=dict(line.split('=',1) for line in service_properties(repo).splitlines())
    if fault=='running':props['MainPID']='9999'
    if fault=='wrong-user':props['User']='root'
    if fault=='wrong-repo':props['WorkingDirectory']='/unknown-installation'
    if fault=='symlink':(source/'linked').symlink_to('/etc/hosts')
    if fault=='root-owner':os.chown(source,0,0)
    def boundary(args,**kwargs):
        assert args[0]=='systemctl' and args[1]=='show'
        return '' if '--value' in args else '\n'.join(k+'='+v for k,v in props.items())
    monkeypatch.setattr(helper,'command',boundary)
    monkeypatch.setattr(helper.subprocess,'run',lambda *a,**k:pytest.fail('unsafe child was launched'))
    with (source/'bot.db').open('rb') if fault=='writer' else __import__('contextlib').nullcontext():
        with pytest.raises(RuntimeError):helper.stopped_migrate(repo,user.pw_name,'fixture.service',environment={})
    assert (source/'bot.db').read_bytes()==original
    assert not (source/'character-active.json').exists()
