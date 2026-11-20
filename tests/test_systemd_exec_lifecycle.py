"""systemctl ExecStart lifecycle metadata is not configured argv."""
from pathlib import Path
import pytest
from deploy import creatorstudio_update as helper
from tests.test_helper_environment import service_properties

@pytest.mark.parametrize('change', ['lifecycle', 'argv', 'path', 'ignore_errors', 'unknown_static'])
def test_stopped_capture_ignores_only_exec_lifecycle_metadata(tmp_path, monkeypatch, change):
    executable = str(tmp_path / '.venv/bin/python')
    before = '{ path=' + executable + ' ; argv[]=' + executable + ' -m uvicorn app.main:app --port 8000 ; ignore_errors=no ; start_time=[Mon 2026-11-16 17:00:00 CET] ; stop_time=[n/a] ; pid=42 ; code=(null) ; status=0/0 }'
    after = before.replace('stop_time=[n/a]', 'stop_time=[Mon 2026-11-16 17:01:00 CET]').replace('code=(null)', 'code=exited')
    if change == 'argv':
        after = after.replace('--port 8000', '--port 8001')
    elif change == 'path':
        after = after.replace('path=' + executable, 'path=/tmp/other-python')
    elif change == 'ignore_errors':
        after = after.replace('ignore_errors=no', 'ignore_errors=yes')
    elif change == 'unknown_static':
        after = after.replace('ignore_errors=no', 'ignore_errors=no ; future_static=yes')
    current = {'text': service_properties(tmp_path, ExecStart=before, MainPID='42', ActiveState='active', SubState='running')}
    def boundary(args, **kwargs):
        assert args[:2] == ['systemctl', 'show']
        return '' if '--value' in args else current['text']
    monkeypatch.setattr(helper, 'command', boundary)
    properties = helper.service_contract(tmp_path, 'nobody', 'fixture.service')
    environment = helper.CapturedEnvironment({}, groups=(), properties=properties)
    environment.files = {'unit': 'unchanged'}
    monkeypatch.setattr(helper, 'configuration_files', lambda *a, **k: {'unit': 'unchanged'})
    current['text'] = service_properties(tmp_path, ExecStart=after, MainPID='0', ActiveState='inactive', SubState='dead')
    if change != 'lifecycle':
        with pytest.raises(RuntimeError, match='configuration changed'):
            helper.validate_capture(tmp_path, 'nobody', 'fixture.service', environment, stopped=True)
    else:
        result = helper.validate_capture(tmp_path, 'nobody', 'fixture.service', environment, stopped=True)
        assert result['ActiveState'] == 'inactive'
