"""Eingebauter Scheduler – führt die Hintergrundjobs im API-Prozess aus.

Damit läuft AutoPoster ohne Redis, Celery-Worker und Beat. Für den portablen
Betrieb auf einem Rechner ist das die richtige Wahl; für mehrere Maschinen oder
sehr hohe Last bleibt SCHEDULER_MODE=celery die bessere Option.

Jeder Job läuft in einer eigenen Schleife. Ein Fehler in einem Job legt die
anderen nicht lahm und beendet den Webserver nicht.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from autoposter import jobs
from autoposter.config import settings

logger = logging.getLogger("autoposter.scheduler")


@dataclass
class Job:
    name: str
    func: Callable[[], Awaitable[Dict[str, Any]]]
    interval_seconds: float
    #: Verzögerung vor dem ersten Lauf, damit nicht alles gleichzeitig startet.
    initial_delay: float = 5.0
    last_run: Optional[datetime] = None
    last_result: Optional[Dict[str, Any]] = None
    last_error: Optional[str] = None
    runs: int = 0


def default_jobs() -> List[Job]:
    return [
        Job("dispatch_due_posts", lambda: jobs.dispatch_due_posts(inline=True), 60, 10),
        Job("generate_plan", lambda: jobs.generate_plan(14), 3600, 60),
        Job("refresh_metrics", lambda: jobs.refresh_metrics(72), 1800, 300),
        Job("describe_assets", lambda: jobs.describe_assets(10), 900, 120),
        Job("inventory_check", lambda: jobs.inventory_check(), 6 * 3600, 180),
        Job("nightly_maintenance", lambda: jobs.nightly_maintenance(), 24 * 3600, 600),
        Job("token_health", lambda: jobs.token_health(), 900, 90),
        # Stündlich aufrufen, die eigentliche Drosselung (Standard 6 h) sitzt
        # in updater.is_due() – so lässt sie sich zur Laufzeit ändern.
    ]


class Scheduler:
    def __init__(self, job_list: Optional[List[Job]] = None) -> None:
        self.jobs: List[Job] = job_list if job_list is not None else default_jobs()
        self._tasks: List[asyncio.Task] = []
        self._stopping = asyncio.Event()

    async def _loop(self, job: Job) -> None:
        # Kleiner Zufallsversatz, damit die Jobs nicht im Gleichtakt laufen.
        await self._sleep(job.initial_delay + random.uniform(0, 5))
        while not self._stopping.is_set():
            started = datetime.now(timezone.utc)
            try:
                job.last_result = await job.func()
                job.last_error = None
                logger.debug("Job %s: %s", job.name, job.last_result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                job.last_error = str(exc)[:500]
                logger.exception("Job %s fehlgeschlagen", job.name)
            finally:
                job.last_run = started
                job.runs += 1
            await self._sleep(job.interval_seconds)

    async def _sleep(self, seconds: float) -> None:
        """Unterbrechbares Warten – der Prozess soll sofort beendbar bleiben."""
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def start(self) -> None:
        if self._tasks:
            return
        self._stopping.clear()
        for job in self.jobs:
            self._tasks.append(asyncio.create_task(self._loop(job), name=f"job:{job.name}"))
        logger.info(
            "Eingebauter Scheduler gestartet (%d Jobs): %s",
            len(self.jobs),
            ", ".join(j.name for j in self.jobs),
        )

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("Scheduler gestoppt")

    async def run_now(self, name: str) -> Dict[str, Any]:
        """Einen Job sofort auslösen (aus dem UI heraus)."""
        job = next((j for j in self.jobs if j.name == name), None)
        if not job:
            raise KeyError(f"Unbekannter Job: {name}")
        job.last_run = datetime.now(timezone.utc)
        job.runs += 1
        try:
            job.last_result = await job.func()
            job.last_error = None
        except Exception as exc:
            job.last_error = str(exc)[:500]
            raise
        return job.last_result or {}

    def status(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": j.name,
                "interval_seconds": j.interval_seconds,
                "runs": j.runs,
                "last_run": j.last_run.isoformat() if j.last_run else None,
                "last_result": j.last_result,
                "last_error": j.last_error,
            }
            for j in self.jobs
        ]


#: Wird im Lifespan der FastAPI-App gesetzt, wenn SCHEDULER_MODE=inprocess.
scheduler: Optional[Scheduler] = None


async def start_if_enabled() -> Optional[Scheduler]:
    global scheduler
    if settings.scheduler_mode != "inprocess":
        logger.info("Eingebauter Scheduler deaktiviert (SCHEDULER_MODE=%s)", settings.scheduler_mode)
        return None
    scheduler = Scheduler()
    await scheduler.start()
    return scheduler


async def stop_if_running() -> None:
    global scheduler
    if scheduler:
        await scheduler.stop()
        scheduler = None
