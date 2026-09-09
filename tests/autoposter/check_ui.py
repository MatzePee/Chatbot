#!/usr/bin/env python3
"""Statische Prüfungen der build-freien Oberfläche.

Ohne Bundler gibt es keinen Übersetzungsschritt, der Fehler aufdeckt. Diese
Prüfungen ersetzen ihn für die Fehlerklassen, die hier tatsächlich aufgetreten
sind. Aufruf: python3 backend/tests/check_ui.py
"""
from __future__ import annotations

import pathlib
import re
import sys

UI = pathlib.Path(__file__).resolve().parents[1] / "ui"
API_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "api"

problems: list[str] = []


def check_duplicate_api_keys() -> None:
    """Zwei Einträge gleichen Namens im api-Objekt.

    In JavaScript gewinnt der letzte – der erste ist unerreichbar, ohne dass
    irgendetwas warnt. Aufgetreten bei 'publish': Der System-Endpunkt wurde
    still vom Post-Endpunkt überdeckt, und der Aufruf landete auf
    /posts/[object Object]/publish.
    """
    source = (UI / "js" / "api.js").read_text(encoding="utf-8")
    body = source[source.index("export const api = {"):]
    seen: dict[str, int] = {}
    for match in re.finditer(r"^\s{2}(\w+)\s*:", body, re.M):
        seen[match.group(1)] = seen.get(match.group(1), 0) + 1
    for name, count in seen.items():
        if count > 1:
            problems.append(f"api.js: '{name}' ist {count}× vergeben – der erste Eintrag ist tot")


def check_imports_resolve() -> None:
    """Importierte Namen müssen im Zielmodul auch exportiert sein."""
    exports: dict[str, set[str]] = {}
    for path in UI.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        names = set(re.findall(r"^export\s+(?:async\s+)?(?:function|class)\s+(\w+)", text, re.M))
        names |= set(re.findall(r"^export\s+(?:const|let|var)\s+(\w+)", text, re.M))
        exports[str(path.relative_to(UI))] = names

    for path in UI.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"import\s*\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"]", text):
            target = (path.parent / match.group(2)).resolve()
            try:
                rel = str(target.relative_to(UI.resolve()))
            except ValueError:
                problems.append(f"{path.name}: Import zeigt aus dem ui-Verzeichnis heraus")
                continue
            if rel not in exports:
                problems.append(f"{path.name}: Modul {match.group(2)} gibt es nicht")
                continue
            for name in (n.strip().split(" as ")[0].strip() for n in match.group(1).split(",")):
                if name and name not in exports[rel]:
                    problems.append(f"{path.name}: '{name}' wird von {rel} nicht exportiert")


def check_api_calls_hit_routes() -> None:
    """Jeder api.*-Aufruf muss eine Route im Backend treffen."""
    routes: set[tuple[str, str]] = set()
    for path in API_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        prefixes: dict[str, str] = {}
        for match in re.finditer(r"^(\w+)\s*=\s*APIRouter\((.*?)\)\s*$", text, re.M | re.S):
            found = re.search(r"prefix=[\"']([^\"']+)[\"']", match.group(2))
            prefixes[match.group(1)] = found.group(1) if found else ""
        for match in re.finditer(
            r"@(\w+)\.(get|post|patch|delete|put)\(\s*[\"']([^\"']*)[\"']", text
        ):
            if match.group(1) in prefixes:
                routes.add((match.group(2).upper(), prefixes[match.group(1)] + match.group(3)))

    def norm(path: str) -> str:
        return re.sub(r"\{[^}]+\}", "{}", path.rstrip("/")) or "/"

    known = {(method, norm(path)) for method, path in routes}
    source = (UI / "js" / "api.js").read_text(encoding="utf-8")
    body = source[source.index("export const api = {"):]

    for name, expr in re.findall(r"^\s{2}(\w+)\s*:\s*(.+)$", body, re.M):
        call = re.search(r"\b(get|post|patch|del)\(\s*(.+)$", expr)
        if not call:
            continue
        literal = re.match(r"[`'\"]([^`'\"]*)[`'\"]((?:\s*\+\s*[\w.]+)*)", call.group(2))
        if not literal:
            continue
        path = literal.group(1) + ("{}" if literal.group(2) else "")
        path = re.sub(r"\$\{[^}]*\}", "{}", path).split("?")[0]
        method = {"del": "DELETE"}.get(call.group(1), call.group(1).upper())
        if (method, norm(path)) not in known and "+ qs" not in expr:
            problems.append(f"api.{name}: {method} {norm(path)} gibt es im Backend nicht")


def check_pages_registered() -> None:
    """Jede Seite in js/pages muss in app.js eingehängt sein."""
    app = (UI / "js" / "autoposter.js").read_text(encoding="utf-8")
    for page in (UI / "js" / "pages").glob("*.js"):
        if page.stem == "login":
            continue  # wird nur bei aktivierter Anmeldung nachgeladen
        if f"pages/{page.name}" not in app:
            problems.append(f"pages/{page.name} ist in app.js nicht eingehängt")


#: Klassen, die es absichtlich nur im JavaScript gibt – reine Ansatzpunkte für
#: querySelector, gestaltet wird über den Elternselektor (z. B. '.nav a').
JS_ONLY_CLASSES = {"navlink"}


def check_css_classes() -> None:
    """Im JavaScript verwendete Klassen sollten es in der CSS-Datei geben."""
    css = (UI / "autoposter.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"\.([a-z][\w-]*)", css))
    used: set[str] = set()
    for path in UI.rglob("*.js"):
        for match in re.finditer(r"class:\s*['\"]([^'\"]+)['\"]", path.read_text(encoding="utf-8")):
            for part in match.group(1).split():
                # 'postchip st-' ist der Anfang einer Verkettung ('st-' + status),
                # keine vollständige Klasse.
                if part.endswith("-") or part.startswith("$"):
                    continue
                used.add(part)
    unknown = sorted(used - defined - JS_ONLY_CLASSES)
    if unknown:
        problems.append("Ohne CSS-Regel: " + ", ".join(unknown))


if __name__ == "__main__":
    check_duplicate_api_keys()
    check_imports_resolve()
    check_api_calls_hit_routes()
    check_pages_registered()
    check_css_classes()

    if problems:
        print("Oberflächen-Prüfung: %d Befund(e)\n" % len(problems))
        for problem in problems:
            print("  ✗", problem)
        sys.exit(1)
    print("Oberflächen-Prüfung: alles in Ordnung")
