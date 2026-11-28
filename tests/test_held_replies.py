from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import db, guardrails, main, poller


def test_switches_save_independently(monkeypatch):
    monkeypatch.setattr(main.fanvue, "is_connected", lambda: False)
    monkeypatch.setattr(guardrails.persona_context, "now_local", lambda: datetime(2026, 1, 1, 20))
    client = TestClient(main.app)
    for enabled in ("time_guard_enabled", "send_promise_guard_enabled"):
        response = client.post("/settings", data={enabled: "on"}, follow_redirects=False)
        assert response.status_code == 303
        assert bool(guardrails.check_outgoing("Guten Morgen!")[1]) == (enabled == "time_guard_enabled")
        assert bool(guardrails.check_outgoing("I will send you a photo.")[1]) == (enabled == "send_promise_guard_enabled")
        assert not guardrails.check_outgoing("I will send you a photo.", has_media=True)[1]


@pytest.mark.parametrize("before_send", [True, False])
def test_new_fan_message_reopens_exhausted_attempts(monkeypatch, before_send):
    db.set_setting("draft_max_regen", 1)
    db.set_setting("draft_recheck_interval_seconds", 0)
    db.set_setting("mode", "auto")
    db.upsert_chat("fan", handle="fan")
    draft_id = db.create_draft(user_uuid="fan", inbound_uuid="old", generated_text="Guten Morgen!",
                               guardrail_note="Zeit-Widerspruch", regen_count=1)
    history = [{"uuid": key, "text": key, "sender": {"uuid": "fan"}} for key in ("old", "new")]
    monkeypatch.setattr(poller, "_fetch_history", lambda user: history)
    monkeypatch.setattr(poller, "_me_uuid", lambda: "creator")
    monkeypatch.setattr(poller.fanvue, "is_connected", lambda: True)
    monkeypatch.setattr(poller.time, "sleep", lambda delay: None)
    attempts = []

    def generate(prompt, messages, *args, **kwargs):
        attempts.append(messages[-1]["uuid"])
        if len(attempts) == 1:
            raise RuntimeError("synthetic failure")
        return "Danke für die Frage!"

    monkeypatch.setattr(poller, "_generate", generate)
    def check():
        if before_send:
            assert poller._check_stale_before_send(db.get_draft(draft_id))
        else:
            poller.recheck_pending_cycle()
    check()
    check()
    assert attempts == ["new"]
    history.append({"uuid": "newest", "text": "Noch eine Frage", "sender": {"uuid": "fan"}})
    check()
    draft = db.get_draft(draft_id)
    assert attempts == ["new", "newest"]
    assert draft["inbound_uuid"] == db.get_chat("fan")["last_inbound_uuid"] == "newest"
    assert draft["regen_count"] == 1 and draft["stale_note"] is None
    assert draft["edited_text"] == "Danke für die Frage!"
    assert draft["auto_send"] == 1 and draft["scheduled_send_at"]
    sent = []
    def send(user, text, **kwargs):
        sent.append(text)
        return {}
    monkeypatch.setattr(poller.fanvue, "send_message", send)
    assert not poller.send_draft_now(draft_id, "Guten Morgen!", expected_inbound="old")
    assert poller.send_draft_now(draft_id, draft["edited_text"], expected_inbound="newest")
    assert sent == ["Danke für die Frage!"]
    assert db.get_draft(draft_id)["status"] == "sent"


@pytest.mark.parametrize("path", ["/chats/fan/ppv/status", "/ppv/subscriber/fan/purchased"])
def test_ppv_purchase_returns_to_fan(path):
    response = TestClient(main.app).post(path, data={"folder_name": "Set", "status": "bought"},
                                         follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/chats/fan/ppv"
    assert "Set" in db.ppv_purchased_sets("fan")


def test_stale_draft_with_anchor_outside_history_window():
    draft = {"inbound_uuid": "outside", "created_at": 100}
    history = [
        {"uuid": "previous", "sentAt": 99, "sender": {"uuid": "fan"}},
        {"uuid": "own", "sentAt": 101, "sender": {"uuid": "creator"}},
        {"uuid": "followup", "sentAt": 102, "sender": {"uuid": "fan"}},
    ]
    assert poller._newer_fan_messages(draft, history, "creator") == [history[-1]]


def test_concurrent_manual_edit_wins_over_model_result():
    draft_id = db.create_draft(user_uuid="fan", generated_text="Original")
    previous = db.get_draft(draft_id)
    db.update_draft(draft_id, edited_text="Handkorrektur", user_edited=1)
    assert not db.save_regeneration(previous, {"edited_text": "Modellantwort"})
    assert db.get_draft(draft_id)["edited_text"] == "Handkorrektur"
