"""Benachrichtigungen: In-App, E-Mail, Telegram."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
import smtplib
import sqlite3
import uuid
from email.message import EmailMessage
from typing import Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config import settings
from autoposter.models import Notification, AppSetting, Post, PostStatus


logger = logging.getLogger("autoposter.notify")


async def push(
    db: AsyncSession,
    *,
    level: str,
    title: str,
    body: str = "",
    entity: str = "",
    entity_id: Optional[str] = None,
    external: bool = True,
) -> Notification:
    acknowledged = await db.get(AppSetting, ack_key(title[:200], body, entity, entity_id))
    note = Notification(
        level=level, title=title[:200], body=body, entity=entity, entity_id=entity_id, is_read=bool(acknowledged)
    )
    db.add(note)
    await db.flush()
    # External messages are only for real failed posts. Keep all other events
    # in the dashboard, including token diagnostics and scheduled retries.
    failed_post = False
    if external and level == "error" and entity == "post" and entity_id:
        try:
            post = await db.get(Post, uuid.UUID(entity_id))
            failed_post = bool(post and post.status == PostStatus.failed.value)
        except (ValueError, TypeError):
            pass
    if failed_post:
        # Acknowledging the dashboard must not silence an ongoing failed post.
        key = "external_alert:" + hashlib.sha256(
            json.dumps([title, entity, entity_id], ensure_ascii=False).encode()
        ).hexdigest()
        last = await db.get(AppSetting, key)
        now = datetime.now(timezone.utc)
        try:
            due = not last or now - datetime.fromisoformat(last.value) >= timedelta(hours=6)
        except (ValueError, TypeError):
            due = True
        if due and await _fanout(title, body):
            if last:
                last.value = now.isoformat()
            else:
                db.add(AppSetting(key=key, value=now.isoformat()))
    return note


async def _fanout(title: str, body: str) -> bool:
    tasks = []
    tasks.append(_telegram(f"AutoPost · {title}\n{body}"))
    if settings.smtp_host and settings.notify_email_to:
        tasks.append(asyncio.to_thread(_email, title, body))
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                # Exception URLs may contain bot tokens: never log their text.
                logger.error("Externe AutoPost-Benachrichtigung fehlgeschlagen (%s)", type(result).__name__)
        # Only confirmed Telegram delivery suppresses another Telegram attempt.
        return results[0] is True
    return False


def telegram_connection():
    # Shared settings take precedence; standalone AutoPoster keeps its env fallback.
    from app import db as shared_db
    try:
        token = str(shared_db.get_setting("telegram_bot_token", "") or "").strip()
        chat_id = str(shared_db.get_setting("telegram_chat_id", "") or "").strip()
    except (sqlite3.Error, OSError):
        logger.warning("Gemeinsame Telegram-Einstellungen nicht lesbar; verwende AutoPost-Konfiguration")
        return settings.telegram_bot_token, settings.telegram_chat_id
    if token and chat_id:
        return token, chat_id
    return settings.telegram_bot_token, settings.telegram_chat_id


async def _telegram(text: str) -> bool:
    token, chat_id = await asyncio.to_thread(telegram_connection)
    if not token or not chat_id:
        logger.warning("AutoPost-Telegram nicht eingerichtet: Bot und Chat-ID in den gemeinsamen Einstellungen hinterlegen")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            url,
            json={"chat_id": chat_id, "text": text[:3800]},
        )
        response.raise_for_status()
        if not response.json().get("ok"):
            raise RuntimeError("Telegram hat die Benachrichtigung nicht bestätigt")
    return True


def _email(subject: str, body: str) -> bool:
    message = EmailMessage()
    message["Subject"] = f"[AutoPoster] {subject}"
    message["From"] = settings.smtp_from or settings.smtp_user or "autoposter@localhost"
    message["To"] = settings.notify_email_to or ""
    message.set_content(body or subject)
    with smtplib.SMTP(settings.smtp_host or "localhost", settings.smtp_port, timeout=20) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password or "")
        smtp.send_message(message)
    return True


def ack_key(title, body, entity, entity_id):
    payload = json.dumps([title, body, entity, entity_id], ensure_ascii=False).encode()
    return 'problem_ack_event:' + hashlib.sha256(payload).hexdigest()
