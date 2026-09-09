"""Dashboard, Inventar, Analytics, Einstellungen."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config import settings
from autoposter.db import get_db
from autoposter.deps import current_user, require_admin
from autoposter.models import (
    AppSetting,
    Channel,
    LlmUsage,
    Notification,
    Post,
    PostStatus,
    User,
)
from autoposter.schemas import (
    ChannelOut,
    DashboardOut,
    InventoryOverview,
    LifecycleCounts,
    PolicyOut,
    PostOut,
)
from autoposter.services import appconfig
from autoposter.services import credentials as cred_service
from autoposter.services import inventory, library, llm as llm_service

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> DashboardOut:
    now = datetime.now(timezone.utc)

    upcoming = (
        await db.execute(
            select(Post)
            .where(
                Post.status.in_(
                    [PostStatus.scheduled.value, PostStatus.needs_review.value]
                ),
                Post.scheduled_at.isnot(None),
                Post.scheduled_at >= now - timedelta(hours=1),
            )
            .order_by(Post.scheduled_at)
            .limit(15)
        )
    ).scalars().all()

    failures = (
        await db.execute(
            select(Post)
            .where(Post.status == PostStatus.failed.value)
            .order_by(Post.last_attempt_at.desc().nullslast())
            .limit(10)
        )
    ).scalars().all()

    channels = (await db.execute(select(Channel).order_by(Channel.display_name))).scalars().all()
    channel_out: List[ChannelOut] = []
    for channel in channels:
        item = ChannelOut.model_validate(channel)
        item.policy = PolicyOut.model_validate(channel.policy) if channel.policy else None
        item.token_expires_at = await cred_service.token_expiry(db, channel)
        item.is_connected = cred_service.is_connected(channel)
        item.connection_source = "chatbot" if channel.platform == "fanvue" else "channel"
        if channel.platform == 'fanvue':
            from autoposter.services.chatbot_fanvue import health
            item.health = health(channel)
        channel_out.append(item)

    notes = (
        await db.execute(
            select(Notification)
            .where(Notification.is_read.is_(False))
            .order_by(Notification.created_at.desc())
            .limit(20)
        )
    ).scalars().all()

    return DashboardOut(
        inventory=await inventory.overview(db),
        lifecycle_counts=await library.lifecycle_counts(db),
        upcoming_posts=[PostOut.model_validate(p) for p in upcoming],
        recent_failures=[PostOut.model_validate(p) for p in failures],
        channel_health=channel_out,
        notifications=[
            {
                "id": str(n.id),
                "level": n.level,
                "title": n.title,
                "body": n.body,
                "created_at": n.created_at.isoformat(),
            }
            for n in notes
        ],
        dry_run=settings.dry_run,
        global_pause=settings.global_pause,
    )


@router.get("/inventory", response_model=InventoryOverview)
async def inventory_overview(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> InventoryOverview:
    return await inventory.overview(db)


@router.get("/counts", response_model=LifecycleCounts)
async def counts(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> LifecycleCounts:
    return await library.lifecycle_counts(db)


@router.get("/analytics/best-times")
async def best_times(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> Dict[str, Any]:
    """Heuristik: durchschnittliche Performance je Wochentag und Stunde."""
    posts = (
        await db.execute(
            select(Post).where(
                Post.channel_id == channel_id,
                Post.status == PostStatus.published.value,
                Post.published_at.isnot(None),
            )
        )
    ).scalars().all()

    buckets: Dict[str, List[float]] = {}
    for post in posts:
        metrics = post.metrics or {}
        score = float(
            metrics.get("impression_count")
            or metrics.get("like_count")
            or metrics.get("likes")
            or 0
        )
        moment = post.published_at
        key = f"{moment.weekday()}-{moment.hour:02d}"
        buckets.setdefault(key, []).append(score)

    scored = [
        {
            "weekday": int(key.split("-")[0]),
            "hour": int(key.split("-")[1]),
            "avg_score": round(sum(values) / len(values), 2),
            "samples": len(values),
        }
        for key, values in buckets.items()
        if values
    ]
    scored.sort(key=lambda item: item["avg_score"], reverse=True)
    return {"slots": scored[:20], "total_posts": len(posts)}


@router.get("/analytics/top-posts")
async def top_posts(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> List[Dict[str, Any]]:
    posts = (
        await db.execute(
            select(Post).where(Post.status == PostStatus.published.value).limit(500)
        )
    ).scalars().all()

    def score(post: Post) -> float:
        metrics = post.metrics or {}
        return float(
            metrics.get("impression_count")
            or metrics.get("like_count")
            or metrics.get("likes")
            or 0
        )

    ranked = sorted(posts, key=score, reverse=True)[:limit]
    return [
        {
            "id": str(p.id),
            "channel_id": str(p.channel_id),
            "published_at": p.published_at.isoformat() if p.published_at else None,
            "text": p.body_text[:160],
            "score": score(p),
            "metrics": p.metrics,
            "external_url": p.external_url,
            "media_asset_ids": p.media_asset_ids,
        }
        for p in ranked
    ]


@router.get("/notifications")
async def notifications(
    unread_only: bool = True,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> List[Dict[str, Any]]:
    stmt = select(Notification).order_by(Notification.created_at.desc()).limit(100)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": str(n.id),
            "level": n.level,
            "title": n.title,
            "body": n.body,
            "entity": n.entity,
            "entity_id": n.entity_id,
            "created_at": n.created_at.isoformat(),
        }
        for n in rows
    ]


@router.post("/notifications/read")
async def mark_read(
    ids: List[uuid.UUID],
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> Dict[str, int]:
    rows = (
        await db.execute(select(Notification).where(Notification.id.in_(ids)))
    ).scalars().all()
    for row in rows:
        row.is_read = True
        db.add(row)
    return {"updated": len(rows)}


@router.get("/settings")
async def get_settings(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> Dict[str, Any]:
    rows = (await db.execute(select(AppSetting))).scalars().all()
    stored = {r.key: r.value for r in rows}
    return {
        "dry_run": settings.dry_run,
        "global_pause": settings.global_pause,
        "inventory_warn_days": settings.inventory_warn_days,
        "x_api_tier": settings.x_api_tier,
        "fanvue_api_version": settings.fanvue_api_version,
        "openrouter_models": {
            "caption": settings.openrouter_model_caption,
            "text": settings.openrouter_model_text,
            "vision": settings.openrouter_model_vision,
            "fallback": settings.openrouter_model_fallback,
        },
        "monthly_budget_usd": settings.openrouter_monthly_budget_usd,
        "monthly_cost_usd": round(await llm_service.monthly_cost(db), 4),
        "stored": stored,
        "port": settings.api_port,
        "database": "sqlite" if settings.is_sqlite else "postgresql",
        "scheduler_mode": settings.scheduler_mode,
    }


@router.post("/settings/runtime")
async def update_runtime(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Kill-Switch und Trockenlauf zur Laufzeit umschalten."""
    if "dry_run" in payload:
        settings.dry_run = bool(payload["dry_run"])
    if "global_pause" in payload:
        settings.global_pause = bool(payload["global_pause"])

    row = (
        await db.execute(select(AppSetting).where(AppSetting.key == "runtime"))
    ).scalar_one_or_none()
    value = {"dry_run": settings.dry_run, "global_pause": settings.global_pause}
    if row:
        row.value = value
        db.add(row)
    else:
        db.add(AppSetting(key="runtime", value=value))
    return value


# --------------------------------------------------------------------------- #
# Integrationen: Fanvue, X und OpenRouter über die Oberfläche verbinden
# --------------------------------------------------------------------------- #
@router.get("/settings/integrations")
async def get_integrations(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> Dict[str, Any]:
    """Aktuelle Werte. Geheimnisse werden nie im Klartext ausgeliefert."""
    values = await appconfig.get_all(db, use_cache=False)
    masked = appconfig.mask(values)
    masked["redirect_uris"] = {
        "fanvue": f"{str(values.get('public_base_url', '')).rstrip('/')}/api/v1/oauth/fanvue/callback",
        "x": f"{str(values.get('public_base_url', '')).rstrip('/')}/api/v1/oauth/x/callback",
    }
    masked["fanvue_ready"] = bool(values.get("fanvue_client_id") and values.get("fanvue_client_secret"))
    from autoposter.services.chatbot_fanvue import oauth_app
    masked["fanvue_connection_source"] = "chatbot"
    masked["redirect_uris"]["fanvue"] = oauth_app().redirect_uri("fanvue")
    masked["x_ready"] = bool(values.get("x_client_id") and values.get("x_client_secret"))
    masked["openrouter_ready"] = bool(values.get("openrouter_api_key"))
    return masked


@router.post("/settings/integrations")
async def save_integrations(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> Dict[str, Any]:
    values = await appconfig.set_many(db, payload)
    return appconfig.mask(values)


@router.post("/settings/integrations/clear")
async def clear_integration_secret(
    payload: Dict[str, str],
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> Dict[str, Any]:
    field = payload.get("field", "")
    if field not in appconfig.SECRET_FIELDS:
        raise HTTPException(400, f"Unbekanntes Geheimnis: {field}")
    await appconfig.clear_secret(db, field)
    return appconfig.mask(await appconfig.get_all(db, use_cache=False))


@router.get("/settings/openrouter/models")
async def openrouter_models(
    refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> Dict[str, Any]:
    """Verfügbare Modelle bei OpenRouter. Das Ergebnis wird zwischengespeichert,
    damit die Einstellungsseite nicht bei jedem Aufruf eine Anfrage auslöst."""
    return await llm_service.list_models(db, refresh=refresh)


@router.post("/settings/openrouter/test")
async def openrouter_test(
    payload: Optional[Dict[str, Any]] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Verbindung prüfen: Schlüssel gültig, Modell erreichbar, Guthaben."""
    model = (payload or {}).get("model")
    return await llm_service.test_connection(db, model=model)


@router.get("/jobs")
async def jobs_status(_: User = Depends(current_user)) -> Dict[str, Any]:
    """Status der Hintergrundjobs. Im Celery-Modus laufen sie in Worker und Beat."""
    from autoposter import scheduler as scheduler_module

    if settings.scheduler_mode != "inprocess" or not scheduler_module.scheduler:
        return {
            "mode": settings.scheduler_mode,
            "jobs": [],
            "note": (
                "Jobs laufen in Celery-Worker und -Beat"
                if settings.scheduler_mode == "celery"
                else "Automatische Jobs sind abgeschaltet"
            ),
        }
    return {"mode": "inprocess", "jobs": scheduler_module.scheduler.status(), "note": ""}


@router.post("/jobs/{name}/run")
async def run_job(name: str, _: User = Depends(require_admin)) -> Dict[str, Any]:
    """Einen Hintergrundjob sofort ausführen."""
    from autoposter import jobs as job_module
    from autoposter import scheduler as scheduler_module

    if settings.scheduler_mode == "inprocess" and scheduler_module.scheduler:
        try:
            return {"ok": True, "result": await scheduler_module.scheduler.run_now(name)}
        except KeyError as exc:
            raise HTTPException(404, str(exc))

    # Ohne laufenden Scheduler direkt aufrufen (z. B. SCHEDULER_MODE=off).
    mapping = {
        "dispatch_due_posts": lambda: job_module.dispatch_due_posts(inline=True),
        "generate_plan": lambda: job_module.generate_plan(14),
        "refresh_metrics": lambda: job_module.refresh_metrics(72),
        "describe_assets": lambda: job_module.describe_assets(10),
        "inventory_check": job_module.inventory_check,
        "nightly_maintenance": job_module.nightly_maintenance,
        "token_health": job_module.token_health,
        "retry_failed": job_module.retry_failed,
    }
    if name not in mapping:
        raise HTTPException(404, f"Unbekannter Job: {name}")
    return {"ok": True, "result": await mapping[name]()}


@router.get("/llm/usage")
async def llm_usage(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> Dict[str, Any]:
    start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = (
        await db.execute(
            select(LlmUsage.task, func.count(LlmUsage.id), func.sum(LlmUsage.cost_usd))
            .where(LlmUsage.created_at >= start)
            .group_by(LlmUsage.task)
        )
    ).all()
    return {
        "month_start": start.isoformat(),
        "budget_usd": settings.openrouter_monthly_budget_usd,
        "total_usd": round(await llm_service.monthly_cost(db), 4),
        "by_task": [
            {"task": task, "calls": int(count), "cost_usd": float(cost or 0)}
            for task, count, cost in rows
        ],
    }


from autoposter.services import image_comments


@router.get("/settings/image-comment", response_model=image_comments.CommentSettings)
async def get_image_comment(db: AsyncSession = Depends(get_db), _: User = Depends(current_user)):
    return await image_comments.get_settings(db)


@router.post("/settings/image-comment", response_model=image_comments.CommentSettings)
async def save_image_comment(payload: image_comments.CommentSettings,
                             db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    return await image_comments.save_settings(db, payload)
