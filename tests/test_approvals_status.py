"""Dashboard approvals: navigation and genuinely stuck draft count."""
from fastapi.testclient import TestClient
from app import db, main


def test_dashboard_links_queue_and_counts_only_drafts_requiring_intervention(monkeypatch):
    monkeypatch.setattr(main.fanvue, 'is_connected', lambda: False)
    db.set_setting('draft_max_regen', 3)
    db.set_setting('draft_regen_on_stale', False)
    db.create_draft(user_uuid='normal', generated_text='okay', status='pending', auto_send=1,
                    scheduled_send_at=1234567)
    db.create_draft(user_uuid='manual', generated_text='reply', status='pending', auto_send=0)
    db.create_draft(user_uuid='retrying', generated_text='', status='pending', regen_count=1,
                    guardrail_note='eckige Klammern')
    db.create_draft(user_uuid='exhausted', generated_text='', status='pending', regen_count=3,
                    guardrail_note='eckige Klammern')
    db.create_draft(user_uuid='escalation', generated_text='reply', status='pending',
                    guardrail_note='Eskalations-Stichwort')
    db.create_draft(user_uuid='stale', generated_text='reply', status='pending',
                    stale_note='Veraltet: neue Fan-Nachricht', scheduled_send_at=None)
    db.create_draft(user_uuid='closed', generated_text='', status='rejected', regen_count=3,
                    guardrail_note='eckige Klammern')
    # No lifespan: never start a worker or connect to a provider.
    html = TestClient(main.app).get('/').text
    assert '<a href="/queue">Wartende Freigaben</a>' in html
    assert 'Hängende Freigaben' in html
    assert 'Wartende Freigaben</a></td><td><strong>6</strong>' in html
    assert 'Hängende Freigaben</a></td><td><strong>3</strong>' in html
