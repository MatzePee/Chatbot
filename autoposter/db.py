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


def make_engine():
    """Normal application factory, also used to reopen offline-converted copies."""
    instance = create_async_engine(settings.database_url, echo=settings.debug, **_engine_kwargs())
    if settings.is_sqlite:
        event.listen(instance.sync_engine, 'connect', _sqlite_pragmas)
    return instance


def _sqlite_pragmas(dbapi_connection, _record) -> None:
    from app.sqlite_layout import configure
    cursor = dbapi_connection.cursor()
    try:
        configure(cursor, legacy_sync='NORMAL')
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.execute('PRAGMA busy_timeout=60000')
    finally:
        cursor.close()


engine = make_engine()

def SessionLocal(**kwargs):
    # Lazy import avoids db → models → workspaces → db during metadata setup.
    from autoposter.workspaces import WorkspaceAsyncSession
    return WorkspaceAsyncSession(engine, expire_on_commit=False, **kwargs)


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
