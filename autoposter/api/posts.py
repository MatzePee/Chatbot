"""Posts, Kalender, Generierung und Veröffentlichung."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.adapters import get_adapter
from autoposter.config import settings
from autoposter.db import get_db, session_scope
from autoposter.deps import current_user, require_editor
from autoposter.models import (
    BlackoutPeriod,
    Channel,
    MediaAsset,
    Persona,
    Post,
    PostRevision,
    PostStatus,
    PublishAttempt,
    User,
)
from autoposter.schemas import (
    TranslateTextRequest,
    TranslateTextResult,
    GenerateRequest,
    GenerateResult,
    PlanFillRequest,
    PlanFillResult,
    PostBulkMove,
    PlanRangeRequest,
    PlanRangeResult,
    PostBulkDelete,
    PostBulkGenerate,
    PostCreate,
    PostDuplicate,
    PostOut,
    PostUpdate,
    PreflightIssue,
    PreflightResult,
)
from autoposter.services import lifecycle, media as media_service, planner, publisher, runs
from autoposter.services.llm import llm
from autoposter.services.fanvue_channels import audience_for, fixed_audience


def _audience(channel, requested):
    if not fixed_audience(channel):
        return requested or channel.default_audience
    try:
        return audience_for(channel, requested)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

def _local_time(post: Optional[Post], channel: Channel) -> Optional[datetime]:
    """Geplante Zeit in der Zeitzone des Kanals.

    Der Tagesrhythmus der Persona ist in Ortszeit gedacht – "Mo-Fr 09:30-16:00
    in der Uni" meint ihre Uhrzeit, nicht UTC.
    """
    if not post or not post.scheduled_at:
        return None
    try:
        return post.scheduled_at.astimezone(ZoneInfo(channel.timezone or "UTC"))
    except Exception:  # pragma: no cover – unbekannte Zeitzone
        return post.scheduled_at


def _fill_alt_texts(post: Optional[Post], assets) -> None:
    """Fehlende Alt-Texte aus der Bildbeschreibung nachtragen.

    Sie entstehen im selben Vision-Aufruf – sie danach liegen zu lassen, hieße
    den Preflight ohne Not meckern zu lassen.
    """
    if not post:
        return
    alt = dict(post.alt_texts or {})
    for asset in assets:
        if alt.get(str(asset.id)):
            continue
        text = (asset.caption_hint or asset.ai_description or "").strip()
        if text:
            alt[str(asset.id)] = text[:900]
    post.alt_texts = alt


router = APIRouter(prefix="/posts", tags=["posts"])


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
@router.get("", response_model=List[PostOut])
async def list_posts(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    channel_ids: Optional[str] = Query(default=None, description="Komma-getrennt"),
    statuses: Optional[str] = Query(default=None, description="Komma-getrennt"),
    limit: int = Query(default=500, le=2000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> List[PostOut]:
    stmt = select(Post).order_by(Post.scheduled_at.asc().nullslast()).limit(limit)
    if start:
        stmt = stmt.where(Post.scheduled_at >= start)
    if end:
        stmt = stmt.where(Post.scheduled_at <= end)
    if channel_ids:
        ids = [uuid.UUID(c) for c in channel_ids.split(",") if c.strip()]
        stmt = stmt.where(Post.channel_id.in_(ids))
    if statuses:
        stmt = stmt.where(Post.status.in_([s.strip() for s in statuses.split(",")]))
    rows = (await db.execute(stmt)).scalars().all()
    return [PostOut.model_validate(r) for r in rows]


@router.post("", response_model=PostOut, status_code=201)
async def create_post(
    payload: PostCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
) -> PostOut:
    channel = (
        await db.execute(select(Channel).where(Channel.id == payload.channel_id))
    ).scalar_one_or_none()
    if not channel:
        raise HTTPException(404, "Kanal nicht gefunden")

    post = Post(
        channel_id=payload.channel_id,
        persona_id=channel.persona_id,
        type=payload.type,
        status=payload.status,
        scheduled_at=payload.scheduled_at,
        body_text=payload.body_text,
        hashtags=payload.hashtags,
        media_asset_ids=[str(a) for a in payload.media_asset_ids],
        media_set_id=payload.media_set_id,
        alt_texts=payload.alt_texts,
        audience=_audience(channel, payload.audience),
        price_cents=payload.price_cents,
        media_preview_id=payload.media_preview_id,
        expires_at=payload.expires_at,
        pin_after_publish=payload.pin_after_publish,
        created_by=user.id,
    )
    db.add(post)
    await db.flush()
    await lifecycle.refresh_assets(db, payload.media_asset_ids)
    return PostOut.model_validate(post)


@router.get("/{post_id}", response_model=PostOut)
async def get_post(
    post_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> PostOut:
    post = (await db.execute(select(Post).where(Post.id == post_id))).scalar_one_or_none()
    if not post:
        raise HTTPException(404, "Post nicht gefunden")
    return PostOut.model_validate(post)


@router.patch("/{post_id}", response_model=PostOut)
async def update_post(
    post_id: uuid.UUID,
    payload: PostUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
) -> PostOut:
    post = (await db.execute(select(Post).where(Post.id == post_id))).scalar_one_or_none()
    if not post:
        raise HTTPException(404, "Post nicht gefunden")
    if post.status == PostStatus.published.value:
        raise HTTPException(409, "Veröffentlichte Posts können nicht geändert werden")

    changes = payload.model_dump(exclude_unset=True)
    if 'audience' in changes:
        channel = await db.get(Channel, post.channel_id)
        if fixed_audience(channel):
            changes['audience'] = _audience(channel, changes['audience'])
    diff: Dict[str, Any] = {}
    old_assets = list(post.media_asset_ids or [])

    for key, value in changes.items():
        old = getattr(post, key)
        if key == "media_asset_ids" and value is not None:
            value = [str(v) for v in value]
        if isinstance(old, datetime) and isinstance(value, datetime):
            if old == value:
                continue
        diff[key] = {"from": str(old)[:200], "to": str(value)[:200]}
        setattr(post, key, value)

    if diff:
        db.add(PostRevision(post_id=post.id, editor_id=user.id, diff=diff))
    db.add(post)
    await db.flush()
    await lifecycle.refresh_assets(
        db,
        [uuid.UUID(str(a)) for a in set(old_assets) | set(post.media_asset_ids or [])],
    )
    return PostOut.model_validate(post)


@router.delete("/{post_id}", status_code=204, response_model=None)
async def delete_post(
    post_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> None:
    post = (await db.execute(select(Post).where(Post.id == post_id))).scalar_one_or_none()
    if post:
        assets = [uuid.UUID(str(a)) for a in (post.media_asset_ids or [])]
        await db.delete(post)
        await db.flush()
        await lifecycle.refresh_assets(db, assets)


@router.post("/bulk-move", response_model=Dict[str, int])
async def bulk_move(
    payload: PostBulkMove,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
) -> Dict[str, int]:
    posts = (
        await db.execute(select(Post).where(Post.id.in_(payload.post_ids)))
    ).scalars().all()
    changed = 0
    for post in posts:
        if post.status == PostStatus.published.value:
            continue
        if payload.shift_minutes and post.scheduled_at:
            post.scheduled_at = post.scheduled_at + timedelta(minutes=payload.shift_minutes)
        if payload.new_status:
            post.status = payload.new_status
        db.add(post)
        db.add(
            PostRevision(
                post_id=post.id,
                editor_id=user.id,
                diff={"bulk": {"shift_minutes": payload.shift_minutes, "status": payload.new_status}},
            )
        )
        changed += 1
    return {"updated": changed}


@router.post("/bulk-delete")
async def bulk_delete(
    payload: PostBulkDelete,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> Dict[str, int]:
    """Mehrere Posts löschen. Veröffentlichte bleiben unangetastet –
    ihre Historie ist die Grundlage der Bestandsrechnung."""
    posts = (
        await db.execute(select(Post).where(Post.id.in_(payload.post_ids)))
    ).scalars().all()
    assets: set = set()
    deleted = skipped = 0
    for post in posts:
        if post.status == PostStatus.published.value:
            skipped += 1
            continue
        assets.update(uuid.UUID(str(a)) for a in (post.media_asset_ids or []))
        await db.delete(post)
        deleted += 1
    await db.flush()
    await lifecycle.refresh_assets(db, assets)
    return {"deleted": deleted, "skipped_published": skipped}


@router.post("/{post_id}/duplicate", response_model=List[PostOut])
async def duplicate_post(
    post_id: uuid.UUID,
    payload: PostDuplicate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
) -> List[PostOut]:
    """Post kopieren – auf einen anderen Tag, in einen anderen Kanal, mehrfach."""
    source = (await db.execute(select(Post).where(Post.id == post_id))).scalar_one_or_none()
    if not source:
        raise HTTPException(404, "Post nicht gefunden")

    target_channel_id = payload.channel_id or source.channel_id
    channel = (
        await db.execute(select(Channel).where(Channel.id == target_channel_id))
    ).scalar_one_or_none()
    if not channel:
        raise HTTPException(404, "Zielkanal nicht gefunden")

    base = payload.scheduled_at or source.scheduled_at or datetime.now(timezone.utc)
    copies = max(1, min(payload.copies, 30))
    made: List[Post] = []

    for index in range(copies):
        when = base + timedelta(days=payload.shift_days * index) if index else base
        clone = Post(
            channel_id=target_channel_id,
            persona_id=channel.persona_id,
            type=source.type,
            status=PostStatus.needs_review.value,
            scheduled_at=when,
            body_text=source.body_text,
            body_text_variants=list(source.body_text_variants or []),
            hashtags=list(source.hashtags or []),
            alt_texts=dict(source.alt_texts or {}),
            media_asset_ids=list(source.media_asset_ids or []),
            media_set_id=source.media_set_id,
            audience=fixed_audience(channel) or source.audience or channel.default_audience,
            price_cents=source.price_cents,
            media_preview_id=source.media_preview_id,
            pin_after_publish=source.pin_after_publish,
            generation_meta={"duplicated_from": str(source.id)},
            created_by=user.id,
        )
        db.add(clone)
        made.append(clone)

    await db.flush()
    await lifecycle.refresh_assets(
        db, [uuid.UUID(str(a)) for a in (source.media_asset_ids or [])]
    )
    return [PostOut.model_validate(p) for p in made]


@router.get("/{post_id}/revisions")
async def revisions(
    post_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> List[Dict[str, Any]]:
    rows = (
        await db.execute(
            select(PostRevision)
            .where(PostRevision.post_id == post_id)
            .order_by(PostRevision.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return [
        {"id": str(r.id), "created_at": r.created_at.isoformat(), "diff": r.diff} for r in rows
    ]


@router.get("/{post_id}/attempts")
async def attempts(
    post_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> List[Dict[str, Any]]:
    rows = (
        await db.execute(
            select(PublishAttempt)
            .where(PublishAttempt.post_id == post_id)
            .order_by(PublishAttempt.started_at.desc())
        )
    ).scalars().all()
    return [
        {
            "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "http_status": r.http_status,
            "outcome": r.outcome,
            "dry_run": r.dry_run,
            "request_id": r.request_id,
            "response": r.response_excerpt,
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Preflight, Generierung, Veröffentlichung
# --------------------------------------------------------------------------- #
@router.get("/{post_id}/preflight", response_model=PreflightResult)
async def preflight(
    post_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> PreflightResult:
    post = (await db.execute(select(Post).where(Post.id == post_id))).scalar_one_or_none()
    if not post:
        raise HTTPException(404, "Post nicht gefunden")
    issues = await planner.preflight(db, post)
    return PreflightResult(
        ok=not any(i["level"] == "error" for i in issues),
        issues=[PreflightIssue(**i) for i in issues],
    )


@router.post("/translate", response_model=TranslateTextResult)
async def translate_text(
    payload: TranslateTextRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> TranslateTextResult:
    text = payload.text.strip()
    if not text:
        raise HTTPException(422, "Bitte zuerst einen Text eingeben.")
    from autoposter.services.llm import BudgetExceeded
    try:
        translation = await llm.translate_german(db, text)
    except BudgetExceeded as exc:
        raise HTTPException(429, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return TranslateTextResult(translation=translation)


@router.post("/generate", response_model=GenerateResult)
async def generate(
    payload: GenerateRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> GenerateResult:
    post: Optional[Post] = None
    channel_id = payload.channel_id
    asset_ids = list(payload.media_asset_ids)

    if payload.post_id:
        post = (
            await db.execute(select(Post).where(Post.id == payload.post_id))
        ).scalar_one_or_none()
        if not post:
            raise HTTPException(404, "Post nicht gefunden")
        channel_id = post.channel_id
        asset_ids = [uuid.UUID(str(a)) for a in (post.media_asset_ids or [])]

    if not channel_id:
        raise HTTPException(400, "Kanal fehlt")

    channel = (
        await db.execute(select(Channel).where(Channel.id == channel_id))
    ).scalar_one_or_none()
    if not channel:
        raise HTTPException(404, "Kanal nicht gefunden")

    persona = (
        (await db.execute(select(Persona).where(Persona.id == channel.persona_id))).scalar_one_or_none()
        if channel.persona_id
        else None
    )
    descriptions: List[str] = []
    if asset_ids:
        assets = (
            await db.execute(select(MediaAsset).where(MediaAsset.id.in_(asset_ids)))
        ).scalars().all()
        await media_service.ensure_descriptions(db, assets)
        _fill_alt_texts(post, assets)
        descriptions = [
            a.ai_description or a.caption_hint for a in assets if (a.ai_description or a.caption_hint)
        ]

    limits = get_adapter(channel.platform).limits()
    try:
        result = await llm.generate_post_text(
            db,
            persona=persona,
            platform=channel.platform,
            channel_id=channel.id,
            image_descriptions=descriptions,
            has_image=bool(asset_ids),
            instruction=payload.instruction,
            variants=payload.variants,
            max_chars=limits.max_chars,
            when_local=_local_time(post, channel),
        )
    except Exception as exc:
        raise HTTPException(502, f"Generierung fehlgeschlagen: {exc}")

    if post:
        post.body_text_variants = result.variants
        post.generation_meta = {
            **(post.generation_meta or {}),
            "model": result.model,
            "cost_usd": result.cost_usd,
        }
        if not post.body_text and result.variants:
            post.body_text = result.variants[0]["text"]
            post.hashtags = result.variants[0].get("hashtags", [])
        db.add(post)

    return GenerateResult(variants=result.variants, model=result.model, cost_usd=result.cost_usd)


@router.post("/bulk-generate")
async def bulk_generate(
    payload: PostBulkGenerate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> Dict[str, Any]:
    """Texte mehrerer Posts neu erzeugen (blockierend, für wenige Posts)."""
    return await _bulk_generate(db, payload)


@router.post("/bulk-generate/start")
async def bulk_generate_start(
    payload: PostBulkGenerate, _: User = Depends(require_editor)
) -> Dict[str, Any]:
    """Dasselbe im Hintergrund, mit Fortschritt und Abbruch."""
    label = f"{len(payload.post_ids)} Texte neu erzeugen"
    run = runs.create(label, total=max(1, len(payload.post_ids)))

    async def work(current: runs.Run) -> Dict[str, Any]:
        async with session_scope() as db:
            return await _bulk_generate(db, payload, run=current)

    runs.launch(run, work)
    return {"run_id": run.id, "label": label}


async def _bulk_generate(
    db: AsyncSession, payload: PostBulkGenerate, run: Optional[runs.Run] = None
) -> Dict[str, Any]:
    """Texte mehrerer Posts neu erzeugen.

    Nacheinander und nicht parallel: Jeder Aufruf sieht die schon erzeugten
    Texte und grenzt sich davon ab. Parallel liefen sie ins selbe Muster.
    """
    posts = (
        await db.execute(
            select(Post)
            .where(Post.id.in_(payload.post_ids))
            .order_by(Post.scheduled_at)
        )
    ).scalars().all()

    done = failed = skipped = 0
    errors: List[str] = []
    if run is not None:
        run.total = max(1, len(posts))
    for index, post in enumerate(posts, start=1):
        runs.check(run)
        if run is not None:
            when = post.scheduled_at.strftime("%d.%m. %H:%M") if post.scheduled_at else "ohne Termin"
            run.message = f"Post {index} von {len(posts)} — {when}"
        if post.status == PostStatus.published.value:
            skipped += 1
            runs.step(run)
            continue
        channel = (
            await db.execute(select(Channel).where(Channel.id == post.channel_id))
        ).scalar_one_or_none()
        if not channel:
            skipped += 1
            runs.step(run)
            continue
        persona = (
            (
                await db.execute(select(Persona).where(Persona.id == channel.persona_id))
            ).scalar_one_or_none()
            if channel.persona_id
            else None
        )
        asset_ids = [uuid.UUID(str(a)) for a in (post.media_asset_ids or [])]
        descriptions: List[str] = []
        if asset_ids:
            assets = (
                await db.execute(select(MediaAsset).where(MediaAsset.id.in_(asset_ids)))
            ).scalars().all()
            await media_service.ensure_descriptions(db, assets)
            _fill_alt_texts(post, assets)
            descriptions = [
                a.ai_description or a.caption_hint
                for a in assets
                if (a.ai_description or a.caption_hint)
            ]

        # Der eigene alte Text darf das Ergebnis nicht beeinflussen – er wird
        # für die Dauer des Aufrufs geleert. Scheitert die Generierung, muss er
        # zurück: ein Post ohne Text ist schlechter als der alte Text.
        previous_text = post.body_text
        previous_hashtags = list(post.hashtags or [])
        post.body_text = ""
        await db.flush()

        try:
            result = await llm.generate_post_text(
                db,
                persona=persona,
                platform=channel.platform,
                channel_id=channel.id,
                image_descriptions=descriptions,
                has_image=bool(asset_ids),
                instruction=payload.instruction,
                variants=payload.variants,
                max_chars=get_adapter(channel.platform).limits().max_chars,
                when_local=_local_time(post, channel),
            )
        except Exception as exc:  # noqa: BLE001 – Sammelvorgang darf nicht abbrechen
            post.body_text = previous_text
            post.hashtags = previous_hashtags
            post.generation_meta = {**(post.generation_meta or {}), "error": str(exc)[:500]}
            db.add(post)
            await db.flush()
            failed += 1
            if len(errors) < 5:
                errors.append(str(exc))
            runs.note(run, str(exc)[:200])
            runs.step(run)
            continue

        if result.variants and result.variants[0]["text"]:
            post.body_text = result.variants[0]["text"]
            post.hashtags = result.variants[0].get("hashtags", [])
            post.body_text_variants = result.variants
            post.generation_meta = {
                **(post.generation_meta or {}),
                "model": result.model,
                "cost_usd": result.cost_usd,
                "regenerated": True,
            }
            post.generation_meta.pop("error", None)
            db.add(post)
            done += 1
        else:
            # Leere Antwort des Modells – auch das ist ein Fehlschlag.
            post.body_text = previous_text
            post.hashtags = previous_hashtags
            post.generation_meta = {
                **(post.generation_meta or {}),
                "error": "Das Modell lieferte keinen Text",
            }
            db.add(post)
            failed += 1
            runs.note(run, "Leere Antwort des Modells")
        await db.flush()
        runs.step(run)
        # Zwischenstand sichern, damit ein Abbruch das Erreichte behält.
        if run is not None and done % 5 == 0:
            await db.commit()

    return {
        "generated": done,
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
    }


@router.post("/{post_id}/publish")
async def publish_now(
    post_id: uuid.UUID,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> Dict[str, Any]:
    post = (await db.execute(select(Post).where(Post.id == post_id))).scalar_one_or_none()
    if not post:
        raise HTTPException(404, "Post nicht gefunden")
    channel = (
        await db.execute(select(Channel).where(Channel.id == post.channel_id))
    ).scalar_one()
    await publisher.split_thread_if_needed(db, post, channel)
    ok, message = await publisher.publish_post(db, post_id, force=force)
    return {"ok": ok, "message": message, "dry_run": settings.dry_run}


@router.post("/{post_id}/approve", response_model=PostOut)
async def approve(
    post_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
) -> PostOut:
    post = (await db.execute(select(Post).where(Post.id == post_id))).scalar_one_or_none()
    if not post:
        raise HTTPException(404, "Post nicht gefunden")
    issues = await planner.preflight(db, post)
    blocking = [i for i in issues if i["level"] == "error"]
    if blocking:
        raise HTTPException(400, "; ".join(i["message"] for i in blocking))
    post.status = PostStatus.scheduled.value
    post.approved_by = user.id
    post.error_message = ""
    db.add(post)
    await db.flush()
    return PostOut.model_validate(post)


# --------------------------------------------------------------------------- #
# Kalender-Automatik
# --------------------------------------------------------------------------- #
def _validate_plan(payload: PlanRangeRequest) -> None:
    if payload.date_to < payload.date_from:
        raise HTTPException(400, "Das Enddatum liegt vor dem Startdatum")
    if (payload.date_to - payload.date_from).days > 180:
        raise HTTPException(400, "Der Zeitraum darf höchstens 180 Tage umfassen")
    if payload.image_posts_per_day + payload.text_posts_per_day <= 0:
        raise HTTPException(400, "Mindestens ein Post pro Tag muss eingestellt sein")


calendar_router = APIRouter(prefix="/calendar", tags=["calendar"])


@calendar_router.post("/fill", response_model=PlanFillResult)
async def fill(
    payload: PlanFillRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
) -> PlanFillResult:
    return await planner.fill_calendar(
        db,
        channel_ids=payload.channel_ids or None,
        days=payload.days,
        dry_run=payload.dry_run,
        actor_id=user.id,
    )


@calendar_router.post("/plan", response_model=PlanRangeResult)
async def plan_range(
    payload: PlanRangeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
) -> PlanRangeResult:
    """Automatisch planen für einen Datumsbereich.

    Mit dry_run=True liefert der Aufruf nur die Vorschau samt Vorratsprüfung –
    ohne irgendetwas anzulegen.
    """
    _validate_plan(payload)
    return await planner.plan_range(db, payload, actor_id=user.id)


@calendar_router.post("/plan/start")
async def plan_range_start(
    payload: PlanRangeRequest,
    user: User = Depends(require_editor),
) -> Dict[str, Any]:
    """Planungslauf im Hintergrund starten.

    Das Planen eines Monats sind schnell hundert LLM-Aufrufe – als eine einzige
    Anfrage minutenlanges Warten ohne Rückmeldung. Hier kommt sofort eine
    Kennung zurück, über die die Oberfläche den Fortschritt abfragt und den
    Lauf abbrechen kann.
    """
    _validate_plan(payload)
    actor_id = user.id  # vor dem Hintergrundlauf festhalten, die Sitzung endet gleich
    label = "Kalender planen: {} bis {}".format(payload.date_from, payload.date_to)
    run = runs.create(label)

    async def work(current: runs.Run) -> Dict[str, Any]:
        async with session_scope() as db:
            result = await planner.plan_range(db, payload, actor_id=actor_id, run=current)
            return {
                "created": len(result.created_post_ids),
                "without_text": len(result.text_failed),
                "gaps": len(result.gaps),
                "gap_reasons": [g.get("reason", "") for g in result.gaps[:10]],
                "capacity": [c.model_dump(mode="json") for c in result.capacity],
            }

    runs.launch(run, work)
    return {"run_id": run.id, "label": label}


@calendar_router.get("/month")
async def month_overview(
    year: int,
    month: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> Dict[str, Any]:
    """Alle Posts eines Monats plus Tageszahlen für die Monatsansicht."""
    from calendar import monthrange

    start = datetime(year, month, 1, tzinfo=timezone.utc)
    last_day = monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
    # Rand mitnehmen, damit die angeschnittenen Wochen gefüllt sind.
    start_pad = start - timedelta(days=7)
    end_pad = end + timedelta(days=7)

    posts = (
        await db.execute(
            select(Post)
            .where(Post.scheduled_at.isnot(None), Post.scheduled_at >= start_pad,
                   Post.scheduled_at <= end_pad)
            .order_by(Post.scheduled_at)
        )
    ).scalars().all()

    by_day: Dict[str, Dict[str, int]] = {}
    for post in posts:
        key = post.scheduled_at.date().isoformat()
        entry = by_day.setdefault(key, {"total": 0, "image": 0, "text": 0, "failed": 0})
        entry["total"] += 1
        entry["image" if post.media_asset_ids else "text"] += 1
        if post.status == PostStatus.failed.value:
            entry["failed"] += 1

    blackouts = (
        await db.execute(
            select(BlackoutPeriod).where(
                BlackoutPeriod.ends_at >= start_pad, BlackoutPeriod.starts_at <= end_pad
            )
        )
    ).scalars().all()

    return {
        "year": year,
        "month": month,
        "posts": [PostOut.model_validate(p).model_dump(mode="json") for p in posts],
        "by_day": by_day,
        "blackouts": [
            {
                "id": str(b.id),
                "channel_id": str(b.channel_id) if b.channel_id else None,
                "name": b.name,
                "starts_at": b.starts_at.isoformat(),
                "ends_at": b.ends_at.isoformat(),
            }
            for b in blackouts
        ],
    }


@calendar_router.get("/blackouts")
async def list_blackouts(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> List[Dict[str, Any]]:
    rows = (await db.execute(select(BlackoutPeriod).order_by(BlackoutPeriod.starts_at))).scalars().all()
    return [
        {
            "id": str(b.id),
            "channel_id": str(b.channel_id) if b.channel_id else None,
            "name": b.name,
            "starts_at": b.starts_at.isoformat(),
            "ends_at": b.ends_at.isoformat(),
        }
        for b in rows
    ]


@calendar_router.post("/blackouts", status_code=201)
async def create_blackout(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> Dict[str, str]:
    blackout = BlackoutPeriod(
        channel_id=uuid.UUID(payload["channel_id"]) if payload.get("channel_id") else None,
        name=payload.get("name", ""),
        starts_at=datetime.fromisoformat(payload["starts_at"]),
        ends_at=datetime.fromisoformat(payload["ends_at"]),
    )
    db.add(blackout)
    await db.flush()
    return {"id": str(blackout.id)}


@calendar_router.delete("/blackouts/{blackout_id}", status_code=204, response_model=None)
async def delete_blackout(
    blackout_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> None:
    row = (
        await db.execute(select(BlackoutPeriod).where(BlackoutPeriod.id == blackout_id))
    ).scalar_one_or_none()
    if row:
        await db.delete(row)


@calendar_router.get("/export.csv")
async def export_csv(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> Dict[str, str]:
    rows = (
        await db.execute(select(Post).order_by(Post.scheduled_at.asc().nullslast()))
    ).scalars().all()
    lines = ["scheduled_at;channel_id;status;type;text;media_count;external_url"]
    for post in rows:
        text = (post.body_text or "").replace("\n", " ").replace(";", ",")
        lines.append(
            ";".join(
                [
                    post.scheduled_at.isoformat() if post.scheduled_at else "",
                    str(post.channel_id),
                    post.status,
                    post.type,
                    text,
                    str(len(post.media_asset_ids or [])),
                    post.external_url,
                ]
            )
        )
    return {"csv": "\n".join(lines)}


# --------------------------------------------------------------------------- #
# Hintergrundläufe: Fortschritt und Abbruch
# --------------------------------------------------------------------------- #
runs_router = APIRouter(prefix="/runs", tags=["runs"])


@runs_router.get("/{run_id}")
async def run_status(run_id: str, _: User = Depends(current_user)) -> Dict[str, Any]:
    run = runs.get(run_id)
    if not run:
        raise HTTPException(404, "Unbekannter Lauf – vermutlich abgelaufen oder Server neu gestartet")
    return run.to_dict()


@runs_router.post("/{run_id}/cancel")
async def run_cancel(run_id: str, _: User = Depends(require_editor)) -> Dict[str, Any]:
    if not runs.cancel(run_id):
        raise HTTPException(409, "Der Lauf läuft nicht mehr")
    return {"cancelled": True}


@runs_router.get("")
async def run_list(_: User = Depends(current_user)) -> List[Dict[str, Any]]:
    """Was läuft gerade? Damit die Oberfläche nach einem Neuladen wieder andockt."""
    return [r.to_dict() for r in runs.active()]
