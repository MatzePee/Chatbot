"""Veroeffentlichen des aktuellen Standes nach GitHub (Seite /upload).

Macht dasselbe wie deploy/git-setup.sh, nur aus der Oberflaeche heraus:
Aenderungen anzeigen, Version festlegen, committen, taggen, hochladen.

Zwei Dinge sind hier bewusst geloest:

1. Der Token wandert NIE in die Kommandozeile (waere in `ps` sichtbar) und
   NIE in .git/config oder die Remote-URL (bliebe dauerhaft im Klartext auf
   der Platte). Stattdessen bekommt git ein temporaeres Askpass-Skript, das
   den Wert aus der Umgebung des Kindprozesses liest.

2. Vor jedem Commit laeuft dieselbe Sicherheitspruefung wie im Skript. Sie
   BLOCKIERT - ein einmal veroeffentlichter Schluessel laesst sich nicht
   zurueckholen, das ist kein Fall fuer eine blosse Warnung.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
from typing import Any, Optional

from . import db, updater

REPO_DIR = updater.REPO_DIR

# Muster echter Anbieter-Schluessel. Bewusst eng, damit Platzhalter in
# .env.example ("dein-key-hier") keinen Fehlalarm ausloesen.
_SECRET_PATTERNS = re.compile(
    r"sk-or-v1-[A-Za-z0-9]{24}"
    r"|sk-[A-Za-z0-9]{32,}"
    r"|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}"
    r"|ghp_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{30,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_FORBIDDEN_PATHS = re.compile(r"^(\.env$|data/|\.venv/|exports/)")


# ------------------------------------------------------------------ Git-Helfer
def _git(*args: str, timeout: int = 30, env_extra: Optional[dict] = None) -> tuple[bool, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    if env_extra:
        env.update(env_extra)
    else:
        env["GIT_ASKPASS"] = "true"          # niemals interaktiv nachfragen
    try:
        res = subprocess.run(["git", "-C", REPO_DIR, *args],
                             capture_output=True, timeout=timeout, env=env)
        out = (res.stdout or b"").decode(errors="replace").strip()
        err = (res.stderr or b"").decode(errors="replace").strip()
        return res.returncode == 0, (out or err)
    except FileNotFoundError:
        return False, "git ist nicht installiert"
    except subprocess.TimeoutExpired:
        return False, "git hat nicht rechtzeitig geantwortet"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _scrub(text: str) -> str:
    """Entfernt einen eventuell durchgereichten Token aus Ausgaben."""
    token = str(db.get_setting("github_token", "") or "").strip()
    if token and token in text:
        text = text.replace(token, "***")
    return re.sub(r"(https://)[^@/\s]+(@)", r"\1***\2", text)


# ------------------------------------------------------------- Versionslogik
def next_versions(tag: str) -> dict[str, str]:
    """Vorschlaege fuer die naechste Version, ausgehend vom hoechsten Tag."""
    parsed = updater.parse_version(tag or "")
    major, minor, patch = parsed if parsed else (0, 0, 0)
    return {
        "patch": f"v{major}.{minor}.{patch + 1}",
        "minor": f"v{major}.{minor + 1}.0",
        "major": f"v{major + 1}.0.0",
    }


def normalize_tag(tag: str) -> str:
    """'1.2.0' oder ' V1.2.0 ' -> 'v1.2.0'. Ungueltiges -> leerer String.

    Das fuehrende 'v' ist nicht Kosmetik: der Updater sucht mit
    `git tag -l 'v*'`. Ein Tag ohne 'v' wuerde bei keiner Instanz jemals als
    Update auftauchen - der Fehler faellt erst Wochen spaeter auf.
    """
    raw = re.sub(r"^V", "v", (tag or "").strip())    # auch grosses V annehmen
    parsed = updater.parse_version(raw)
    return f"v{parsed[0]}.{parsed[1]}.{parsed[2]}" if parsed else ""


def latest_tag() -> str:
    ok, out = _git("tag", "-l", "v*", "--sort=-v:refname")
    if not ok or not out:
        return ""
    for line in out.splitlines():
        if updater.parse_version(line.strip()):
            return line.strip()
    return ""


# ----------------------------------------------------------------- Zustand
def status() -> dict[str, Any]:
    """Alles, was die Seite anzeigen muss."""
    st: dict[str, Any] = {"repo": REPO_DIR, "error": None}
    if not updater.is_git_checkout():
        st["error"] = ("Hier liegt kein Git-Repository. Einmalig auf dem Server "
                       "einrichten:  bash deploy/git-setup.sh")
        return st

    ok, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    st["branch"] = branch if ok else "?"
    ok, remote = _git("remote", "get-url", "origin")
    st["remote"] = _scrub(remote) if ok else ""

    # Geaenderte und neue Dateien (Kurzformat: XY <pfad>)
    ok, out = _git("status", "--porcelain=v1", "--untracked-files=all")
    changes = []
    if ok:
        for line in out.splitlines():
            if len(line) < 4:
                continue
            # Format ist "XY<leer>Pfad", aber X oder Y koennen selbst ein
            # Leerzeichen sein (" M pfad" vs "M  pfad"). Feste Indizes gehen
            # hier schief - deshalb ab Position 2 den Rest links trimmen.
            code, path = line[:2].strip(), line[2:].lstrip()
            if " -> " in path:                      # Umbenennung
                path = path.split(" -> ", 1)[1]
            path = path.strip().strip('"')
            if _FORBIDDEN_PATHS.match(path):
                continue                            # ignorierte Pfade nicht anzeigen
            kind = {"A": "neu", "??": "neu", "M": "geändert", "D": "gelöscht",
                    "R": "umbenannt"}.get(code, "geändert")
            changes.append({"path": path, "kind": kind})
    st["changes"] = sorted(changes, key=lambda c: c["path"])
    st["change_count"] = len(changes)

    tag = latest_tag()
    st["latest_tag"] = tag
    st["suggestions"] = next_versions(tag)
    ok, desc = _git("describe", "--tags", "--always", "--dirty")
    st["described"] = desc if ok else ""
    ok, last = _git("log", "-1", "--pretty=%s (%cr)")
    st["last_commit"] = last if ok else ""

    # Vorne/hinten gegenueber dem Remote. Achtung: das rechnet gegen den ZULETZT
    # geholten Stand von origin. Ohne fetch merkt man nicht, dass jemand (oder
    # man selbst ueber die GitHub-Weboberflaeche) dort etwas geaendert hat.
    ok, counts = _git("rev-list", "--left-right", "--count",
                      f"origin/{st['branch']}...HEAD")
    behind = ahead = 0
    if ok:
        parts = counts.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            behind, ahead = int(parts[0]), int(parts[1])
    st["unpushed"] = ahead
    st["behind"] = behind

    st["token_set"] = bool(str(db.get_setting("github_token", "") or "").strip())
    return st


# ------------------------------------------------------- Sicherheitspruefung
def guard() -> list[str]:
    """Blockierende Befunde. Leere Liste = Veroeffentlichen erlaubt."""
    problems: list[str] = []

    gitignore = os.path.join(REPO_DIR, ".gitignore")
    try:
        with open(gitignore, encoding="utf-8") as fh:
            lines = {ln.strip() for ln in fh}
    except OSError:
        return ["Keine .gitignore vorhanden – Veröffentlichen wäre zu riskant."]
    for needed in (".env", "data/"):
        if needed not in lines:
            problems.append(f".gitignore enthält '{needed}' nicht.")

    # Was wuerde tatsaechlich committet? (nach dem Vormerken pruefen)
    ok, out = _git("status", "--porcelain=v1", "--untracked-files=all")
    if ok:
        for line in out.splitlines():
            if len(line) < 4:
                continue
            path = line[2:].lstrip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            path = path.strip().strip('"')
            if _FORBIDDEN_PATHS.match(path) and not _is_ignored(path):
                problems.append(f"'{path}' würde hochgeladen werden.")

    # Inhalte auf echte Schluessel pruefen
    ok, files = _git("ls-files", "--cached", "--others", "--exclude-standard")
    if ok:
        for rel in files.splitlines():
            rel = rel.strip()
            if not rel or _FORBIDDEN_PATHS.match(rel):
                continue
            full = os.path.join(REPO_DIR, rel)
            try:
                if os.path.getsize(full) > 2_000_000:
                    continue
                with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                    if _SECRET_PATTERNS.search(fh.read()):
                        problems.append(f"'{rel}' enthält offenbar einen echten Schlüssel.")
            except OSError:
                continue
    return problems


def _is_ignored(path: str) -> bool:
    ok, _ = _git("check-ignore", "-q", path)
    return ok


# --------------------------------------------------------------- Askpass
def _askpass_env() -> tuple[dict[str, str], Optional[str]]:
    """Baut Umgebung samt temporaerem Askpass-Skript fuer den Push.

    Der Token steht dadurch nur in der Umgebung des Kindprozesses - nicht in
    argv (waere via `ps` lesbar) und nicht in .git/config.
    """
    token = str(db.get_setting("github_token", "") or "").strip()
    if not token:
        return {}, None
    user = str(db.get_setting("git_user_name", "") or "git").strip() or "git"
    fd, path = tempfile.mkstemp(prefix=".askpass-", suffix=".sh", dir=REPO_DIR)
    with os.fdopen(fd, "w") as fh:
        fh.write('#!/bin/sh\ncase "$1" in\n'
                 '  *[Uu]sername*) printf "%s\\n" "$GIT_ASKPASS_USER" ;;\n'
                 '  *) printf "%s\\n" "$GIT_ASKPASS_TOKEN" ;;\n'
                 'esac\n')
    os.chmod(path, stat.S_IRWXU)          # nur der Dienst-Benutzer
    return {"GIT_ASKPASS": path, "GIT_ASKPASS_USER": user,
            "GIT_ASKPASS_TOKEN": token}, path


def _set_tag(tag: str, log: list, result: dict) -> bool:
    """Setzt das Versions-Tag auf HEAD. Rueckgabe: weitermachen?

    Sonderfall: Das Tag existiert bereits, zeigt aber auf einen Commit, der
    nicht mehr im Branch liegt. Das passiert nach einem Rebase - etwa wenn ein
    frueherer Upload scheiterte, Commit und Tag aber schon erzeugt waren. Ein
    solches Tag ist ein Ueberbleibsel und wird auf HEAD nachgezogen; ein Tag,
    das bereits auf GitHub liegt, bleibt unangetastet.
    """
    exists, _ = _git("rev-parse", "--verify", f"refs/tags/{tag}")
    if exists:
        reachable, _ = _git("merge-base", "--is-ancestor", f"{tag}^{{commit}}", "HEAD")
        if reachable:
            log.append(f"Tag {tag} existierte bereits – übersprungen")
            return True
        on_remote, _ = _git("ls-remote", "--exit-code", "--tags", "origin", f"refs/tags/{tag}")
        if on_remote:
            result["error"] = (
                f"Version {tag} liegt bereits auf GitHub, zeigt hier aber auf einen "
                f"anderen Stand. Bitte eine neue Versionsnummer wählen.")
            return False
        _git("tag", "-d", tag)
        log.append(f"Verwaistes Tag {tag} aus einem früheren Versuch neu gesetzt")

    ok, out = _git("tag", "-a", tag, "-m", tag)
    if not ok:
        result["error"] = f"Tag fehlgeschlagen: {_scrub(out)[:300]}"
        return False
    log.append(f"Version {tag} markiert")
    return True


# ------------------------------------------------------------ Veroeffentlichen
def publish(message: str, tag: str = "", do_push: bool = True) -> dict[str, Any]:
    """Committet den aktuellen Stand, setzt optional ein Tag und laedt hoch."""
    log: list[str] = []
    result = {"ok": False, "log": log}

    if not updater.is_git_checkout():
        result["error"] = "Kein Git-Repository."
        return result

    problems = guard()
    if problems:
        result["error"] = "Sicherheitsprüfung fehlgeschlagen – es wurde nichts hochgeladen."
        result["problems"] = problems
        return result

    if tag:
        tag = normalize_tag(tag)
        if not tag:
            result["error"] = ("Ungültige Version. Erwartet wird vX.Y.Z, z.B. v1.2.0.")
            return result

    # Identitaet nur setzen, wenn hinterlegt und noch nicht konfiguriert
    for key, cfg in (("git_user_name", "user.name"), ("git_user_email", "user.email")):
        val = str(db.get_setting(key, "") or "").strip()
        if val:
            _git("config", cfg, val)

    ok, out = _git("add", "-A")
    if not ok:
        result["error"] = f"Vormerken fehlgeschlagen: {_scrub(out)[:300]}"
        return result

    ok, staged = _git("diff", "--cached", "--name-only")
    if staged.strip():
        ok, out = _git("commit", "-m", message or "Aktueller Stand")
        if not ok:
            result["error"] = f"Commit fehlgeschlagen: {_scrub(out)[:300]}"
            return result
        log.append(f"Commit erstellt ({len(staged.splitlines())} Dateien)")
    else:
        log.append("Keine Änderungen – kein neuer Commit nötig")

    if not do_push:
        if tag and not _set_tag(tag, log, result):
            return result
        result["ok"] = True
        log.append("Nur lokal festgehalten – nicht hochgeladen")
        return result

    remote = str(db.get_setting("git_remote_url", "") or "").strip()
    if remote:
        ok, cur = _git("remote", "get-url", "origin")
        if not ok:
            _git("remote", "add", "origin", remote)
        elif cur.strip() != remote:
            _git("remote", "set-url", "origin", remote)

    branch = str(db.get_setting("git_branch", "main") or "main").strip()
    env_extra, askpass_path = _askpass_env()
    try:
        # Erst holen und ggf. aufsetzen, DANN taggen. Liegen auf GitHub Commits,
        # die hier fehlen (z.B. eine Änderung über die Weboberfläche), scheitert
        # der Push sonst mit "non-fast-forward" - für den Nutzer unverständlich.
        # Die Reihenfolge ist wichtig: ein vor dem Rebase gesetztes Tag würde auf
        # einem Commit hängenbleiben, der danach nicht mehr im Branch liegt.
        ok, out = _git("fetch", "origin", branch, timeout=120, env_extra=env_extra)
        if ok:
            ok2, counts = _git("rev-list", "--left-right", "--count",
                               f"origin/{branch}...HEAD")
            parts = counts.split() if ok2 else []
            behind = int(parts[0]) if len(parts) == 2 and parts[0].isdigit() else 0
            if behind:
                ok3, rout = _git("rebase", f"origin/{branch}", timeout=120)
                if not ok3:
                    _git("rebase", "--abort")
                    result["error"] = (
                        f"Auf GitHub liegen {behind} Änderung(en), die sich nicht "
                        f"automatisch mit deinen zusammenführen lassen. Auf dem Server: "
                        f"git pull --rebase origin {branch} – Konflikte lösen – dann erneut. "
                        f"Details: {_scrub(rout)[:200]}")
                    return result
                log.append(f"{behind} Commit(s) von GitHub geholt und aufgesetzt")

        if tag and not _set_tag(tag, log, result):
            return result

        ok, out = _git("push", "-u", "origin", f"HEAD:{branch}",
                       timeout=180, env_extra=env_extra)
        if not ok:
            result["error"] = _scrub(out)[:600]
            return result
        log.append(f"Nach origin/{branch} hochgeladen")
        if tag:
            ok, out = _git("push", "origin", tag, timeout=120, env_extra=env_extra)
            log.append(f"Tag {tag} hochgeladen" if ok
                       else f"Tag konnte nicht hochgeladen werden: {_scrub(out)[:200]}")
    finally:
        if askpass_path:
            try:
                os.unlink(askpass_path)
            except OSError:
                pass

    result["ok"] = True
    db.log("info", "upload", f"Stand veröffentlicht{f' als {tag}' if tag else ''}",
           " · ".join(log))
    return result
