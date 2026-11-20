import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import time

import pytest
from app.sqlite_layout import stopped_authority
from deploy import creatorstudio_update as helper


@pytest.mark.parametrize('fault',['writable','linked','expired','wrong-owner','wrong-parent','wrong-scope'])
def test_invalid_root_attestation_cannot_authorize_inaccessible_proc(tmp_path,fault):
    user=pwd.getpwnam('nobody')
    # Source dir must be traversable to the actual non-root process.
    with tempfile.TemporaryDirectory(prefix='authority-') as directory:
        source=Path(directory);source.chmod(0o755)
        path=source/'proof'
        value={'parent':os.getpid(),'uid':user.pw_uid,'source':str(source),'created':time.monotonic()}
        if fault=='expired':value['created']-=1201
        if fault=='wrong-owner':value['uid']=12345
        if fault=='wrong-parent':value['parent']=1
        if fault=='wrong-scope':value['source']=str(source/'other')
        path.write_text(json.dumps(value));path.chmod(0o400)
        fd=os.open(path,os.O_RDWR if fault=='writable' else os.O_RDONLY)
        if fault!='linked':path.unlink()
        try:
            result=subprocess.run([sys.executable,'-c',
                'from pathlib import Path;from app.sqlite_layout import stopped_authority;'
                'assert not stopped_authority(Path('+repr(str(source))+'))'],
                env={**os.environ,'MP_STOPPED_SOURCE_FD':str(fd)},pass_fds=(fd,),
                user=user.pw_uid,group=user.pw_gid,extra_groups=[],capture_output=True,text=True)
            assert result.returncode==0,result.stderr
        finally:os.close(fd)


@pytest.mark.parametrize('fault',[subprocess.TimeoutExpired('migration',1200),OSError('synthetic launch failure')])
def test_preimport_launch_failure_retains_originals(tmp_path,monkeypatch,fault):
    from app import character_upgrade as upgrade
    (tmp_path/'data').mkdir()
    original=b'original unopened synthetic database'
    (tmp_path/'data/bot.db').write_bytes(original)
    upgrade.stage(tmp_path,name='Cherry',confirmed=True)
    monkeypatch.setattr(upgrade,'_LOCK',None)
    def failed(*a,**k):raise fault
    monkeypatch.setattr(upgrade.subprocess,'run',failed)
    upgrade.bootstrap(tmp_path)
    assert upgrade.status(tmp_path)['phase']=='failed'
    assert (tmp_path/'data/bot.db').read_bytes()==original
    assert not (tmp_path/'data/character-active.json').exists()
    import fcntl
    with (tmp_path/'.character-upgrade.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)

def test_nonroot_cannot_create_supervisor_authority(monkeypatch,tmp_path):
    monkeypatch.setattr(helper.os,'geteuid',lambda:65534)
    with pytest.raises(RuntimeError,match='root service supervisor'):
        helper.stopped_migrate(tmp_path,'nobody','fixture.service')
