"""Anti-AI Namens-/Wiederholungs-Filter.

Verhindert, dass der Bot Kosenamen ("Schatz", "babe" ...) oder den Namen des Fans
in aufeinanderfolgenden Nachrichten wiederholt – eine typische Verraeter-Signatur
von Chatbots.
"""
from __future__ import annotations

import re

from . import db


def _petnames() -> list[str]:
    raw = db.get_setting("anti_ai_petnames", "") or ""
    return [w.strip().lower() for w in raw.split(",") if w.strip()]


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-zäöüßA-ZÄÖÜ]+", (text or "").lower()))


def extract_banned(last_outbound_text: str, fan_name: str = "") -> list[str]:
    """Woerter, die im naechsten Draft NICHT vorkommen sollen:
    alle Kosenamen, die in der letzten eigenen Nachricht standen, plus der Name
    des Fans, falls er dort benutzt wurde."""
    present = _words(last_outbound_text)
    banned = [p for p in _petnames() if p in present]
    for token in re.findall(r"[a-zäöüßA-ZÄÖÜ]+", (fan_name or "").lower()):
        if len(token) >= 3 and token in present and token not in banned:
            banned.append(token)
    return banned


def violations(draft: str, banned: list[str]) -> list[str]:
    """Verbotene Woerter, die im Draft (erneut) vorkommen."""
    present = _words(draft)
    return [b for b in banned if b in present]
