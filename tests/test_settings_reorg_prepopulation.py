from fastapi.testclient import TestClient
from app import main

def test_common_page_prepopulates_existing_post_task_values(monkeypatch):
    from autoposter.services import appconfig
    monkeypatch.setattr(appconfig,'_snapshot',{'openrouter_model_vision':'exact/existing-post-vision','openrouter_model_caption':'exact/existing-caption'})
    monkeypatch.setattr(main.fanvue,'is_connected',lambda:False)
    r=TestClient(main.app).get('/settings/shared')
    assert 'name="openrouter_model_vision" value="exact/existing-post-vision"' in r.text
    assert 'name="openrouter_model_caption" value="exact/existing-caption"' in r.text
