"""Nach GitHub veröffentlichen.

Reihenfolge in `publish()` ist tragend:

    guard → add → commit → fetch → rebase → TAG → push branch → push tag

Ein vor dem Rebase gesetztes Tag hängt danach an einem Commit, der nicht mehr
im Branch liegt. Es wird zwar gepusht, zeigt aber auf toten Code – und der
Fehler fällt erst auf, wenn jemand dieses Release installiert.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.services import appconfig, gitops

logger = logging.getLogger("autoposter.gitpublish")

#: Was niemals ins Repository darf. Anker beachten: '\.env' ohne Anker träfe
#: auch '.env.example' – und die gehört hinein.
FORBIDDEN_PATHS = re.compile(
    r"^(\.env$|\.env\.local$|data/|\.venv/|_entfernt_.*\.tar\.gz$|\.?askpass-|autoposter-askpass-)"
)

#: Eng gefasst, damit Platzhalter in .env.example keinen Fehlalarm auslösen.
#: Ein Riegel, der ständig grundlos blockiert, wird abgeschaltet – und schützt
#: dann gar nicht mehr.
SECRET_PATTERNS = re.compile(
    r"sk-or-v1-[A-Za-z0-9]{24}"           # OpenRouter
    r"|sk-[A-Za-z0-9]{32,}"               # OpenAI und ähnliche
    r"|ghp_[A-Za-z0-9]{30,}"              # GitHub-Token, klassisch
    r"|github_pat_[A-Za-z0-9_]{30,}"      # GitHub-Token, fein granular
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)

#: Dateien, die man nicht auf Geheimnisse absucht (Binäres, Beispiele).
_SKIP_SCAN = re.compile(r"\.(png|jpe?g|gif|webp|ico|woff2?|ttf|zip|gz|db|sqlite3?)$", re.I)
_MAX_SCAN_BYTES = 512_000


# --------------------------------------------------------------------------- #
# Zustand
# --------------------------------------------------------------------------- #
async def _settings(db: AsyncSession) -> Dict[str, str]:
    values = await appconfig.get_all(db)
    return {
        "remote": str(values.get("git_remote_url") or ""),
        "branch": str(values.get("git_branch") or "main"),
        "user": str(values.get("git_user_name") or ""),
        "email": str(values.get("git_user_email") or ""),
        "token": str(values.get("github_token") or ""),
    }


def changed_files() -> List[Dict[str, str]]:
    """Geänderte Dateien aus `git status --porcelain`.

    Das Format ist `XY<leer>Pfad`, aber X *oder* Y können selbst ein Leerzeichen
    sein (" M pfad" gegenüber "M  pfad"). Ein festes line[3:] liefert dann
    'pp/main.py' statt 'app/main.py'.
    """
    result = gitops.run("status", "--porcelain", timeout=30)
    if not result.ok:
        return []
    out: List[Dict[str, str]] = []
    for line in result.out.splitlines():
        if not line.strip():
            continue
        code, path = line[:2].strip(), line[2:].lstrip()
        if " -> " in path:  # Umbenennung
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        out.append({"code": code or "?", "path": path})
    return out


def ahead_behind(branch: str) -> Dict[str, int]:
    """Wie weit auseinander? Rechnet gegen den zuletzt geholten Stand.

    Ohne vorheriges `fetch` sagt das nichts über den echten Zustand auf GitHub
    aus – beim Push wird deshalb immer frisch geholt.
    """
    result = gitops.run(
        "rev-list", "--left-right", "--count", f"origin/{branch}...HEAD", timeout=20
    )
    if not result.ok:
        return {"behind": 0, "ahead": 0, "known": False}
    parts = result.out.split()
    if len(parts) != 2:
        return {"behind": 0, "ahead": 0, "known": False}
    return {"behind": int(parts[0]), "ahead": int(parts[1]), "known": True}


def next_versions(current: str) -> Dict[str, str]:
    parsed = gitops.parse_version(current) or (0, 0, 0)
    major, minor, patch = parsed
    return {
        "patch": f"v{major}.{minor}.{patch + 1}",
        "minor": f"v{major}.{minor + 1}.0",
        "major": f"v{major + 1}.0.0",
    }


async def status(db: AsyncSession) -> Dict[str, Any]:
    conf = await _settings(db)
    if not gitops.is_git_checkout():
        return {
            "is_git": False,
            "error": "Dieses Verzeichnis ist kein Git-Repository. "
                     "Einrichten mit './run.sh git init' auf dem Server.",
            "current": gitops.current_version(),
            "files": [], "issues": [], "suggestions": {}, "settings": _public(conf),
        }

    branch = conf["branch"] or gitops.current_branch()
    files = changed_files()
    current = gitops.current_version()
    tags = gitops.run("tag", "-l", "v*", timeout=20)
    known = gitops.sort_tags(tags.out.splitlines()) if tags.ok else []

    return {
        "is_git": True,
        "error": "",
        "current": current,
        "latest_tag": known[-1] if known else "",
        "branch": branch,
        "files": files,
        "sync": ahead_behind(branch),
        "issues": guard(files),
        "suggestions": next_versions(known[-1] if known else current),
        "settings": _public(conf),
    }


def _public(conf: Dict[str, str]) -> Dict[str, Any]:
    return {
        "git_remote_url": conf["remote"],
        "git_branch": conf["branch"],
        "git_user_name": conf["user"],
        "git_user_email": conf["email"],
        "github_token_set": bool(conf["token"]),
    }


# --------------------------------------------------------------------------- #
# Schutzriegel – blockiert, warnt nicht
# --------------------------------------------------------------------------- #
def guard(files: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, str]]:
    """Drei Prüfungen. Ein einmal veröffentlichter Schlüssel lässt sich nicht
    zurückholen – deshalb ist das kein Hinweis, sondern ein Abbruch."""
    issues: List[Dict[str, str]] = []
    files = changed_files() if files is None else files

    # 1. .gitignore muss .env und data/ enthalten
    ignore = gitops.REPO_DIR / ".gitignore"
    if not ignore.exists():
        issues.append({
            "level": "error", "code": "no_gitignore",
            "message": "Es gibt keine .gitignore. Ohne sie landen .env und data/ auf GitHub.",
        })
    else:
        content = ignore.read_text(encoding="utf-8", errors="ignore")
        for needed in (".env", "data/"):
            if not re.search(rf"^{re.escape(needed)}\s*$", content, re.M):
                issues.append({
                    "level": "error", "code": "gitignore_incomplete",
                    "message": f"'{needed}' fehlt in der .gitignore.",
                })

    # 2. Kein verbotener Pfad unter den Änderungen.
    #    Eine LÖSCHUNG ist ausgenommen: Wurde so eine Datei versehentlich
    #    eingecheckt, muss man sie auch wieder herausbekommen. Ein Riegel, der
    #    das Aufräumen verhindert, wäre eine Sackgasse.
    for entry in files:
        if "D" in entry["code"]:
            continue
        if FORBIDDEN_PATHS.match(entry["path"]):
            issues.append({
                "level": "error", "code": "forbidden_path",
                "message": f"{entry['path']} darf nicht ins Repository.",
            })

    # 3. Kein echter Schlüssel im Inhalt
    for entry in files:
        path = gitops.REPO_DIR / entry["path"]
        if not path.is_file() or _SKIP_SCAN.search(entry["path"]):
            continue
        try:
            if path.stat().st_size > _MAX_SCAN_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = SECRET_PATTERNS.search(text)
        if match:
            issues.append({
                "level": "error", "code": "secret_found",
                "message": f"{entry['path']} enthält offenbar einen echten Schlüssel "
                           f"({match.group(0)[:12]}…).",
            })
    return issues


# --------------------------------------------------------------------------- #
# Tag setzen
# --------------------------------------------------------------------------- #
def _set_tag(tag: str, branch: str, env: Dict[str, str], token: str) -> Optional[str]:
    """Verwaistes Tag aus einem Fehlversuch sauber behandeln.

    Rückgabe: Fehlermeldung oder None.
    """
    exists = gitops.run("rev-parse", "-q", "--verify", f"refs/tags/{tag}", timeout=15)
    if exists.ok:
        # Liegt das Tag schon auf GitHub? Dann darf es nicht verschoben werden –
        # andere Instanzen haben es womöglich bereits geholt.
        remote = gitops.run("ls-remote", "--tags", "origin", tag, env=env, timeout=45, secret=token)
        if remote.ok and tag in remote.out:
            return (f"Version {tag} liegt bereits auf GitHub. "
                    "Bitte eine neue Nummer wählen – ein veröffentlichtes Tag "
                    "zu verschieben bringt andere Installationen durcheinander.")

        contained = gitops.run("branch", "--contains", tag, timeout=15)
        if contained.ok and branch in contained.out:
            return None  # zeigt korrekt in den Branch, nichts zu tun
        gitops.run("tag", "-d", tag, timeout=15)  # lokales Überbleibsel

    created = gitops.run("tag", "-a", tag, "-m", f"Release {tag}", timeout=20)
    return None if created.ok else (created.err or f"Tag {tag} konnte nicht gesetzt werden")


# --------------------------------------------------------------------------- #
# Veröffentlichen
# --------------------------------------------------------------------------- #
async def publish(
    db: AsyncSession, *, message: str, tag: str = "", push: bool = True
) -> Dict[str, Any]:
    steps: List[str] = []

    if not gitops.is_git_checkout():
        return {"ok": False, "message": "Kein Git-Repository. './run.sh git init' ausführen.", "steps": steps}

    conf = await _settings(db)
    branch = conf["branch"] or gitops.current_branch()
    token = conf["token"]

    # 1. Schutzriegel
    issues = guard()
    blocking = [i for i in issues if i["level"] == "error"]
    if blocking:
        return {"ok": False, "message": "Abgebrochen: " + blocking[0]["message"],
                "issues": issues, "steps": steps}

    normalized = gitops.normalize_tag(tag) if tag else ""
    if tag and not normalized:
        return {"ok": False, "message": f"'{tag}' ist keine gültige Version. Erwartet: 1.2.3",
                "steps": steps}

    env, askpass = gitops.askpass_env(token, conf["user"] or "git")
    try:
        # 2. Autor
        if conf["user"]:
            gitops.run("config", "user.name", conf["user"], timeout=10)
        if conf["email"]:
            gitops.run("config", "user.email", conf["email"], timeout=10)

        # 3./4. Vormerken und committen – nur wenn wirklich etwas ansteht
        gitops.run("add", "-A", timeout=60)
        staged = gitops.run("diff", "--cached", "--name-only", timeout=30)
        if staged.out.strip():
            committed = gitops.run("commit", "-m", message or "Aktualisierung", timeout=60)
            if not committed.ok:
                return {"ok": False, "message": committed.err or "Commit fehlgeschlagen", "steps": steps}
            steps.append(f"Commit angelegt ({len(staged.out.splitlines())} Dateien)")
        else:
            steps.append("Nichts zu committen – der Stand war bereits gesichert")

        if not push:
            if normalized:
                error = _set_tag(normalized, branch, env, token)
                if error:
                    return {"ok": False, "message": error, "steps": steps}
                steps.append(f"Tag {normalized} lokal gesetzt")
            return {"ok": True, "message": "Lokal gesichert, nicht hochgeladen.", "steps": steps}

        # 5. Remote sicherstellen
        if conf["remote"]:
            current_remote = gitops.run("remote", "get-url", "origin", timeout=10)
            if not current_remote.ok:
                gitops.run("remote", "add", "origin", conf["remote"], timeout=10)
                steps.append("Remote 'origin' angelegt")
            elif current_remote.out.strip() != conf["remote"]:
                gitops.run("remote", "set-url", "origin", conf["remote"], timeout=10)
                steps.append("Remote 'origin' aktualisiert")

        # 6. Holen und aufsetzen. Ohne das scheitert jeder Push mit
        #    'non-fast-forward', sobald jemand über die GitHub-Oberfläche etwas
        #    geändert hat – für Nicht-Git-Kundige eine Sackgasse.
        fetched = gitops.run("fetch", "origin", branch, env=env, timeout=90, secret=token)
        if fetched.ok:
            sync = ahead_behind(branch)
            if sync["known"] and sync["behind"] > 0:
                rebased = gitops.run("rebase", f"origin/{branch}", env=env, timeout=120, secret=token)
                if not rebased.ok:
                    gitops.run("rebase", "--abort", timeout=30)
                    return {
                        "ok": False,
                        "message": "Auf GitHub liegen Änderungen, die sich nicht automatisch "
                                   "zusammenführen lassen. Auf dem Server ausführen: "
                                   f"git pull --rebase origin {branch}",
                        "steps": steps,
                    }
                steps.append(f"Auf origin/{branch} aufgesetzt ({sync['behind']} Commits geholt)")

        # 7. ERST JETZT taggen – nach dem Rebase
        if normalized:
            error = _set_tag(normalized, branch, env, token)
            if error:
                return {"ok": False, "message": error, "steps": steps}
            steps.append(f"Tag {normalized} gesetzt")

        # 8. Branch pushen
        pushed = gitops.run("push", "origin", f"HEAD:{branch}", env=env, timeout=180, secret=token)
        if not pushed.ok:
            hint = ""
            if "Authentication" in pushed.err or "403" in pushed.err or "could not read" in pushed.err.lower():
                hint = " Stimmt der Token? Er braucht das Recht 'repo'."
            return {"ok": False, "message": (pushed.err or "Push fehlgeschlagen") + hint, "steps": steps}
        steps.append(f"Nach origin/{branch} hochgeladen")

        # 9. Tag pushen – erst dadurch wird daraus ein Update für alle Instanzen
        if normalized:
            pushed_tag = gitops.run("push", "origin", normalized, env=env, timeout=120, secret=token)
            if not pushed_tag.ok:
                return {"ok": False,
                        "message": f"Der Code ist oben, aber Tag {normalized} nicht: "
                                   + (pushed_tag.err or ""),
                        "steps": steps}
            steps.append(f"Version {normalized} veröffentlicht")

        return {
            "ok": True,
            "message": (f"Version {normalized} veröffentlicht." if normalized
                        else "Änderungen hochgeladen (ohne neue Version)."),
            "steps": steps,
        }
    finally:
        gitops.drop_askpass(askpass)
