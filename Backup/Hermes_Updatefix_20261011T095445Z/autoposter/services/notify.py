"""Benachrichtigungen: In-App, E-Mail, Telegram."""
from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config import settings
from autoposter.models import Notification


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
    note = Notification(
        level=level, title=title[:200], body=body, entity=entity, entity_id=entity_id
    )
    db.add(note)
    await db.flush()
    if external and level in ("error", "warning"):
        await _fanout(title, body)
    return note


async def _fanout(title: str, body: str) -> None:
    tasks = []
    if settings.telegram_bot_token and settings.telegram_chat_id:
        tasks.append(_telegram(f"*{title}*\n{body}"))
    if settings.smtp_host and settings.notify_email_to:
        tasks.append(asyncio.to_thread(_email, title, body))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(
            url,
            json={
                "chat_id": settings.telegram_chat_id,
                "text": text[:3800],
                "parse_mode": "Markdown",
            },
        )


def _email(subject: str, body: str) -> None:
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
