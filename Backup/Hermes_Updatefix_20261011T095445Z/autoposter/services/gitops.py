"""Git-Grundlagen für Veröffentlichen und Aktualisieren.

Gemeinsame Basis von `gitpublish` (hochladen) und `updater` (herunterladen).
Hier steht alles, was beide brauchen: git aufrufen, Zugangsdaten sicher
durchreichen, Ausgaben von Geheimnissen befreien, Versionen vergleichen.

Grundmodell, aus dem sich der Rest ergibt:

    Committen darf man beliebig. Als Update gilt nur ein Git-Tag der Form vX.Y.Z.

Damit trägt Git allein Code *und* Versionsstand — es braucht keinen Paketserver,
keine Release-API und keinen zweiten Kanal.
"""
from __future__ import annotations

import logging
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from autoposter.config import PROJECT_ROOT

logger = logging.getLogger("autoposter.git")

REPO_DIR = PROJECT_ROOT

#: Nur Tags dieser Form gelten als Version. Das führende "v" ist Pflicht, weil
#: der Updater mit `git tag -l 'v*'` sucht – ein Tag "1.2.4" fände er nie.
TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.]?(.+))?$")


class GitError(RuntimeError):
    """git hat mit einem Fehler geantwortet – Ausgabe bereits bereinigt."""


@dataclass
class GitResult:
    ok: bool
    out: str
    err: str
    code: int

    @property
    def text(self) -> str:
        return (self.out + ("\n" + self.err if self.err else "")).strip()


# --------------------------------------------------------------------------- #
# Geheimnisse aus Ausgaben entfernen
# --------------------------------------------------------------------------- #
def scrub(text: str, *secrets: Optional[str]) -> str:
    """Token und eingebettete Zugangsdaten unkenntlich machen.

    Ohne das landet ein Token über eine beliebige Fehlermeldung von git doch
    noch im Protokoll oder in der Oberfläche.
    """
    cleaned = text or ""
    for secret in secrets:
        if secret and len(secret) > 6:
            cleaned = cleaned.replace(secret, "***")
    # https://benutzer:token@github.com/... in jeder Schreibweise
    cleaned = re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1***@", cleaned)
    return cleaned


# --------------------------------------------------------------------------- #
# git aufrufen
# --------------------------------------------------------------------------- #
def run(
    *args: str,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 120,
    secret: Optional[str] = None,
    check: bool = False,
) -> GitResult:
    """Ein git-Kommando im Projektverzeichnis ausführen.

    GIT_TERMINAL_PROMPT=0 ist wichtig: Ohne das bleibt ein Aufruf ohne
    Zugangsdaten ewig an einer Passwortabfrage hängen, die niemand sieht, und
    der Webserver wartet mit.
    """
    full_env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "true",
        "LC_ALL": "C",
        **(env or {}),
    }
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(REPO_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=full_env,
        )
    except FileNotFoundError:
        raise GitError("git ist auf diesem System nicht installiert")
    except subprocess.TimeoutExpired:
        raise GitError(f"git {' '.join(args[:2])} hat nach {timeout} s nicht geantwortet")

    result = GitResult(
        ok=proc.returncode == 0,
        out=scrub(proc.stdout, secret).strip(),
        err=scrub(proc.stderr, secret).strip(),
        code=proc.returncode,
    )
    if check and not result.ok:
        raise GitError(result.err or result.out or f"git {args[0]} fehlgeschlagen")
    return result


def is_git_checkout() -> bool:
    """Wurde per `git clone` installiert? Nur dann gibt es Updates."""
    if not (REPO_DIR / ".git").exists():
        return False
    return run("rev-parse", "--git-dir", timeout=10).ok


# --------------------------------------------------------------------------- #
# Zugangsdaten: Askpass statt Token in der URL
# --------------------------------------------------------------------------- #
def askpass_env(token: str, user: str = "git") -> Tuple[Dict[str, str], Optional[str]]:
    """Temporäres Askpass-Skript, das den Token aus der Umgebung liest.

    Der naheliegende Weg – https://benutzer:token@github.com/… als Remote-URL –
    schreibt den Token dauerhaft im Klartext in .git/config. Ihn als Argument
    zu übergeben ist ebenso falsch: jeder Benutzer sieht ihn per `ps`.
    """
    if not token:
        return {}, None

    # BEWUSST außerhalb des Projektverzeichnisses: Das Veröffentlichen ruft
    # `git add -A` auf, und alles im Repository landet damit im Commit. Genau
    # das ist einmal passiert – die Datei enthält zwar kein Geheimnis, hat aber
    # nichts im Verlauf verloren.
    fd, path = tempfile.mkstemp(prefix="autoposter-askpass-", suffix=".sh")
    with os.fdopen(fd, "w") as handle:
        handle.write(
            '#!/bin/sh\ncase "$1" in\n'
            '  *[Uu]sername*) printf "%s\\n" "$GIT_ASKPASS_USER" ;;\n'
            '  *) printf "%s\\n" "$GIT_ASKPASS_TOKEN" ;;\n'
            "esac\n"
        )
    os.chmod(path, stat.S_IRWXU)  # 0700 – nur der Dienst-Benutzer
    return (
        {
            "GIT_ASKPASS": path,
            "GIT_ASKPASS_USER": user or "git",
            "GIT_ASKPASS_TOKEN": token,
        },
        path,
    )


def drop_askpass(path: Optional[str]) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        logger.warning("Askpass-Skript %s konnte nicht entfernt werden", path)


# --------------------------------------------------------------------------- #
# Versionen
# --------------------------------------------------------------------------- #
def parse_version(tag: str) -> Optional[Tuple[int, int, int]]:
    """'v1.10.0' -> (1, 10, 0). Ungültiges -> None.

    Numerisch, nicht als Zeichenkette: Als Text verglichen wäre 'v1.9.0'
    größer als 'v1.10.0'. Das fällt beim zehnten Release auf – also genau dann,
    wenn schon Instanzen draußen sind.
    """
    match = TAG_RE.match((tag or "").strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def normalize_tag(tag: str) -> str:
    """'1.2.0' oder ' V1.2.0 ' -> 'v1.2.0'. Ungültiges -> ''."""
    raw = re.sub(r"^V", "v", (tag or "").strip())
    parsed = parse_version(raw)
    return f"v{parsed[0]}.{parsed[1]}.{parsed[2]}" if parsed else ""


def sort_tags(tags: Sequence[str]) -> List[str]:
    """Aufsteigend nach Zahlen sortiert, Unlesbares fliegt raus."""
    valid = [(parse_version(t), t) for t in tags]
    return [t for parsed, t in sorted((p, t) for p, t in valid if p)]


def current_version() -> str:
    """Welche Version läuft hier?

    `git describe` liefert bei einem Stand hinter dem Tag etwas wie
    'v1.2.0-3-gabc1234' – das ist gewollt, es zeigt ehrlich an, dass hier nicht
    genau ein Release liegt.
    """
    if is_git_checkout():
        described = run("describe", "--tags", "--always", timeout=15)
        if described.ok and described.out:
            return described.out
    version_file = REPO_DIR / "VERSION"
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip() or "unbekannt"
    return "unbekannt"


def current_branch(default: str = "main") -> str:
    result = run("rev-parse", "--abbrev-ref", "HEAD", timeout=10)
    branch = result.out.strip() if result.ok else ""
    return branch if branch and branch != "HEAD" else default


def changelog(from_ref: str, to_ref: str, limit: int = 50) -> List[str]:
    """Commit-Titel zwischen zwei Ständen – daraus wird die Änderungsliste."""
    if not from_ref or not to_ref:
        return []
    result = run(
        "log", "--no-merges", "--pretty=%s", f"{from_ref}..{to_ref}", f"-{limit}", timeout=30
    )
    if not result.ok:
        return []
    return [line.strip() for line in result.out.splitlines() if line.strip()]
