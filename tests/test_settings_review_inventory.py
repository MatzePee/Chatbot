"""Fail-closed review inventory in addition to form/renderer behavioral tests."""
from collections import Counter
import ast
import hashlib
import json
from pathlib import Path
import re

from tests.test_settings_hub import Controls

ROOT = Path(__file__).resolve().parents[1]


def canonical(rows):
    return Counter(json.dumps(row, sort_keys=True) for row in rows)


def test_reviewed_settings_inventory_accounts_every_control_mapping_and_scope():
    manifest = json.loads((ROOT/'docs/settings-completeness.json').read_text())
    review = manifest['reviewed_current_inventory']
    scopes = json.loads((ROOT/'docs/settings-scope.json').read_text())
    for section in ('shared','autochat','updates'):
        old = manifest[section]
        parser = Controls(); parser.feed((ROOT/old['source']).read_text())
        assert canonical(parser.items) == canonical(review[section]['current_controls']), 'Unreviewed addition/removal fails closed'
        assert canonical(row['old'] for row in old['mapping']) == canonical(old['controls_actions'])
        for row in old['mapping']:
            if row['old'] != row['new']['identity']:
                assert row['old'] == {'tag':'a','href':'/settings/shared'}
                assert row['new']['identity'] == {'tag':'a','href':'/settings/shared#ai'}
        assert all(row['new']['source'] in manifest['relocated_sources'] for row in old['mapping'])
        for row in old['mapping']:
            assert row['new']['identity'] in manifest['relocated_sources'][row['new']['source']]
        for field,scope in review[section]['field_scopes'].items():
            assert scope == scopes['stores']['autochat'][field.removeprefix('clear_')]['scope']
    source=(ROOT/manifest['autopost']['source']).read_text()
    actual=[list(row) for row in re.findall(r"const\s+(\w+)\s*=\s*h\('(input|select|textarea)'", source)]
    assert actual == review['autopost']['controls']
    # Every retired control has a precise relocation or the pre-existing retired interval.
    retired = set(map(tuple,manifest['autopost']['controls'])) - set(map(tuple,actual))
    assert {name for name,tag in retired} <= set(manifest['autopost']['relocations']) | {'autoEvery'}
    all_js = source + (ROOT/'app/static/settings-common.js').read_text()
    assert set(manifest['autopost']['api_calls']) - {'clearSecret'} <= set(re.findall(r'api\.(\w+)\(', all_js))
    clear = next(row for row in manifest['autopost']['mapping'] if row['old_api_action']=='clearSecret')
    assert clear['relocated_form_action']=='/settings/shared/ai'
    assert {'tag':'input','type':'checkbox','name':'clear_openrouter_api_key'} in manifest['relocated_sources']['app/templates/shared_settings.html']
    assert {x['old_api_action'] for x in manifest['autopost']['mapping']} == set(manifest['autopost']['api_calls'])
    assert hashlib.sha256(source.encode()).hexdigest()==review['autopost']['baseline_sha256']
    for rel, digest in review['relocated_js_sources'].items():
        assert hashlib.sha256((ROOT/rel).read_bytes()).hexdigest() == digest
    for rel, controls in manifest['relocated_sources'].items():
        parsed=Controls(); parsed.feed((ROOT/rel).read_text())
        assert canonical(parsed.items)==canonical(controls)



def test_every_autopost_api_route_preserves_reviewed_permission_dependency():
    manifest=json.loads((ROOT/'docs/settings-completeness.json').read_text())
    actual=[]
    for f in (ROOT/'autoposter/api').glob('*.py'):
        for n in ast.walk(ast.parse(f.read_text())):
            if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
            dec=[ast.unparse(d) for d in n.decorator_list if isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and d.func.attr in ['get','post','put','patch','delete']]
            if dec: actual.append({'source':str(f.relative_to(ROOT)),'function':n.name,'routes':dec,'dependencies':[ast.unparse(x) for x in n.args.defaults if isinstance(x,ast.Call) and ast.unparse(x.func)=='Depends']})
    assert canonical(actual)==canonical(manifest['reviewed_current_inventory']['api_permissions'])


def test_migration_settings_controls_have_explicit_reviewed_admin_actions():
    manifest=json.loads((ROOT/'docs/settings-completeness.json').read_text())
    review=manifest['reviewed_current_inventory']['upgrade']
    parser=Controls();parser.feed((ROOT/'app/templates/_character_upgrade.html').read_text())
    assert parser.items==review['current_controls']
    assert review['permission']=='require_admin; preview mutations blocked; helper4 rechecked before stage'
