"""Updates prüfen und einspielen.

Nur lesend, mit einer Ausnahme: `install()` ruft das Root-Helferskript auf.
Der Webprozess installiert bewusst nicht selbst – er würde sich beim
`git checkout` die eigenen Quelldateien unter den Füßen wegziehen und beim
Neustart die noch laufende HTTP-Antwort abwürgen.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.services import appconfig, gitops

logger = logging.getLogger("autoposter.updater")

ADMIN_SCRIPT = "/usr/local/bin/autoposter-admin"
DEFAULT_INTERVAL_HOURS = 6


def _empty_state(**extra: Any) -> Dict[str, Any]:
    state = {
        "checked_at": None,
        "current": gitops.current_version(),
        "latest": "",
        "update_available": False,
        "changelog": [],
        "error": "",
        "is_git": gitops.is_git_checkout(),
    }
    state.update(extra)
    return state


async def cached_state(db: AsyncSession) -> Dict[str, Any]:
    """Nur die gespeicherte Antwort lesen – **kein Netzwerk**.

    Das nutzt jeder Seitenaufruf. Würde hier gefetcht, hinge der Seitenaufbau
    an der Erreichbarkeit von GitHub.
    """
    raw = await appconfig.get(db, "update_state", "") or ""
    if not raw:
        return _empty_state()
    try:
        state = json.loads(raw)
    except (ValueError, TypeError):
        return _empty_state()
    # Die installierte Version immer frisch – sie ändert sich beim Update,
    # ohne dass jemand eine Prüfung anstößt.
    state["current"] = gitops.current_version()
    state["is_git"] = gitops.is_git_checkout()
    if state.get("latest"):
        current = gitops.parse_version(state["current"])
        latest = gitops.parse_version(state["latest"])
        state["update_available"] = bool(current and latest and latest > current)
    return state


async def is_due(db: AsyncSession) -> bool:
    """Drosselung sitzt hier, nicht beim Aufrufer.

    So kann der Hintergrund-Zyklus die Prüfung in jedem Durchlauf aufrufen.
    """
    if not await appconfig.get(db, "update_check_enabled", True):
        return False
    state = await cached_state(db)
    stamp = state.get("checked_at")
    if not stamp:
        return True
    try:
        last = datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    hours = int(await appconfig.get(db, "update_check_interval_hours", DEFAULT_INTERVAL_HOURS) or DEFAULT_INTERVAL_HOURS)
    return datetime.now(timezone.utc) - last >= timedelta(hours=max(1, hours))


async def check(db: AsyncSession, *, fetch: bool = True) -> Dict[str, Any]:
    """Tags holen, höchstes ermitteln, mit dem installierten Stand vergleichen."""
    state = _empty_state(checked_at=datetime.now(timezone.utc).isoformat())

    if not gitops.is_git_checkout():
        state["error"] = (
            "Kein Git-Arbeitsverzeichnis. Updates brauchen eine Installation per "
            "'git clone' – siehe docs/handbuch.md, Abschnitt „Veröffentlichen und Aktualisieren“."
        )
        await _store(db, state)
        return state

    token = str(await appconfig.get(db, "github_token", "") or "")
    user = str(await appconfig.get(db, "git_user_name", "") or "git")
    env, askpass = gitops.askpass_env(token, user)
    try:
        if fetch:
            result = gitops.run(
                "fetch", "--tags", "--prune-tags", "--force", "origin",
                env=env, timeout=90, secret=token,
            )
            if not result.ok:
                state["error"] = result.err or "Abrufen von GitHub fehlgeschlagen"
                await _store(db, state)
                return state

        tags = gitops.run("tag", "-l", "v*", timeout=20)
        ordered = gitops.sort_tags(tags.out.splitlines()) if tags.ok else []
        state["latest"] = ordered[-1] if ordered else ""

        current = gitops.parse_version(state["current"])
        latest = gitops.parse_version(state["latest"])
        state["update_available"] = bool(current and latest and latest > current)

        if state["update_available"]:
            # Von der installierten Version bis zum neuen Tag – das ist die
            # Liste dessen, was das Update mitbringt.
            base = state["current"].split("-")[0]
            state["changelog"] = gitops.changelog(base, state["latest"])
    finally:
        gitops.drop_askpass(askpass)

    await _store(db, state)
    return state


async def _store(db: AsyncSession, state: Dict[str, Any]) -> None:
    await appconfig.set_many(db, {"update_state": json.dumps(state, ensure_ascii=False)})


def _run_admin(action: str, *, timeout: int) -> Dict[str, Any]:
    """Den Root-Helfer aufrufen – die einzige Stelle mit sudo.

    `-n` heißt: nie interaktiv nach einem Passwort fragen, lieber scheitern.
    Sonst hinge der Aufruf an einer Eingabeaufforderung, die niemand sieht.
    """
    if not shutil.which("sudo"):
        return {"ok": False, "message": "sudo ist nicht verfügbar (auf dem Mac erwartbar)"}
    if not os.path.exists(ADMIN_SCRIPT):
        return {
            "ok": False,
            "message": (
                f"{ADMIN_SCRIPT} fehlt. Der Neustart über die Oberfläche gibt es nur "
                "auf dem Server; dort einmalig './run.sh admin install' ausführen."
            ),
        }

    try:
        proc = subprocess.run(
            ["sudo", "-n", ADMIN_SCRIPT, action],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return {"ok": False, "message": f"{ADMIN_SCRIPT} fehlt – './run.sh admin install' ausführen"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": f"Keine Antwort nach {timeout} Sekunden"}

    output = gitops.scrub((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        hint = ""
        low = output.lower()
        if "sudo:" in low and "password" in low:
            hint = " Die sudo-Regel fehlt: './run.sh admin install' auf dem Server ausführen."
        elif "no new privileges" in low:
            hint = (
                " Die systemd-Härtung blockiert sudo. Siehe docs/handbuch.md, "
                "Abschnitt „Wenn das Update mit ‚no new privileges' abbricht“."
            )
        return {"ok": False, "message": (output or f"'{action}' fehlgeschlagen") + hint}
    return {"ok": True, "output": output}


async def restart_service(db: AsyncSession) -> Dict[str, Any]:
    """Den Dienst neu starten.

    Der Helfer stößt das über `systemd-run --no-block` an. Das ist wesentlich:
    Ein direktes `systemctl restart` würde diesen Prozess abräumen, bevor er
    antworten kann – die Bedienerin sähe einen Verbindungsfehler statt einer
    Bestätigung.
    """
    result = _run_admin("restart-service", timeout=30)
    if not result.get("ok"):
        return result
    return {
        "ok": True,
        "message": "Neustart angestoßen. In etwa 10 Sekunden ist der Dienst wieder da.",
        "output": result.get("output", ""),
    }


async def install(db: AsyncSession) -> Dict[str, Any]:
    """Update einspielen. Installiert wird nur auf Knopfdruck, nie von allein."""
    if not gitops.is_git_checkout():
        return {"ok": False, "message": "Kein Git-Arbeitsverzeichnis – Update nicht möglich"}

    result = _run_admin("update", timeout=300)
    if not result.get("ok"):
        return result

    # Nach dem Update ist der gespeicherte Zustand überholt.
    await appconfig.set_many(db, {"update_state": ""})
    return {
        "ok": True,
        "message": "Update eingespielt. Der Dienst startet gleich neu.",
        "output": result.get("output", ""),
    }


async def cycle(db: AsyncSession) -> Dict[str, Any]:
    """Vom Hintergrund-Zyklus aufgerufen. Meldet nur beim ersten Auftauchen.

    Ohne diese Bedingung käme alle sechs Stunden dieselbe Nachricht.
    """
    if not await is_due(db):
        return {"skipped": "noch nicht fällig"}

    before = (await cached_state(db)).get("latest", "")
    state = await check(db, fetch=True)

    if state.get("update_available") and state.get("latest") and state["latest"] != before:
        from autoposter.services import notify

        await notify.push(
            db,
            level="info",
            title=f"Neue Version verfügbar: {state['latest']}",
            body=(
                f"Installiert ist {state['current']}. "
                + (" · ".join(state.get("changelog", [])[:5]) or "Details unter System.")
            ),
            entity="system",
            entity_id="update",
            external=False,
        )
    return {"latest": state.get("latest", ""), "update_available": state.get("update_available", False)}
