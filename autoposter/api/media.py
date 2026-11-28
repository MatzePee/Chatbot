"""Bibliothek: Upload, Filter, Detail, Sets, gespeicherte Ansichten, Wartung."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
import zipfile
from io import BytesIO
from typing import Any, Dict, List, Optional, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db import get_db, write_session
from autoposter.deps import current_user, require_admin, require_editor
from autoposter.models import (
    Channel,
    Lifecycle,
    MediaAsset,
    MediaAssignment,
    MediaChannelUsage,
    MediaSet,
    MediaSetItem,
    MediaStatus,
    Post,
    PostStatus,
    SavedView,
    User,
)
from autoposter.schemas import (
    LifecycleCounts,
    MediaBulkUpdate,
    MediaDetail,
    MediaOut,
    MediaPage,
    MediaQuery,
    MediaSetCreate,
    MediaSetOut,
    MediaSetUpdate,
    MediaUpdate, ImageCropSave,
    MediaUsageEntry,
    MaintenanceReport,
    SavedViewIn,
    SavedViewOut,
)
from autoposter.services import library, reuse, lifecycle as lifecycle_service, media as media_service

router = APIRouter(prefix="/media", tags=["media"])


def _to_out(asset: MediaAsset) -> MediaOut:
    out = MediaOut.model_validate(asset)
    out.thumb_url, out.url = media_service.public_urls(asset)
    return out


async def _attach_sets(db: AsyncSession, items: List[MediaOut]) -> List[MediaOut]:
    """Set-Zugehörigkeit nachtragen – ein Query für die ganze Seite.

    Je Bild einzeln zu fragen wäre bei 120 Kacheln 120 Abfragen; die Bibliothek
    lädt aber laufend nach.
    """
    if not items:
        return items
    ids = [uuid.UUID(str(i.id)) for i in items]
    rows = (
        await db.execute(
            select(MediaSetItem.media_asset_id, MediaSet.id, MediaSet.name)
            .join(MediaSet, MediaSet.id == MediaSetItem.media_set_id)
            .where(MediaSetItem.media_asset_id.in_(ids))
            .order_by(MediaSetItem.position)
        )
    ).all()
    by_asset: Dict[str, List[tuple]] = {}
    for asset_id, set_id, name in rows:
        by_asset.setdefault(str(asset_id), []).append((str(set_id), name))
    for item in items:
        entries = by_asset.get(str(item.id), [])
        item.set_ids = [e[0] for e in entries]
        item.set_names = [e[1] for e in entries]
    return items


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
@router.post("/upload", status_code=201)
async def upload(
    files: List[UploadFile] = File(...),
    tags: str = Form(default=""),
    nsfw_level: str = Form(default="sfw"),
    source_note: str = Form(default=""),
    assign_channel_ids: str = Form(default=""),
    user: User = Depends(require_editor),
) -> Dict[str, Any]:
    """Bilder aufnehmen.

    Bewusst OHNE die übliche get_db-Sitzung: jede Datei bekommt ihre eigene,
    sehr kurze Transaktion. Die Bildverarbeitung läuft dazwischen in einem
    Thread. Sonst hielte ein Upload mit vielen Bildern die SQLite-Schreibsperre
    minutenlang und alles andere liefe auf "database is locked".
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    channel_ids = [uuid.UUID(c) for c in assign_channel_ids.split(",") if c.strip()]

    created: List[MediaOut] = []
    created_ids: List[uuid.UUID] = []
    duplicates: List[Dict[str, str]] = []
    errors: List[Dict[str, str]] = []

    async def handle(name: str, raw: bytes) -> None:
        try:
            # 1) Rechnen – ohne Datenbank, ohne Ereignisschleife zu blockieren.
            prepared = await asyncio.to_thread(media_service.prepare_image, name, raw)
        except Exception as exc:
            errors.append({"filename": name, "message": str(exc)})
            return
        try:
            # 2) Schreiben – Millisekunden.
            async with write_session() as db:
                asset = await media_service.store_prepared(
                    db,
                    prepared,
                    uploaded_by=user.id,
                    tags=tag_list,
                    nsfw_level=nsfw_level,
                    source_note=source_note,
                )
                created.append(_to_out(asset))
                created_ids.append(asset.id)
        except media_service.DuplicateUpload as dup:
            duplicates.append(
                {
                    "filename": name,
                    "existing_id": str(dup.existing.id),
                    "message": "Diese Datei ist bereits im Bestand",
                }
            )
        except Exception as exc:
            media_service.discard_prepared(prepared)
            errors.append({"filename": name, "message": str(exc)})

    for upload_file in files:
        raw = await upload_file.read()
        filename = upload_file.filename or "upload"
        if filename.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(BytesIO(raw)) as archive:
                    members = [
                        m for m in archive.namelist()
                        if not m.endswith("/") and not m.startswith("__MACOSX")
                    ]
                    for member in members:
                        await handle(member.split("/")[-1], archive.read(member))
            except zipfile.BadZipFile:
                errors.append({"filename": filename, "message": "Kein gültiges ZIP-Archiv"})
        else:
            await handle(filename, raw)

    # 3) Zuordnung – ebenfalls in einer eigenen kurzen Transaktion.
    assigned = 0
    rejected: List[Dict[str, str]] = []
    if channel_ids and created_ids:
        from autoposter.schemas import AssignmentBatch
        from autoposter.services import assignment

        async with write_session() as db:
            outcome = await assignment.assign(
                db,
                AssignmentBatch(asset_ids=created_ids, channel_ids=channel_ids, mode="copy"),
                actor_id=user.id,
            )
            assigned = outcome.created
            rejected = outcome.rejected

    library.invalidate_counts()
    return {
        "created": created,
        "duplicates": duplicates,
        "errors": errors,
        "assigned": assigned,
        "rejected": rejected,
        "summary": {
            "created": len(created),
            "duplicates": len(duplicates),
            "errors": len(errors),
            "assigned": assigned,
        },
    }


# --------------------------------------------------------------------------- #
# Suche & Zähler
# --------------------------------------------------------------------------- #
@router.post("/search", response_model=MediaPage)
async def search(
    query: MediaQuery,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> MediaPage:
    assets, cursor = await library.search(db, query)
    items = await _attach_sets(db, [_to_out(a) for a in assets])
    return MediaPage(items=items, next_cursor=cursor)


@router.get("/counts", response_model=LifecycleCounts)
async def counts(
    fresh: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> LifecycleCounts:
    return await library.lifecycle_counts(db, use_cache=not fresh)


@router.get("/tags")
async def tags(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> List[Dict[str, Any]]:
    return await library.tag_facets(db)


@router.get("/matrix")
async def matrix(
    limit: int = Query(default=200, le=1000),
    cursor: Optional[str] = None,
    lifecycle: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> Dict[str, Any]:
    """Bilder x Kanäle als Matrix — schnellste Sicht auf 'was fehlt wo noch'."""
    query = MediaQuery(limit=limit, cursor=cursor)
    if lifecycle:
        query.lifecycle = [lifecycle]
    assets, next_cursor = await library.search(db, query)
    channels = (
        await db.execute(select(Channel).where(Channel.is_active.is_(True)).order_by(Channel.display_name))
    ).scalars().all()

    releases = await reuse.all_channels(db)
    rows = []
    for asset in assets:
        cells = {}
        for channel in channels:
            cid = str(channel.id)
            if cid in (asset.used_channel_ids or []) and cid not in releases.get(str(asset.id), []):
                cells[cid] = "published"
            elif cid in (asset.scheduled_channel_ids or []):
                cells[cid] = "scheduled"
            elif cid in (asset.assigned_channel_ids or []):
                cells[cid] = "assigned"
            else:
                cells[cid] = "none"
        out = _to_out(asset)
        out.reusable_channel_ids = releases.get(str(asset.id), [])
        rows.append(
            {
                "asset": out.model_dump(mode="json"),
                "cells": cells,
            }
        )
    return {
        "channels": [
            {
                "id": str(c.id),
                "name": c.display_name,
                "platform": c.platform,
                "color": c.color,
            }
            for c in channels
        ],
        "rows": rows,
        "next_cursor": next_cursor,
    }


@router.get("/timeline")
async def timeline(
    year: int,
    month: int,
    channel_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> List[Dict[str, Any]]:
    """Archiv-Kalender: welches Bild lief wann auf welchem Kanal."""
    from datetime import datetime, timezone

    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year + (month // 12), (month % 12) + 1, 1, tzinfo=timezone.utc)
    stmt = (
        select(MediaChannelUsage, MediaAsset, Channel)
        .join(MediaAsset, MediaAsset.id == MediaChannelUsage.media_asset_id)
        .join(Channel, Channel.id == MediaChannelUsage.channel_id)
        .where(MediaChannelUsage.used_at >= start, MediaChannelUsage.used_at < end)
        .order_by(MediaChannelUsage.used_at)
    )
    if channel_id:
        stmt = stmt.where(MediaChannelUsage.channel_id == channel_id)
    rows = (await db.execute(stmt)).all()
    return [
        {
            "used_at": usage.used_at.isoformat(),
            "asset_id": str(asset.id),
            "thumb_url": media_service.public_urls(asset)[0],
            "filename": asset.filename,
            "channel_id": str(channel.id),
            "channel_name": channel.display_name,
            "platform": channel.platform,
            "color": channel.color,
            "post_id": str(usage.post_id) if usage.post_id else None,
        }
        for usage, asset, channel in rows
    ]


# --------------------------------------------------------------------------- #
# Einzelnes Asset
# --------------------------------------------------------------------------- #
@router.get("/{asset_id}", response_model=MediaDetail)
async def detail(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> MediaDetail:
    asset = (
        await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    ).scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Bild nicht gefunden")

    rows = (
        await db.execute(
            select(MediaChannelUsage, Channel)
            .join(Channel, Channel.id == MediaChannelUsage.channel_id)
            .where(MediaChannelUsage.media_asset_id == asset_id)
            .order_by(MediaChannelUsage.used_at.desc())
        )
    ).all()

    history: List[MediaUsageEntry] = []
    for usage, channel in rows:
        post = None
        if usage.post_id:
            post = (
                await db.execute(select(Post).where(Post.id == usage.post_id))
            ).scalar_one_or_none()
        history.append(
            MediaUsageEntry(
                channel_id=channel.id,
                channel_name=channel.display_name,
                platform=channel.platform,
                used_at=usage.used_at,
                post_id=usage.post_id,
                post_text=(post.body_text[:160] if post else ""),
                external_url=(post.external_url if post else ""),
                metrics=(post.metrics if post else {}),
            )
        )

    set_ids = (
        await db.execute(
            select(MediaSetItem.media_set_id).where(MediaSetItem.media_asset_id == asset_id)
        )
    ).scalars().all()

    out = MediaDetail(**(await _attach_sets(db, [_to_out(asset)]))[0].model_dump())
    out.history = history
    out.open_channels = [
        uuid.UUID(c)
        for c in (asset.assigned_channel_ids or [])
        if c not in (asset.used_channel_ids or [])
    ]
    out.sets = list(set_ids)
    return out


@router.get("/{asset_id}/edit-source/{version}")
async def edit_source(asset_id: uuid.UUID, version: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)):
    from autoposter.services.image_edits import retouch_source
    asset = await db.get(MediaAsset, asset_id)
    if not asset:
        raise HTTPException(404, "Bild nicht gefunden")
    return FileResponse(retouch_source(asset, version), headers={"Cache-Control": "private, no-store"})


@router.post("/{asset_id}/erase")
async def erase(asset_id: uuid.UUID, image: UploadFile, mask: Optional[UploadFile] = None,
                operation: Literal["erase", "inpaint", "remove", "outpaint", "full", "krea", "face", "hands", "feet"] = Form("erase"), prompt: str = Form("", max_length=4000),
                steps: int = Form(3, ge=1, le=100), denoise: Optional[float] = Form(None, ge=0, le=1),
                db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)):
    from autoposter.services import comfyui
    from autoposter.crypto import encrypt
    if not await db.get(MediaAsset, asset_id):
        raise HTTPException(404, "Bild nicht gefunden")
    detail = operation if operation in comfyui.detailers.DEFAULT_DENOISE else None
    limit = (20 if detail or operation in ("full", "krea") else 5) * 1024 * 1024
    if not detail and operation != "full" and mask is None:
        raise HTTPException(422, "Für dieses Werkzeug wird eine Maske benötigt.")
    photo = await image.read(limit + 1)
    matte = await mask.read(limit + 1) if mask and not detail and operation != "full" else None
    if max(len(photo), len(matte or b"")) > limit:
        raise HTTPException(413, "Bildausschnitt zu groß")
    instruction = prompt.strip()
    if operation in ("inpaint", "full", "krea") and not instruction:
        raise HTTPException(422, "Bitte beschreibe die gewünschte Änderung.")
    if operation == "outpaint":
        instruction = comfyui.OUTPAINT_PROMPT + " " + instruction
    job = await comfyui.submit(photo, matte, instruction if operation != "erase" else None, remove=operation == "remove", krea=operation == "krea",
                               detail=detail, steps=steps, denoise=denoise)
    return {"job": encrypt(f"{asset_id}/{job}")}


@router.get("/{asset_id}/erase/{token}")
async def erase_result(asset_id: uuid.UUID, token: str, db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)):
    from autoposter.crypto import decrypt
    from autoposter.services import comfyui
    try:
        owner, job = decrypt(token).split("/")
        if owner != str(asset_id) or not await db.get(MediaAsset, asset_id):
            raise ValueError
    except (ValueError, RuntimeError):
        raise HTTPException(404, "Auftrag nicht gefunden")
    return await comfyui.result(job)


@router.put("/{asset_id}/crop", response_model=MediaDetail)
async def save_crop(asset_id: uuid.UUID, payload: ImageCropSave, db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)):
    from autoposter.services import image_edits
    asset = await db.get(MediaAsset, asset_id)
    if not asset:
        raise HTTPException(404, "Bild nicht gefunden")
    await image_edits.save(db, asset, payload)
    await db.commit()
    return await detail(asset_id, db, _)


@router.get("/{asset_id}/file")
async def file(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(current_user), variant: Literal["all", "instagram", "original"] = "all") -> FileResponse:
    asset = (
        await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    ).scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Bild nicht gefunden")
    try:
        from autoposter.services.image_edits import selected_path
        path = selected_path(asset, "instagram" if variant == "instagram" else None, original=variant == "original")
    except ValueError:
        raise HTTPException(404, "Datei nicht im Character-Arbeitsbereich")
    if not path.is_file():
        raise HTTPException(404, "Datei fehlt auf der Platte")
    return FileResponse(path, media_type=asset.mime, headers={"Cache-Control": "private, no-store"})


@router.get("/{asset_id}/thumb")
async def thumb(
    asset_id: uuid.UUID, size: int = 256, db: AsyncSession = Depends(get_db), _: User = Depends(current_user), variant: Literal["all", "instagram", "original"] = "all"
) -> FileResponse:
    asset = (
        await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    ).scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Bild nicht gefunden")
    try:
        path = media_service.thumb_path(asset, size, platform="instagram" if variant == "instagram" else None, original=variant == "original") or media_service.thumb_path(asset, 256, platform="instagram" if variant == "instagram" else None, original=variant == "original")
    except ValueError:
        raise HTTPException(404, "Vorschaubild nicht im Character-Arbeitsbereich")
    if not path:
        return await file(asset_id, db, variant=variant)
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"}
    )


@router.patch("/{asset_id}", response_model=MediaOut)
async def update(
    asset_id: uuid.UUID,
    payload: MediaUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> MediaOut:
    asset = (
        await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    ).scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Bild nicht gefunden")
    old_price = asset.telegram_price_stars
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(asset, key, value)
    if old_price != asset.telegram_price_stars:
        from autoposter.services import telegram_paid
        await telegram_paid.invalidate(db, asset_id=asset.id)
    db.add(asset)
    await db.flush()
    await lifecycle_service.refresh_asset(db, asset)
    library.invalidate_counts()
    return _to_out(asset)


@router.post("/bulk", response_model=Dict[str, int])
async def bulk_update(
    payload: MediaBulkUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> Dict[str, int]:
    assets = (
        await db.execute(select(MediaAsset).where(MediaAsset.id.in_(payload.asset_ids)))
    ).scalars().all()
    for asset in assets:
        tags = set(asset.tags or [])
        tags |= set(payload.add_tags)
        tags -= set(payload.remove_tags)
        asset.tags = sorted(tags)
        if payload.nsfw_level:
            asset.nsfw_level = payload.nsfw_level
        if payload.archive is not None:
            asset.status = (
                MediaStatus.archived.value if payload.archive else MediaStatus.ready.value
            )
        db.add(asset)
        if payload.archive:
            from autoposter.services import telegram_paid
            await telegram_paid.invalidate(db, asset_id=asset.id, include_sets=True)
    await db.flush()
    await lifecycle_service.refresh_assets(db, [a.id for a in assets])
    library.invalidate_counts()
    return {"updated": len(assets)}


@router.post("/{asset_id}/archive", response_model=MediaOut)
async def archive(
    asset_id: uuid.UUID,
    archived: bool = True,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> MediaOut:
    await media_service.archive(db, [asset_id], archived)
    if archived:
        from autoposter.services import telegram_paid
        await telegram_paid.invalidate(db, asset_id=asset_id, include_sets=True)
    library.invalidate_counts()
    asset = (
        await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    ).scalar_one()
    return _to_out(asset)


@router.delete("/{asset_id}", status_code=204, response_model=None)
async def delete_asset(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> None:
    asset = (
        await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    ).scalar_one_or_none()
    if asset:
        open_posts = (await db.execute(select(Post).where(Post.status.notin_(
            [PostStatus.published.value, PostStatus.cancelled.value, PostStatus.skipped.value]
        )))).scalars().all()
        if any(str(asset_id) in {str(value) for value in (post.media_asset_ids or [])} for post in open_posts):
            raise HTTPException(409, "Dieses Bild wird noch in offenen Posts verwendet. Entferne es zuerst aus diesen Posts oder archiviere es stattdessen.")
        releases = await reuse.all_channels(db)
        for channel_id in releases.get(str(asset_id), []):
            await reuse.consume(db, [asset_id], channel_id)
        await db.delete(asset)
        await db.flush()
        library.invalidate_counts()


@router.get("/{asset_id}/similar")
async def similar(
    asset_id: uuid.UUID,
    max_distance: int = 8,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> List[Dict[str, Any]]:
    asset = (
        await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    ).scalar_one_or_none()
    if not asset or not asset.phash:
        return []
    hits = await media_service.find_similar(
        db, asset.phash, max_distance=max_distance, exclude_id=asset_id
    )
    return [
        {"asset": _to_out(a).model_dump(mode="json"), "distance": d} for a, d in hits[:50]
    ]


# --------------------------------------------------------------------------- #
# Sets
# --------------------------------------------------------------------------- #
sets_router = APIRouter(prefix="/sets", tags=["sets"])


async def _set_out(db: AsyncSession, media_set: MediaSet) -> MediaSetOut:
    items = (
        await db.execute(
            select(MediaSetItem.media_asset_id)
            .where(MediaSetItem.media_set_id == media_set.id)
            .order_by(MediaSetItem.position)
        )
    ).scalars().all()
    channels = (
        await db.execute(
            select(MediaAssignment.channel_id).where(MediaAssignment.media_set_id == media_set.id)
        )
    ).scalars().all()
    out = MediaSetOut.model_validate(media_set)
    out.asset_ids = list(items)
    out.assigned_channel_ids = [str(c) for c in channels]
    return out


@sets_router.get("", response_model=List[MediaSetOut])
async def list_sets(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> List[MediaSetOut]:
    rows = (await db.execute(select(MediaSet).order_by(MediaSet.created_at.desc()))).scalars().all()
    return [await _set_out(db, s) for s in rows]


@sets_router.post("", response_model=MediaSetOut, status_code=201)
async def create_set(
    payload: MediaSetCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> MediaSetOut:
    if len(payload.asset_ids) != len(set(payload.asset_ids)):
        raise HTTPException(422, 'Ein Bild darf nur einmal im Set vorkommen.')
    if payload.telegram_price_stars and not 2 <= len(payload.asset_ids) <= 10:
        raise HTTPException(422, 'Telegram-Pay-Sets benötigen 2 bis 10 Bilder.')
    media_set = MediaSet(
        name=payload.name,
        telegram_price_stars=payload.telegram_price_stars,
        description=payload.description,
        nsfw_level=payload.nsfw_level,
        tags=payload.tags,
        is_ordered=payload.is_ordered,
    )
    db.add(media_set)
    await db.flush()
    for position, asset_id in enumerate(payload.asset_ids):
        db.add(
            MediaSetItem(media_set_id=media_set.id, media_asset_id=asset_id, position=position)
        )
    if payload.asset_ids:
        media_set.cover_media_id = payload.asset_ids[0]
        media_set.preview_media_id = payload.asset_ids[0]
    await db.flush()
    await lifecycle_service.refresh_assets(db, payload.asset_ids)
    return await _set_out(db, media_set)


@sets_router.patch("/{set_id}", response_model=MediaSetOut)
async def update_set(
    set_id: uuid.UUID,
    payload: MediaSetUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> MediaSetOut:
    media_set = (
        await db.execute(select(MediaSet).where(MediaSet.id == set_id))
    ).scalar_one_or_none()
    if not media_set:
        raise HTTPException(404, "Set nicht gefunden")
    data = payload.model_dump(exclude_unset=True)
    old_price = media_set.telegram_price_stars
    old_members = [i.media_asset_id for i in sorted(media_set.items, key=lambda i:i.position)]
    asset_ids = data.pop("asset_ids", None)
    if asset_ids is not None and len(asset_ids) != len(set(asset_ids)):
        raise HTTPException(422, 'Ein Bild darf nur einmal im Set vorkommen.')
    stars = data.get('telegram_price_stars', media_set.telegram_price_stars)
    count = len(asset_ids) if asset_ids is not None else len(media_set.items)
    if stars and not 2 <= count <= 10:
        raise HTTPException(422, 'Telegram-Pay-Sets benötigen 2 bis 10 Bilder.')
    for key, value in data.items():
        setattr(media_set, key, value)
    db.add(media_set)

    if asset_ids is not None:
        await db.execute(delete(MediaSetItem).where(MediaSetItem.media_set_id == set_id))
        for position, asset_id in enumerate(asset_ids):
            db.add(
                MediaSetItem(media_set_id=set_id, media_asset_id=asset_id, position=position)
            )
        await db.flush()
        await lifecycle_service.refresh_assets(db, asset_ids)
    await db.flush()
    if (asset_ids is not None and asset_ids != old_members) or old_price != media_set.telegram_price_stars:
        from autoposter.services import telegram_paid
        await telegram_paid.invalidate(db, set_id=set_id)
    return await _set_out(db, media_set)


@sets_router.delete("/{set_id}", status_code=204, response_model=None)
async def delete_set(
    set_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> None:
    media_set = (
        await db.execute(select(MediaSet).where(MediaSet.id == set_id))
    ).scalar_one_or_none()
    if media_set:
        from autoposter.services import telegram_paid
        await telegram_paid.invalidate(db, set_id=set_id)
        members = (
            await db.execute(
                select(MediaSetItem.media_asset_id).where(MediaSetItem.media_set_id == set_id)
            )
        ).scalars().all()
        await db.delete(media_set)
        await db.flush()
        await lifecycle_service.refresh_assets(db, members)


# --------------------------------------------------------------------------- #
# Gespeicherte Ansichten & Wartung
# --------------------------------------------------------------------------- #
views_router = APIRouter(prefix="/views", tags=["views"])


@views_router.get("", response_model=List[SavedViewOut])
async def list_views(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> List[SavedViewOut]:
    rows = (
        await db.execute(select(SavedView).order_by(SavedView.position, SavedView.name))
    ).scalars().all()
    return [SavedViewOut.model_validate(r) for r in rows]


@views_router.post("", response_model=SavedViewOut, status_code=201)
async def create_view(
    payload: SavedViewIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
) -> SavedViewOut:
    view = SavedView(**payload.model_dump(), owner_id=user.id)
    db.add(view)
    await db.flush()
    return SavedViewOut.model_validate(view)


@views_router.delete("/{view_id}", status_code=204, response_model=None)
async def delete_view(
    view_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> None:
    view = (
        await db.execute(select(SavedView).where(SavedView.id == view_id))
    ).scalar_one_or_none()
    if view is None:
        raise HTTPException(404, 'Ansicht nicht gefunden')
    if not view.is_system:
        await db.delete(view)


maintenance_router = APIRouter(prefix="/maintenance", tags=["maintenance"])


@maintenance_router.get("/report", response_model=MaintenanceReport)
async def report(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> MaintenanceReport:
    unassigned = int(
        (
            await db.execute(
                select(func.count(MediaAsset.id)).where(
                    MediaAsset.lifecycle == Lifecycle.new.value
                )
            )
        ).scalar_one()
    )
    all_assets = (
        await db.execute(
            select(MediaAsset).where(MediaAsset.status != MediaStatus.archived.value)
        )
    ).scalars().all()
    untagged = sum(1 for a in all_assets if not a.tags)
    failed = sum(1 for a in all_assets if a.status == MediaStatus.failed.value)
    candidates = await media_service.archive_candidates(db, 30)

    near: List[Dict[str, Any]] = []
    seen = set()
    for asset in all_assets:
        if not asset.phash or asset.id in seen:
            continue
        for other in all_assets:
            if other.id == asset.id or other.id in seen or not other.phash:
                continue
            distance = media_service.hamming(asset.phash, other.phash)
            if distance <= 4:
                near.append(
                    {
                        "a": str(asset.id),
                        "b": str(other.id),
                        "a_name": asset.filename,
                        "b_name": other.filename,
                        "distance": distance,
                    }
                )
                seen.add(other.id)
                if len(near) >= 100:
                    break
        if len(near) >= 100:
            break

    orphans = int(
        (
            await db.execute(
                select(func.count(MediaSetItem.id))
                .outerjoin(MediaAsset, MediaAsset.id == MediaSetItem.media_asset_id)
                .where(MediaAsset.id.is_(None))
            )
        ).scalar_one()
    )

    return MaintenanceReport(
        unassigned=unassigned,
        untagged=untagged,
        archive_candidates=len(candidates),
        near_duplicates=near,
        failed_assets=failed,
        orphan_set_items=orphans,
    )


@maintenance_router.post("/archive-used")
async def archive_used(
    older_than_days: int = 30,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> Dict[str, int]:
    candidates = await media_service.archive_candidates(db, older_than_days)
    count = await media_service.archive(db, [c.id for c in candidates], True)
    library.invalidate_counts()
    return {"archived": count}


@maintenance_router.post("/rebuild-counters")
async def rebuild_counters(
    db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)
) -> Dict[str, int]:
    total = await lifecycle_service.refresh_all(db)
    library.invalidate_counts()
    return {"refreshed": total}


@maintenance_router.post("/delete-planned")
async def delete_planned_posts(
    dry_run: bool = True,
    channel_id: Optional[uuid.UUID] = None,
    only_future: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> Dict[str, Any]:
    """Alle geplanten Posts löschen – Kalender leerräumen.

    Veröffentlichte Posts bleiben immer erhalten: Sie sind die Grundlage der
    Verbrauchs- und Reichweitenrechnung, und rückgängig machen ließe sich das
    Löschen ohnehin nicht.

    Mit dry_run=True wird nur gezählt. Die Oberfläche fragt damit erst nach,
    bevor sie wirklich löscht.
    """
    stmt = select(Post).where(
        Post.status.notin_([PostStatus.published.value, PostStatus.publishing.value])
    )
    if channel_id:
        stmt = stmt.where(Post.channel_id == channel_id)
    if only_future:
        stmt = stmt.where(Post.scheduled_at >= datetime.now(timezone.utc))
    posts = (await db.execute(stmt)).scalars().all()

    channels = {
        c.id: c.display_name
        for c in (await db.execute(select(Channel))).scalars().all()
    }
    by_channel: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    assets: set = set()
    for post in posts:
        name = channels.get(post.channel_id, "unbekannt")
        by_channel[name] = by_channel.get(name, 0) + 1
        by_status[post.status] = by_status.get(post.status, 0) + 1
        assets.update(uuid.UUID(str(a)) for a in (post.media_asset_ids or []))

    published = (
        await db.execute(
            select(func.count())
            .select_from(Post)
            .where(Post.status == PostStatus.published.value)
        )
    ).scalar_one()

    if dry_run:
        return {
            "dry_run": True,
            "would_delete": len(posts),
            "kept_published": int(published),
            "by_channel": by_channel,
            "by_status": by_status,
        }

    for post in posts:
        await db.delete(post)
    await db.flush()
    # Die Bilder werden dadurch wieder verfügbar – Zähler und Lebenszyklus
    # müssen das mitbekommen.
    await lifecycle_service.refresh_assets(db, assets)
    library.invalidate_counts()
    return {
        "dry_run": False,
        "deleted": len(posts),
        "kept_published": int(published),
        "freed_assets": len(assets),
        "by_channel": by_channel,
        "by_status": by_status,
    }
