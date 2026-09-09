from app import main


def test_context_anchors_uuid_not_repeated_text(monkeypatch):
    rows = [dict(msg_uuid=str(i), direction='out' if i == 0 else 'in', text='Hi', has_media=False, sent_at=100+i) for i in range(4)]
    monkeypatch.setattr(main.db, 'messages_tail_chronological', lambda *args: rows)
    context, timestamp = main._draft_context('test', '2', 2, 'me', allow_remote=False)
    assert timestamp == 102
    assert [m['sent_at'] for m in context] == [100, 101]
    assert [m['who'] for m in context] == ['Ich', 'Fan']
    assert main._draft_context('test', '2', 0, 'me', allow_remote=False) == ([], 102)


def test_missing_anchor_does_not_show_future_context(monkeypatch):
    monkeypatch.setattr(main.db, 'messages_tail_chronological', lambda *args: [])
    assert main._draft_context('unknown', 'unknown', 2, 'me', allow_remote=False) == ([], None)


def test_message_timezone_and_unknown():
    assert main._fmt_message_time('2026-09-09T10:15:00Z') == '09.09.2026 · 12:15 CEST'
    assert main._fmt_message_time(None) == 'Zeitpunkt nicht verfügbar'
    assert main._fmt_message_time('invalid') == 'Zeitpunkt nicht verfügbar'
