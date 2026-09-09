"""Initialisierung: Schema anlegen, Admin, Persona 'Sally Larsen', Demo-Kanäle.

Aufruf:  python -m app.seed            (nur Basis)
         python -m app.seed --demo     (zusätzlich 40 Platzhalterbilder)
"""
from __future__ import annotations

import asyncio
import random
import sys
from io import BytesIO

from PIL import Image, ImageDraw
from slugify import slugify
from sqlalchemy import select

from autoposter.config import settings
from autoposter.db import Base, SessionLocal, engine
from autoposter.models import (
    Channel,
    ChannelHealth,
    ContentPlan,
    Persona,
    PostingPolicy,
    Role,
    SavedView,
    User,
)
from autoposter.security import hash_password
from autoposter.services import library, media as media_service, rhythm

SALLY_SYSTEM_PROMPT = """\
Du bist Sally Larsen: 26, Fotografin und Fitness-Enthusiastin, lebt in Kopenhagen.
Du schreibst locker, selbstbewusst und warm, mit trockenem Humor und ohne Effekthascherei.
Du sprichst in kurzen, konkreten Sätzen und erzählst kleine Momente aus deinem Alltag,
statt allgemeine Sprüche zu posten. Du stellst deinem Publikum gelegentlich eine echte Frage.
Du wirkst nahbar, nicht anbiedernd. Du übertreibst nicht mit Superlativen.
Du erwähnst niemals, dass du ein Model bist, und sprichst nie über Technik hinter den Bildern.
"""


async def ensure_schema() -> None:
    """Tabellen anlegen UND fehlende Spalten in bestehenden Tabellen ergänzen.

    Ohne den zweiten Schritt bleibt eine bereits vorhandene Datenbank stumm
    veraltet, sobald das Modell um Felder wächst.
    """
    from autoposter import schema_sync

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    report = await schema_sync.sync(engine)
    print("  " + schema_sync.summarize(report))
    for entry in report["added_columns"]:
        print(f"    + {entry}")
    for entry in report["failed"]:
        print(f"    ! {entry['column']}: {entry['error']}")


async def seed_base() -> None:
    async with SessionLocal() as db:
        # Admin
        admin = (
            await db.execute(select(User).where(User.email == settings.admin_email.lower()))
        ).scalar_one_or_none()
        if not admin:
            admin = User(
                email=settings.admin_email.lower(),
                password_hash=hash_password(settings.admin_password),
                display_name="Administrator",
                role=Role.admin.value,
            )
            db.add(admin)
            print(f"  Admin angelegt: {settings.admin_email}")

        # Persona
        persona = (
            await db.execute(select(Persona).where(Persona.slug == "sally-larsen"))
        ).scalar_one_or_none()
        if not persona:
            persona = Persona(
                name="Sally Larsen",
                slug=slugify("Sally Larsen"),
                bio="26, Fotografin aus Kopenhagen. Kaffee, Kamera, Krafttraining.",
                tone_guidelines=(
                    "Locker, selbstbewusst, warm. Trockener Humor. Kurze Sätze. "
                    "Konkrete Alltagsmomente statt allgemeiner Sprüche."
                ),
                system_prompt=SALLY_SYSTEM_PROMPT,
                daily_rhythm=rhythm.EXAMPLE_PLAN,
                forbidden_topics=["Politik", "Religion", "Krankheiten", "andere Creator"],
                emoji_policy="sparse",
                language="en",
                hashtag_pool=[
                    "copenhagen", "filmphotography", "gymlife", "morningroutine",
                    "goldenhour", "coffeefirst", "sundayreset",
                ],
                cta_pool=[
                    "Mehr davon drüben auf Fanvue.",
                    "Der Rest der Serie ist bei mir im Feed.",
                ],
                temperature=0.9,
            )
            db.add(persona)
            await db.flush()
            print("  Persona 'Sally Larsen' angelegt")

        # Gespeicherte Standard-Ansichten
        existing_views = {
            v.name for v in (await db.execute(select(SavedView))).scalars().all()
        }
        for position, view in enumerate(library.DEFAULT_SAVED_VIEWS):
            if view["name"] in existing_views:
                continue
            db.add(
                SavedView(
                    name=view["name"],
                    icon=view["icon"],
                    query=view["query"],
                    is_system=True,
                    position=position,
                )
            )

        await db.commit()


async def seed_demo_channels() -> None:
    async with SessionLocal() as db:
        if (await db.execute(select(Channel))).scalars().first():
            print("  Kanäle existieren bereits, übersprungen")
            return
        persona = (
            await db.execute(select(Persona).where(Persona.slug == "sally-larsen"))
        ).scalar_one()

        definitions = [
            ("x", "Sally on X (Haupt)", "@sallylarsen", "#1d9bf0", "suggestive", 3.0, 0.4),
            ("x", "Sally on X (Zweit)", "@sally_daily", "#38bdf8", "sfw", 2.0, 0.3),
            ("fanvue", "Sally on Fanvue", "sallylarsen", "#f43f5e", "explicit", 1.0, 0.0),
        ]
        for platform, name, handle, color, nsfw, per_day, text_ratio in definitions:
            policy = PostingPolicy(
                posts_per_day=per_day,
                text_only_ratio=text_ratio,
                min_gap_minutes=150 if platform == "x" else 480,
                daily_api_quota=25 if platform == "x" else 100,
                reuse_cooldown_days=0,
                allowed_time_windows=[
                    {"dow": [0, 1, 2, 3, 4], "from": "08:00", "to": "22:00"},
                    {"dow": [5, 6], "from": "10:00", "to": "23:00"},
                ],
            )
            db.add(policy)
            await db.flush()
            channel = Channel(
                platform=platform,
                display_name=name,
                handle=handle,
                color=color,
                nsfw_level=nsfw,
                persona_id=persona.id,
                policy_id=policy.id,
                timezone="Europe/Copenhagen",
                health=ChannelHealth.needs_reauth.value,
                health_note="Noch nicht verbunden",
                default_audience="subscribers" if platform == "fanvue" else "",
            )
            db.add(channel)
            await db.flush()
            db.add(
                ContentPlan(
                    channel_id=channel.id,
                    themes={
                        "0": "Wochenstart, Gym",
                        "2": "Behind the scenes",
                        "4": "Freitagsfrage an die Community",
                        "6": "Ruhiger Sonntag",
                    },
                )
            )
        await db.commit()
        print("  3 Demo-Kanäle angelegt (2x X, 1x Fanvue)")


def _placeholder_image(index: int, seed: int = 0) -> bytes:
    random.seed(seed + index)
    width, height = 1200, 1500
    base = (
        random.randint(30, 200),
        random.randint(30, 200),
        random.randint(30, 200),
    )
    image = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(image)
    for _ in range(18):
        x0, y0 = random.randint(0, width), random.randint(0, height)
        draw.ellipse(
            [x0, y0, x0 + random.randint(80, 500), y0 + random.randint(80, 500)],
            fill=(
                min(255, base[0] + random.randint(-60, 60)),
                min(255, base[1] + random.randint(-60, 60)),
                min(255, base[2] + random.randint(-60, 60)),
            ),
        )
    draw.text((60, 60), f"Demo {index:03d}", fill=(255, 255, 255))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


async def seed_demo_media(count: int = 40) -> None:
    tag_pool = ["beach", "gym", "coffee", "studio", "street", "portrait", "sunset"]
    async with SessionLocal() as db:
        created = 0
        for index in range(count):
            try:
                await media_service.ingest(
                    db,
                    filename=f"demo_{index:03d}.jpg",
                    data=_placeholder_image(index),
                    tags=random.sample(tag_pool, k=random.randint(1, 3)),
                    nsfw_level=random.choice(["sfw", "sfw", "suggestive"]),
                    source_note="Demodaten (generiert)",
                )
                created += 1
            except media_service.DuplicateUpload:
                continue
        await db.commit()
        print(f"  {created} Demo-Bilder erzeugt")


async def main() -> None:
    demo = "--demo" in sys.argv
    print("AutoPoster – Initialisierung")
    await ensure_schema()
    print("  Schema geprüft/angelegt")
    await seed_base()
    if demo:
        await seed_demo_channels()
        await seed_demo_media()
    print("Fertig.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
