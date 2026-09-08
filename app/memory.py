"""Gespraechsgedaechtnis: Kurzzeit und Langzeit.

Der Bot schickt bewusst nicht den ganzen Chatverlauf ans Modell, sondern nur die
letzten `history_messages` Nachrichten. Alles davor war bisher verloren. Dieses
Modul haelt stattdessen einen kompakten, gepflegten Wissensstand je Fan vor.

ZWEI Schichten mit klar getrennter Aufgabe:

  long   LANGZEIT  - was den Fan ausmacht, in festen Kategorien:
                     Name, Alter, Herkunft, Beruf, Familie, Vorlieben, Grenzen,
                     Sonstiges. Aendert sich selten, verfaellt nie von selbst.

  short  KURZZEIT  - datierte Gespraechsnotizen der letzten Tage/Wochen.
                     Verfaellt doppelt: nach Anzahl UND nach Alter.
                     Eine Notiz kann als "offen" markiert sein - das ist ein
                     Gespraechsfaden, auf den man zurueckkommen kann und den die
                     Reaktivierung als Anknuepfungspunkt nutzt.

Warum feste Kategorien im Langzeitteil: laesst man das Modell seine Schlagworte
selbst erfinden, entstehen Faecher wie "Stockings" und "Colors" neben "Name" -
danach laesst sich weder pruefen noch zusammenfuehren, was es schon weiss. Mit
festen Faechern ist jede Zeile sofort einzuordnen und ein Widerspruch faellt auf.

Drei Dinge sind nicht verhandelbar:

1. Das handgepflegte Feld `chats.notes` gehoert dem Menschen. Die Automatik
   fasst es nie an. Ebenso bleiben Eintraege mit src="manual" unangetastet -
   sonst loescht das Modell nachts die Arbeit des Creators.
2. Der Block geht bei JEDER Antwort mit ins Modell. Deshalb eine harte
   Zeichenobergrenze (`memory_max_chars`), sonst waechst er unbemerkt und
   kostet mehr als der Verlauf, den er ersetzen soll.
3. Kurzzeit verfaellt, Langzeit nicht. Was dauerhaft zaehlt, muss das Modell
   beim Verdichten aus der Notiz in die Langzeit-Kategorie heben - danach darf
   die Notiz ruhig herausfallen.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from . import db, openrouter, persona_context

LAYERS = ("long", "short")

# Zuordnung Schicht -> Spalte in chat_memory
_COLS = {"long": "long_json", "short": "short_json"}

# Feste Faecher der Langzeitschicht. Schluessel gehen ins Modell, Label in die
# Oberflaeche. Reihenfolge = Reihenfolge im Prompt-Block.
# Reihenfolge = Reihenfolge im Prompt-Block. Bewusst so sortiert, dass oben
# steht, was eine Antwort sofort richtig oder falsch klingen laesst.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("name",          "Name"),
    ("anrede",        "Anrede & Kosenamen"),
    ("alter",         "Alter"),
    ("koerper",       "Körper & Aussehen"),
    ("herkunft",      "Herkunft"),
    ("beruf",         "Beruf"),
    ("familie",       "Familie"),
    ("vorlieben",     "Vorlieben"),
    ("grenzen",       "Grenzen"),
    ("kaufverhalten", "Kaufverhalten"),
    ("sonstiges",     "Sonstiges"),
)
CATEGORY_KEYS = tuple(k for k, _ in CATEGORIES)
CATEGORY_LABELS = dict(CATEGORIES)
_DEFAULT_CATEGORY = "sonstiges"


# ------------------------------------------------------------------ Hilfsmittel
def _loads(raw: Any) -> list[dict[str, Any]]:
    """JSON-Liste aus der DB lesen. Defekte Inhalte duerfen den Bot nie stoppen."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]


def _clean(value: Any, limit: int = 200) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text[:limit]


def _cat(value: Any) -> str:
    """Beliebige Modellausgabe auf ein bekanntes Fach zwingen."""
    key = str(value or "").strip().lower()
    return key if key in CATEGORY_KEYS else _DEFAULT_CATEGORY


# Masseinheiten und Fuss/Zoll-Schreibweisen. Alles, was so aussieht, ist eine
# Messung und kein Lebensalter.
_RE_MASS = re.compile(
    r"(\d\s*(?:cm|mm|m\b|kg|g\b|lbs?|pfund|ft\b|foot|feet|inch|zoll|\"|\u2033))"
    r"|(\d\s*[\'\u2032]\s*\d)",
    re.I)


def _fix_category(key: str, value: str) -> str:
    """Offensichtliche Fehleinsortierungen geradeziehen.

    Das Modell presst einen Fakt lieber in ein halbwegs passendes Fach, als
    "sonstiges" zu waehlen - der Prompt allein haelt das nicht zuverlaessig.
    Beobachtet: die Koerpergroesse "5'6.5\" (168.9 cm)" landete unter ALTER.
    Im Prompt-Block liest die Creatorin dann "Alter: 5'6.5\"", und der Bot
    glaubt es.

    Nur ein Fall wird hart korrigiert, und zwar der eindeutige: eine Zahl mit
    Masseinheit ist nie ein Alter, sondern gehoert zu "koerper". Alles andere
    bleibt beim Modell - eine Automatik, die zu viel umsortiert, macht es nur
    unberechenbar.
    """
    if key == "alter" and _RE_MASS.search(value or ""):
        return "koerper"
    return key


def _day(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts or time.time()).strftime("%Y-%m-%d")


def _fmt_day(raw: str) -> str:
    """'2026-08-14' -> '14.08.' (kurz, damit der Prompt-Block schlank bleibt)."""
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").strftime("%d.%m.")
    except (ValueError, TypeError):
        return str(raw or "")


def _day_epoch(raw: Any) -> float:
    """'2026-08-14' -> Unix-Zeit. 0.0, wenn unlesbar (gilt dann als uralt)."""
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").timestamp()
    except (ValueError, TypeError):
        return 0.0


def _msg_epoch(msg: dict[str, Any]) -> Optional[float]:
    """Zeitstempel einer Fanvue-Nachricht als Unix-Zeit.

    Fanvue liefert je nach Endpunkt ISO-Strings oder Epoch (Sek. oder Millisek.);
    welches Feld den Stempel traegt, weiss persona_context.
    """
    raw = persona_context.message_timestamp(msg)
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value / 1000.0 if value > 1e11 else value
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        value = float(text)
        return value / 1000.0 if value > 1e11 else value
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _limits() -> dict[str, int]:
    return {
        "long": int(db.get_setting("memory_max_long", 14) or 14),
        "short": int(db.get_setting("memory_max_short", 12) or 12),
    }


# ------------------------------------------------- Schalter: gilt er fuer DIESEN Fan?
def enabled_for(chat: Any) -> bool:
    """Ist das Gedaechtnis fuer diesen einen Fan aktiv?

    Dreistufig, damit sich das Gedaechtnis an einzelnen Fans testen laesst,
    bevor es fuer alle laeuft:

        memory_mode = 'on'   -> immer an,  auch wenn global aus
        memory_mode = 'off'  -> immer aus, auch wenn global an
        memory_mode = ''     -> folgt dem globalen Schalter (Standard)
    """
    mode = ""
    if chat is not None:
        try:
            mode = (chat["memory_mode"] or "").strip().lower()
        except (KeyError, IndexError, TypeError):
            mode = ""
    if mode == "on":
        return True
    if mode == "off":
        return False
    return bool(db.get_setting("memory_enabled", False))


def enabled_for_uuid(user_uuid: str) -> bool:
    return enabled_for(db.get_chat(user_uuid))


def set_mode(user_uuid: str, mode: str) -> str:
    """Schalter je Fan setzen. Unbekannte Werte fallen auf 'Standard' zurueck."""
    mode = (mode or "").strip().lower()
    if mode not in ("on", "off"):
        mode = ""
    db.update_chat(user_uuid, memory_mode=mode)
    db.add_memory_log(user_uuid, "mode", "",
                      {"on": "immer an", "off": "immer aus"}.get(mode, "folgt global"))
    return mode


# --------------------------------------------------------- Chatverlauf sichern
def store_history(user_uuid: str, messages_chrono: list, me_uuid: str) -> int:
    """Schreibt den gerade geholten Verlauf mit. Kostet keinen API-Aufruf, weil
    der Poller diese Nachrichten ohnehin schon in der Hand hat."""
    if not db.get_setting("messages_store_enabled", True):
        return 0
    rows: list[dict[str, Any]] = []
    for msg in messages_chrono or []:
        msg_uuid = msg.get("uuid")
        if not msg_uuid:
            continue
        sender = (msg.get("sender") or {}).get("uuid", "")
        rows.append({
            "user_uuid": user_uuid,
            "msg_uuid": msg_uuid,
            "direction": "out" if sender == me_uuid else "in",
            "text": (msg.get("text") or "").strip(),
            "has_media": bool(msg.get("hasMedia")),
            "msg_type": str(msg.get("type") or ""),
            "sent_at": _msg_epoch(msg),
        })
    try:
        return db.insert_messages(rows)
    except Exception as exc:  # noqa: BLE001 - Mitschreiben darf nie den Chat stoppen
        db.log("warn", "memory", "Verlauf konnte nicht gespeichert werden", str(exc))
        return 0


# ----------------------------------------------------------------- Lesen/Schreiben
def _upgrade_v1(row: Any) -> tuple[list[dict], list[dict]]:
    """Einmalige Umstellung der alten drei Schichten auf zwei.

    profile -> long (Fach 'sonstiges', ein Mensch sortiert das schneller nach
                     als jedes Modell es raten koennte)
    notes   -> short
    loops   -> short mit open=1

    Passiert beim Lesen, nicht per SQL: so ist die Umstellung fertig, sobald ein
    Fan das erste Mal angefasst wird, und ein Rueckbau auf v1.4 findet seine
    alten Spalten unveraendert vor.
    """
    long_out: list[dict[str, Any]] = []
    for e in _loads(row["profile_json"]):
        value = _clean(e.get("v"))
        if not value:
            continue
        long_out.append({"k": _DEFAULT_CATEGORY, "v": value,
                         "src": e.get("src", "auto"), "at": e.get("at") or time.time()})
    short_out: list[dict[str, Any]] = []
    for e in _loads(row["notes_json"]):
        value = _clean(e.get("v"))
        if not value:
            continue
        short_out.append({"d": _clean(e.get("d"), 10) or _day(), "v": value,
                          "open": 0, "due": "", "src": e.get("src", "auto")})
    for e in _loads(row["loops_json"]):
        value = _clean(e.get("v"))
        if not value or e.get("done"):
            continue
        short_out.append({"d": _day(e.get("at")), "v": value, "open": 1,
                          "due": _clean(e.get("due"), 10), "src": e.get("src", "auto")})
    short_out.sort(key=lambda e: e.get("d") or "")
    return long_out, short_out


def _from_row(row: Any) -> dict[str, Any]:
    if row is None:
        return {"long": [], "short": [], "last_run_at": None,
                "last_msg_id": 0, "dirty_count": 0}
    keys = row.keys()
    long_items = _loads(row["long_json"]) if "long_json" in keys else []
    short_items = _loads(row["short_json"]) if "short_json" in keys else []
    # Nichts in den neuen Spalten, aber etwas in den alten? Dann liegt hier ein
    # Fan aus der Zeit vor der Umstellung.
    if not long_items and not short_items and "profile_json" in keys:
        long_items, short_items = _upgrade_v1(row)
    return {
        "long": long_items,
        "short": short_items,
        "last_run_at": row["last_run_at"],
        "last_msg_id": int(row["last_msg_id"] or 0),
        "dirty_count": int(row["dirty_count"] or 0),
    }


def get(user_uuid: str) -> dict[str, Any]:
    return _from_row(db.get_memory_row(user_uuid))


def get_many(uuids: list[str]) -> dict[str, dict[str, Any]]:
    """Fuer Listenansichten: eine Abfrage statt einer pro Fan-Karte."""
    rows = db.memory_rows(uuids)
    return {u: _from_row(rows.get(u)) for u in uuids}


def is_empty(mem: dict[str, Any]) -> bool:
    return not (mem.get("long") or mem.get("short"))


def by_category(mem: dict[str, Any]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """Langzeitschicht fuer die Anzeige: (Schluessel, Label, Eintraege) je Fach.

    Der Index jedes Eintrags bleibt der Index in der Gesamtliste - die
    Loesch-Route arbeitet damit, unabhaengig von der Gruppierung.
    """
    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in CATEGORY_KEYS}
    for i, e in enumerate(mem.get("long") or []):
        item = dict(e)
        item["_i"] = i
        buckets[_cat(e.get("k"))].append(item)
    return [(k, CATEGORY_LABELS[k], buckets[k]) for k in CATEGORY_KEYS if buckets[k]]


def open_notes(mem: dict[str, Any]) -> list[dict[str, Any]]:
    """Offene Gespraechsfaeden, juengste zuerst."""
    offen = [n for n in (mem.get("short") or []) if n.get("open")]
    return sorted(offen, key=lambda e: e.get("d") or "", reverse=True)


# Alter Name, damit vorhandene Aufrufer (Reaktivierung) weiterlaufen.
def open_loops(mem: dict[str, Any]) -> list[dict[str, Any]]:
    return open_notes(mem)


def save(user_uuid: str, mem: dict[str, Any], model: str = "",
         reset_dirty: bool = False, last_msg_id: Optional[int] = None) -> None:
    fields: dict[str, Any] = {}
    for layer, col in _COLS.items():
        fields[col] = json.dumps(mem.get(layer) or [], ensure_ascii=False)
    if reset_dirty:
        fields["dirty_count"] = 0
        fields["last_run_at"] = time.time()
    if last_msg_id is not None:
        fields["last_msg_id"] = int(last_msg_id)
    db.upsert_memory(user_uuid, **fields)


def mark_dirty(user_uuid: str, n: int = 1) -> None:
    try:
        db.bump_memory_dirty(user_uuid, n)
    except Exception:  # noqa: BLE001 - Zaehler darf den Chat nie aufhalten
        pass


# ------------------------------------------------------- Handpflege ueber die GUI
def add_manual(user_uuid: str, layer: str, value: str, due: str = "",
               category: str = "", is_open: bool = False) -> bool:
    """Eintrag von Hand anlegen. src='manual' schuetzt ihn vor der Automatik."""
    layer = layer if layer in LAYERS else "long"
    value = _clean(value)
    if not value:
        return False
    mem = get(user_uuid)
    now = time.time()
    if layer == "short":
        mem["short"].append({"d": _day(now), "v": value, "src": "manual",
                             "open": 1 if (is_open or due) else 0,
                             "due": _clean(due, 10)})
    else:
        mem["long"].append({"k": _cat(category), "v": value, "at": now, "src": "manual"})
    save(user_uuid, mem)
    db.add_memory_log(user_uuid, "manual", layer, value)
    return True


def delete_entry(user_uuid: str, layer: str, index: int) -> bool:
    if layer not in LAYERS:
        return False
    mem = get(user_uuid)
    items = mem.get(layer) or []
    if not 0 <= index < len(items):
        return False
    removed = items.pop(index)
    save(user_uuid, mem)
    db.add_memory_log(user_uuid, "remove", layer, str(removed.get("v", "")))
    return True


def close_loop(user_uuid: str, index: int) -> bool:
    """Offenen Faden abhaken. Die Notiz bleibt stehen, nur der Merker faellt weg."""
    mem = get(user_uuid)
    items = mem.get("short") or []
    if not 0 <= index < len(items):
        return False
    items[index]["open"] = 0
    items[index]["closed_at"] = time.time()
    save(user_uuid, mem)
    db.add_memory_log(user_uuid, "close", "short", str(items[index].get("v", "")))
    return True


def promote(user_uuid: str, index: int, category: str = "") -> bool:
    """Eine Kurzzeit-Notiz von Hand in die Langzeitschicht heben.

    Der Knopf dafuer ist wichtig: das Modell uebersieht beim Verdichten mal
    etwas, und dann soll der Creator die Notiz retten koennen, bevor sie
    verfaellt.
    """
    mem = get(user_uuid)
    items = mem.get("short") or []
    if not 0 <= index < len(items):
        return False
    value = _clean(items[index].get("v"))
    if not value:
        return False
    mem["long"].append({"k": _cat(category), "v": value, "at": time.time(),
                        "src": "manual"})
    save(user_uuid, mem)
    db.add_memory_log(user_uuid, "promote", "long", value)
    return True


# ------------------------------------------------------------- Prompt-Darstellung
def render_block(user_uuid: str = "", mem: Optional[dict[str, Any]] = None,
                 max_chars: int = 0) -> str:
    """Baut den Text, der in den System-Prompt wandert.

    Reihenfolge ist zugleich Prioritaet beim Kuerzen: Langzeit zuerst, dann
    offene Faeden, dann die juengsten Notizen. Faellt die Grenze, fliegen Notizen
    als erstes raus - ein vergessener Smalltalk ist verschmerzbar, ein
    vergessener Beruf nicht.
    """
    if mem is None:
        mem = get(user_uuid)
    if max_chars <= 0:
        max_chars = int(db.get_setting("memory_max_chars", 1200) or 1200)

    parts: list[str] = []

    # --- Langzeit, nach Faechern gruppiert -------------------------------
    lines: list[str] = []
    for _key, label, items in by_category(mem):
        werte = [_clean(e.get("v")) for e in items if _clean(e.get("v"))]
        if werte:
            lines.append(f"- {label}: " + "; ".join(werte))
    if lines:
        parts.append("Was du ueber diesen Fan weisst:\n" + "\n".join(lines))

    # --- Offene Faeden ----------------------------------------------------
    offen = open_notes(mem)[:3]
    if offen:
        lines = []
        for n in offen:
            due = _fmt_day(n.get("due")) if n.get("due") else ""
            lines.append(f"- {_clean(n.get('v'))}" + (f" ({due})" if due else ""))
        parts.append("Offen - darauf kannst du zurueckkommen:\n" + "\n".join(lines))

    # --- Kurzzeit, juengste zuerst ---------------------------------------
    # Was oben schon als offener Faden steht, hier NICHT wiederholen: der Block
    # geht bei jeder Antwort mit, doppelte Zeilen kosten dauerhaft Platz und
    # lassen ein Thema wichtiger wirken, als es ist.
    schon = {id(n) for n in offen}
    notes = [n for n in (mem.get("short") or [])
             if _clean(n.get("v")) and id(n) not in schon][-5:]
    if notes:
        lines = [f"- {_fmt_day(n.get('d'))} {_clean(n.get('v'))}" for n in reversed(notes)]
        parts.append("Zuletzt besprochen:\n" + "\n".join(lines))

    # Von hinten kuerzen, solange die Grenze gerissen wird.
    while parts and len("\n\n".join(parts)) > max_chars:
        last = parts[-1].split("\n")
        if len(last) > 2:                 # noch Zeilen im letzten Abschnitt
            parts[-1] = "\n".join(last[:-1])
        else:
            parts.pop()
    return "\n\n".join(parts)


# ------------------------------------------------------------------ Kurzzeit-Verfall
def prune_short(mem: dict[str, Any], now: Optional[float] = None) -> int:
    """Laesst die Kurzzeitschicht altern. Gibt zurueck, wie viel entfallen ist.

    Zwei Grenzen, und es greift, was zuerst zieht:
      * ALTER  - aelter als `memory_short_max_age_days`
      * ANZAHL - mehr als `memory_max_short` Notizen

    Beides zusammen, weil eine Grenze allein je nach Fan versagt: bei einem
    Vielschreiber sind 12 Notizen nach drei Tagen voll, bei einem stillen Fan
    steht sonst monatealter Smalltalk als "zuletzt besprochen" im Prompt.

    Handeintraege und offene Faeden ueberleben beides - ein offener Faden ist
    genau das, was nach einer laengeren Pause noch etwas wert ist.
    """
    now = now or time.time()
    items = mem.get("short") or []
    if not items:
        return 0
    vorher = len(items)
    max_age_days = float(db.get_setting("memory_short_max_age_days", 21) or 0)
    if max_age_days > 0:
        grenze = now - max_age_days * 86400
        items = [e for e in items
                 if e.get("src") == "manual" or e.get("open")
                 or _day_epoch(e.get("d")) >= grenze]
    items.sort(key=lambda e: e.get("d") or "")
    max_n = _limits()["short"]
    if len(items) > max_n:
        geschuetzt = [e for e in items if e.get("src") == "manual" or e.get("open")]
        rest = [e for e in items if e not in geschuetzt]
        frei = max(0, max_n - len(geschuetzt))
        behalten = geschuetzt + (rest[-frei:] if frei else [])
        items = sorted(behalten, key=lambda e: e.get("d") or "")
    mem["short"] = items
    return vorher - len(items)


# ---------------------------------------------------------------- Verdichtung
def _due_reason(chat_row: Any, mem_row: Any, now: float) -> str:
    """Warum ist dieser Chat faellig - oder leer, wenn er es nicht ist."""
    dirty = int(mem_row["dirty_count"] or 0)
    if dirty <= 0:
        return ""
    trigger_n = int(db.get_setting("memory_trigger_messages", 20) or 20)
    if dirty >= trigger_n:
        return f"{dirty} neue Nachrichten"
    quiet_h = float(db.get_setting("memory_quiet_hours", 2) or 2)
    last_in = chat_row["last_inbound_at"] if chat_row is not None else None
    if last_in and (now - float(last_in)) >= quiet_h * 3600.0:
        return "Chat ruhig"
    sweep_hour = int(db.get_setting("memory_sweep_hour", 4) or 4)
    if datetime.fromtimestamp(now).hour == sweep_hour:
        return "naechtlicher Sweep"
    return ""


def consolidate(user_uuid: str, force: bool = False) -> tuple[bool, str]:
    """Verdichtet die neuen Nachrichten eines Fans ins Gedaechtnis.

    Gibt (Erfolg, Meldung) zurueck. Schlaegt irgendetwas fehl, bleibt das alte
    Gedaechtnis unveraendert stehen - lieber veraltet als kaputt.
    """
    mem = get(user_uuid)
    min_fan = int(db.get_setting("memory_min_fan_messages", 5) or 0)
    if not force and min_fan and db.fan_message_count(user_uuid) < min_fan:
        # Einmal-Schreiber: kostet einen LLM-Aufruf und liefert nur Datenmuell.
        # Der Zaehler wird trotzdem geleert, sonst laeuft der Chat ewig als faellig mit.
        db.upsert_memory(user_uuid, dirty_count=0, last_run_at=time.time())
        return (False, "zu wenige Fan-Nachrichten")

    new_rows = db.messages_since(user_uuid, mem["last_msg_id"], limit=60)
    new_rows = [r for r in new_rows if (r["text"] or "").strip()]
    if not new_rows:
        db.upsert_memory(user_uuid, dirty_count=0, last_run_at=time.time())
        return (False, "keine neuen Nachrichten")

    payload = [{"von": "fan" if r["direction"] == "in" else "ich",
                "zeit": _day(r["sent_at"] or r["seen_at"]),
                "text": (r["text"] or "")[:400]} for r in new_rows]

    result = openrouter.consolidate_memory(mem, payload, limits=_limits())
    if not result:
        return (False, "Modell lieferte kein verwertbares Ergebnis")

    model = db.get_setting("memory_model", "") or db.get_setting("openrouter_model", "")
    merged = _merge(mem, result)
    verfallen = prune_short(merged)
    save(user_uuid, merged, model=model, reset_dirty=True,
         last_msg_id=int(new_rows[-1]["id"]))

    added = (len(merged["long"]) - len(mem["long"])
             + len(merged["short"]) - len(mem["short"]))
    db.add_memory_log(user_uuid, "consolidate", "",
                      f"{len(new_rows)} Nachrichten verdichtet, {added:+d} Eintraege"
                      + (f", {verfallen} Notiz(en) verfallen" if verfallen else ""),
                      model)
    return (True, f"{len(new_rows)} Nachrichten verdichtet")


def _merge(old: dict[str, Any], new: dict[str, Any],
           stamp: Optional[float] = None) -> dict[str, Any]:
    """Fuehrt das Modellergebnis mit dem Bestand zusammen.

    Die Regel, an der alles haengt: was von Hand kam, ueberlebt IMMER. Das Modell
    darf ergaenzen und seine eigenen frueheren Eintraege ersetzen, aber niemals
    die Handarbeit des Creators anfassen.
    """
    out: dict[str, Any] = {}
    limits = _limits()
    jetzt = stamp or time.time()
    for layer in LAYERS:
        manual = [e for e in (old.get(layer) or []) if e.get("src") == "manual"]
        auto = [e for e in (new.get(layer) or []) if isinstance(e, dict)]
        cleaned: list[dict[str, Any]] = []
        seen: set[str] = set()
        manual_values = {_clean(e.get("v")).lower() for e in manual}
        for e in auto:
            value = _clean(e.get("v"))
            key = value.lower()
            if not value or key in seen or key in manual_values:
                continue
            seen.add(key)
            entry: dict[str, Any] = {"v": value, "src": "auto"}
            # `stamp` ist beim Nachtragen alter Verlaeufe wichtig: ein Eintrag
            # aus einem zwei Jahre alten Gespraech darf nicht so aussehen, als
            # waere er von heute.
            if layer == "short":
                entry["d"] = _clean(e.get("d"), 10) or _day(jetzt)
                entry["open"] = 1 if e.get("open") else 0
                entry["due"] = _clean(e.get("due"), 10)
            else:
                entry["k"] = _fix_category(_cat(e.get("k")), value)
                entry["at"] = e.get("at") or jetzt
            cleaned.append(entry)
        # Beim Kuerzen zuerst die Automatik opfern. Frueher wurde stumpf die
        # zusammengefuegte Liste hinten abgeschnitten - bei vielen Automatik-
        # Eintraegen fielen dadurch ausgerechnet die Handeintraege heraus, die
        # nie verloren gehen duerfen.
        frei = max(0, limits[layer] - len(manual))
        out[layer] = manual + (cleaned[-frei:] if frei else [])
    if out.get("short"):
        out["short"].sort(key=lambda e: e.get("d") or "")
    out["last_run_at"] = old.get("last_run_at")
    out["last_msg_id"] = old.get("last_msg_id", 0)
    return out


_last_purge_at = 0.0


def cycle() -> int:
    """Ein Verdichtungs-Durchlauf. Wird vom Poller-Loop aufgerufen.

    Bewusst schmal gehalten: `memory_max_per_cycle` Chats pro Durchlauf, dazu ein
    Mindestabstand je Fan. Der eigentliche Kostenhebel ist der Mindestabstand,
    nicht die Zahl pro Durchlauf - der Loop laeuft jede Minute.
    """
    global _last_purge_at
    if not db.get_setting("memory_consolidate_enabled", False):
        return 0
    now = time.time()

    # Aufraeumen einmal taeglich, nicht bei jedem Durchlauf
    if now - _last_purge_at > 86400:
        _last_purge_at = now
        try:
            removed = db.purge_old_messages(int(db.get_setting("messages_retention_days", 90) or 0))
            if removed:
                db.log("info", "memory", f"{removed} alte Rohnachrichten geloescht", "")
        except Exception as exc:  # noqa: BLE001
            db.log("warn", "memory", "Aufraeumen fehlgeschlagen", str(exc))

    min_interval = float(db.get_setting("memory_min_interval_hours", 6) or 6) * 3600.0
    limit = int(db.get_setting("memory_max_per_cycle", 3) or 3)
    try:
        rows = db.memory_candidates(now, min_interval, limit * 4)
    except Exception as exc:  # noqa: BLE001
        db.log("error", "memory", "Faellige Chats nicht ermittelbar", str(exc))
        return 0

    done = 0
    for row in rows:
        if done >= limit:
            break
        chat = db.get_chat(row["user_uuid"])
        if chat is None or not chat["bot_enabled"]:
            continue
        # Ein Fan auf 'off' wird nicht verdichtet. Ein Fan auf 'on' schon,
        # auch wenn das Gedaechtnis global aus ist - genau dafuer ist der
        # Schalter da.
        try:
            if (chat["memory_mode"] or "").strip().lower() == "off":
                continue
        except (KeyError, IndexError, TypeError):
            pass
        reason = _due_reason(chat, row, now)
        if not reason:
            continue
        try:
            ok, note = consolidate(row["user_uuid"])
        except Exception as exc:  # noqa: BLE001 - eine kaputte Verdichtung darf
            # den Loop nicht mitreissen; der naechste Durchlauf versucht es erneut
            db.log("error", "memory",
                   f"Verdichtung fuer {chat['handle'] or row['user_uuid']} fehlgeschlagen",
                   str(exc))
            continue
        if ok:
            done += 1
            db.log("info", "memory",
                   f"Gedaechtnis aktualisiert: {chat['handle'] or row['user_uuid']} "
                   f"({reason}, {note})", "")
    return done


# ============================================================ Ganzen Chat einlesen
# Ein gewachsener Chat kann mehrere tausend Nachrichten haben. Das sprengt beides:
# die Fanvue-API liefert nur seitenweise, und in einen LLM-Aufruf passt der
# Verlauf ohnehin nicht. Deshalb zwei getrennte Phasen, beide haeppchenweise:
#
#   1. HOLEN      Seite fuer Seite aus Fanvue in die lokale messages-Tabelle.
#                 Wiederholbar - schon bekannte Nachrichten fallen still weg.
#   2. VERDICHTEN Bloecke von `memory_backfill_chunk` Nachrichten, chronologisch
#                 von alt nach neu. Jeder Block bekommt den bis dahin
#                 gewachsenen Wissensstand mit und gibt ihn erweitert zurueck.
#
# Die Reihenfolge ist wichtig: alt -> neu. So kann ein spaeterer Block einen
# frueheren Fakt korrigieren ("bin umgezogen") und ein offenes Thema schliessen,
# das weiter vorne aufkam. Umgekehrt wuerde das Gedaechtnis mit veralteten
# Angaben enden.

_backfill_lock = threading.Lock()
_backfill_queue: list[dict[str, str]] = []
_backfill_thread: Optional[threading.Thread] = None
_backfill_state: dict[str, Any] = {
    "running": False, "user_uuid": "", "name": "", "phase": "", "fetched": 0,
    "pages": 0, "chunks_done": 0, "chunks_total": 0, "queued": 0, "queue_names": [],
    "error": None, "note": "", "cost": 0.0, "finished_at": 0.0,
    # Der letzte Fehlschlag bleibt sichtbar, auch wenn danach ein anderer Fan
    # erfolgreich durchlaeuft - sonst geht die Meldung lautlos verloren.
    "last_error": None, "last_error_name": "",
}


def backfill_status() -> dict[str, Any]:
    st = dict(_backfill_state)
    with _backfill_lock:
        st["queued"] = len(_backfill_queue)
        st["queue_names"] = [e.get("name") or e["user_uuid"][:8] for e in _backfill_queue]
    return st


def start_backfill(user_uuid: str, name: str = "") -> tuple[bool, str]:
    """Reiht einen Fan zum Einlesen ein und startet bei Bedarf den Arbeiter.

    Bewusst EIN Arbeiter fuer alle: mehrere gleichzeitige Durchlaeufe wuerden
    Fanvue und OpenRouter parallel bombardieren und sich gegenseitig ausbremsen.

    Gibt (gestartet, Klartext) zurueck. Der Klartext landet als Meldung auf der
    Seite - jede Absage muss also von sich aus erklaeren, woran es lag.
    """
    global _backfill_thread
    if not db.get_setting("messages_store_enabled", True):
        return (False, "Einstellung 'Chatverlauf lokal mitschreiben' ist ausgeschaltet")
    if not db.get_setting("openrouter_api_key", ""):
        return (False, "kein OpenRouter-API-Key hinterlegt")
    with _backfill_lock:
        if _backfill_state.get("user_uuid") == user_uuid and _backfill_state.get("running"):
            return (False, "läuft bereits für diesen Fan")
        if any(e["user_uuid"] == user_uuid for e in _backfill_queue):
            return (False, "steht bereits in der Warteschlange")
        _backfill_queue.append({"user_uuid": user_uuid, "name": name})
        position = len(_backfill_queue)
        frisch = _backfill_thread is None or not _backfill_thread.is_alive()
        if frisch:
            # Zustand sofort setzen, nicht erst im Thread: sonst zeigt die
            # Seite direkt nach dem Klick noch "nichts laeuft".
            _backfill_state.update(running=True, user_uuid=user_uuid,
                                   name=name or user_uuid[:8], phase="start",
                                   fetched=0, pages=0, chunks_done=0, chunks_total=0,
                                   error=None, note="", cost=0.0, finished_at=0.0)
            _backfill_thread = threading.Thread(target=_backfill_worker, daemon=True,
                                                name="memory-backfill")
            _backfill_thread.start()
    db.log("info", "memory", f"Chat einlesen angestoßen: {name or user_uuid[:8]}",
           "sofort" if position == 1 else f"Warteschlange Platz {position}")
    return (True, "läuft – Fortschritt oben auf der Seite" if position == 1
            else f"eingereiht (Platz {position})")


def _backfill_worker() -> None:
    while True:
        with _backfill_lock:
            if not _backfill_queue:
                return
            auftrag = _backfill_queue.pop(0)
        try:
            _backfill_one(auftrag["user_uuid"], auftrag.get("name", ""))
        except Exception as exc:  # noqa: BLE001 - ein kaputter Fan darf die
            # Warteschlange nicht anhalten
            _backfill_state.update(running=False, error=str(exc)[:300],
                                   last_error=str(exc)[:300],
                                   last_error_name=auftrag.get("name", ""),
                                   finished_at=time.time())
            db.log("error", "memory",
                   f"Einlesen fehlgeschlagen ({auftrag.get('name') or auftrag['user_uuid']})",
                   str(exc))


def _fetch_all_messages(user_uuid: str, me_uuid: str) -> int:
    """Phase 1: kompletten Verlauf seitenweise in die lokale Tabelle holen."""
    from . import fanvue
    max_pages = int(db.get_setting("memory_backfill_max_pages", 60) or 60)
    size = 100
    seite = 1
    gesamt = 0
    while seite <= max_pages:
        try:
            res = fanvue.list_messages(user_uuid, size=size, mark_as_read=False, page=seite)
        except fanvue.FanvueError as exc:
            # Nur bei abgelehnter Seitengroesse kleiner werden - ein 401 oder
            # 429 muss durchschlagen statt still halbiert zu werden.
            if size == 50 or getattr(exc, "status", 0) not in (400, 413, 422):
                raise
            size = 50
            continue
        daten = res.get("data", []) or []
        # API liefert neueste zuerst
        gesamt += store_history(user_uuid, list(reversed(daten)), me_uuid)
        _backfill_state.update(fetched=gesamt, pages=seite)
        if not (res.get("pagination") or {}).get("hasMore"):
            break
        seite += 1
        time.sleep(0.25)      # freundlich zur API
    return gesamt


def _backfill_one(user_uuid: str, name: str = "") -> None:
    from . import fanvue
    beginn = time.time()
    _backfill_state.update(running=True, user_uuid=user_uuid, name=name or user_uuid[:8],
                           phase="holen", fetched=0, pages=0, chunks_done=0,
                           chunks_total=0, error=None, note="", cost=0.0, finished_at=0.0)
    try:
        me_uuid = fanvue.account_uuid()
        neu = _fetch_all_messages(user_uuid, me_uuid)

        # --- Phase 2: verdichten --------------------------------------------
        _backfill_state["phase"] = "verdichten"
        chunk_n = max(20, int(db.get_setting("memory_backfill_chunk", 80) or 80))
        pause = float(db.get_setting("memory_backfill_delay", 1.0) or 0)
        # ECHTE Zeitreihenfolge, nicht id-Reihenfolge: beim Einlesen bekommen
        # die aeltesten Nachrichten die hoechsten ids (Fanvue liefert die
        # neueste Seite zuerst). Nach id sortiert liefe die Verdichtung
        # rueckwaerts durch die Geschichte und endete beim aeltesten Stand.
        rows = db.messages_chronological(user_uuid)
        rows = [r for r in rows if (r["text"] or "").strip()]
        if not rows:
            _backfill_state.update(running=False, note="keine Nachrichten mit Text",
                                   finished_at=time.time())
            return

        bloecke = [rows[i:i + chunk_n] for i in range(0, len(rows), chunk_n)]
        _backfill_state["chunks_total"] = len(bloecke)
        mem = get(user_uuid)
        limits = _limits()
        fehler = 0
        for i, block in enumerate(bloecke):
            payload = [{"von": "fan" if r["direction"] == "in" else "ich",
                        "zeit": _day(r["sent_at"] or r["seen_at"]),
                        "text": (r["text"] or "")[:400]} for r in block]
            ergebnis = openrouter.consolidate_memory(mem, payload, limits=limits)
            if ergebnis:
                # Zeitstempel des Blocks statt "jetzt": sonst sieht ein Eintrag
                # aus einem alten Gespraech taufrisch aus.
                stempel = block[-1]["sent_at"] or block[-1]["seen_at"] or time.time()
                mem = _merge(mem, ergebnis, stamp=stempel)
                save(user_uuid, mem)          # Zwischenstand sichern
            else:
                fehler += 1
            _backfill_state["chunks_done"] = i + 1
            if pause:
                time.sleep(pause)

        # --- Nachbereitung ---------------------------------------------------
        # Kurzzeitnotizen aus laengst vergangenen Gespraechen taugen nicht fuer
        # die Reaktivierung: "Wie war dein Umzug?" zu einem Umzug von vor zwei
        # Jahren wirkt nicht aufmerksam, sondern falsch. Was dauerhaft zaehlt,
        # steht nach dem Einlesen ohnehin in der Langzeitschicht.
        verfallen = prune_short(mem)

        # Fuer den laufenden Betrieb zaehlt die hoechste vergebene id: alles
        # danach ist wirklich neu. rows[-1] waere die chronologisch juengste
        # Nachricht - die kann eine kleine id haben (siehe oben).
        save(user_uuid, mem, reset_dirty=True, last_msg_id=db.last_message_id(user_uuid))

        kosten = db.api_cost_between(beginn, time.time() + 1, category="memory")
        note = (f"{len(rows)} Nachrichten in {len(bloecke)} Blöcken, "
                f"{neu} neu geholt"
                + (f", {fehler} Block/Blöcke ohne Ergebnis" if fehler else "")
                + (f", {verfallen} veraltete Notizen verworfen" if verfallen else ""))
        _backfill_state.update(running=False, phase="fertig", note=note,
                               cost=round(kosten, 4), finished_at=time.time())
        db.add_memory_log(user_uuid, "backfill", "", note,
                          db.get_setting("memory_model", ""))
        db.log("info", "memory", f"Chat eingelesen: {name or user_uuid[:8]} – {note}",
               f"Kosten: ${kosten:.4f}")
    finally:
        _backfill_state["running"] = False
