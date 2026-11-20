"""Exercise the actual assembled app, not an independently mounted test router."""
from fastapi.testclient import TestClient
from autoposter.main import app
from autoposter.deps import require_admin


def test_actual_app_exposes_only_character_upgrade_system_routes(tmp_path, monkeypatch):
    from app import character_upgrade, helper_readiness
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '0')
    monkeypatch.setattr(character_upgrade, 'ROOT', tmp_path)
    monkeypatch.setattr(helper_readiness, 'readiness', lambda root: {'ready': True})
    monkeypatch.setitem(app.dependency_overrides, require_admin, lambda: object())
    paths = {getattr(route, 'path', '') for route in app.routes}
    assert '/api/v1/system/character-upgrade' in paths
    # Older duplicated updater/publisher routes must remain unmounted.
    assert '/api/v1/system/update' not in paths
    assert '/api/v1/system/publish' not in paths
    client = TestClient(app)  # deliberately no lifespan/workers
    response = client.get('/api/v1/system/character-upgrade')
    assert response.status_code == 200
    assert response.json()['phase'] == 'legacy'
    response = client.post('/api/v1/system/character-upgrade', json={'name': 'Cherry', 'confirmed': True})
    assert response.status_code == 200
    assert response.json()['phase'] == 'pending_restart'
    assert character_upgrade.status(tmp_path)['name'] == 'Cherry'
    response = client.delete('/api/v1/system/character-upgrade')
    assert response.status_code == 200
    assert response.json()['phase'] == 'legacy'
