"""Explicit checked-in field classification: additions need a policy review."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def declared_dict(path, name):
    tree = ast.parse((ROOT / path).read_text())
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return {key.value for key in node.value.keys}
    raise AssertionError('Missing declaration: ' + name)


def test_every_declared_setting_has_explicit_scope_and_migration_policy():
    path = ROOT / 'docs/settings-scope.json'
    assert path.is_file(), 'Missing complete declared-field scope inventory'
    inventory = json.loads(path.read_text())
    expected = {
        'autochat': declared_dict('app/db.py', 'DEFAULT_SETTINGS'),
        'autopost_integrations': declared_dict('autoposter/services/appconfig.py', 'FIELDS'),
    }
    tree = ast.parse((ROOT / 'autoposter/config.py').read_text())
    config = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Settings')
    expected['environment'] = {node.target.id for node in config.body if isinstance(node, ast.AnnAssign)}
    for store, keys in expected.items():
        mapped = inventory['stores'][store]
        assert set(mapped) == keys, {'unclassified': sorted(keys-set(mapped)), 'obsolete': sorted(set(mapped)-keys)}
        for key, policy in mapped.items():
            assert policy['scope'] in {'installation', 'character', 'legacy_character_bootstrap'}
            assert policy['destination'] and policy['migration']
            assert type(policy['secret']) is bool
    from app.setting_scope import GLOBAL_TECHNICAL_KEYS
    from autoposter.services.appconfig import FIELDS
    for store in ('autochat', 'autopost_integrations'):
        for key, policy in inventory['stores'][store].items():
            assert (policy['scope'] == 'installation') == (key in GLOBAL_TECHNICAL_KEYS)
    assert set(inventory['technical_allowlist']) == GLOBAL_TECHNICAL_KEYS
    assert set(FIELDS) == expected['autopost_integrations']


def test_literal_autochat_setting_uses_cannot_bypass_classification():
    inventory = json.loads((ROOT / 'docs/settings-scope.json').read_text())
    known = set(inventory['stores']['autochat']) | set(inventory['autochat_additional_keys'])
    unknown = []
    for directory in ('app', 'autoposter'):
        for path in (ROOT / directory).rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {'get_setting', 'set_setting'} and node.args
                        and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                        and node.args[0].value not in known):
                    unknown.append((str(path.relative_to(ROOT)), node.lineno, node.args[0].value))
    assert not unknown, {'unclassified_setting_uses': unknown}
