"""Celery-Anwendung und Zeitplan."""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from autoposter.config import settings

celery_app = Celery("autoposter", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_time_limit=1800,
    task_soft_time_limit=1500,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
)

celery_app.conf.beat_schedule = {
    "delete-old-fanvue-posts": {
        "task": "autoposter.delete_old_fanvue_posts",
        "schedule": crontab(minute=20, hour=3),
    },
    "age-fanvue-visibility": {
        "task": "autoposter.age_fanvue_visibility",
        "schedule": 60.0,
    },
    # Poll only: the shared collector gates on 06:00 Europe/Berlin (DST-safe),
    # without changing the UTC timezone of any existing schedule.
    "collect-daily-followers": {
        "task": "autoposter.collect_daily_followers",
        "schedule": 60.0,
    },
    # Jede Minute: fällige Posts einreihen.
    "dispatch-due-posts": {
        "task": "autoposter.dispatch_due_posts",
        "schedule": 60.0,
    },
    # Minütlich prüfen; tägliche Mitternachtssperre in Europe/Berlin im Job.
    "generate-plan": {
        "task": "autoposter.generate_plan",
        "schedule": 60.0,
    },
    # Alle 30 Minuten: Kennzahlen der letzten Posts nachziehen.
    "refresh-metrics": {
        "task": "autoposter.refresh_metrics",
        "schedule": crontab(minute="*/30"),
    },
    # Stündlich: bestehende Probleme prüfen und Bestandswarnungen aktualisieren.
    "inventory-check": {
        "task": "autoposter.inventory_check",
        "schedule": crontab(minute=30),
    },
    # Täglich 03:15 UTC: verbrauchte Bilder archivieren, Zähler auffrischen.
    "nightly-maintenance": {
        "task": "autoposter.nightly_maintenance",
        "schedule": crontab(hour=3, minute=15),
    },
    # Alle 15 Minuten: Token-Gültigkeit prüfen.
    "reconcile-upload-post-posts": {
        "task": "autoposter.reconcile_upload_post_posts",
        "schedule": 300.0,
    },
    "reconcile-buffer-posts": {
        "task": "autoposter.reconcile_buffer_posts",
        "schedule": 3600.0,
    },
    "token-health": {
        "task": "autoposter.token_health",
        "schedule": crontab(minute="*/15"),
    },
    # Alle 15 Minuten: fehlende KI-Bildbeschreibungen nachziehen.
    "describe-assets": {
        "task": "autoposter.describe_assets",
        "schedule": crontab(minute="*/15"),
    },
}

# A Beat tick is control-plane only. Productive tasks never infer an owner.
import os
if os.environ.get('MP_CHARACTERS_ENABLED') == '1':
    for entry in celery_app.conf.beat_schedule.values():
        job_name = entry['task'].removeprefix('autoposter.')
        entry['task'] = 'autoposter.fanout'
        entry['kwargs'] = {'job_name':job_name}

import autoposter.tasks  # noqa: E402,F401  (Registrierung der Tasks)
