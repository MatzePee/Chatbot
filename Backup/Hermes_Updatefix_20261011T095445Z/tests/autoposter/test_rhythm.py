"""Tagesrhythmus: Vorrangregel, Mitternacht, Lücken, Prompt-Anschluss."""
from datetime import datetime

from autoposter.services import rhythm

PLAN = """# Kommentar wird ignoriert
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


def _at(day: int, hour: int, minute: int = 0) -> str:
    """3.8.2026 ist ein Montag – so bleiben die Wochentage im Test lesbar."""
    moment = datetime(2026, 8, 3 + day, hour, minute)
    hit = rhythm.activity_at(PLAN, moment)
    return hit.activity if hit else ""


def test_parser_versteht_den_beispielplan():
    parsed = rhythm.parse(PLAN)
    assert parsed.ok
    assert len(parsed.rules) == 11


def test_wenige_tage_schlagen_viele():
    # Fr 22:00 trifft "Mo-Fr 18:00-00:30" und "Fr 21:00-03:00".
    assert _at(4, 22) == "unterwegs, feiern mit den Maedels"
    # So 20:00 trifft "Sa,So 19:00-00:30" und "So 19:00-00:30".
    assert _at(6, 20) == "ruhiger Sonntagabend, frueh muede"


def test_fenster_ueber_mitternacht_gehoert_zum_starttag():
    assert _at(5, 1) == "unterwegs, feiern mit den Maedels"     # Sa 01:00 aus Fr-Nacht
    assert _at(5, 5) == "schlaefst tief und fest"                # Sa 05:00 wieder normal
    # Mo 00:15 gehört noch zum Sonntagabend.
    assert rhythm.activity_at(PLAN, datetime(2026, 8, 10, 0, 15)).activity.startswith("ruhiger")


def test_werktage_und_wochenende_unterscheiden_sich():
    assert _at(0, 11) == "in der Uni, Vorlesungen und Lernen"
    assert _at(5, 11) == "gemuetlicher Wochenendstart, ausgeschlafen"


def test_beispielplan_ist_lueckenlos():
    assert rhythm.gaps(rhythm.parse(PLAN).rules) == []


def test_luecken_werden_gemeldet():
    parsed = rhythm.parse("Mo-So 09:00-18:00 wach")
    gaps = rhythm.gaps(parsed.rules)
    assert len(gaps) == 14  # je Tag vor 09:00 und nach 18:00
    assert gaps[0]["from"] == "00:00" and gaps[0]["to"] == "09:00"


def test_fehlerhafte_zeilen_werden_mit_zeilennummer_gemeldet():
    parsed = rhythm.parse("Mo-Fr 09:30 ohne Ende\nXy 10-12 unbekannter Tag\nMo 25:00-26 kaputt")
    assert not parsed.ok
    assert [e["line"] for e in parsed.errors] == [1, 2, 3]
    assert parsed.rules == []


def test_schreibweisen():
    assert rhythm.parse("werktags 9-17 Arbeit").rules[0].days == {0, 1, 2, 3, 4}
    assert rhythm.parse("wochenende 10-12 frei").rules[0].days == {5, 6}
    assert rhythm.parse("täglich 8-9 Kaffee").rules[0].days == set(range(7))
    assert rhythm.parse("Fr-Mo 22-2 lang").rules[0].days == {4, 5, 6, 0}
    assert rhythm.parse("Mo 9-18:30 x").rules[0].start.hour == 9


def test_prompt_fragment_mit_und_ohne_treffer():
    hit = rhythm.prompt_fragment(PLAN, datetime(2026, 8, 7, 22, 30))
    assert "Freitag, 22:30" in hit and "feiern" in hit

    partial = rhythm.prompt_fragment("Mo 09:00-10:00 kurz wach", datetime(2026, 8, 3, 15, 0))
    assert "vage" in partial

    assert rhythm.prompt_fragment("", datetime.now()) == ""
    assert rhythm.prompt_fragment(PLAN, None) == ""


def test_grid_hat_sieben_zeilen_und_24_spalten():
    grid = rhythm.grid(rhythm.parse(PLAN).rules)
    assert len(grid) == 7
    assert all(len(row) == 24 for row in grid)
    assert grid[0][3] == "schlaefst tief und fest"
