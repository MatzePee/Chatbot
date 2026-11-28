"""Non-destructive image variants: original, common crop and Instagram crop."""
from __future__ import annotations

import asyncio
import base64
import io
import uuid
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageOps
from sqlalchemy import delete, select, update

from autoposter.models import Channel, ExternalMediaRef, MediaAsset, MediaImageEdit

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
INSTAGRAM_MIN = 3 / 4
INSTAGRAM_MAX = 1.91


def variants(asset):
    return {row.scope: row for row in getattr(asset, "image_edits", []) or []}


def selected(asset, platform=None):
    rows = variants(asset)
    return rows.get("instagram") or rows.get("all") if platform == "instagram" else rows.get("all")


def dimensions(asset, platform=None):
    edit = selected(asset, platform)
    return (edit.width, edit.height) if edit else (asset.width, asset.height)


def instagram_ok(width, height):
    return bool(width and height and INSTAGRAM_MIN * height - 1 <= width <= INSTAGRAM_MAX * height + 1)


def describe(asset):
    rows = variants(asset)
    width, height = dimensions(asset, "instagram")
    return {"variants": {scope: {"id": str(row.id), "width": row.width, "height": row.height,
                                "settings": dict(row.settings or {})} for scope, row in rows.items()},
            "instagram": {"width": width, "height": height, "valid": instagram_ok(width, height)},
            "original_width": asset.width, "original_height": asset.height}


def selected_path(asset, platform=None, *, original=False):
    from autoposter.services import media
    media._assert_asset_owner(asset)
    edit = None if original else selected(asset, platform)
    if not edit:
        return media.original_path(asset)
    if edit.media_asset_id != asset.id or edit.character_id != asset.character_id:
        raise ValueError("Image edit does not belong to this character/asset")
    return media.resolve_media_path(edit.storage_path)


def render(data, config, mime):
    """Coordinates are normalized on the rotated/flipped/straightened image."""
    with Image.open(io.BytesIO(data)) as source:
        if getattr(source, "is_animated", False):
            raise HTTPException(422, "Animierte Bilder können nicht zugeschnitten werden.")
        image = ImageOps.exif_transpose(source).convert("RGBA")
        quarter = config["rotation"] % 360
        if quarter:
            image = image.transpose({90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}[quarter])
        if config["flip_x"]:
            image = ImageOps.mirror(image)
        if config["flip_y"]:
            image = ImageOps.flip(image)
        if config["straighten"]:
            image = image.rotate(-config["straighten"], Image.Resampling.BICUBIC, expand=True, fillcolor=(255, 255, 255, 255))
        x, y, width, height = (config[key] for key in ("x", "y", "width", "height"))
        if x + width > 1.000001 or y + height > 1.000001:
            raise HTTPException(422, "Der Bildausschnitt liegt außerhalb des Bildes.")
        left, top = round(x * image.width), round(y * image.height)
        right, bottom = min(image.width, round((x + width) * image.width)), min(image.height, round((y + height) * image.height))
        if right - left < 16 or bottom - top < 16:
            raise HTTPException(422, "Der Ausschnitt muss mindestens 16 × 16 Pixel groß sein.")
        result = image.crop((left, top, right, bottom))
        fmt = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[mime]
        if fmt == "JPEG":
            clean = Image.new("RGB", result.size, "white")
            clean.paste(result, mask=result.getchannel("A"))
            result = clean
        output = io.BytesIO()
        result.save(output, format=fmt, **({"quality": 94} if fmt in ("JPEG", "WEBP") else {}))
        return output.getvalue(), result.width, result.height


def retouch_source(asset, version):
    from autoposter.services import media
    if version:
        row = next((r for r in variants(asset).values() if r.id == version), None)
        if not row:
            raise HTTPException(409, "Bild inzwischen geändert. Bitte den Editor erneut öffnen.")
        if (row.settings or {}).get("retouched"):
            return media.resolve_media_path(row.storage_path).with_name("retouched.png")
    return media.original_path(asset)


def apply_grain(image, seed, strength):
    # Same per-pixel integer hash as the browser preview. Chunk rows to bound
    # working memory for full-resolution photos. Alpha is preserved.
    import numpy as np
    size = image.width
    for y in range(0, image.height, 128):
        end = min(y + 128, image.height)
        box = (0, y, size, end)
        pixels = np.array(image.crop(box))
        v = np.arange(y * size, end * size, dtype=np.uint32) + np.uint32(seed)
        v = (v ^ (v >> 16)) * np.uint32(0x7feb352d)
        v = (v ^ (v >> 15)) * np.uint32(0x846ca68b)
        v ^= v >> 16
        amplitude = (v & 255).astype(np.int32) + ((v >> 8) & 255).astype(np.int32) - 255
        delta = ((amplitude * strength + 127) // 255).reshape(end-y, size, 1)
        pixels[:, :, :3] = np.clip(pixels[:, :, :3].astype(np.int16) + delta, 0, 255)
        image.paste(Image.fromarray(pixels), box)


def apply_patches(data, patches):
    with Image.open(io.BytesIO(data)) as source:
        if source.width * source.height > 24_000_000 or getattr(source, "is_animated", False):
            raise HTTPException(422, "Der Radierer unterstützt Standbilder bis 24 Megapixel.")
        image = ImageOps.exif_transpose(source).convert("RGBA")
    for patch in patches:
        if getattr(patch, "kind", None) == "grain":
            apply_grain(image, patch.seed, patch.strength)
            continue
        try:
            padding = patch.padding
            if padding:
                size = (image.width + padding.left + padding.right, image.height + padding.top + padding.bottom)
                if size != (patch.width, patch.height) or patch.x or patch.y or size[0] * size[1] > 24_000_000:
                    raise ValueError
            elif patch.x + patch.width > image.width or patch.y + patch.height > image.height:
                raise ValueError
            raw = base64.b64decode(patch.png, validate=True)
            with Image.open(io.BytesIO(raw)) as overlay:
                if overlay.format != "PNG" or max(overlay.size) > 2048:
                    raise ValueError
                tile = overlay.convert("RGBA").resize((patch.width, patch.height), Image.Resampling.BILINEAR)
            if padding:
                tile.paste(image, (padding.left, padding.top))
                image = tile
            else:
                image.alpha_composite(tile, (patch.x, patch.y))
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise HTTPException(422, "Ungültige Retusche im Bild.") from exc
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def save(db, asset, payload):
    from autoposter.services import media, library
    if asset.mime not in IMAGE_TYPES:
        raise HTTPException(422, "Der Bildeditor unterstützt JPEG, PNG und WebP; keine Videos oder GIFs.")
    # Serialize edits to this image, including two tabs and reset vs save.
    await db.execute(update(MediaAsset).where(MediaAsset.id == asset.id).values(id=MediaAsset.id, updated_at=MediaAsset.updated_at))
    await db.refresh(asset)
    current = variants(asset)
    if payload.expected_version is not None:
        existing = current.get(payload.scope)
        if (str(existing.id) if existing else "") != payload.expected_version:
            raise HTTPException(409, "Das Bild wurde inzwischen bearbeitet. Editor schließen und neu öffnen.")
    scopes = ["all", "instagram"] if payload.scope == "all" else ["instagram"]
    if payload.reset:
        data = None
    else:
        config = payload.model_dump(exclude={"scope", "reset", "expected_version", "patches", "source_version"})
        version = payload.source_version
        if "source_version" not in payload.model_fields_set:
            prior = selected(asset, payload.scope)
            version = prior.id if prior else None
        source = retouch_source(asset, version)
        source_data = await asyncio.to_thread(source.read_bytes)
        if payload.patches:
            source_data = await asyncio.to_thread(apply_patches, source_data, payload.patches)
        config["retouched"] = bool(payload.patches) or source != media.original_path(asset)
        data, width, height = await asyncio.to_thread(render, source_data, config, asset.mime)
        if payload.scope == "instagram" and not instagram_ok(width, height):
            raise HTTPException(422, "Instagram benötigt ein Seitenverhältnis zwischen 3:4 und 1,91:1. Bitte ein passendes Format wählen.")
    for scope in scopes:
        existing = current.get(scope)
        if existing:
            await db.delete(existing)
    await db.flush()
    if data is not None:
        identity = uuid.uuid4()
        folder = media.asset_dir(asset.id) / "edits" / str(identity)
        folder.mkdir(parents=True, exist_ok=False)
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[asset.mime]
        path = folder / ("image." + ext)
        if config["retouched"]:
            await asyncio.to_thread((folder / "retouched.png").write_bytes, source_data)
        await asyncio.to_thread(path.write_bytes, data)
        await asyncio.to_thread(media._write_thumbnails, folder, data)
        db.add(MediaImageEdit(id=identity, media_asset_id=asset.id, scope=payload.scope,
            storage_path=str(path.relative_to(media.media_root())), width=width, height=height, settings=config))
    cache = delete(ExternalMediaRef).where(ExternalMediaRef.media_asset_id == asset.id)
    if payload.scope == "instagram":
        cache = cache.where(ExternalMediaRef.channel_id.in_(select(Channel.id).where(Channel.platform == "instagram")))
    await db.execute(cache)
    await db.flush()
    await db.refresh(asset, ["image_edits"])
    library.invalidate_counts()
    return asset
