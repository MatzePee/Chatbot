"""Schema-Abgleich: fehlende Spalten in bestehenden Tabellen ergänzen.

Hintergrund: `Base.metadata.create_all` legt nur fehlende TABELLEN an. Kommt zu
einer bestehenden Tabelle eine Spalte hinzu, bleibt die Datenbank stumm veraltet
und jede Abfrage scheitert mit "no such column".

Für eine selbst gehostete Anwendung mit SQLite ist ein automatischer Abgleich beim
Start die richtige Antwort: ADD COLUMN ist auf SQLite wie auf PostgreSQL eine
schnelle, verlustfreie Operation. Umbenennungen, Typänderungen und Löschungen
macht dieser Abgleich bewusst NICHT — dafür ist Alembic zuständig.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from autoposter.db import Base
import autoposter.models  # noqa: F401  – registriert alle Tabellen

logger = logging.getLogger("autoposter.schema")


def _default_literal(column) -> str:
    """SQL-Literal für den Vorgabewert einer neuen Spalte.

    Nicht-nullbare Spalten brauchen bei ADD COLUMN zwingend einen Default,
    sonst würden bestehende Zeilen ungültig.
    """
    default = column.default
    if default is not None and getattr(default, "is_scalar", False):
        value = default.arg
    elif default is not None and getattr(default, "is_callable", False):
        try:
            value = default.arg(None)
        except Exception:
            value = None
    else:
        value = None

    if value is None and column.nullable:
        return "NULL"

    if value is None:
        # Typgerechter Ersatzwert für Pflichtspalten ohne Default.
        python_type = None
        try:
            python_type = column.type.python_type
        except (NotImplementedError, AttributeError):
            pass
        if python_type is bool:
            value = False
        elif python_type is int:
            value = 0
        elif python_type is float:
            value = 0.0
        elif python_type in (list,):
            value = []
        elif python_type in (dict,):
            value = {}
        else:
            value = ""

    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, dict)):
        return "'" + json.dumps(value).replace("'", "''") + "'"
    return "'" + str(value).replace("'", "''") + "'"


def _plan(sync_connection) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Ermittelt fehlende Tabellen und Spalten."""
    inspector = inspect(sync_connection)
    existing_tables = set(inspector.get_table_names())

    missing_tables: List[str] = []
    missing_columns: List[Dict[str, Any]] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            missing_tables.append(table.name)
            continue
        present = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in present:
                missing_columns.append({"table": table.name, "column": column})

    return missing_tables, missing_columns


async def sync(engine: AsyncEngine, *, dry_run: bool = False) -> Dict[str, Any]:
    """Schema angleichen. Gibt zurück, was getan wurde (bzw. nötig wäre)."""
    async with engine.begin() as conn:
        missing_tables, missing_columns = await conn.run_sync(_plan)

        report: Dict[str, Any] = {
            "created_tables": [],
            "added_columns": [],
            "failed": [],
            "dry_run": dry_run,
        }

        if missing_tables and not dry_run:
            await conn.run_sync(Base.metadata.create_all)
            report["created_tables"] = missing_tables
        elif missing_tables:
            report["created_tables"] = missing_tables

        dialect = conn.dialect
        for entry in missing_columns:
            column = entry["column"]
            table_name = entry["table"]
            try:
                type_sql = column.type.compile(dialect=dialect)
            except Exception:
                type_sql = "TEXT"
            nullable = "" if column.nullable else " NOT NULL"
            default_sql = _default_literal(column)
            ddl = (
                f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" '
                f"{type_sql}{nullable} DEFAULT {default_sql}"
            )
            label = f"{table_name}.{column.name}"

            if dry_run:
                report["added_columns"].append(label)
                continue
            try:
                await conn.execute(text(ddl))
                report["added_columns"].append(label)
                logger.info("Spalte ergänzt: %s", label)
            except Exception as exc:
                report["failed"].append({"column": label, "error": str(exc)[:200], "sql": ddl})
                logger.error("Spalte %s konnte nicht ergänzt werden: %s", label, exc)

    return report


def summarize(report: Dict[str, Any]) -> str:
    parts = []
    if report["created_tables"]:
        parts.append(f"{len(report['created_tables'])} Tabelle(n) angelegt")
    if report["added_columns"]:
        parts.append(f"{len(report['added_columns'])} Spalte(n) ergänzt")
    if report["failed"]:
        parts.append(f"{len(report['failed'])} FEHLGESCHLAGEN")
    return ", ".join(parts) if parts else "Schema war bereits aktuell"
