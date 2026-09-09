"""Zeit- und Situationskontext fuer die Persona.

Das Sprachmodell bekam bisher keinerlei Zeitinformation und musste die
Tageszeit raten -> "ich geh ins Bett" um 10 Uhr morgens.

Dieses Modul loest das in zwei Schritten:

1. Zeitkontext: aktueller Wochentag, Datum, Uhrzeit in der Zeitzone der
   Persona (nicht der Serverzeit!) plus eine Tageszeit-Bezeichnung.
2. Wochenplan: ein deterministisch ausgewerteter Tagesrhythmus. Der Code
   rechnet aus, was die Persona gerade tut, und schreibt es als Fakt in den
   Prompt. Das Modell muss also nicht selbst aus der Uhrzeit schliessen -
   genau da entstanden die Fehler.

Schedule-Format (eine Regel pro Zeile):

    Mo-Fr 08:00-09:00  aufstehen, Kaffee, Insta checken
    Mo-Fr 09:00-16:00  Uni, Vorlesungen und Lernen
    Sa    21:00-03:00  unterwegs, feiern mit den Maedels
    taeglich 01:00-08:00  schlaeft

Erlaubt sind: Einzeltage (Mo), Bereiche (Mo-Fr), Listen (Mo,Mi,Fr) und die
Schluesselwoerter "taeglich"/"jeden tag"/"immer". Zeiten mit oder ohne
Minuten ("9" == "09:00"). Fenster duerfen ueber Mitternacht laufen.
Bei mehreren passenden Regeln gewinnt die spezifischste (wenigste Tage,
dann kuerzestes Zeitfenster) - so schlaegt "Sa 21-03" das "taeglich 01-08".

Zeilen, die mit # beginnen, sind Kommentare.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple, Optional

from . import db

try:  # ab Python 3.9 in der Standardbibliothek
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - sehr alte Umgebungen
    ZoneInfo = None  # type: ignore[assignment]


# ------------------------------------------------------------------ Konstanten
WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
               "Freitag", "Samstag", "Sonntag"]

# Kuerzel -> Index (0 = Montag), bewusst grosszuegig
_DAY_ALIASES: dict[str, int] = {
    "mo": 0, "mon": 0, "montag": 0, "monday": 0,
    "di": 1, "die": 1, "dienstag": 1, "tue": 1, "tuesday": 1,
    "mi": 2, "mit": 2, "mittwoch": 2, "wed": 2, "wednesday": 2,
    "do": 3, "don": 3, "donnerstag": 3, "thu": 3, "thursday": 3,
    "fr": 4, "fre": 4, "freitag": 4, "fri": 4, "friday": 4,
    "sa": 5, "sam": 5, "samstag": 5, "sat": 5, "saturday": 5,
    "so": 6, "son": 6, "sonntag": 6, "sun": 6, "sunday": 6,
}

_ALL_DAYS_WORDS = {"taeglich", "täglich", "jeden", "jedentag", "jeden tag",
                   "immer", "alle", "daily", "everyday", "every day"}

# Tageszeit-Bezeichnungen: (Startstunde, Endstunde, Label)
_DAYPARTS = [
    (5, 8, "frueher Morgen"),
    (8, 11, "Vormittag"),
    (11, 14, "Mittag"),
    (14, 17, "Nachmittag"),
    (17, 21, "Abend"),
    (21, 24, "spaeter Abend"),
    (0, 5, "tiefe Nacht"),
]

_LINE_RE = re.compile(
    r"^\s*(?P<days>[^0-9]+?)\s+"
    r"(?P<start>\d{1,2}(?::\d{2})?)\s*[-–bis]{1,3}\s*(?P<end>\d{1,2}(?::\d{2})?)"
    r"\s+(?P<activity>.+?)\s*$"
)


class Rule(NamedTuple):
    days: frozenset[int]      # 0 = Montag ... 6 = Sonntag
    start_min: int            # Minuten seit Mitternacht
    end_min: int              # Minuten seit Mitternacht (kann < start sein)
    activity: str

    @property
    def duration(self) -> int:
        """Fensterlaenge in Minuten, Mitternachtsueberlauf beruecksichtigt."""
        if self.end_min > self.start_min:
            return self.end_min - self.start_min
        return (24 * 60 - self.start_min) + self.end_min


# --------------------------------------------------------------------- Zeitzone
def get_tz() -> Any:
    """Zeitzone der Persona. Faellt bei unbekanntem Namen auf Serverzeit zurueck."""
    name = str(db.get_setting("persona_timezone", "") or "").strip()
    if not name or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - ungueltiger Name oder fehlende tzdata
        return None


def now_local() -> datetime:
    """Aktuelle Zeit in der Zeitzone der Persona."""
    tz = get_tz()
    return datetime.now(tz) if tz else datetime.now()


def daypart(dt: datetime) -> str:
    for start, end, label in _DAYPARTS:
        if start <= dt.hour < end:
            return label
    return "Nacht"


# ----------------------------------------------------------------- Schedule
def _parse_days(raw: str) -> frozenset[int]:
    """'Mo-Fr' / 'Mo,Mi,Fr' / 'Sa' / 'taeglich' -> Menge von Wochentag-Indizes."""
    token = raw.strip().lower().rstrip(":").strip()
    if not token:
        return frozenset()
    if token in _ALL_DAYS_WORDS or token.replace(" ", "") in _ALL_DAYS_WORDS:
        return frozenset(range(7))

    days: set[int] = set()
    for part in re.split(r"[,/+&]| und ", token):
        part = part.strip()
        if not part:
            continue
        if "-" in part or "–" in part:
            a, _, b = part.replace("–", "-").partition("-")
            start = _DAY_ALIASES.get(a.strip())
            end = _DAY_ALIASES.get(b.strip())
            if start is None or end is None:
                continue
            idx = start
            days.add(idx)
            # zyklisch hochzaehlen, damit auch 'Fr-Mo' funktioniert
            while idx != end:
                idx = (idx + 1) % 7
                days.add(idx)
        else:
            idx = _DAY_ALIASES.get(part)
            if idx is not None:
                days.add(idx)
    return frozenset(days)


def _parse_time(raw: str) -> Optional[int]:
    """'9' oder '09:30' -> Minuten seit Mitternacht."""
    raw = raw.strip()
    if ":" in raw:
        hh, _, mm = raw.partition(":")
    else:
        hh, mm = raw, "0"
    try:
        hours, minutes = int(hh), int(mm)
    except ValueError:
        return None
    if not (0 <= hours <= 24 and 0 <= minutes < 60):
        return None
    return (hours % 24) * 60 + minutes


def parse_schedule(text: str) -> list[Rule]:
    """Wandelt den Schedule-Text in Regeln. Ungueltige Zeilen werden ignoriert."""
    rules: list[Rule] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        days = _parse_days(match.group("days"))
        start = _parse_time(match.group("start"))
        end = _parse_time(match.group("end"))
        activity = match.group("activity").strip()
        if not days or start is None or end is None or not activity:
            continue
        if start == end:  # 24h-Fenster ergibt keinen Sinn -> ueberspringen
            continue
        rules.append(Rule(days, start, end, activity))
    return rules


def _matches(rule: Rule, weekday: int, minute_of_day: int) -> bool:
    """Passt die Regel auf diesen Zeitpunkt?

    Bei Mitternachtsueberlauf (z.B. 21:00-03:00) gehoert die Zeit nach
    Mitternacht noch zum *Vortag* der Regel - 'Sa 21-03' deckt also auch
    Sonntag 01:00 ab.
    """
    if rule.end_min > rule.start_min:
        return weekday in rule.days and rule.start_min <= minute_of_day < rule.end_min
    # Ueberlauf: entweder am Regeltag nach start, oder am Folgetag vor end
    if minute_of_day >= rule.start_min:
        return weekday in rule.days
    if minute_of_day < rule.end_min:
        return ((weekday - 1) % 7) in rule.days
    return False


def current_activity(dt: datetime, rules: list[Rule]) -> Optional[str]:
    """Was tut die Persona jetzt? Spezifischste passende Regel gewinnt."""
    minute_of_day = dt.hour * 60 + dt.minute
    hits = [r for r in rules if _matches(r, dt.weekday(), minute_of_day)]
    if not hits:
        return None
    hits.sort(key=lambda r: (len(r.days), r.duration))
    return hits[0].activity


def next_change(dt: datetime, rules: list[Rule]) -> Optional[tuple[str, str]]:
    """Naechster Aktivitaetswechsel als (Uhrzeit, Aktivitaet).

    Schaut in 15-Minuten-Schritten bis zu 24h voraus. Gibt der Persona ein
    Gefuehl fuer 'gleich muss ich los' statt nur fuer den Ist-Zustand.
    """
    current = current_activity(dt, rules)
    probe = dt.replace(second=0, microsecond=0)
    for _ in range(96):
        probe += timedelta(minutes=15)
        activity = current_activity(probe, rules)
        if activity != current:
            return probe.strftime("%H:%M"), (activity or "nichts Bestimmtes geplant")
    return None


# ------------------------------------------------------------- Kontext-Block
def build_context_block(dt: Optional[datetime] = None,
                        schedule_text: Optional[str] = None,
                        force: bool = False) -> str:
    """Baut den Text, der an den System-Prompt gehaengt wird.

    dt / schedule_text sind Overrides fuer die Vorschau in den Einstellungen.
    force=True umgeht den Aktiv-Schalter (ebenfalls nur fuer die Vorschau).
    Leerer String = Feature aus.
    """
    if not force and not db.get_setting("time_context_enabled", True):
        return ""

    dt = dt or now_local()
    weekday = WEEKDAYS_DE[dt.weekday()]
    is_weekend = dt.weekday() >= 5

    lines = [
        "[AKTUELLER KONTEXT - verbindlich, hat Vorrang vor dem Gespraechsverlauf]",
        f"Jetzt ist: {weekday}, {dt.strftime('%d.%m.%Y')}, {dt.strftime('%H:%M')} Uhr.",
        f"Tageszeit: {daypart(dt)}." + (" Es ist Wochenende." if is_weekend else ""),
    ]

    if schedule_text is None:
        schedule_text = str(db.get_setting("persona_schedule", "") or "")
    rules = parse_schedule(schedule_text)
    activity = current_activity(dt, rules) if rules else None
    if activity:
        lines.append(f"Du bist gerade: {activity}.")
        upcoming = next_change(dt, rules)
        if upcoming:
            lines.append(f"Danach (ab {upcoming[0]} Uhr): {upcoming[1]}.")

    lines.append("")
    lines.append("Regeln dazu:")
    lines.append("- Begruessung, Stimmung und alle erwaehnten Taetigkeiten muessen zu "
                 "diesen Angaben passen.")
    if activity:
        lines.append("- Erfinde KEINE Taetigkeit, die dem widerspricht. Beispiel: nicht "
                     "\"ich geh jetzt schlafen\" schreiben, wenn du laut Angabe gerade "
                     "unterwegs bist; nicht \"ich sitze in der Uni\" ausserhalb der "
                     "genannten Zeiten.")
    else:
        lines.append("- Halte Aussagen ueber deine aktuelle Taetigkeit vage, wenn du sie "
                     "nicht sicher weisst. Erfinde nichts Konkretes.")
    lines.append("- Nenne Uhrzeit oder Wochentag nur, wenn es natuerlich in den Chat passt.")
    lines.append("- Erwaehne diesen Block niemals und zitiere ihn nicht.")
    return "\n".join(lines)


# ------------------------------------------------- Relative Zeit fuer History
def relative_time(ts: Any, now: Optional[datetime] = None) -> str:
    """Fanvue-Zeitstempel -> kurze deutsche Zeitmarke, z.B. 'vor 2 Std.'.

    Akzeptiert ISO-Strings und Unix-Timestamps. Bei Unklarheit: leerer String
    (dann wird einfach keine Marke gesetzt).
    """
    dt = _to_datetime(ts)
    if dt is None:
        return ""
    now = now or now_local()
    # Beide Seiten auf denselben tz-Status bringen
    if dt.tzinfo is None and now.tzinfo is not None:
        dt = dt.replace(tzinfo=now.tzinfo)
    elif dt.tzinfo is not None and now.tzinfo is None:
        dt = dt.replace(tzinfo=None)
    elif dt.tzinfo is not None and now.tzinfo is not None:
        dt = dt.astimezone(now.tzinfo)

    delta = (now - dt).total_seconds()
    if delta < 0:
        return "gerade eben"
    minutes = delta / 60
    if minutes < 2:
        return "gerade eben"
    if minutes < 60:
        return f"vor {int(minutes)} Min."
    hours = minutes / 60
    if hours < 24 and dt.date() == now.date():
        return f"vor {int(hours)} Std."
    days = (now.date() - dt.date()).days
    if days == 1:
        return f"gestern {dt.strftime('%H:%M')}"
    if days < 7:
        return f"{WEEKDAYS_DE[dt.weekday()]} {dt.strftime('%H:%M')} (vor {days} Tagen)"
    return f"am {dt.strftime('%d.%m.')} (vor {days} Tagen)"


_TS_FIELDS = ("sentAt", "createdAt", "sentDate", "created_at", "sent_at", "timestamp")


def message_timestamp(msg: dict[str, Any]) -> Any:
    """Sucht das Zeitstempel-Feld einer Fanvue-Nachricht (Feldname variiert)."""
    for field in _TS_FIELDS:
        if msg.get(field):
            return msg[field]
    return None


def _from_epoch(value: float) -> Optional[datetime]:
    """Unix-Timestamp (Sek. oder Millisek.) -> tz-bewusste UTC-Zeit.

    Bewusst tz-bewusst: sonst wuerde die Serverzeitzone eingerechnet, obwohl
    der Vergleich gegen die Zeitzone der Persona laeuft.
    """
    if value > 1e11:  # sehr wahrscheinlich Millisekunden
        value /= 1000
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def _to_datetime(ts: Any) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, (int, float)):
        return _from_epoch(float(ts))
    if isinstance(ts, str):
        raw = ts.strip()
        if not raw:
            return None
        if raw.isdigit():
            return _from_epoch(float(raw))
        cleaned = raw.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw[:19], fmt)
            except ValueError:
                continue
    return None
