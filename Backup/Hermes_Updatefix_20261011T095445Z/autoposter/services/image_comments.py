"""One durable, non-retrying X reply per newly published image post."""
from datetime import datetime, timezone
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, update

from autoposter.adapters import AdapterError, get_adapter
from autoposter.config import settings
from autoposter.models import AppSetting, Channel, Post, XImageComment
from autoposter.services import credentials, notify

KEY = "x_image_comment"


class CommentSettings(BaseModel):
    enabled: bool = False
    text: str = Field(default="", max_length=250)
    url: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def validate_content(self):
        self.text, self.url = self.text.strip(), self.url.strip()
        parsed = urlsplit(self.url)
        if self.url and (parsed.scheme not in ("https", "http") or not parsed.hostname
                         or parsed.username or parsed.password or any(c.isspace() for c in self.url)):
            raise ValueError("Bitte einen vollständigen http(s)-Link ohne Leerzeichen eingeben.")
        if self.enabled and (not self.text or not self.url):
            raise ValueError("Für Auto-Kommentare werden Text und Link benötigt.")
        # Conservative X weighting, including t.co's 23-character URL length.
        weight = sum(1 if (ord(c) <= 0x10ff or 0x2000 <= ord(c) <= 0x200d
                            or 0x2010 <= ord(c) <= 0x201f or 0x2032 <= ord(c) <= 0x2037) else 2
                     for c in self.text) + (24 if self.url else 0)
        if weight > 280:
            raise ValueError("Text und Link überschreiten das X-Zeichenlimit (Emojis zählen stärker).")
        return self

    def body(self):
        return f"{self.text}\n{self.url}"


async def get_settings(db):
    row = await db.get(AppSetting, KEY)
    return CommentSettings.model_validate(row.value if row else {})


async def save_settings(db, value: CommentSettings):
    row = await db.get(AppSetting, KEY)
    if row is None:
        row = AppSetting(key=KEY)
    row.value = value.model_dump()
    db.add(row)
    await db.flush()
    return value


def record(post, comment):
    post.generation_meta = {**(post.generation_meta or {}), "x_image_comment": {
        "state": comment.state, "text": comment.text, "error": comment.error,
        "external_id": comment.external_id,
    }}


async def prepare(db, post, channel, assets):
    from app.preview import enabled
    if enabled() or settings.dry_run or channel.platform != "x" or post.thread_parent_id:
        return None
    if not assets or not all((a.mime or "").startswith("image/") for a in assets):
        return None
    config = await get_settings(db)
    if not config.enabled:
        return None
    existing = await db.get(XImageComment, post.id)
    if existing:
        return existing
    comment = XImageComment(post_id=post.id, text=config.body(), state="pending", error="", external_id="")
    db.add(comment)
    record(post, comment)
    await db.flush()
    return comment


async def deliver(db, post_id):
    from app.preview import enabled
    if enabled() or settings.dry_run or settings.global_pause:
        return
    comment = await db.get(XImageComment, post_id)
    post = await db.get(Post, post_id)
    if not comment or comment.state != "pending" or not post or post.status != "published":
        return
    channel = await db.get(Channel, post.channel_id)
    if not channel or channel.platform != "x" or not channel.is_active or channel.health == "paused":
        return
    if not (await get_settings(db)).enabled:
        comment.state = "cancelled"
        record(post, comment)
        await db.commit()
        return
    # Claim BEFORE the external request. An interrupted/ambiguous attempt is
    # never retried automatically: X provides no idempotency key for replies.
    claim = await db.execute(update(XImageComment).where(
        XImageComment.post_id == post_id, XImageComment.state == "pending"
    ).values(state="sending", attempted_at=datetime.now(timezone.utc)))
    if claim.rowcount != 1:
        await db.rollback()
        return
    comment.state = "sending"
    record(post, comment)
    await db.commit()  # Also durably saves the already published image.
    try:
        cred = await credentials.get_valid_credentials(db, channel)
        comment.external_id = await get_adapter("x").create_comment(cred, post.external_post_id, comment.text)
        comment.state = "sent"
        channel.quota_used_today += 1
    except Exception as exc:
        comment.state = "failed" if isinstance(exc, AdapterError) and exc.status and 400 <= exc.status < 500 and exc.status != 408 else "unknown"
        comment.error = str(exc)[:700]
        await notify.push(db, level="warn", title="Bild veröffentlicht · X-Kommentar prüfen",
                          body="Der Kommentar konnte nicht bestätigt werden. Vor erneutem Senden auf X prüfen. " + comment.error,
                          entity="post", entity_id=str(post.id))
    record(post, comment)
    await db.commit()


async def deliver_pending(db):
    from app.preview import enabled
    if enabled() or settings.dry_run or settings.global_pause:
        return
    ids = (await db.execute(select(XImageComment.post_id).where(XImageComment.state == "pending").limit(50))).scalars().all()
    for post_id in ids:
        await deliver(db, post_id)
