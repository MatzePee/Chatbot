"""AutoPost alerts use shared Telegram settings; no real messages are sent."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import respx
from sqlalchemy import select

from app import db as shared_db
from autoposter.models import AppSetting, Post
from autoposter.config import settings
from autoposter.adapters.base import AdapterError
from autoposter.services import notify, publisher
from tests.autoposter.test_duplicates import make_channel


def test_shared_connection_wins_over_standalone_settings(monkeypatch):
    values = {"telegram_bot_token": "shared", "telegram_chat_id": "123"}
    monkeypatch.setattr(shared_db, "get_setting", lambda key, default="": values.get(key, default))
    monkeypatch.setattr(settings, "telegram_bot_token", "old")
    monkeypatch.setattr(settings, "telegram_chat_id", "456")
    assert notify.telegram_connection() == ("shared", "123")
    values.clear()
    assert notify.telegram_connection() == ("old", "456")


@pytest.mark.asyncio
@pytest.mark.parametrize("ok", [True, False])
async def test_telegram_plain_text_and_delivery_confirmation(monkeypatch, ok):
    monkeypatch.setattr(notify, "telegram_connection", lambda: ("fake", "123"))
    with respx.mock() as mock:
        route = mock.post("https://api.telegram.org/botfake/sendMessage").respond(200, json={"ok": ok})
        if ok:
            assert await notify._telegram("X_error [account]*")
        else:
            with pytest.raises(RuntimeError):
                await notify._telegram("X_error [account]*")
        payload = json.loads(route.calls.last.request.content)
        assert payload == {"chat_id": "123", "text": "X_error [account]*"}


@pytest.mark.asyncio
async def test_failed_post_reminder_survives_acknowledgement_and_is_throttled(db, monkeypatch):
    channel = await make_channel(db, "X", "x")
    post = Post(channel_id=channel.id, status="failed", body_text="synthetic")
    db.add(post)
    await db.flush()
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(notify, "_fanout", send)
    event = dict(level="error", title="Veröffentlichung fehlgeschlagen: X", body="expired", entity="post", entity_id=str(post.id))
    db.add(AppSetting(key=notify.ack_key(event["title"], event["body"], "post", str(post.id)), value={"ack": True}))
    await db.flush()
    await notify.push(db, **event)
    await notify.push(db, **event)
    assert send.await_count == 1
    last = (await db.execute(select(AppSetting).where(AppSetting.key.like("external_alert:%")))).scalar_one()
    last.value = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    await notify.push(db, **event)
    assert send.await_count == 2


@pytest.mark.asyncio
async def test_delivery_failure_is_retried_without_breaking_notifications(db, monkeypatch):
    monkeypatch.setattr(notify, "_telegram", AsyncMock(side_effect=RuntimeError("unreachable")))
    monkeypatch.setattr(settings, "smtp_host", None)
    channel = await make_channel(db, "X", "x")
    post = Post(channel_id=channel.id, status="failed", body_text="synthetic")
    db.add(post)
    await db.flush()
    event = dict(level="error", title="Veröffentlichung fehlgeschlagen: X", entity="post", entity_id=str(post.id))
    await notify.push(db, **event)
    await notify.push(db, **event)
    assert notify._telegram.await_count == 2
    assert not (await db.execute(select(AppSetting).where(AppSetting.key.like("external_alert:%")))).scalars().all()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [AdapterError("timeout", retryable=True), RuntimeError("unexpected")])
async def test_scheduled_retry_is_quiet_but_final_publish_error_notifies(db, monkeypatch, failure):
    channel = await make_channel(db, "X", "x")
    post = Post(channel_id=channel.id, status="scheduled", body_text="hello", media_asset_ids=[])
    db.add(post)
    await db.flush()
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "global_pause", False)
    monkeypatch.setattr(publisher.planner, "preflight", AsyncMock(return_value=[]))
    monkeypatch.setattr(publisher.cred_service, "get_valid_credentials", AsyncMock(side_effect=failure))
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(notify, "_fanout", send)
    ok, _ = await publisher.publish_post(db, post.id)
    assert not ok
    assert send.await_count == (0 if isinstance(failure, AdapterError) else 1)
    if send.await_count:
        assert "Veröffentlichung fehlgeschlagen: X" in send.call_args.args[0]
