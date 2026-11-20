"""Real assembled application saves and layouts, six synthetic workspaces."""
import json, os
from pathlib import Path
import pytest
from app import db as chat
from autoposter.workspaces import character_context
from tests.autoposter.test_six_character_acceptance import installation
from app.setting_scope import GLOBAL_AI_KEYS


@pytest.mark.asyncio
async def test_common_ai_form_owner_saves_and_six_character_render(installation):
    client, owners, engine = installation
    for owner in owners:
        oid=owner['id']
        with character_context(oid):
            chat.set_setting('vision_prompt','Private '+owner['name'])
        common=await client.get('/settings/shared?character_id='+oid)
        auto=await client.get('/settings/shared?section=autochat&character_id='+oid)
        assert common.status_code==auto.status_code==200
        assert 'Gilt für alle Charaktere' in common.text
        assert 'Gilt für: '+owner['name'] in auto.text
        assert 'name="fanvue_client_secret"' not in common.text
        assert 'name="fanvue_client_secret"' in auto.text
        for key in GLOBAL_AI_KEYS:
            assert f'name="{key}"' in common.text,key
            assert f'name="{key}"' not in auto.text,key
        response=await client.post('/settings/shared/ai?character_id='+oid,data={
            'openrouter_model':'deepseek/deepseek-v4-pro',
            'vision_model':'qwen/qwen3-vl-235b-a22b-instruct',
            'openrouter_model_vision':'openai/gpt-5.6-luna-pro',
            'openrouter_api_key':'synthetic-shared', 'temperature':'0.7',
            'system_prompt':'must-not-change-owner'})
        assert response.status_code==303,response.text
        assert oid in response.headers['location']
        assert chat.get_setting('openrouter_model')=='deepseek/deepseek-v4-pro'
        response=await client.post('/settings?character_id='+oid,data={
            'openrouter_model':'forbidden/owner','openrouter_api_key':'forbidden',
            'clear_vision_api_key':'on','system_prompt':'Local '+owner['name']})
        assert response.status_code==303
        assert chat.get_setting('openrouter_model')=='deepseek/deepseek-v4-pro'
        assert chat.get_setting('openrouter_api_key')=='synthetic-shared'
        with character_context(oid):
            assert chat.get_setting('system_prompt')=='Local '+owner['name']
            assert chat.get_setting('vision_prompt')=='Private '+owner['name']
        for mask in ['', '********','••••••••']:
            r=await client.post('/settings/shared/ai?character_id='+oid,data={'openrouter_api_key':mask})
            assert r.status_code==303 and chat.get_setting('openrouter_api_key')=='synthetic-shared'
        r=await client.post('/settings/shared/ai?character_id='+oid,data={'clear_openrouter_api_key':'on'})
        assert r.status_code==303 and chat.technical_setting('openrouter_api_key')==(True,'')
    if output:=os.environ.get('SETTINGS_REORG_RENDER'):
        out=Path(output);out.mkdir(parents=True,exist_ok=True)
        (out/'owners.json').write_text(json.dumps(owners))
        for owner in owners:
            for section in ('shared','autochat','autopost','channels','personas'):
                r=await client.get(f"/settings/shared?section={section}&character_id={owner['id']}")
                assert r.status_code==200
                (out/f"{owner['id']}-{section}.html").write_text(r.text)


@pytest.mark.asyncio
async def test_common_ai_validation_is_atomic_and_preview_blocked(installation,monkeypatch):
    client,owners,_=installation
    url='/settings/shared/ai?character_id='+owners[0]['id']
    chat.set_setting('openrouter_model','existing/model')
    r=await client.post(url,data={'openrouter_model':'not/committed','temperature':'bad'})
    assert r.status_code==422
    assert chat.get_setting('openrouter_model')=='existing/model'
    monkeypatch.setenv('MP_PREVIEW','1')
    r=await client.post(url,data={'openrouter_model':'not/committed'})
    assert r.status_code==403
    assert chat.get_setting('openrouter_model')=='existing/model'
