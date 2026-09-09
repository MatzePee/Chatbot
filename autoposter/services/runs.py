"""Langläufer im Hintergrund, mit Fortschritt und Abbruch.

Das Planen eines Monats sind schnell hundert LLM-Aufrufe. Als eine einzige
HTTP-Anfrage bedeutet das minutenlanges Warten vor einem Ladekreisel, ohne zu
wissen, ob überhaupt etwas passiert – und ohne die Möglichkeit abzubrechen.

Deshalb: Der Aufruf startet den Lauf und bekommt sofort eine Kennung zurück.
Die Oberfläche fragt den Fortschritt ab. Der Zustand liegt im Speicher des
Prozesses; im portablen Betrieb mit einem Prozess ist das genau richtig, und
nach einem Neustart ist ein abgebrochener Lauf ohnehin gegenstandslos.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("autoposter.runs")

#: Fertige Läufe werden nach dieser Zeit vergessen.
KEEP_FINISHED = timedelta(minutes=30)


class Cancelled(RuntimeError):
    """Der Lauf wurde von der Oberfläche abgebrochen."""


@dataclass
class Run:
    id: str
    label: str
    total: int = 0
    done: int = 0
    message: str = ""
    #: Meldungen, die den Nutzer betreffen (übersprungene Slots o. Ä.)
    notes: List[str] = field(default_factory=list)
    status: str = "running"  # running | done | error | cancelled
    result: Optional[Dict[str, Any]] = None
    error: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    _cancel: bool = False

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return min(100, int(round(100 * self.done / self.total)))

    @property
    def seconds_left(self) -> Optional[int]:
        """Grobe Restzeit aus dem bisherigen Tempo."""
        if self.done <= 0 or self.status != "running":
            return None
        elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        per_item = elapsed / self.done
        return int(round(per_item * max(0, self.total - self.done)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "total": self.total,
            "done": self.done,
            "percent": self.percent,
            "message": self.message,
            "notes": self.notes[-20:],
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "seconds_left": self.seconds_left,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


_runs: Dict[str, Run] = {}
_tasks: Dict[str, asyncio.Task] = {}


def _prune() -> None:
    cutoff = datetime.now(timezone.utc) - KEEP_FINISHED
    for run_id, run in list(_runs.items()):
        if run.finished_at and run.finished_at < cutoff:
            _runs.pop(run_id, None)
            _tasks.pop(run_id, None)


def create(label: str, total: int = 0) -> Run:
    _prune()
    run = Run(id=uuid.uuid4().hex[:12], label=label, total=total)
    _runs[run.id] = run
    return run


def get(run_id: str) -> Optional[Run]:
    return _runs.get(run_id)


def active() -> List[Run]:
    return [r for r in _runs.values() if r.status == "running"]


def cancel(run_id: str) -> bool:
    """Abbruch anfordern. Der Lauf beendet sich beim nächsten Prüfpunkt."""
    run = _runs.get(run_id)
    if not run or run.status != "running":
        return False
    run._cancel = True
    run.message = "Abbruch angefordert – laufender Schritt wird noch beendet"
    return True


def is_cancelled(run: Run) -> bool:
    return run._cancel


def check(run: Optional[Run]) -> None:
    """In Schleifen aufrufen: bricht den Lauf sauber ab."""
    if run is not None and run._cancel:
        raise Cancelled()


def step(run: Optional[Run], message: str = "", *, increment: int = 1) -> None:
    if run is None:
        return
    run.done += increment
    if message:
        run.message = message


def note(run: Optional[Run], text: str) -> None:
    if run is not None and text:
        run.notes.append(text)


def launch(run: Run, work: Callable[[Run], Awaitable[Dict[str, Any]]]) -> Run:
    """Arbeit im Hintergrund starten und den Lauf zu Ende führen."""

    async def _wrapper() -> None:
        try:
            run.result = await work(run)
            run.status = "cancelled" if run._cancel else "done"
            if run.status == "done":
                run.message = "Fertig"
                run.done = max(run.done, run.total)
        except Cancelled:
            run.status = "cancelled"
            run.message = "Abgebrochen"
        except Exception as exc:  # noqa: BLE001 – der Fehler gehört in die Oberfläche
            logger.exception("Lauf %s fehlgeschlagen", run.label)
            run.status = "error"
            run.error = str(exc)
            run.message = "Fehlgeschlagen"
        finally:
            run.finished_at = datetime.now(timezone.utc)

    _tasks[run.id] = asyncio.create_task(_wrapper())
    return run
