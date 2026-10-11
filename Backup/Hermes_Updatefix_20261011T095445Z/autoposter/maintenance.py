"""Wartungs-CLI.

    python -m app.maintenance rebuild-counters
    python -m app.maintenance archive-used 30
    python -m app.maintenance report
    python -m app.maintenance users                 # Benutzer auflisten
    python -m app.maintenance passwd <email>        # Passwort neu setzen
"""
from __future__ import annotations

import asyncio
import getpass
import sys

from sqlalchemy import select

from autoposter.db import engine, session_scope
from autoposter.models import Role, User
from autoposter.security import hash_password
from autoposter.services import library, lifecycle, media as media_service


async def rebuild_counters() -> None:
    async with session_scope() as db:
        total = await lifecycle.refresh_all(db)
    library.invalidate_counts()
    print(f"{total} Assets neu berechnet")


async def archive_used(days: int) -> None:
    async with session_scope() as db:
        candidates = await media_service.archive_candidates(db, days)
        count = await media_service.archive(db, [c.id for c in candidates], True)
    library.invalidate_counts()
    print(f"{count} Bilder archiviert (vollständig verbraucht, älter als {days} Tage)")


async def report() -> None:
    async with session_scope() as db:
        counts = await library.lifecycle_counts(db, use_cache=False)
    for field, value in counts.model_dump().items():
        print(f"  {field:<16} {value}")


async def list_users() -> None:
    async with session_scope() as db:
        users = (await db.execute(select(User).order_by(User.created_at))).scalars().all()
    if not users:
        print("  Keine Benutzer vorhanden.")
        return
    print(f"  {'E-Mail':<36} {'Rolle':<8} {'Aktiv':<6} Zuletzt angemeldet")
    for u in users:
        last = u.last_login_at.strftime("%d.%m.%Y %H:%M") if u.last_login_at else "nie"
        print(f"  {u.email:<36} {u.role:<8} {'ja' if u.is_active else 'nein':<6} {last}")


async def set_password(email: str, password: str | None = None) -> int:
    """Passwort neu setzen. Legt den Benutzer an, falls er noch nicht existiert."""
    email = email.strip().lower()
    if not password:
        password = getpass.getpass("  Neues Passwort (min. 10 Zeichen): ")
        if password != getpass.getpass("  Wiederholen: "):
            print("  Die Eingaben stimmen nicht überein.")
            return 1
    if len(password) < 10:
        print("  Passwort zu kurz (mindestens 10 Zeichen).")
        return 1

    async with session_scope() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user:
            user.password_hash = hash_password(password)
            user.failed_logins = 0
            user.locked_until = None
            user.is_active = True
            db.add(user)
            print(f"  Passwort für {email} neu gesetzt. Sperren wurden aufgehoben.")
        else:
            db.add(
                User(
                    email=email,
                    password_hash=hash_password(password),
                    display_name=email.split("@")[0],
                    role=Role.admin.value,
                )
            )
            print(f"  Benutzer {email} als Administrator angelegt.")
    return 0


async def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "report"
    code = 0
    if command == "rebuild-counters":
        await rebuild_counters()
    elif command == "archive-used":
        await archive_used(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    elif command == "report":
        await report()
    elif command == "users":
        await list_users()
    elif command == "passwd":
        if len(sys.argv) < 3:
            print("  Aufruf: python -m app.maintenance passwd <email>")
            code = 1
        else:
            code = await set_password(sys.argv[2])
    else:
        print(__doc__)
    await engine.dispose()
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    asyncio.run(main())
