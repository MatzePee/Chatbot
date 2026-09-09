"""Datenbank-Session-Handling (async)."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, TypeVar

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from autoposter.config import settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs() -> dict:
    """SQLite und PostgreSQL brauchen unterschiedliche Pool-Einstellungen."""
    if settings.is_sqlite:
        settings.ensure_dirs()
        return {
            # SQLite-Verbindungen wandern zwischen asyncio-Tasks.
            "connect_args": {"check_same_thread": False, "timeout": 30},
            # Ein einzelner Writer: verhindert 'database is locked' unter Last.
            "poolclass": NullPool,
        }
    return {"pool_pre_ping": True, "pool_size": 10, "max_overflow": 20}


engine = create_async_engine(settings.database_url, echo=settings.debug, **_engine_kwargs())


if settings.is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:
        """WAL erlaubt gleichzeitiges Lesen und Schreiben – ohne das wird SQLite
        sofort zum Flaschenhals, sobald der Scheduler nebenher arbeitet."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=60000")
        cursor.close()

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI-Dependency."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def write_session() -> AsyncIterator[AsyncSession]:
    """Kurze Schreibtransaktion.

    Für SQLite entscheidend: Schreibzugriffe sollen so kurz wie möglich sein.
    Alles Rechenintensive (Bildverarbeitung) und alles Netzgebundene gehört
    AUSSERHALB dieses Blocks – sonst blockiert es die einzige Schreibsperre und
    andere Zugriffe scheitern mit "database is locked".
    """
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Für Tasks und Skripte."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


T = TypeVar("T")


def run_async(coro: Awaitable[T]) -> T:
    """Async-Code aus einem sync Celery-Worker heraus ausführen."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]
    return loop.run_until_complete(coro)  # pragma: no cover


AsyncCallable = Callable[..., Awaitable[T]]
