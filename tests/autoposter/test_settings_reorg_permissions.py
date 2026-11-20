import pytest
from tests.autoposter.test_six_character_acceptance import installation

@pytest.mark.asyncio
async def test_all_common_forms_require_admin_and_common_usage_is_installation_wide(installation,monkeypatch):
    from autoposter import db
    from autoposter.config import settings
    from autoposter.models import User
    from autoposter.security import create_token
    from autoposter.workspaces import control_context
    client,owners,_=installation
    with control_context():
        async with db.SessionLocal() as session:
            user=User(email='synthetic-editor@test.invalid',password_hash='unused',role='editor')
            session.add(user);await session.commit();token=create_token(str(user.id))
    monkeypatch.setattr(settings,'auth_enabled',True)
    for path,form in [('ai',{'openrouter_model':'forbidden'}),('urls',{'public_base_url':'https://forbidden.invalid'}),('telegram',{'telegram_chat_id':'forbidden'}),('updates',{'update_check_interval_hours':'24'})]:
        r=await client.post('/settings/shared/'+path+'?character_id='+owners[0]['id'],data=form,headers={'Authorization':'Bearer '+token})
        assert r.status_code==403,(path,r.text)
    monkeypatch.setattr(settings,'auth_enabled',False)
    r=await client.get('/settings/shared/ai-usage?character_id='+owners[0]['id'])
    assert r.status_code==200,r.text
    assert r.json()['scope']=='installation'
