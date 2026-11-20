"""systemctl serializes even empty structured credential arrays as unprintable."""
import pytest
from deploy import creatorstudio_update as helper
from tests.test_helper_environment import service_properties

KEYS = {'LoadCredential': 'a(ss)', 'LoadCredentialEncrypted': 'a(ss)',
        'SetCredential': 'a(say)', 'SetCredentialEncrypted': 'a(say)'}

@pytest.mark.parametrize('fault', [None, 'nonempty', 'wrong-signature', 'malformed-object', 'dbus-error'])
def test_structured_credential_emptiness_requires_typed_dbus_proof(tmp_path, monkeypatch, fault):
    calls = []
    props = service_properties(tmp_path, **{key: '[unprintable]' for key in KEYS})
    def boundary(args, **kwargs):
        calls.append(args)
        if args[:2] == ['systemctl', 'show']:
            return '' if '--value' in args else props
        if args[:2] == ['busctl', 'call']:
            assert args[-1] == 'fixture.service'
            return 'o "/unexpected/path"' if fault == 'malformed-object' else 'o "/org/freedesktop/systemd1/unit/fixture_2eservice"'
        if args[:2] == ['busctl', 'get-property']:
            key = args[-1]
            assert key in KEYS
            assert args[3] == '/org/freedesktop/systemd1/unit/fixture_2eservice'
            assert args[4] == 'org.freedesktop.systemd1.Service'
            if fault == 'dbus-error':
                raise RuntimeError('query failed')
            if key == 'SetCredential':
                if fault == 'nonempty': return 'a(say) 1 "private" 1 42'
                if fault == 'wrong-signature': return 'as 0'
            return KEYS[key] + ' 0'
        pytest.fail('Unexpected command or side effect: ' + repr(args))
    monkeypatch.setattr(helper, 'command', boundary)
    if fault is None:
        result = helper.service_contract(tmp_path, 'nobody', 'fixture')
        assert all(result[key] == '' for key in KEYS)
        assert len([c for c in calls if c[:2] == ['busctl', 'get-property']]) == 4
    else:
        with pytest.raises(RuntimeError):
            helper.service_contract(tmp_path, 'nobody', 'fixture')
    assert not any(c[:2] in (['systemctl', 'stop'], ['systemctl', 'start']) for c in calls)
