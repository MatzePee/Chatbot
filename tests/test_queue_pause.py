"""Per-draft auto-send countdown and safe pause (disposable SQLite only)."""
import time
from fastapi.testclient import TestClient
from app import db, main, poller


def _draft(user, due):
    return db.create_draft(user_uuid=user, status='pending', generated_text='original',
                           auto_send=1, scheduled_send_at=due)


def test_pause_is_per_draft_and_preserves_submitted_edit(monkeypatch):
    monkeypatch.setattr(main.fanvue, 'is_connected', lambda: False)
    later = time.time() + 80
    first = _draft('one', later)
    second = _draft('two', later)
    client = TestClient(main.app)  # no lifespan / worker
    page = client.get('/queue').text
    assert f'data-send-at="{later}"' in page
    assert 'Wird gesendet in' in page
    assert f'/queue/{first}/pause' in page
    res = client.post(f'/queue/{first}/pause',data={'edited_text':'edited'}, follow_redirects=False)
    assert res.status_code == 303
    d = db.get_draft(first)
    assert d['status'] == 'pending' and d['auto_send'] == -1 and d['scheduled_send_at'] is None
    assert d['edited_text'] == 'edited' and d['user_edited'] == 1
    assert db.get_draft(second)['scheduled_send_at'] == later
    assert 'Automatischer Versand pausiert' in client.get('/queue').text


def test_pause_wins_against_due_sender_claim(monkeypatch):
    first = _draft('first', time.time()-1)
    assert db.pause_auto_draft(first, 'my edited text')
    monkeypatch.setattr(poller.fanvue, 'is_connected', lambda: True)
    monkeypatch.setattr(poller, '_within_active_hours', lambda: True)
    called = []
    monkeypatch.setattr(poller, '_send_draft', lambda draft_id: called.append(draft_id))
    poller.send_due_cycle()
    assert called == []


def test_claimed_draft_cannot_be_reported_as_paused():
    first = _draft('first', time.time()-1)
    assert db.claim_due_auto_draft(first, time.time())
    assert not db.pause_auto_draft(first, 'too late')
    assert db.get_draft(first)['edited_text'] is None


def test_manual_send_cannot_overlap_auto_claim(monkeypatch):
    first = _draft('first', time.time()-1)
    assert db.claim_due_auto_draft(first, time.time())
    called = []
    monkeypatch.setattr(poller, '_send_draft', lambda draft_id: called.append(draft_id) or True)
    assert not poller.send_draft_now(first)
    assert called == []


def test_manual_send_claims_before_calling_provider(monkeypatch):
    first = _draft('first', time.time()-1)
    seen = []
    def fake_send(draft_id):
        seen.append(db.get_draft(draft_id)['auto_send'])
        assert not db.claim_due_auto_draft(draft_id, time.time())
        return True
    monkeypatch.setattr(poller, '_send_draft', fake_send)
    assert poller.send_draft_now(first)
    assert seen == [2]


def test_preview_allows_safe_pause_but_not_send(monkeypatch):
    monkeypatch.setenv('MP_PREVIEW', '1')
    monkeypatch.setattr(main.fanvue, 'is_connected', lambda: False)
    first = _draft('preview', time.time() + 60)
    client = TestClient(main.app)
    assert client.post(f'/queue/{first}/pause', data={'edited_text':'safe'},
                       follow_redirects=False).status_code == 303
    assert db.get_draft(first)['auto_send'] == -1
    assert client.post(f'/queue/{first}/approve').status_code == 403


def test_manual_approval_has_no_auto_countdown(monkeypatch):
    monkeypatch.setattr(main.fanvue, 'is_connected', lambda: False)
    db.create_draft(user_uuid='manual', status='pending', generated_text='reply', auto_send=0)
    page = TestClient(main.app).get('/queue').text
    assert 'Wird gesendet in' not in page
    assert '/pause' not in page
