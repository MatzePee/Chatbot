"""Tagesrhythmus einer Persona.

Ein Post um 03:00 nachts, in dem die Persona im Gym steht, zerstört die
Glaubwürdigkeit schneller als jeder Rechtschreibfehler. Deshalb bekommt jede
Persona einen Wochenplan in Textform:

    # Kommentare beginnen mit einer Raute
    taeglich 00:30-08:30  schlaefst tief und fest
    Mo-Fr 09:30-16:00     in der Uni, Vorlesungen und Lernen
    Fr 21:00-03:00        unterwegs, feiern mit den Maedels

Treffen mehrere Regeln zu, gewinnt die **spezifischste**: zuerst die mit den
wenigsten Wochentagen, dann die mit dem kürzesten Zeitfenster. So schlägt
"Fr 21:00-03:00" das allgemeinere "Mo-Fr 18:00-00:30", ohne dass man
Reihenfolgen im Kopf behalten muss.

Zeitfenster dürfen über Mitternacht laufen. Sie gehören dann zum **Starttag**:
"Fr 21:00-03:00" meint die Nacht von Freitag auf Samstag.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Dict, List, Optional, Sequence, Set, Tuple

# Reihenfolge ist die Wochenordnung: 0 = Montag.
DAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
DAY_LONG = [
    "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag",
]
_DAY_INDEX = {name.lower(): index for index, name in enumerate(DAY_NAMES)}
# Freundlich gegenüber Schreibweisen, die einem beim Tippen unterlaufen.
_DAY_INDEX.update({
    "mon": 0, "montag": 0, "die": 1, "dienstag": 1, "mit": 2, "mittwoch": 2,
    "don": 3, "donnerstag": 3, "fre": 4, "freitag": 4, "sam": 5, "samstag": 5,
    "son": 6, "sonntag": 6, "su": 6, "sa": 5,
})
_ALL_DAYS = {"taeglich", "täglich", "jeden tag", "alle", "daily", "immer"}
_WEEKDAYS_WORD = {"werktags", "wochentags"}
_WEEKEND_WORD = {"wochenende", "we"}

DEFAULT_HELP = """# Tagesrhythmus der Persona. Eine Regel pro Zeile:
#   <Tage> <von>-<bis> <Aktivitaet>
# Tage: Mo | Mo-Fr | Mo,Mi,Fr | taeglich    Zeiten: 9 oder 09:30
# Bei mehreren Treffern gewinnt die spezifischste Regel
# (wenigste Tage, dann kuerzestes Zeitfenster).
# Der Plan sollte LUECKENLOS sein - fuer nicht abgedeckte Zeiten bekommt
# das Modell die Anweisung, vage zu bleiben."""


EXAMPLE_PLAN = DEFAULT_HELP + """
taeglich 00:30-08:30  schlaefst tief und fest
taeglich 08:30-09:30  gerade aufgestanden, Kaffee, noch verschlafen
Mo-Fr 09:30-16:00  in der Uni, Vorlesungen und Lernen
Mo-Fr 16:00-18:00  im Gym oder beim Einkaufen, unterwegs
Mo-Fr 18:00-00:30  zuhause, entspannt auf der Couch
Sa,So 09:30-13:00  gemuetlicher Wochenendstart, ausgeschlafen
Sa,So 13:00-19:00  Freizeit, Freundinnen treffen, Content drehen
Sa,So 19:00-00:30  Abend zuhause, Serie und Handy
So 19:00-00:30  ruhiger Sonntagabend, frueh muede
Fr 21:00-03:00  unterwegs, feiern mit den Maedels
Sa 21:00-03:00  unterwegs, feiern mit den Maedels
"""


@dataclass
class Rule:
    days: Set[int]
    start: time
    end: time
    activity: str
    line_no: int
    raw: str = ""

    @property
    def crosses_midnight(self) -> bool:
        return self._minutes(self.end) <= self._minutes(self.start)

    @property
    def length_minutes(self) -> int:
        span = self._minutes(self.end) - self._minutes(self.start)
        return span if span > 0 else span + 24 * 60

    @staticmethod
    def _minutes(value: time) -> int:
        return value.hour * 60 + value.minute

    @property
    def specificity(self) -> Tuple[int, int, int]:
        """Kleiner ist spezifischer: wenige Tage, kurzes Fenster, späte Zeile."""
        return (len(self.days), self.length_minutes, -self.line_no)

    def label(self) -> str:
        days = ",".join(DAY_NAMES[d] for d in sorted(self.days))
        if len(self.days) == 7:
            days = "täglich"
        return f"{days} {self.start:%H:%M}-{self.end:%H:%M}"


@dataclass
class ParseResult:
    rules: List[Rule] = field(default_factory=list)
    errors: List[Dict[str, object]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _parse_time(text: str) -> Optional[time]:
    text = text.strip()
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    # 24:00 ist eine gängige Schreibweise für Mitternacht am Ende eines Fensters.
    if hour == 24 and minute == 0:
        return time(0, 0)
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _parse_days(text: str) -> Optional[Set[int]]:
    lowered = text.strip().lower()
    if lowered in _ALL_DAYS:
        return set(range(7))
    if lowered in _WEEKDAYS_WORD:
        return {0, 1, 2, 3, 4}
    if lowered in _WEEKEND_WORD:
        return {5, 6}

    days: Set[int] = set()
    for part in lowered.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            first, _, last = part.partition("-")
            start = _DAY_INDEX.get(first.strip())
            end = _DAY_INDEX.get(last.strip())
            if start is None or end is None:
                return None
            index = start
            days.add(index)
            # Über das Wochenende hinaus zählen, damit "Fr-Mo" möglich ist.
            while index != end:
                index = (index + 1) % 7
                days.add(index)
        else:
            index = _DAY_INDEX.get(part)
            if index is None:
                return None
            days.add(index)
    return days or None


def parse(text: str) -> ParseResult:
    """Regeltext einlesen. Fehlerhafte Zeilen werden gemeldet, nicht verschluckt."""
    result = ParseResult()
    for line_no, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(None, 2)
        if len(parts) < 3:
            result.errors.append({
                "line": line_no, "text": raw,
                "message": "Erwartet werden drei Angaben: Tage, Zeitfenster, Aktivität",
            })
            continue

        days_text, window_text, activity = parts
        days = _parse_days(days_text)
        if days is None:
            result.errors.append({
                "line": line_no, "text": raw,
                "message": f"Tage nicht verstanden: '{days_text}' (z. B. Mo, Mo-Fr, Sa,So, taeglich)",
            })
            continue

        if "-" not in window_text:
            result.errors.append({
                "line": line_no, "text": raw,
                "message": f"Zeitfenster braucht einen Bindestrich: '{window_text}'",
            })
            continue
        from_text, _, to_text = window_text.partition("-")
        start = _parse_time(from_text)
        end = _parse_time(to_text)
        if start is None or end is None:
            result.errors.append({
                "line": line_no, "text": raw,
                "message": f"Uhrzeit nicht verstanden: '{window_text}' (z. B. 9 oder 09:30)",
            })
            continue
        if start == end:
            result.errors.append({
                "line": line_no, "text": raw,
                "message": "Anfang und Ende sind gleich – das Fenster wäre leer",
            })
            continue

        activity = activity.strip()
        if not activity:
            result.errors.append({
                "line": line_no, "text": raw, "message": "Aktivität fehlt",
            })
            continue

        result.rules.append(
            Rule(days=days, start=start, end=end, activity=activity, line_no=line_no, raw=line)
        )
    return result


def _matches(rule: Rule, weekday: int, minute_of_day: int) -> bool:
    start = Rule._minutes(rule.start)
    end = Rule._minutes(rule.end)
    if not rule.crosses_midnight:
        return weekday in rule.days and start <= minute_of_day < end
    # Über Mitternacht: erste Hälfte am Starttag, zweite am Folgetag.
    if weekday in rule.days and minute_of_day >= start:
        return True
    return ((weekday - 1) % 7) in rule.days and minute_of_day < end


def rule_for(rules: Sequence[Rule], weekday: int, minute_of_day: int) -> Optional[Rule]:
    """Die spezifischste Regel für einen Zeitpunkt, oder None."""
    hits = [r for r in rules if _matches(r, weekday, minute_of_day)]
    if not hits:
        return None
    return min(hits, key=lambda r: r.specificity)


def activity_at(text_or_rules, moment: datetime) -> Optional[Rule]:
    """Aktivität zu einem konkreten (lokalen!) Zeitpunkt."""
    rules = text_or_rules if isinstance(text_or_rules, list) else parse(text_or_rules).rules
    return rule_for(rules, moment.weekday(), moment.hour * 60 + moment.minute)


def gaps(rules: Sequence[Rule]) -> List[Dict[str, object]]:
    """Nicht abgedeckte Zeiträume je Wochentag, minutengenau zusammengefasst."""
    out: List[Dict[str, object]] = []
    for weekday in range(7):
        open_from: Optional[int] = None
        for minute in range(24 * 60 + 1):
            covered = minute < 24 * 60 and rule_for(rules, weekday, minute) is not None
            if not covered and open_from is None and minute < 24 * 60:
                open_from = minute
            elif covered and open_from is not None:
                out.append({
                    "weekday": weekday,
                    "day": DAY_LONG[weekday],
                    "from": f"{open_from // 60:02d}:{open_from % 60:02d}",
                    "to": f"{minute // 60:02d}:{minute % 60:02d}",
                })
                open_from = None
        if open_from is not None:
            out.append({
                "weekday": weekday,
                "day": DAY_LONG[weekday],
                "from": f"{open_from // 60:02d}:{open_from % 60:02d}",
                "to": "24:00",
            })
    return out


def grid(rules: Sequence[Rule], step_minutes: int = 60) -> List[List[Optional[str]]]:
    """7 Zeilen (Mo–So) × Zeitschritte – Grundlage für die Vorschau."""
    steps = (24 * 60) // step_minutes
    out: List[List[Optional[str]]] = []
    for weekday in range(7):
        row: List[Optional[str]] = []
        for index in range(steps):
            # Mitte des Schritts abfragen: robuster an den Rändern.
            minute = index * step_minutes + step_minutes // 2
            found = rule_for(rules, weekday, minute)
            row.append(found.activity if found else None)
        out.append(row)
    return out


def prompt_fragment(text: str, moment: Optional[datetime]) -> str:
    """Der Satz, der im LLM-Prompt landet.

    Ohne Treffer wird ausdrücklich Vagheit verlangt – lieber ein zeitloser Post
    als einer, der die Persona an den falschen Ort stellt.
    """
    if not moment:
        return ""
    parsed = parse(text or "")
    if not parsed.rules:
        return ""
    found = rule_for(parsed.rules, moment.weekday(), moment.hour * 60 + moment.minute)
    when = f"{DAY_LONG[moment.weekday()]}, {moment:%H:%M} Uhr"
    if found:
        return (
            f"Zeitpunkt des Posts: {when}. Zu dieser Zeit gilt für dich: {found.activity}. "
            "Der Text muss dazu passen und darf keine Tätigkeit behaupten, die dem "
            "widerspricht. Die Aktivität muss nicht ausdrücklich genannt werden – "
            "sie ist der Rahmen, nicht das Thema."
        )
    return (
        f"Zeitpunkt des Posts: {when}. Für diese Zeit ist nichts hinterlegt. "
        "Bleibe deshalb vage: keine konkrete Tätigkeit, kein Ort, keine Aussage "
        "darüber, was du gerade tust."
    )
