"""Versionsanzeige und Update-Pruefung gegen das Git-Repository.

Modell: Als Update gilt NUR ein gesetztes Git-Tag der Form vX.Y.Z. Damit kann
im Repo beliebig committet werden, ohne dass bei einer laufenden Instanz etwas
als Update erscheint - erst ein Tag macht einen Stand offiziell.

Die eigentliche Installation macht bewusst NICHT dieses Modul, sondern das
Root-Helferskript `fanvue-admin update` (siehe deploy/). Der Webprozess darf
sich nicht selbst ueberschreiben, und die sudo-Regel bleibt eng begrenzt.

Alles hier ist unkritisch: schlaegt etwas fehl, wird geloggt und ein Zustand
mit `error` zurueckgegeben. Die App laeuft davon unbeeindruckt weiter.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any, Optional

from . import db

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.]?(.+))?$")


# ---------------------------------------------------------------- Git-Helfer
def _git(*args: str, timeout: int = 25) -> tuple[bool, str]:
    """Fuehrt ein git-Kommando im Repo aus. Rueckgabe: (Erfolg, Ausgabe)."""
    try:
        res = subprocess.run(
            ["git", "-C", REPO_DIR, *args],
            capture_output=True, timeout=timeout,
            # Niemals interaktiv nach Zugangsdaten fragen - sonst haengt der
            # Aufruf ewig, wenn der Deploy-Key fehlt.
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"},
        )
        out = (res.stdout or b"").decode(errors="replace").strip()
        err = (res.stderr or b"").decode(errors="replace").strip()
        return res.returncode == 0, out or err
    except FileNotFoundError:
        return False, "git ist auf diesem System nicht installiert"
    except subprocess.TimeoutExpired:
        return False, "git hat nicht rechtzeitig geantwortet (Netzwerk?)"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def is_git_checkout() -> bool:
    ok, _ = _git("rev-parse", "--git-dir", timeout=5)
    return ok


def parse_version(tag: str) -> Optional[tuple[int, int, int]]:
    """'v1.10.0' -> (1, 10, 0). Ungueltiges -> None.

    Bewusst numerisch: als Text verglichen waere 'v1.9.0' groesser als
    'v1.10.0', und genau das faellt erst beim zehnten Release auf.
    """
    m = _TAG_RE.match((tag or "").strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _newest(tags: list[str]) -> Optional[str]:
    valid = [(parse_version(t), t) for t in tags]
    valid = [(v, t) for v, t in valid if v]
    return max(valid)[1] if valid else None


# ------------------------------------------------------------ Lokaler Stand
def current_version() -> dict[str, Any]:
    """Installierte Version. Faellt ohne Git auf die Datei VERSION zurueck."""
    if not is_git_checkout():
        path = os.path.join(REPO_DIR, "VERSION")
        try:
            with open(path, encoding="utf-8") as fh:
                return {"version": fh.read().strip(), "exact": True, "source": "VERSION-Datei"}
        except OSError:
            return {"version": "unbekannt", "exact": False, "source": "keine Versionsangabe"}

    ok, described = _git("describe", "--tags", "--always", "--dirty")
    ok2, tag = _git("describe", "--tags", "--abbrev=0")
    if not ok:
        return {"version": "unbekannt", "exact": False, "source": "git-Fehler"}
    exact = ok2 and described == tag
    return {
        "version": tag if ok2 else described,
        "described": described,          # z.B. v1.2.0-3-gabc1234 = 3 Commits nach dem Tag
        "exact": bool(exact),
        "source": "git",
        "dirty": described.endswith("-dirty"),
    }


def _commit_of(ref: str) -> str:
    ok, out = _git("rev-list", "-n", "1", ref)
    return out if ok else ""


# ------------------------------------------------------------ Update-Pruefung
def check(fetch: bool = True) -> dict[str, Any]:
    """Prueft, ob ein neueres Tag vorliegt. Ergebnis wird auch zwischengespeichert."""
    state: dict[str, Any] = {"checked_at": time.time(), "error": None,
                             "update_available": False, "changelog": []}
    cur = current_version()
    state["current"] = cur["version"]
    state["current_exact"] = cur.get("exact", False)
    state["dirty"] = cur.get("dirty", False)

    if not is_git_checkout():
        state["error"] = ("Kein Git-Checkout – Updates sind nur möglich, wenn das "
                          "Programm per 'git clone' installiert wurde.")
        _save(state)
        return state

    if fetch:
        ok, msg = _git("fetch", "--tags", "--prune", "--quiet", timeout=60)
        if not ok:
            state["error"] = f"Verbindung zum Repository fehlgeschlagen: {msg[:300]}"
            _save(state)
            return state

    ok, out = _git("tag", "-l", "v*")
    if not ok:
        state["error"] = f"Tags nicht lesbar: {out[:200]}"
        _save(state)
        return state

    latest = _newest([t for t in out.splitlines() if t.strip()])
    state["latest"] = latest or cur["version"]
    cv, lv = parse_version(cur["version"]), parse_version(latest or "")
    if lv and (cv is None or lv > cv):
        state["update_available"] = True
        state["changelog"] = _changelog(cur["version"], latest)
        state["published_at"] = _tag_date(latest)
    _save(state)
    return state


def _changelog(from_ref: str, to_ref: str, limit: int = 25) -> list[str]:
    """Commit-Titel zwischen zwei Staenden - das ist die Änderungsliste."""
    if not from_ref or not to_ref:
        return []
    rng = f"{from_ref}..{to_ref}" if _commit_of(from_ref) else to_ref
    ok, out = _git("log", "--no-merges", f"--max-count={limit}", "--pretty=format:%s", rng)
    if not ok:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _tag_date(tag: str) -> Optional[float]:
    ok, out = _git("log", "-1", "--format=%ct", tag)
    try:
        return float(out) if ok and out.strip().isdigit() else None
    except ValueError:
        return None


# ------------------------------------------------------- Zustand persistieren
def _save(state: dict[str, Any]) -> None:
    try:
        db.set_setting("update_state", json.dumps(state))
    except Exception:  # noqa: BLE001
        pass


def cached_state() -> dict[str, Any]:
    """Letztes Pruefergebnis ohne Netzwerkzugriff (fuer den Seitenaufbau)."""
    raw = db.get_setting("update_state", "")
    if raw:
        try:
            return json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (ValueError, TypeError):
            pass
    return {"current": current_version()["version"], "update_available": False,
            "checked_at": None, "error": None, "changelog": []}


def is_due(now: Optional[float] = None) -> bool:
    if not db.get_setting("update_check_enabled", True):
        return False
    hours = float(db.get_setting("update_check_interval_hours", 6) or 6)
    last = cached_state().get("checked_at")
    return not last or ((now or time.time()) - float(last)) >= hours * 3600


# --------------------------------------------------------------- Installation
def install() -> tuple[bool, str]:
    """Stoesst die Installation ueber das Root-Helferskript an.

    Das Skript sichert die Datenbank, wechselt auf das neueste Tag, installiert
    Abhaengigkeiten nach und startet den Dienst neu - entkoppelt, damit sich der
    Dienst selbst neu starten kann.
    """
    try:
        res = subprocess.run(["sudo", "-n", "/usr/local/bin/fanvue-admin", "update"],
                             capture_output=True, timeout=300)
        out = (res.stdout or b"").decode(errors="replace").strip()
        err = (res.stderr or b"").decode(errors="replace").strip()
        if res.returncode != 0:
            db.log("error", "update", "Update fehlgeschlagen", (err or out)[:800])
            return False, (err or out or "Unbekannter Fehler")[:400]
        db.log("info", "update", "Update eingespielt, Dienst startet neu", out[:800])
        db.set_setting("update_state", "")     # erzwingt frische Pruefung nach dem Neustart
        return True, out[:400]
    except FileNotFoundError:
        return False, "sudo oder /usr/local/bin/fanvue-admin nicht gefunden"
    except subprocess.TimeoutExpired:
        return False, "Update hat zu lange gedauert (Zeitüberschreitung)"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
