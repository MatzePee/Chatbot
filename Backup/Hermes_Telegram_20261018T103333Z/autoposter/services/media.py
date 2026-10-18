"""Medienverarbeitung: Import, EXIF-Strippen, Hashes, Thumbnails, Duplikate."""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import imagehash
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config import settings
from autoposter.models import MediaAsset, MediaStatus, NsfwLevel
from autoposter.services import lifecycle

logger = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = 200_000_000


class DuplicateUpload(Exception):
    """Ebene 1: dieselbe Datei existiert bereits im Bestand."""

    def __init__(self, existing: MediaAsset) -> None:
        super().__init__(f"Datei existiert bereits als Asset {existing.id}")
        self.existing = existing


def media_root() -> Path:
    root = Path(settings.media_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def asset_dir(asset_id: uuid.UUID) -> Path:
    key = str(asset_id)
    return media_root() / key[:2] / key[2:4] / key


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strip_and_normalize(data: bytes) -> Tuple[bytes, int, int, str]:
    """EXIF entfernen (inkl. GPS), Orientierung anwenden, Format beibehalten."""
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img)
        fmt = (img.format or "JPEG").upper()
        if fmt == "JPEG" and img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if fmt not in ("JPEG", "PNG", "WEBP"):
            fmt = "JPEG"
            img = img.convert("RGB")
        clean = Image.new(img.mode, img.size)
        clean.putdata(list(img.getdata()))
        buffer = io.BytesIO()
        save_kwargs = {"quality": 92, "optimize": True} if fmt in ("JPEG", "WEBP") else {}
        clean.save(buffer, format=fmt, **save_kwargs)
        mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[fmt]
        return buffer.getvalue(), clean.width, clean.height, mime


def _phash(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as img:
        return str(imagehash.phash(img.convert("RGB")))


def _write_thumbnails(directory: Path, data: bytes) -> None:
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        for size in settings.thumbnail_sizes:
            thumb = img.copy()
            thumb.thumbnail((size, size), Image.LANCZOS)
            thumb.save(directory / f"thumb_{size}.jpg", format="JPEG", quality=82, optimize=True)


def hamming(a: Optional[str], b: Optional[str]) -> int:
    """Distanz zweier perceptual hashes. 999 = nicht vergleichbar."""
    if not a or not b or len(a) != len(b):
        return 999
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 999


async def find_exact_duplicate(db: AsyncSession, digest: str) -> Optional[MediaAsset]:
    return (
        await db.execute(select(MediaAsset).where(MediaAsset.sha256 == digest))
    ).scalar_one_or_none()


async def find_similar(
    db: AsyncSession, phash: str, *, max_distance: int = 8, exclude_id: Optional[uuid.UUID] = None
) -> List[Tuple[MediaAsset, int]]:
    """Ähnlichkeitssuche. Reine Information — blockiert nichts."""
    stmt = select(MediaAsset).where(MediaAsset.phash.isnot(None))
    if exclude_id:
        stmt = stmt.where(MediaAsset.id != exclude_id)
    candidates = (await db.execute(stmt)).scalars().all()
    hits = [(a, hamming(phash, a.phash)) for a in candidates]
    return sorted([h for h in hits if h[1] <= max_distance], key=lambda x: x[1])


@dataclass
class PreparedImage:
    """Ergebnis der reinen Bildverarbeitung – noch ohne Datenbank."""

    asset_id: uuid.UUID
    filename: str
    storage_path: str
    sha256: str
    phash: str
    width: int
    height: int
    bytes: int
    mime: str


def prepare_image(filename: str, data: bytes) -> PreparedImage:
    """CPU-Arbeit: EXIF entfernen, Hashes rechnen, Thumbnails schreiben.

    Läuft bewusst OHNE Datenbanksitzung. Steckte das in einer offenen Transaktion,
    hielte ein Upload mit vielen Bildern die SQLite-Schreibsperre sekundenlang und
    andere Schreibzugriffe scheiterten mit "database is locked".
    """
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ValueError(f"Datei zu groß (> {settings.max_upload_mb} MB)")

    normalized, width, height, mime = _strip_and_normalize(data)
    if mime not in settings.allowed_mimes:
        raise ValueError(f"Nicht unterstützter Typ: {mime}")

    asset_id = uuid.uuid4()
    directory = asset_dir(asset_id)
    directory.mkdir(parents=True, exist_ok=True)
    suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[mime]
    original = directory / f"original{suffix}"
    original.write_bytes(normalized)
    _write_thumbnails(directory, normalized)

    return PreparedImage(
        asset_id=asset_id,
        filename=filename[:255],
        storage_path=str(original.relative_to(media_root())),
        sha256=sha256_of(normalized),
        phash=_phash(normalized),
        width=width,
        height=height,
        bytes=len(normalized),
        mime=mime,
    )


def discard_prepared(prepared: PreparedImage) -> None:
    """Dateien einer verworfenen Aufbereitung wieder entfernen (z. B. bei Duplikat)."""
    directory = asset_dir(prepared.asset_id)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)


async def store_prepared(
    db: AsyncSession,
    prepared: PreparedImage,
    *,
    uploaded_by: Optional[uuid.UUID] = None,
    tags: Optional[Sequence[str]] = None,
    nsfw_level: str = NsfwLevel.sfw.value,
    source_note: str = "",
    caption_hint: str = "",
) -> MediaAsset:
    """Nur noch der Datenbankteil – dauert Millisekunden."""
    existing = await find_exact_duplicate(db, prepared.sha256)
    if existing:
        discard_prepared(prepared)
        raise DuplicateUpload(existing)

    asset = MediaAsset(
        id=prepared.asset_id,
        filename=prepared.filename,
        storage_path=prepared.storage_path,
        sha256=prepared.sha256,
        phash=prepared.phash,
        width=prepared.width,
        height=prepared.height,
        bytes=prepared.bytes,
        mime=prepared.mime,
        exif_stripped=True,
        nsfw_level=nsfw_level,
        tags=list(tags or []),
        caption_hint=caption_hint,
        source_note=source_note,
        status=MediaStatus.ready.value,
        uploaded_by=uploaded_by,
    )
    db.add(asset)
    await db.flush()
    await lifecycle.refresh_asset(db, asset)
    return asset


async def ingest(
    db: AsyncSession,
    *,
    filename: str,
    data: bytes,
    uploaded_by: Optional[uuid.UUID] = None,
    tags: Optional[Sequence[str]] = None,
    nsfw_level: str = NsfwLevel.sfw.value,
    source_note: str = "",
    caption_hint: str = "",
) -> MediaAsset:
    """Ein Bild aufnehmen. Wirft DuplicateUpload, wenn die Datei schon existiert.

    Wichtig: Diese Duplikatsperre betrifft NUR den Bestand (keine zweite Kopie derselben
    Datei). Sie schränkt die Verwendung auf mehreren Kanälen in keiner Weise ein.

    Die Bildverarbeitung läuft in einem Thread, damit weder der Ereignisschleife
    noch der Datenbank die Luft ausgeht.
    """
    prepared = await asyncio.to_thread(prepare_image, filename, data)
    return await store_prepared(
        db,
        prepared,
        uploaded_by=uploaded_by,
        tags=tags,
        nsfw_level=nsfw_level,
        source_note=source_note,
        caption_hint=caption_hint,
    )


def read_bytes(asset: MediaAsset) -> bytes:
    return (media_root() / asset.storage_path).read_bytes()


def thumb_path(asset: MediaAsset, size: int = 256) -> Optional[Path]:
    candidate = asset_dir(asset.id) / f"thumb_{size}.jpg"
    return candidate if candidate.exists() else None


def public_urls(asset: MediaAsset) -> Tuple[str, str]:
    base = f"/api/v1/media/{asset.id}"
    return f"{base}/thumb?size=256", f"{base}/file"


async def archive_candidates(db: AsyncSession, older_than_days: int = 30) -> List[MediaAsset]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    from autoposter.models import Lifecycle

    return list(
        (
            await db.execute(
                select(MediaAsset).where(
                    MediaAsset.lifecycle == Lifecycle.fully_used.value,
                    MediaAsset.last_used_at.isnot(None),
                    MediaAsset.last_used_at < cutoff,
                )
            )
        ).scalars().all()
    )


async def archive(db: AsyncSession, asset_ids: Iterable[uuid.UUID], archived: bool = True) -> int:
    ids = list(asset_ids)
    if not ids:
        return 0
    assets = (
        await db.execute(select(MediaAsset).where(MediaAsset.id.in_(ids)))
    ).scalars().all()
    for asset in assets:
        asset.status = MediaStatus.archived.value if archived else MediaStatus.ready.value
        asset.archived_at = datetime.now(timezone.utc) if archived else None
        db.add(asset)
    await db.flush()
    await lifecycle.refresh_assets(db, [a.id for a in assets])
    return len(assets)


# --------------------------------------------------------------------------- #
# KI-Bildbeschreibung
# --------------------------------------------------------------------------- #
async def ensure_descriptions(
    db: AsyncSession, assets: Sequence[MediaAsset], *, force: bool = False
) -> int:
    """Fehlende Bildbeschreibungen sofort nachziehen.

    Der Hintergrundjob arbeitet in Häppchen und kann beim Planen noch nicht
    fertig sein. Ohne Beschreibung bekäme das Sprachmodell nichts über das Bild
    zu sehen – und schriebe irgendetwas, das zufällig nicht dazu passt. Deshalb
    wird hier notfalls unmittelbar vor der Textgenerierung beschrieben.
    """
    # Lokaler Import: llm hängt an appconfig, media wird sehr früh geladen.
    from autoposter.services.appconfig import current as cfg
    from autoposter.services.llm import llm

    if not cfg("openrouter_api_key"):
        return 0

    done = 0
    for asset in assets:
        if not force and asset.ai_description:
            continue
        if asset.status not in (MediaStatus.ready.value, MediaStatus.processing.value):
            continue
        try:
            data = await asyncio.to_thread(read_bytes, asset)
            result = await llm.describe_image(db, data, asset.mime)
        except Exception as exc:  # noqa: BLE001 – eine Beschreibung darf nichts abbrechen
            logger.warning("Bildbeschreibung für %s fehlgeschlagen: %s", asset.id, exc)
            continue

        asset.ai_description = str(result.get("description", ""))[:2000]
        if not asset.caption_hint:
            asset.caption_hint = str(result.get("alt_text", ""))[:900]
        suggested = [str(t).strip() for t in (result.get("tags") or []) if str(t).strip()][:8]
        if suggested:
            asset.tags = sorted(set((asset.tags or []) + suggested))
        db.add(asset)
        await db.flush()
        done += 1
    return done
