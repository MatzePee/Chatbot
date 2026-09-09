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
    # Jede Minute: fällige Posts einreihen.
    "dispatch-due-posts": {
        "task": "autoposter.dispatch_due_posts",
        "schedule": 60.0,
    },
    # Stündlich: Kalenderlücken schließen.
    "generate-plan": {
        "task": "autoposter.generate_plan",
        "schedule": crontab(minute=7),
    },
    # Alle 30 Minuten: Kennzahlen der letzten Posts nachziehen.
    "refresh-metrics": {
        "task": "autoposter.refresh_metrics",
        "schedule": crontab(minute="*/30"),
    },
    # Täglich 07:30 UTC: Bestandswarnung und Zusammenfassung.
    "inventory-check": {
        "task": "autoposter.inventory_check",
        "schedule": crontab(hour=7, minute=30),
    },
    # Täglich 03:15 UTC: verbrauchte Bilder archivieren, Zähler auffrischen.
    "nightly-maintenance": {
        "task": "autoposter.nightly_maintenance",
        "schedule": crontab(hour=3, minute=15),
    },
    # Alle 15 Minuten: Token-Gültigkeit prüfen.
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

import autoposter.tasks  # noqa: E402,F401  (Registrierung der Tasks)
