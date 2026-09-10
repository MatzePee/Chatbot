"""Automatische Kalenderplanung: Slots berechnen, Bilder wählen, Texte erzeugen."""
from __future__ import annotations

import random
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.adapters import get_adapter
from autoposter.models import (
    Audience,
    BlackoutPeriod,
    Channel,
    ContentPlan,
    MediaAsset,
    MediaChannelUsage,
    MediaSet,
    MediaSetItem,
    Persona,
    Post,
    PostStatus,
    PostType,
    PostingPolicy,
)
from autoposter.schemas import PlanFillResult, PlannedSlot
from autoposter.services import appconfig, assignment, lifecycle, media as media_service, runs
from autoposter.services.llm import BudgetExceeded, llm
from autoposter.services.fanvue_channels import fixed_audience

WEEKDAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


# --------------------------------------------------------------------------- #
# Zeitfenster
# --------------------------------------------------------------------------- #
def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def windows_for(policy: PostingPolicy, kind: str) -> List[Dict[str, Any]]:
    """Zeitfenster für einen Posttyp.

    kind = "image" oder "text". Ist kein eigenes Fenster hinterlegt, gilt das
    allgemeine. So kann man z. B. Bilder abends und Textposts morgens planen.
    """
    specific = policy.image_time_windows if kind == "image" else policy.text_time_windows
    if specific:
        return list(specific)
    return list(policy.allowed_time_windows or [
        {"dow": [0, 1, 2, 3, 4, 5, 6], "from": "09:00", "to": "22:00"}
    ])


def slots_from_windows(
    channel: Channel,
    policy: PostingPolicy,
    start: datetime,
    days: int,
    windows: Sequence[Dict[str, Any]],
    per_day: float,
) -> List[datetime]:
    """Zeitpunkte in der Kanal-Zeitzone erzeugen, Rückgabe in UTC."""
    tz = ZoneInfo(channel.timezone or "UTC")
    local_start = start.astimezone(tz)
    slots: List[datetime] = []
    count = max(1, int(round(per_day)))

    for offset in range(days + 1):
        day = (local_start + timedelta(days=offset)).date()
        for window in windows:
            if day.weekday() not in (window.get("dow") or list(range(7))):
                continue
            begin = datetime.combine(day, _parse_hhmm(window.get("from", "09:00")), tzinfo=tz)
            end = datetime.combine(day, _parse_hhmm(window.get("to", "22:00")), tzinfo=tz)
            if end <= begin:
                continue
            span = (end - begin).total_seconds()
            step = span / count
            for index in range(count):
                moment = begin + timedelta(seconds=step * index + step / 2)
                # Fester Kanal-Versatz gegen zeitgleiche Posts mehrerer Kanäle …
                moment += timedelta(minutes=policy.stagger_minutes or 0)
                # … plus Zufallsversatz gegen erkennbare Bot-Muster.
                if policy.jitter_minutes:
                    moment += timedelta(
                        minutes=random.randint(-policy.jitter_minutes, policy.jitter_minutes)
                    )
                if begin <= moment <= end and moment.astimezone(timezone.utc) > start:
                    slots.append(moment.astimezone(timezone.utc))
    return sorted(set(slots))


def candidate_slots(
    channel: Channel, policy: PostingPolicy, start: datetime, days: int
) -> List[datetime]:
    """Rückwärtskompatible Gesamtliste über alle Fenster."""
    return sorted(
        set(
            slots_from_windows(
                channel, policy, start, days, windows_for(policy, "image"), policy.posts_per_day
            )
            + slots_from_windows(
                channel, policy, start, days, windows_for(policy, "text"), policy.posts_per_day
            )
        )
    )


def typed_slots(
    channel: Channel, policy: PostingPolicy, start: datetime, days: int
) -> List[Tuple[datetime, str]]:
    """Slots mit ihrem erlaubten Posttyp.

    Fällt ein Zeitpunkt in beide Fenster, entscheidet der eingestellte Anteil
    reiner Textposts (text_only_ratio).
    """
    image_windows = windows_for(policy, "image")
    text_windows = windows_for(policy, "text")
    same = image_windows == text_windows

    image_slots = (
        slots_from_windows(channel, policy, start, days, image_windows, policy.posts_per_day)
        if policy.text_only_ratio < 1.0
        else []
    )
    text_slots = (
        slots_from_windows(channel, policy, start, days, text_windows, policy.posts_per_day)
        if policy.text_only_ratio > 0.0
        else []
    )

    if same:
        # Ein gemeinsames Fenster: Typ pro Slot auslosen, gewichtet nach Anteil.
        return sorted(
            (slot, "text" if random.random() < policy.text_only_ratio else "image")
            for slot in set(image_slots) | set(text_slots)
        )

    combined = [(slot, "image") for slot in image_slots] + [(slot, "text") for slot in text_slots]
    combined.sort(key=lambda pair: pair[0])
    return combined


async def _blackouts(db: AsyncSession, channel_id: uuid.UUID) -> List[Tuple[datetime, datetime]]:
    rows = (
        await db.execute(
            select(BlackoutPeriod).where(
                (BlackoutPeriod.channel_id == channel_id) | (BlackoutPeriod.channel_id.is_(None))
            )
        )
    ).scalars().all()
    return [(b.starts_at, b.ends_at) for b in rows]


async def existing_times(db: AsyncSession, channel_id: uuid.UUID) -> List[datetime]:
    rows = (
        await db.execute(
            select(Post.scheduled_at).where(
                Post.channel_id == channel_id,
                Post.scheduled_at.isnot(None),
                Post.status.in_(lifecycle.ACTIVE_SCHEDULE_STATES + (PostStatus.published.value,)),
            )
        )
    ).scalars().all()
    return sorted(r for r in rows if r)


async def other_channel_times(
    db: AsyncSession, channel_id: uuid.UUID
) -> List[datetime]:
    """Geplante Zeiten ALLER anderen Kanäle – für den Mindestabstand zwischen
    Kanälen, damit nicht überall gleichzeitig gepostet wird."""
    rows = (
        await db.execute(
            select(Post.scheduled_at).where(
                Post.channel_id != channel_id,
                Post.scheduled_at.isnot(None),
                Post.status.in_(
                    lifecycle.ACTIVE_SCHEDULE_STATES + (PostStatus.published.value,)
                ),
            )
        )
    ).scalars().all()
    return sorted(r for r in rows if r)


def free_slots(
    slots: Sequence[datetime],
    taken: Sequence[datetime],
    min_gap_minutes: int,
    blackouts: Sequence[Tuple[datetime, datetime]],
    *,
    foreign: Sequence[datetime] = (),
    cross_channel_gap_minutes: int = 0,
) -> List[datetime]:
    """Freie Zeitpunkte auswählen.

    min_gap_minutes           – Abstand zu Posts DESSELBEN Kanals
    cross_channel_gap_minutes – Abstand zu Posts ANDERER Kanäle
    """
    gap = timedelta(minutes=min_gap_minutes)
    cross = timedelta(minutes=cross_channel_gap_minutes)
    accepted: List[datetime] = []
    occupied = list(taken)
    foreign_times = list(foreign)

    for slot in slots:
        if any(start <= slot <= end for start, end in blackouts):
            continue
        if any(abs((slot - other).total_seconds()) < gap.total_seconds() for other in occupied):
            continue
        if cross_channel_gap_minutes and any(
            abs((slot - other).total_seconds()) < cross.total_seconds() for other in foreign_times
        ):
            continue
        accepted.append(slot)
        occupied.append(slot)
        foreign_times.append(slot)
    return accepted


# --------------------------------------------------------------------------- #
# Asset-Auswahl
# --------------------------------------------------------------------------- #
async def _recent_phashes(
    db: AsyncSession, channel_id: uuid.UUID, lookback: int
) -> List[str]:
    """Nur die Historie DIESES Kanals — plattformübergreifend wird nichts geprüft."""
    rows = (
        await db.execute(
            select(MediaAsset.phash)
            .join(MediaChannelUsage, MediaChannelUsage.media_asset_id == MediaAsset.id)
            .where(MediaChannelUsage.channel_id == channel_id)
            .order_by(MediaChannelUsage.used_at.desc())
            .limit(lookback)
        )
    ).scalars().all()
    return [p for p in rows if p]


async def pick_assets(
    db: AsyncSession,
    channel: Channel,
    policy: PostingPolicy,
    *,
    count: int,
    exclude: Optional[set] = None,
    tags_any: Optional[Sequence[str]] = None,
    keep_set_order: bool = False,
) -> Tuple[List[MediaAsset], str]:
    """Wählt Bilder ausschließlich aus dem manuell zugeordneten Pool des Kanals.

    tags_any grenzt zusätzlich ein, aus welchem Teil des Vorrats gezogen wird.

    keep_set_order gilt für Kanäle, die Sets einzeln posten: Aus einem Set kommt
    dann immer nur das nächste noch offene Bild in Frage. So erscheint eine
    Bildstrecke über mehrere Posts hinweg in der richtigen Reihenfolge, statt
    durcheinander.
    """
    exclude = exclude or set()
    wanted_tags = {t.strip().lower() for t in (tags_any or []) if t.strip()}
    pool = await assignment.channel_pool(db, channel.id, include_used=False)
    if not pool:
        return [], "Keine zugeordneten Bilder verfügbar"

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=policy.reuse_cooldown_days)
        if policy.reuse_cooldown_days
        else None
    )
    recent_hashes = await _recent_phashes(db, channel.id, policy.phash_lookback_posts)

    # Bei Kanälen, die Sets auflösen: je Set nur das vorderste offene Bild.
    set_head: Dict[uuid.UUID, uuid.UUID] = {}
    if keep_set_order:
        positions = await set_positions(db, [a.id for a in pool])
        best: Dict[uuid.UUID, Tuple[int, uuid.UUID]] = {}
        for asset in pool:
            entry = positions.get(asset.id)
            if not entry:
                continue
            set_id, position = entry
            if set_id not in best or position < best[set_id][0]:
                best[set_id] = (position, asset.id)
        set_head = {set_id: asset_id for set_id, (_, asset_id) in best.items()}
        blocked = {
            asset.id
            for asset in pool
            if (e := positions.get(asset.id)) and set_head.get(e[0]) != asset.id
        }
    else:
        blocked = set()

    chosen: List[MediaAsset] = []
    for asset in pool:
        if asset.id in exclude or asset.id in blocked:
            continue
        if wanted_tags and not (wanted_tags & {t.lower() for t in (asset.tags or [])}):
            continue
        if assignment.nsfw_rank(asset.nsfw_level) > assignment.nsfw_rank(channel.nsfw_level):
            continue
        if str(channel.id) in (asset.used_channel_ids or []):
            if not cutoff or not asset.last_used_at or asset.last_used_at > cutoff:
                continue
        if str(channel.id) in (asset.scheduled_channel_ids or []):
            continue
        if asset.phash and any(
            media_service.hamming(asset.phash, other) < policy.phash_min_distance
            for other in recent_hashes
        ):
            continue
        chosen.append(asset)
        if len(chosen) >= count:
            break

    if not chosen:
        reason = "Pool erschöpft (Cooldown, NSFW-Regel oder Ähnlichkeitsfilter)"
        if wanted_tags:
            reason = f"Keine freien Bilder mit den Tags {', '.join(sorted(wanted_tags))}"
        return [], reason
    return chosen, ""


def set_mode(channel: Channel, policy: PostingPolicy) -> str:
    """Wie geht dieser Kanal mit Sets um: "together" oder "single"?

    Ohne ausdrückliche Einstellung entscheidet die Plattform: Fanvue lebt von
    Bildstrecken, auf X wirken einzelne Bilder besser und die Bildstrecke würde
    im Feed als ein Post untergehen.
    """
    value = (policy.set_handling or "").strip().lower()
    if value in ("together", "single"):
        return value
    return "single" if channel.platform == "x" else "together"


async def set_positions(
    db: AsyncSession, asset_ids: Sequence[uuid.UUID]
) -> Dict[uuid.UUID, Tuple[uuid.UUID, int]]:
    """Zu jedem Bild sein Set und die Position darin – ein Query für alle."""
    ids = list(asset_ids)
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(MediaSetItem.media_asset_id, MediaSetItem.media_set_id, MediaSetItem.position)
            .where(MediaSetItem.media_asset_id.in_(ids))
            .order_by(MediaSetItem.position)
        )
    ).all()
    out: Dict[uuid.UUID, Tuple[uuid.UUID, int]] = {}
    for asset_id, set_id, position in rows:
        # Mehrfachzugehörigkeit: der erste Treffer gewinnt, das reicht für die
        # Reihenfolge und hält die Regel einfach erklärbar.
        out.setdefault(asset_id, (set_id, position))
    return out


async def set_of(db: AsyncSession, asset_id: uuid.UUID) -> Tuple[Optional[uuid.UUID], List[uuid.UUID]]:
    """Set eines Bildes samt seiner Bilder in der festgelegten Reihenfolge.

    Gehört ein Bild zu mehreren Sets, gewinnt das zuletzt angelegte – das ist
    in aller Regel das, was gerade gemeint war.
    """
    row = (
        await db.execute(
            select(MediaSetItem.media_set_id)
            .join(MediaSet, MediaSet.id == MediaSetItem.media_set_id)
            .where(MediaSetItem.media_asset_id == asset_id)
            .order_by(MediaSet.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not row:
        return None, []
    members = (
        await db.execute(
            select(MediaSetItem.media_asset_id)
            .where(MediaSetItem.media_set_id == row)
            .order_by(MediaSetItem.position)
        )
    ).scalars().all()
    return row, list(members)


async def expand_to_set(
    db: AsyncSession,
    channel: Channel,
    policy: PostingPolicy,
    first: MediaAsset,
    *,
    exclude: Optional[set] = None,
) -> Tuple[Optional[uuid.UUID], List[MediaAsset]]:
    """Gehört das gewählte Bild zu einem Set, kommt das ganze Set in den Post.

    Genau dafür sind Sets da: Bilder, die zusammen gepostet werden sollen. Die
    Obergrenze der Plattform (max_images_per_post) gilt trotzdem – der Rest
    bleibt im Vorrat und wird nicht angefasst.
    """
    exclude = exclude or set()
    if set_mode(channel, policy) != "together":
        # Der Kanal postet Sets einzeln – die Reihenfolge hat pick_assets
        # bereits sichergestellt.
        return None, [first]
    set_id, member_ids = await set_of(db, first.id)
    if not set_id or len(member_ids) < 2:
        return None, [first]

    limit = max(1, policy.max_images_per_post)
    pool = {a.id: a for a in await assignment.channel_pool(db, channel.id, include_used=False)}
    ordered: List[MediaAsset] = []
    for member_id in member_ids:
        asset = pool.get(member_id)
        if asset is None:
            continue  # nicht zugeordnet oder schon verbraucht
        if asset.id != first.id and asset.id in exclude:
            continue
        if assignment.nsfw_rank(asset.nsfw_level) > assignment.nsfw_rank(channel.nsfw_level):
            continue
        ordered.append(asset)
        if len(ordered) >= limit:
            break

    if len(ordered) < 2:
        return None, [first]
    if all(a.id != first.id for a in ordered):
        ordered.insert(0, first)
    return set_id, ordered[:limit]


# --------------------------------------------------------------------------- #
# Planung
# --------------------------------------------------------------------------- #
async def fill_calendar(
    db: AsyncSession,
    *,
    channel_ids: Optional[Sequence[uuid.UUID]] = None,
    days: int = 14,
    dry_run: bool = True,
    generate_text: bool = True,
    actor_id: Optional[uuid.UUID] = None,
) -> PlanFillResult:
    result = PlanFillResult()
    stmt = select(Channel).where(Channel.is_active.is_(True))
    if channel_ids:
        stmt = stmt.where(Channel.id.in_(list(channel_ids)))
    channels = (await db.execute(stmt)).scalars().all()
    now = datetime.now(timezone.utc)

    for channel in channels:
        tz = ZoneInfo(channel.timezone or "UTC")
        policy = channel.policy or PostingPolicy()
        plan = (
            await db.execute(
                select(ContentPlan).where(
                    ContentPlan.channel_id == channel.id, ContentPlan.is_active.is_(True)
                )
            )
        ).scalar_one_or_none()
        horizon = min(days, plan.horizon_days if plan else days)

        taken = await existing_times(db, channel.id)
        blackouts = await _blackouts(db, channel.id)
        foreign = await other_channel_times(db, channel.id)
        cross_gap = int(await appconfig.get(db, "cross_channel_min_gap_minutes", 0) or 0)

        # Slots inklusive ihres erlaubten Posttyps (Bild oder reiner Text).
        typed = typed_slots(channel, policy, now, horizon)
        allowed = set(
            free_slots(
                [slot for slot, _ in typed],
                taken,
                policy.min_gap_minutes,
                blackouts,
                foreign=foreign,
                cross_channel_gap_minutes=cross_gap,
            )
        )
        open_slots = [(slot, kind) for slot, kind in typed if slot in allowed]

        adapter = get_adapter(channel.platform)
        limits = adapter.limits()
        persona = (
            (await db.execute(select(Persona).where(Persona.id == channel.persona_id)))
            .scalar_one_or_none()
            if channel.persona_id
            else None
        )
        used_in_run: set = set()

        # Zähler für die Free/Sub-Verteilung bei Fanvue.
        free_count = 0
        planned_count = 0

        for slot, kind in open_slots:
            weekday = slot.astimezone(tz).weekday()
            theme = (plan.themes or {}).get(str(weekday), "") if plan else ""

            wants_text_only = kind == "text" and limits.supports_text_only

            assets: List[MediaAsset] = []
            set_id: Optional[uuid.UUID] = None
            reason = ""
            if not wants_text_only:
                assets, reason = await pick_assets(
                    db, channel, policy, count=1, exclude=used_in_run,
                    keep_set_order=set_mode(channel, policy) != "together",
                )
                if assets:
                    set_id, assets = await expand_to_set(
                        db, channel, policy, assets[0], exclude=used_in_run
                    )
                if not assets:
                    result.gaps.append(
                        {
                            "channel_id": str(channel.id),
                            "channel_name": channel.display_name,
                            "scheduled_at": slot.isoformat(),
                            "reason": reason,
                        }
                    )
                    continue
                used_in_run.update(a.id for a in assets)

            post_type = (
                PostType.text_only.value
                if not assets
                else PostType.image_set.value if len(assets) > 1
                else PostType.image_single.value
            )

            # Fanvue: Free-Post oder Sub-Post nach eingestelltem Anteil.
            # Deterministisch statt zufällig, damit das Verhältnis auch bei
            # wenigen Posts stimmt.
            fixed = fixed_audience(channel)
            audience = fixed or channel.default_audience or Audience.subscribers.value
            if channel.platform == "fanvue" and not fixed:
                planned_count += 1
                target_free = policy.free_post_ratio * planned_count
                if free_count < target_free:
                    audience = Audience.free.value
                    free_count += 1
                else:
                    audience = Audience.subscribers.value

            result.slots.append(
                PlannedSlot(
                    channel_id=channel.id,
                    scheduled_at=slot,
                    type=post_type,
                    media_asset_ids=[a.id for a in assets],
                    reason=theme,
                    audience=audience,
                )
            )

            if dry_run:
                continue

            body_text = ""
            hashtags: List[str] = []
            alt_texts: Dict[str, str] = {}
            meta: Dict[str, object] = {"auto": True, "theme": theme}

            if generate_text:
                # Ohne Bildbeschreibung schriebe das Modell am Bild vorbei.
                if assets:
                    await media_service.ensure_descriptions(db, assets)
                try:
                    generated = await llm.generate_post_text(
                        db,
                        persona=persona,
                        platform=channel.platform,
                        channel_id=channel.id,
                        has_image=bool(assets),
                        image_descriptions=[
                            a.ai_description or a.caption_hint for a in assets if (a.ai_description or a.caption_hint)
                        ],
                        theme=theme,
                        variants=3,
                        max_chars=limits.max_chars,
                        when_local=slot.astimezone(tz),
                    )
                    if generated.variants:
                        body_text = generated.variants[0]["text"]
                        hashtags = generated.variants[0].get("hashtags", [])
                    meta.update(
                        {
                            "model": generated.model,
                            "cost_usd": generated.cost_usd,
                            "variants": len(generated.variants),
                        }
                    )
                    variants_payload = generated.variants
                except BudgetExceeded as exc:
                    meta["error"] = str(exc)
                    variants_payload = []
                except Exception as exc:  # pragma: no cover - defensiv
                    meta["error"] = f"Textgenerierung fehlgeschlagen: {exc}"
                    variants_payload = []
            else:
                variants_payload = []

            for asset in assets:
                alt_texts[str(asset.id)] = (asset.caption_hint or asset.ai_description or "")[:900]

            post = Post(
                channel_id=channel.id,
                persona_id=channel.persona_id,
                type=post_type,
                status=(
                    PostStatus.scheduled.value
                    if policy.auto_approve and body_text
                    else PostStatus.needs_review.value
                ),
                scheduled_at=slot,
                body_text=body_text,
                body_text_variants=variants_payload,
                hashtags=hashtags,
                alt_texts=alt_texts,
                media_asset_ids=[str(a.id) for a in assets],
                media_set_id=set_id,
                audience=audience,
                generation_meta=meta,
                created_by=actor_id,
            )
            db.add(post)
            await db.flush()
            result.created_post_ids.append(post.id)
            await lifecycle.refresh_assets(db, [a.id for a in assets])

    return result


# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #
async def preflight(db: AsyncSession, post: Post) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    channel = (
        await db.execute(select(Channel).where(Channel.id == post.channel_id))
    ).scalar_one_or_none()
    if not channel:
        return [{"level": "error", "code": "no_channel", "message": "Kanal fehlt"}]

    adapter = get_adapter(channel.platform)
    limits = adapter.limits()
    policy = channel.policy or PostingPolicy()

    text_length = len(post.body_text or "") + sum(len(h) + 2 for h in (post.hashtags or []))
    if not post.body_text:
        # Steht ein Grund in den Generierungsdaten, gehört er hierher – sonst
        # sieht man im Kalender nur "(kein Text)" und rätselt.
        reason = (post.generation_meta or {}).get("error")
        if reason:
            issues.append({
                "level": "error",
                "code": "generation_failed",
                "message": f"Text konnte nicht erzeugt werden: {reason}",
            })
        elif post.type == PostType.text_only.value:
            issues.append({"level": "error", "code": "empty_text", "message": "Textpost ohne Text"})
    if text_length > limits.max_chars:
        issues.append(
            {
                "level": "error",
                "code": "too_long",
                "message": f"Text ist {text_length} Zeichen lang, erlaubt sind {limits.max_chars}",
            }
        )

    asset_ids = [uuid.UUID(str(a)) for a in (post.media_asset_ids or [])]
    if post.type != PostType.text_only.value and not asset_ids:
        issues.append({"level": "error", "code": "no_media", "message": "Kein Bild ausgewählt"})

    if asset_ids:
        assets = (
            await db.execute(select(MediaAsset).where(MediaAsset.id.in_(asset_ids)))
        ).scalars().all()
        for asset in assets:
            reason = assignment.check_compatibility(asset, channel)
            if reason:
                issues.append({"level": "error", "code": "incompatible", "message": reason})
            # Ohne Bildbeschreibung hatte das Modell beim Schreiben nichts vom
            # Bild gesehen – der Text kann nur zufällig dazu passen.
            if not asset.ai_description and post.body_text:
                issues.append(
                    {
                        "level": "warning",
                        "code": "no_description",
                        "message": (
                            f"{asset.filename} war beim Schreiben noch nicht beschrieben – "
                            "der Text passt womöglich nicht zum Bild. 'Varianten erzeugen' "
                            "holt die Beschreibung nach."
                        ),
                    }
                )
            send_alt = channel.platform != 'x' or channel.x_send_alt_text
            if limits.supports_alt_text and send_alt and not (post.alt_texts or {}).get(str(asset.id)):
                issues.append(
                    {
                        "level": "warning",
                        "code": "missing_alt",
                        "message": f"Alt-Text fehlt für {asset.filename}",
                    }
                )
            if str(channel.id) not in (asset.assigned_channel_ids or []):
                issues.append(
                    {
                        "level": "warning",
                        "code": "not_assigned",
                        "message": f"{asset.filename} ist diesem Kanal nicht zugeordnet",
                    }
                )
            # Duplikat-Prüfung ausschließlich innerhalb dieses Kanals.
            if str(channel.id) in (asset.used_channel_ids or []):
                cooldown_ok = (
                    policy.reuse_cooldown_days > 0
                    and asset.last_used_at
                    and asset.last_used_at
                    < datetime.now(timezone.utc) - timedelta(days=policy.reuse_cooldown_days)
                )
                issues.append(
                    {
                        "level": "warning" if cooldown_ok else "error",
                        "code": "already_used_here",
                        "message": (
                            f"{asset.filename} lief bereits auf diesem Kanal"
                            + (" (Cooldown abgelaufen)" if cooldown_ok else "")
                        ),
                    }
                )

        if len(assets) > limits.max_images_per_post:
            issues.append(
                {
                    "level": "warning",
                    "code": "thread_split",
                    "message": (
                        f"{len(assets)} Bilder werden auf mehrere Beiträge à "
                        f"{limits.max_images_per_post} aufgeteilt"
                    ),
                }
            )

    if post.price_cents:
        if not limits.supports_price:
            issues.append(
                {"level": "error", "code": "no_price", "message": "Plattform unterstützt keine Preise"}
            )
        elif post.price_cents < 300:
            issues.append(
                {"level": "error", "code": "price_low", "message": "Mindestpreis sind 300 Cent"}
            )

    if channel.platform == "fanvue" and not post.audience:
        issues.append(
            {"level": "error", "code": "no_audience", "message": "Zielgruppe (audience) fehlt"}
        )
    if fixed_audience(channel) and post.audience and post.audience != fixed_audience(channel):
        issues.append({'level': 'error', 'code': 'audience_mismatch',
                       'message': 'Die Sichtbarkeit passt nicht zur festen Zielgruppe dieses Fanvue-Kanals.'})

    if post.scheduled_at:
        for start, end in await _blackouts(db, channel.id):
            if start <= post.scheduled_at <= end:
                issues.append(
                    {"level": "error", "code": "blackout", "message": "Zeitpunkt liegt in einer Sperrzeit"}
                )
        neighbours = [
            t for t in await existing_times(db, channel.id) if t != post.scheduled_at
        ]
        if any(
            abs((post.scheduled_at - other).total_seconds()) < policy.min_gap_minutes * 60
            for other in neighbours
        ):
            issues.append(
                {
                    "level": "warning",
                    "code": "min_gap",
                    "message": f"Weniger als {policy.min_gap_minutes} Minuten Abstand zum Nachbarpost",
                }
            )

    effective_health = channel.health
    if channel.platform == 'fanvue':
        from autoposter.services.chatbot_fanvue import health
        effective_health = health(channel)
    if effective_health != "ok":
        issues.append(
            {"level": "error", "code": "channel_health", "message": f"Kanalstatus: {effective_health}"}
        )
    return issues


# --------------------------------------------------------------------------- #
# Zeitraum-Planung („Automatisch planen")
# --------------------------------------------------------------------------- #
def _window_minutes(time_from: str, time_to: str) -> int:
    """Länge eines Tagesfensters in Minuten, auch über Mitternacht."""
    start = _parse_hhmm(time_from)
    end = _parse_hhmm(time_to)
    span = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    return span if span > 0 else span + 24 * 60


def day_slots(
    day,
    tz: ZoneInfo,
    time_from: str,
    time_to: str,
    count: int,
    *,
    stagger_minutes: int = 0,
    jitter_minutes: int = 0,
) -> List[datetime]:
    """count Zeitpunkte gleichmäßig über ein Tagesfenster verteilen.

    Die Punkte liegen jeweils in der Mitte gleich großer Abschnitte – so gibt es
    weder eine Häufung am Rand noch zwei Posts direkt hintereinander.
    """
    if count <= 0:
        return []
    begin = datetime.combine(day, _parse_hhmm(time_from), tzinfo=tz)
    end = datetime.combine(day, _parse_hhmm(time_to), tzinfo=tz)
    if end < begin:
        # Fenster über Mitternacht, z. B. 22:00 bis 06:00.
        end += timedelta(days=1)
    if end == begin:
        # Punktgenaue Vorgabe (beide Zeiten gleich): exakt diese Uhrzeit treffen,
        # statt sie in die Mitte eines erfundenen Fensters zu schieben.
        moments = [begin + timedelta(minutes=index) for index in range(count)]
        return [m.astimezone(timezone.utc) for m in moments]

    span = (end - begin).total_seconds()
    step = span / count
    out: List[datetime] = []
    for index in range(count):
        moment = begin + timedelta(seconds=step * index + step / 2)
        moment += timedelta(minutes=stagger_minutes)
        if jitter_minutes:
            moment += timedelta(minutes=random.randint(-jitter_minutes, jitter_minutes))
        # Innerhalb des Fensters halten, auch nach Versatz.
        moment = max(begin, min(end, moment))
        out.append(moment.astimezone(timezone.utc))

    # An Zeitumstellungstagen kann eine nicht existierende Ortszeit (02:30 im
    # Frühjahr) auf denselben UTC-Zeitpunkt fallen wie ein anderer Slot. Solche
    # Dubletten würden zwei Posts in dieselbe Minute legen.
    unique: List[datetime] = []
    for moment in sorted(out):
        while unique and (moment - unique[-1]).total_seconds() < 60:
            moment += timedelta(minutes=1)
        unique.append(moment)
    return unique


async def plan_range(
    db: AsyncSession,
    request,
    actor_id: Optional[uuid.UUID] = None,
    run: Optional[Any] = None,
):
    """Posts für einen Datumsbereich planen.

    Anders als fill_calendar arbeitet diese Funktion mit ausdrücklichen Vorgaben
    statt mit den Kanal-Regeln: Zeitraum, Anzahl je Typ, Uhrzeiten, Wochentage.
    Bestehende Posts werden bei fill_gaps_only nur gezählt, nie verändert.

    `run` ist ein Lauf aus app.services.runs: Damit meldet die Funktion ihren
    Fortschritt und prüft, ob abgebrochen wurde. Ohne `run` verhält sie sich
    unverändert.
    """
    from autoposter.schemas import PlanCapacity, PlanRangeResult

    result = PlanRangeResult()

    stmt = select(Channel).where(Channel.is_active.is_(True))
    if request.channel_ids:
        stmt = stmt.where(Channel.id.in_(list(request.channel_ids)))
    channels = (await db.execute(stmt)).scalars().all()
    if not channels:
        return result

    now = datetime.now(timezone.utc)
    cross_gap = int(await appconfig.get(db, "cross_channel_min_gap_minutes", 0) or 0)
    days = []
    current = request.date_from
    while current <= request.date_to:
        if current.weekday() in (request.weekdays or list(range(7))):
            days.append(current)
        current += timedelta(days=1)

    # Obergrenze der Schritte: mehr Posts als das kann kein Lauf erzeugen.
    if run is not None:
        per_day = max(0, request.image_posts_per_day) + max(0, request.text_posts_per_day)
        run.total = max(1, len(channels) * len(days) * per_day)
        run.message = f"{len(days)} Tage, {len(channels)} Kanäle"

    for channel in channels:
        runs.check(run)
        policy = channel.policy or PostingPolicy()
        tz = ZoneInfo(channel.timezone or "UTC")
        adapter = get_adapter(channel.platform)
        limits = adapter.limits()
        persona = (
            (await db.execute(select(Persona).where(Persona.id == channel.persona_id)))
            .scalar_one_or_none()
            if channel.persona_id
            else None
        )
        plan = (
            await db.execute(
                select(ContentPlan).where(
                    ContentPlan.channel_id == channel.id, ContentPlan.is_active.is_(True)
                )
            )
        ).scalar_one_or_none()

        min_gap = (
            request.min_gap_minutes
            if request.min_gap_minutes is not None
            else policy.min_gap_minutes
        )
        jitter = (
            request.jitter_minutes if request.jitter_minutes is not None else policy.jitter_minutes
        )

        taken = await existing_times(db, channel.id)
        foreign = await other_channel_times(db, channel.id)
        blackouts = await _blackouts(db, channel.id)

        # Wie viele Posts liegen an einem Tag schon? (nur zum Zählen)
        existing_by_day: Dict[str, int] = {}
        for moment in taken:
            key = moment.astimezone(tz).date().isoformat()
            existing_by_day[key] = existing_by_day.get(key, 0) + 1
        result.existing_in_range += sum(
            count for day in days for key, count in existing_by_day.items()
            if key == day.isoformat()
        )

        used_in_run: set = set()
        free_count = 0
        planned_for_channel = 0
        needed_images = 0

        for day in days:
            runs.check(run)
            already = existing_by_day.get(day.isoformat(), 0) if request.fill_gaps_only else 0

            # Zwei Betriebsarten. Nach Sichtbarkeit geplant wird nur, wenn der
            # Dialog das ausdrücklich verlangt – sonst bliebe das automatische
            # Nachplanen und der X-Dialog auf der Strecke.
            by_audience = (
                channel.platform == "fanvue"
                and (request.sub_posts_per_day or request.free_posts_per_day)
            )
            if by_audience:
                fixed = fixed_audience(channel)
                sub_count = request.sub_posts_per_day if fixed != Audience.free.value else 0
                follower_count = request.free_posts_per_day if fixed != Audience.subscribers.value else 0
                want_total = sub_count + follower_count
            else:
                want_total = request.image_posts_per_day + request.text_posts_per_day

            open_count = max(0, want_total - already)
            if open_count <= 0:
                runs.step(run, f"{channel.display_name}: {day:%d.%m.} schon voll",
                          increment=want_total)
                continue

            #: Je Eintrag: (Art des Posts, Sichtbarkeit oder None für "wie bisher")
            kinds: List[Tuple[str, Optional[str]]]
            if by_audience:
                # Fanvue: keine reinen Textposts, dafür Abonnenten und Follower.
                want_sub = min(sub_count, open_count)
                want_free = min(follower_count, open_count - want_sub)
                want_images = want_sub + want_free
                kinds = (
                    [("image", Audience.subscribers.value)] * want_sub
                    + [("image", Audience.free.value)] * want_free
                )
            else:
                # Fehlende Posts auf die Typen verteilen: Bilder zuerst, dann Text.
                want_images = min(request.image_posts_per_day, open_count)
                want_texts = min(request.text_posts_per_day, open_count - want_images)
                if not limits.supports_text_only:
                    want_images += want_texts
                    want_texts = 0
                kinds = [("image", None)] * want_images + [("text", None)] * want_texts
            # Ohne Mischen kämen morgens immer die Bilder und abends immer die
            # Texte – ein Muster, das ein Konto als automatisiert verrät. Bei
            # Fanvue gilt dasselbe für Abonnenten- und Follower-Posts.
            random.shuffle(kinds)
            if not kinds:
                runs.step(run, increment=want_total)
                continue
            needed_images += want_images
            # Was an diesem Tag schon stand, ist kein offener Schritt mehr.
            if already:
                runs.step(run, increment=min(already, want_total))

            candidates = day_slots(
                day, tz, request.time_from, request.time_to, len(kinds),
                stagger_minutes=policy.stagger_minutes, jitter_minutes=jitter,
            )
            usable = free_slots(
                candidates, taken, min_gap, blackouts,
                foreign=foreign, cross_channel_gap_minutes=cross_gap,
            )

            # Der Mindestabstand kann mehr Zeitpunkte verwerfen, als man ahnt:
            # 5 Posts zwischen 09:00 und 22:00 liegen 156 Minuten auseinander –
            # bei 180 Minuten Mindestabstand fällt jeder zweite weg. Das muss
            # als Grund nachlesbar sein, nicht als stilles Weniger.
            if len(usable) < len(kinds) and min_gap > 0:
                window = _window_minutes(request.time_from, request.time_to)
                # day_slots legt die Zeitpunkte in die Mitte gleich großer
                # Abschnitte – der Abstand ist also Fenster geteilt durch Anzahl.
                spacing = window // max(1, len(kinds))
                if spacing < min_gap:
                    fits = max(1, window // min_gap)
                    result.gaps.append({
                        "channel_id": str(channel.id),
                        "channel_name": channel.display_name,
                        "scheduled_at": None,
                        "reason": (
                            "{day:%d.%m.}: {want} Posts passen nicht in {frm}–{to}. "
                            "Der Mindestabstand von {gap} Minuten lässt dort höchstens "
                            "{fits} Posts zu. Fenster verbreitern, Anzahl senken oder "
                            "Mindestabstand im Dialog überschreiben."
                        ).format(
                            day=day, want=len(kinds), frm=request.time_from,
                            to=request.time_to, gap=min_gap, fits=fits,
                        ),
                    })
                    runs.note(
                        run,
                        f"{day:%d.%m.}: nur {fits} von {len(kinds)} Posts möglich "
                        f"(Mindestabstand {min_gap} Min.)",
                    )

            pending = list(kinds)
            for slot in usable:
                if not pending:
                    break
                if slot <= now:
                    # Vergangene Zeitpunkte überspringen, ohne den Typ zu
                    # verbrauchen – sonst rutscht ein Bildpost zum Textpost.
                    continue
                kind, wanted_audience = pending[0]

                weekday = slot.astimezone(tz).weekday()
                theme = (plan.themes or {}).get(str(weekday), "") if plan else ""

                assets: List[MediaAsset] = []
                set_id: Optional[uuid.UUID] = None
                if kind == "image":
                    assets, reason = await pick_assets(
                        db, channel, policy, count=1,
                        exclude=used_in_run, tags_any=request.tags_any,
                        keep_set_order=set_mode(channel, policy) != "together",
                    )
                    if not assets:
                        result.gaps.append(
                            {
                                "channel_id": str(channel.id),
                                "channel_name": channel.display_name,
                                "scheduled_at": slot.isoformat(),
                                "reason": reason,
                            }
                        )
                        runs.step(run, f"{channel.display_name}: kein Bild verfügbar")
                        runs.note(run, f"{slot.astimezone(tz):%d.%m. %H:%M}: {reason}")
                        continue
                    # Gehört das Bild zu einem Set, wandert das ganze Set in
                    # diesen einen Post – genau dafür sind Sets da.
                    set_id, assets = await expand_to_set(
                        db, channel, policy, assets[0], exclude=used_in_run
                    )
                    used_in_run.update(a.id for a in assets)

                pending.pop(0)
                runs.check(run)
                if len(assets) > 1:
                    runs.note(run, f"Set mit {len(assets)} Bildern als ein Post")
                if run is not None:
                    run.message = (
                        f"{channel.display_name}: {slot.astimezone(tz):%d.%m. %H:%M} — "
                        + ("Bildpost" if assets else "Textpost")
                        + (", Text wird erzeugt" if request.generate_text else "")
                    )
                post_type = (
                    PostType.text_only.value
                    if not assets
                    else PostType.image_set.value if len(assets) > 1
                    else PostType.image_single.value
                )

                fixed = fixed_audience(channel)
                audience = fixed or channel.default_audience or Audience.subscribers.value
                if not fixed and wanted_audience:
                    # Im Dialog ausdrücklich angefordert – das schlägt jede Quote.
                    audience = wanted_audience
                elif channel.platform == "fanvue" and not fixed:
                    planned_for_channel += 1
                    if free_count < policy.free_post_ratio * planned_for_channel:
                        audience = Audience.free.value
                        free_count += 1
                    else:
                        audience = Audience.subscribers.value

                result.slots.append(
                    PlannedSlot(
                        channel_id=channel.id,
                        scheduled_at=slot,
                        type=post_type,
                        media_asset_ids=[a.id for a in assets],
                        reason=theme,
                        audience=audience,
                    )
                )
                # Damit die nächsten Slots dieses Laufs den Abstand einhalten.
                taken.append(slot)
                foreign.append(slot)

                if request.dry_run:
                    runs.step(run)
                    continue

                body_text, hashtags, variants_payload = "", [], []
                meta: Dict[str, object] = {"auto": True, "theme": theme, "range_plan": True}
                if request.generate_text:
                    if assets:
                        await media_service.ensure_descriptions(db, assets)
                    try:
                        generated = await llm.generate_post_text(
                            db,
                            persona=persona,
                            platform=channel.platform,
                            channel_id=channel.id,
                            has_image=bool(assets),
                            image_descriptions=[
                                a.ai_description or a.caption_hint
                                for a in assets
                                if (a.ai_description or a.caption_hint)
                            ],
                            theme=theme,
                            variants=3,
                            max_chars=limits.max_chars,
                            when_local=slot.astimezone(tz),
                        )
                        if generated.variants:
                            body_text = generated.variants[0]["text"]
                            hashtags = generated.variants[0].get("hashtags", [])
                        variants_payload = generated.variants
                        meta.update({"model": generated.model, "cost_usd": generated.cost_usd})
                    except BudgetExceeded as exc:
                        meta["error"] = str(exc)
                        runs.note(run, f"Text nicht erzeugt: {exc}")
                    except Exception as exc:  # pragma: no cover - defensiv
                        meta["error"] = f"Textgenerierung fehlgeschlagen: {exc}"
                        runs.note(run, f"Text nicht erzeugt: {str(exc)[:160]}")

                post = Post(
                    channel_id=channel.id,
                    persona_id=channel.persona_id,
                    type=post_type,
                    status=(
                        PostStatus.scheduled.value
                        if policy.auto_approve and body_text
                        else PostStatus.needs_review.value
                    ),
                    scheduled_at=slot,
                    body_text=body_text,
                    body_text_variants=variants_payload,
                    hashtags=hashtags,
                    alt_texts={
                        str(a.id): (a.caption_hint or a.ai_description or "")[:900] for a in assets
                    },
                    media_asset_ids=[str(a.id) for a in assets],
                    media_set_id=set_id,
                    audience=audience,
                    generation_meta=meta,
                    created_by=actor_id,
                )
                db.add(post)
                await db.flush()
                result.created_post_ids.append(post.id)
                if not body_text:
                    result.text_failed.append(post.id)
                await lifecycle.refresh_assets(db, [a.id for a in assets])
                runs.step(run)
                # Zwischenstand sichern: Ein Abbruch nach 40 von 60 Posts soll
                # die 40 behalten, nicht alles verwerfen.
                if run is not None and len(result.created_post_ids) % 5 == 0:
                    await db.commit()

        # Hat dieser Kanal überhaupt etwas abbekommen? Ohne diese Meldung sieht
        # ein Lauf über zwei Kanäle, bei dem einer leer ausgeht, aus wie „nur
        # ein Kanal wird beplant" – der Grund steht dann nirgends.
        for_this_channel = sum(1 for s in result.slots if s.channel_id == channel.id)
        if for_this_channel == 0:
            runs.note(
                run,
                f"{channel.display_name}: kein einziger Post angelegt. "
                "Häufigste Gründe: Kanal pausiert, das Zeitfenster liegt komplett "
                "in der Vergangenheit, der Mindestabstand lässt keinen Slot zu, "
                "oder es sind keine Bilder zugeordnet.",
            )
        else:
            runs.note(run, f"{channel.display_name}: {for_this_channel} Posts angelegt.")

        # Vorratsprüfung: reicht der zugeordnete Pool für den Zeitraum?
        pool = await assignment.channel_pool(db, channel.id, include_used=False)
        if request.tags_any:
            wanted = {t.strip().lower() for t in request.tags_any if t.strip()}
            pool = [a for a in pool if wanted & {t.lower() for t in (a.tags or [])}]
        available = len(pool)
        result.capacity.append(
            PlanCapacity(
                channel_id=channel.id,
                channel_name=channel.display_name,
                needed_images=needed_images,
                available_images=available,
                enough=available >= needed_images,
                missing=max(0, needed_images - available),
            )
        )

    result.slots.sort(key=lambda s: s.scheduled_at)
    return result
