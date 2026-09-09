"""Auswertung der Modellantwort — die Stelle, an der es zweimal geknallt hat.

Beide Fehler waren im Betrieb schwer zu finden, weil die Meldung
(„'NoneType' object has no attribute 'strip'") nichts über die Ursache sagte.
Deshalb stehen sie hier als Test: Sie kommen sonst mit dem nächsten Modell
zurück, das seine Antwort anders verpackt.
"""
from __future__ import annotations

import pytest

from autoposter.services.llm import (
    _extract_json,
    content_of,
    empty_answer_reason,
    strip_reasoning,
)


def antwort(content, *, finish="stop", model="ein/modell", **extra):
    message = {"content": content}
    message.update(extra)
    return {"choices": [{"message": message, "finish_reason": finish}], "_model": model}


# --------------------------------------------------------------------- Denk-Blöcke
@pytest.mark.parametrize(
    "roh, erwartet",
    [
        ("<think>Kurz halten.</think>Rain the whole run home.", "Rain the whole run home."),
        ("<thinking>\nHmm.\n</thinking>\n\nSeminar ran long.", "Seminar ran long."),
        ("<REASONING>laut denken</REASONING>Coffee survived.", "Coffee survived."),
        ("◁think▷planen◁/think▷Deadlift moved today.", "Deadlift moved today."),
        ("Ganz normaler Post.", "Ganz normaler Post."),
        ("", ""),
    ],
)
def test_denkbloecke_verschwinden(roh, erwartet):
    assert strip_reasoning(roh) == erwartet


@pytest.mark.parametrize(
    "roh",
    ["<think>abgeschnitten mitten im Denken", "◁think▷ohne Ende"],
)
def test_offener_denkblock_gilt_als_leer(roh):
    """Ein Block ohne Schluss heißt: Das Modell hat beim Denken aufgehört.
    Was danach steht, ist kein fertiger Post — lieber nichts als Bruchstücke."""
    assert strip_reasoning(roh) == ""


# --------------------------------------------------------------------- content
def test_content_null_stuerzt_nicht_ab():
    """Der ursprüngliche Fehler: message.content ist nullable."""
    assert content_of(antwort(None, finish="length")) == ""


def test_content_als_blockliste():
    payload = antwort([{"type": "text", "text": "Guten Morgen"}])
    assert content_of(payload) == "Guten Morgen"


def test_reasoning_als_rueckfallebene():
    """Denkende Modelle legen die Ausgabe manchmal nur in `reasoning` ab."""
    payload = antwort(None, reasoning="Kurzer Text ohne Marken.")
    assert content_of(payload) == "Kurzer Text ohne Marken."


def test_kaputte_antwort_ergibt_leer_statt_ausnahme():
    for payload in ({}, {"choices": []}, {"choices": [{}]}, {"error": {"message": "x"}}):
        assert content_of(payload) == ""


def test_denkblock_wird_auch_aus_content_entfernt():
    assert content_of(antwort("<think>plan</think>Short and dry.")) == "Short and dry."


# --------------------------------------------------------------------- Begründung
def test_begruendung_nennt_modell_und_grund():
    text = empty_answer_reason(antwort(None, finish="length", model="qwen/qwen3.6-35b-a3b"))
    assert "qwen/qwen3.6-35b-a3b" in text
    assert "length" in text
    assert "Token-Budget" in text


def test_begruendung_ohne_finish_reason():
    assert empty_answer_reason({}).endswith(".")


# --------------------------------------------------------------------- JSON
def test_extract_json_vertraegt_none():
    """Vorher lief hier direkt .strip() auf None."""
    with pytest.raises(ValueError):  # json.JSONDecodeError erbt von ValueError
        _extract_json(None)


def test_extract_json_aus_codeblock():
    roh = 'Hier:\n```json\n{"variants":[{"text":"hi"}]}\n```\n'
    assert _extract_json(roh)["variants"][0]["text"] == "hi"
