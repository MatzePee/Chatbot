"""Celery-Tasks.

Dünne Hüllen um die Funktionen aus app.jobs – die eigentliche Logik steht dort,
damit sie auch vom eingebauten Scheduler (ohne Redis) genutzt werden kann.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict

from celery import shared_task

from autoposter import jobs
from autoposter.db import run_async


@shared_task(name="autoposter.dispatch_due_posts")
def dispatch_due_posts() -> Dict[str, Any]:
    result = run_async(jobs.dispatch_due_posts(inline=False))
    for post_id in result.get("post_ids", []):
        publish_post.delay(post_id)
    return {"dispatched": result.get("dispatched", 0)}


@shared_task(name="autoposter.publish_post", bind=True, max_retries=5, acks_late=True)
def publish_post(self, post_id: str) -> Dict[str, Any]:
    return run_async(jobs.publish_one(uuid.UUID(post_id)))


@shared_task(name="autoposter.retry_failed")
def retry_failed() -> Dict[str, Any]:
    return run_async(jobs.retry_failed())


@shared_task(name="autoposter.generate_plan")
def generate_plan(days: int = 14) -> Dict[str, Any]:
    return run_async(jobs.generate_plan(days))


@shared_task(name="autoposter.describe_assets")
def describe_assets(limit: int = 20) -> Dict[str, Any]:
    return run_async(jobs.describe_assets(limit))


@shared_task(name="autoposter.refresh_metrics")
def refresh_metrics(hours: int = 72) -> Dict[str, Any]:
    return run_async(jobs.refresh_metrics(hours))


@shared_task(name="autoposter.inventory_check")
def inventory_check() -> Dict[str, Any]:
    return run_async(jobs.inventory_check())


@shared_task(name="autoposter.nightly_maintenance")
def nightly_maintenance() -> Dict[str, Any]:
    return run_async(jobs.nightly_maintenance())


@shared_task(name="autoposter.token_health")
def token_health() -> Dict[str, Any]:
    return run_async(jobs.token_health())
