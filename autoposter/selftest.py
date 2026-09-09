"""Selbsttest: schreibt und liest jede Modellklasse einmal durch.

Zweck: Fehler in der Typ-Abbildung finden, bevor der Server startet. Genau diese
Klasse von Problemen (etwa ein UUID-Typ, den der Treiber nicht binden kann) fällt
weder beim Kompilieren noch bei einer Importprüfung auf – nur beim echten INSERT.

    python -m app.selftest            # gegen In-Memory-SQLite (schnell, folgenlos)
    python -m app.selftest --live     # zusätzlich gegen die konfigurierte Datenbank

Benötigt kein Netz und keine laufenden Dienste.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from sqlalchemy import (
    Boolean, DateTime, Float, Integer, JSON, Numeric, String, Text, Uuid, inspect, select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from autoposter.db import Base
import autoposter.models as models  # noqa: F401  – registriert alle Tabellen

GREEN, RED, YELLOW, DIM, NC = "\033[0;32m", "\033[0;31m", "\033[1;33m", "\033[2m", "\033[0m"


def sample_value(column) -> Any:
    """Repräsentativen Wert für eine Spalte erzeugen."""
    t = column.type
    # Eigene Typen (z. B. UTCDateTime) auf ihren zugrunde liegenden Typ zurückführen.
    while isinstance(t, TypeDecorator):
        t = t.impl if not isinstance(t.impl, type) else t.impl()
    if isinstance(t, Uuid):
        return uuid.uuid4()
    if isinstance(t, (JSONB, JSON)):
        return [] if "ids" in column.name or "topics" in column.name or "pool" in column.name else {}
    if isinstance(t, Boolean):
        return True
    if isinstance(t, (Integer,)):
        return 1
    if isinstance(t, (Float, Numeric)):
        return 1.5
    if isinstance(t, DateTime):
        return datetime.now(timezone.utc)
    if isinstance(t, (String, Text)):
        length = getattr(t, "length", None)
        value = "selftest"
        return value[:length] if length else value
    return "selftest"


def build_instance(model) -> Any:
    """Instanz mit allen Pflichtfeldern, die keinen Default haben."""
    kwargs: Dict[str, Any] = {}
    mapper = inspect(model)
    for column in mapper.columns:
        if column.nullable:
            continue
        if column.default is not None or column.server_default is not None:
            continue
        attr = mapper.get_property_by_column(column).key
        kwargs[attr] = sample_value(column)
    return model(**kwargs)


async def run(database_url: str, label: str) -> Tuple[int, List[str]]:
    engine = create_async_engine(
        database_url,
        connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {},
    )
    problems: List[str] = []
    checked = 0

    try:
        async with engine.begin() as conn:
            if database_url.startswith("sqlite"):
                await conn.run_sync(Base.metadata.create_all)

        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        models_to_test = [m.class_ for m in Base.registry.mappers]
        models_to_test.sort(key=lambda m: m.__name__)

        for model in models_to_test:
            name = model.__name__
            async with maker() as session:
                try:
                    obj = build_instance(model)
                    session.add(obj)
                    await session.flush()

                    mapper = inspect(model)
                    pk_col = list(mapper.primary_key)[0]
                    pk_attr = mapper.get_property_by_column(pk_col).key
                    pk_value = getattr(obj, pk_attr)

                    await session.commit()

                    async with maker() as verify:
                        loaded = (
                            await verify.execute(
                                select(model).where(pk_col == pk_value)
                            )
                        ).scalar_one_or_none()

                    if loaded is None:
                        problems.append(f"{name}: geschrieben, aber nicht wiedergefunden")
                        continue

                    back = getattr(loaded, pk_attr)
                    if isinstance(pk_col.type, Uuid) and not isinstance(back, uuid.UUID):
                        problems.append(
                            f"{name}: Primärschlüssel kommt als {type(back).__name__} zurück, erwartet UUID"
                        )
                        continue

                    # Zeitstempel MÜSSEN mit Zeitzone zurückkommen, sonst
                    # scheitert jeder Vergleich mit datetime.now(timezone.utc).
                    for column in mapper.columns:
                        if isinstance(column.type, TypeDecorator) and isinstance(
                            column.type.impl, DateTime
                        ):
                            attr = mapper.get_property_by_column(column).key
                            value = getattr(loaded, attr)
                            if value is not None and value.tzinfo is None:
                                problems.append(
                                    f"{name}.{attr}: Zeitstempel ohne Zeitzone – "
                                    f"Vergleiche mit UTC schlagen fehl"
                                )

                    # Listen- und Objektspalten müssen ihren Python-Typ behalten.
                    for column in mapper.columns:
                        if isinstance(column.type, (JSONB, JSON)):
                            attr = mapper.get_property_by_column(column).key
                            value = getattr(loaded, attr)
                            if value is not None and not isinstance(value, (list, dict)):
                                problems.append(
                                    f"{name}.{attr}: JSON-Spalte kommt als {type(value).__name__} zurück"
                                )

                    checked += 1
                    print(f"  {GREEN}✓{NC} {name}")
                except Exception as exc:
                    await session.rollback()
                    first_line = str(exc).split("\n")[0][:160]
                    problems.append(f"{name}: {first_line}")
                    print(f"  {RED}✗{NC} {name} {DIM}{first_line}{NC}")
    finally:
        await engine.dispose()

    return checked, problems


def check_app() -> List[str]:
    """Die FastAPI-Anwendung wirklich laden.

    Viele Fehler in Routendefinitionen (falscher Statuscode, fehlender
    Pfadparameter, unauflösbares Antwortmodell) treten erst beim Registrieren
    der Routen auf – also beim Import, nicht beim Kompilieren.
    """
    problems: List[str] = []
    try:
        from autoposter.main import autoposter  # noqa: WPS433 – bewusst spät importiert

        paths = {
            getattr(route, "path", "")
            for route in app.routes
            if getattr(route, "path", "").startswith("/api/")
        }
        print(f"  {GREEN}✓{NC} FastAPI geladen, {len(paths)} API-Routen registriert")
    except Exception as exc:
        problems.append(f"Anwendung lässt sich nicht laden: {type(exc).__name__}: {exc}")
        print(f"  {RED}✗{NC} FastAPI: {exc}")
    return problems


async def main() -> int:
    print(f"AutoPoster Selbsttest – {len(Base.registry.mappers)} Modelle\n")

    print(f"{DIM}In-Memory-SQLite{NC}")
    checked, problems = await run("sqlite+aiosqlite:///:memory:", "sqlite")

    print(f"\n{DIM}Anwendung und Routen{NC}")
    problems += check_app()

    # Weicht die echte Datenbank vom Modell ab?
    try:
        from autoposter import schema_sync
        from autoposter.config import settings as app_settings
        from sqlalchemy.ext.asyncio import create_async_engine as _create

        app_settings.ensure_dirs()
        live_engine = _create(app_settings.database_url)
        pending = await schema_sync.sync(live_engine, dry_run=True)
        await live_engine.dispose()
        if pending["added_columns"] or pending["created_tables"]:
            print(f"\n{YELLOW}Schema der Datenbank ist veraltet{NC}")
            for entry in pending["created_tables"]:
                print(f"  fehlende Tabelle: {entry}")
            for entry in pending["added_columns"]:
                print(f"  fehlende Spalte:  {entry}")
            print("  Wird beim nächsten Start automatisch ergänzt.")
    except Exception as exc:
        print(f"  {YELLOW}Schema konnte nicht geprüft werden: {exc}{NC}")

    if "--live" in sys.argv:
        from autoposter.config import settings

        print(f"\n{DIM}Konfigurierte Datenbank ({'sqlite' if settings.is_sqlite else 'postgresql'}){NC}")
        settings.ensure_dirs()
        live_checked, live_problems = await run(settings.database_url, "live")
        checked += live_checked
        problems += [f"[live] {p}" for p in live_problems]

    print()
    if problems:
        print(f"{RED}{len(problems)} Problem(e):{NC}")
        for p in problems:
            print(f"  • {p}")
        return 1

    print(f"{GREEN}Alle {checked} Modelle schreiben und lesen sauber.{NC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
