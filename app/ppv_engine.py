"""PPV-Entscheidungslogik (Auto-Selling).

Kapselt die komplette Logik aus der Spezifikation:
- Trigger-Erkennung (Keywords, Priority-Trigger, Foto, Intent-Score)
- Cooldown mit Bypass-Layern
- Set-Auswahl (Tag-Matching gegen Vorlieben, keine Wiederholung)
- Aufbau des Sende-Payloads (Media-UUIDs, Preis, Vorschaubild)

Bewusst mit moeglichst reinen Funktionen, damit die Logik testbar bleibt.
"""
from __future__ import annotations

import random
from typing import Any, Optional

from . import db, fanvue


# --------------------------------------------------------------- Keyword-Helfer
def _kw_list(setting_key: str) -> list[str]:
    raw = db.get_setting(setting_key, "") or ""
    return [w.strip().lower() for w in raw.split(",") if w.strip()]


def _first_hit(text: str, words: list[str]) -> Optional[str]:
    return next((w for w in words if w and w in text), None)


def _row_request_only(row: Any) -> bool:
    try:
        return bool(row["request_only"])
    except (IndexError, KeyError):
        return False


# ------------------------------------------------------------------- Cooldown
def cooldown_passed(state: dict[str, Any], now: float) -> tuple[bool, float]:
    """Rueckgabe: (cooldown_vorbei?, aktuelle_cooldown_sekunden)."""
    threshold = int(db.get_setting("ppv_unpurchased_threshold", 3))
    short_s = float(db.get_setting("ppv_cooldown_minutes", 8)) * 60
    long_s = float(db.get_setting("ppv_cooldown_long_minutes", 30)) * 60
    need_outbound = int(db.get_setting("ppv_cooldown_outbound", 2))

    cd = long_s if state.get("unpurchased_streak", 0) >= threshold else short_s
    last = state.get("last_ppv_at")
    if not last:
        return True, cd  # noch nie ein PPV geschickt
    time_ok = (now - last) >= cd
    outbound_ok = state.get("outbound_since_ppv", 0) >= need_outbound
    return (time_ok and outbound_ok), cd


# ----------------------------------------------------------------- Entscheidung
def evaluate(state: dict[str, Any], text: str, has_photo: bool,
             classifier: dict[str, Any], now: float,
             fan_msg_count: int = 999, explicit_image: bool = False) -> dict[str, Any]:
    """Kernentscheidung: soll ein PPV gesendet werden?

    Grundregel (bewusst zurueckhaltend): verkauft wird nur bei einem ECHTEN
    Kaufsignal – entweder einer eindeutigen Aufforderung (Foto/Bildanfrage/
    Kauf-Keyword) oder ausreichendem Kaufinteresse (Intent-Score). Blosse
    sexuelle Stimmung, ein Gag oder ein erwaehntes Stichwort reichen NICHT.

    state: {last_ppv_at, outbound_since_ppv, unpurchased_streak, sexual_streak}
    classifier: {sexual_energy, intent_score, describes_fantasy, preferences} oder {}
    fan_msg_count: Anzahl bisheriger Nachrichten des Fans (fuer die Aufwaermphase)
    """
    t = (text or "").lower()
    kw_l1 = _first_hit(t, _kw_list("ppv_keywords"))
    kw_body = _first_hit(t, _kw_list("ppv_bodyparts_keywords"))
    kw_pic = _first_hit(t, _kw_list("ppv_pic_request_keywords"))
    kw_free = _first_hit(t, _kw_list("ppv_freecontent_keywords"))
    # Wuenscht sich der Fan ausdruecklich ein Video oder Fotos? Das entscheidet,
    # welche Sets ueberhaupt in Frage kommen. Video hat Vorrang: "schick mal ein
    # Video von den Bildern neulich" ist eine Video-Anfrage.
    kw_video = _first_hit(t, _kw_list("ppv_video_keywords"))
    kw_photo = _first_hit(t, _kw_list("ppv_photo_keywords"))
    wanted_kind = "video" if kw_video else ("image" if kw_photo else "")

    intent = int(classifier.get("intent_score", 0) or 0)
    intent_threshold = int(db.get_setting("ppv_intent_threshold", 4))
    intent_high = intent >= intent_threshold
    fantasy = bool(classifier.get("describes_fantasy", False))
    preferences = list(classifier.get("preferences", []) or [])
    # Der Klassifikator erkennt explizite Content-Anfragen ("hast du content wo...",
    # "zeig mir ...") auch ohne exaktes Keyword.
    content_request = bool(classifier.get("content_request", False))
    # Emotionale Ausnahmesituation (Traurigkeit, Krise, Trennung, Verletzlichkeit ...)
    emotional_distress = bool(classifier.get("emotional_distress", False))

    sexual_energy = bool(classifier.get("sexual_energy", False)) or bool(
        kw_l1 or kw_body or kw_pic or intent_high or fantasy or content_request
        or explicit_image
    )
    # Ein normales Fan-Foto ist KEIN Kaufsignal (es wird separat analysiert und im
    # Chat beantwortet, siehe poller). NUR ein explizites Bild (nackte Frau/Penis)
    # gilt als Kaufsignal -> explicit_image.
    sexual_streak = state.get("sexual_streak", 0) + 1 if sexual_energy else 0
    streak_trigger = int(db.get_setting("ppv_sexual_streak_trigger", 3))

    # Eindeutige Aufforderung -> darf auch in der Aufwaermphase verkaufen.
    # Ein explizites Bild (nackte Frau/Penis) zaehlt als starkes Signal dazu,
    # ein normales Fan-Foto NICHT.
    # Eine Videoanfrage ist genauso eine eindeutige Content-Anfrage wie eine
    # Bildanfrage - ohne das waere "schick mir bitte ein Video" kein Kaufsignal.
    explicit = bool(kw_pic) or bool(kw_l1) or content_request or explicit_image or bool(kw_video)
    # "Nur auf Anfrage"-Sets werden NUR durch eine echte Content-Anfrage
    # freigeschaltet (Bildanfrage-Keyword oder vom Klassifikator erkannte
    # Content-Anfrage) - NICHT durch ein allgemeines Kauf-Keyword ("ausziehen")
    # oder ein Fan-Foto.
    request_unlock = bool(kw_pic) or content_request or bool(kw_video)
    min_fan = int(db.get_setting("ppv_min_fan_messages", 4))
    warmup_ok = fan_msg_count >= min_fan

    cd_passed, cd_seconds = cooldown_passed(state, now)

    decision: dict[str, Any] = {
        "send": False, "bypass": False, "reason": "kein Kaufsignal",
        "sexual_energy": sexual_energy, "sexual_streak": sexual_streak,
        "preferences": preferences, "intent": intent,
        "free_request": bool(kw_free), "cooldown_passed": cd_passed,
        "cooldown_seconds": cd_seconds,
        # explizite Aufforderung (Foto/Bildanfrage/Kauf-Keyword) -> darf auch
        # in der Aufwaermphase verkaufen
        "explicit": explicit,
        # nur eine echte Content-Anfrage schaltet "nur auf Anfrage"-Sets frei
        "request_unlock": request_unlock,
        "emotional_distress": emotional_distress,
        # '' = kein bestimmter Wunsch, sonst 'video' oder 'image'
        "wanted_kind": wanted_kind,
    }

    # HARTES Verkaufsverbot: ist der Fan gerade seelisch verletzlich / in einer
    # emotionalen Ausnahmesituation, wird NIE ein PPV angeboten - egal welche
    # anderen Signale vorliegen. Der Bot antwortet dann nur zugewandt.
    if emotional_distress and db.get_setting("ppv_block_on_distress", True):
        decision["reason"] = "Emotionale Ausnahmesituation erkannt -> kein PPV (nur zugewandt antworten)"
        return decision

    # Aufwaermphase: ohne eindeutige Aufforderung erst nach genug Fan-Nachrichten
    if not explicit and not warmup_ok:
        decision["reason"] = (f"Aufwaermphase: erst ab {min_fan} Fan-Nachrichten "
                              f"(aktuell {fan_msg_count})")
        return decision

    # Moderate Inhaltssignale (Koerperteil/Fantasie/sexuelle Serie) zaehlen nur
    # zusammen mit zumindest maessigem Kaufinteresse.
    soft_intent_ok = intent >= max(1, intent_threshold - 1)
    content_signal = bool(kw_body or fantasy or (sexual_streak >= streak_trigger))

    # 1) Liegt ueberhaupt ein Kaufsignal vor? (bestimmt nur den Grund)
    trigger: Optional[str] = None
    if explicit_image:
        trigger = "Explizites Bild vom Fan (nackte Frau/Penis)"
    elif kw_l1:
        trigger = f"Kauf-Keyword '{kw_l1}'"
    elif kw_pic:
        trigger = f"Bildanfrage '{kw_pic}'"
    elif kw_video:
        # Eine Videoanfrage ist genauso eindeutig wie eine Bildanfrage. Ohne
        # diesen Zweig haenge die Entscheidung allein am LLM-Klassifikator -
        # faellt der aus oder ist abgeschaltet, wuerde "schick mir ein Video"
        # als Kaufsignal durchrutschen, "schick mir ein Bild" aber nicht.
        trigger = f"Videoanfrage '{kw_video}'"
    elif content_request:
        trigger = "Explizite Content-Anfrage"
    elif intent_high:
        trigger = f"Kaufinteresse (Intent {intent})"
    elif content_signal and soft_intent_ok:
        why = []
        if kw_body:
            why.append(f"Koerperteil '{kw_body}'")
        if fantasy:
            why.append("Fantasie")
        if sexual_streak >= streak_trigger:
            why.append(f"{sexual_streak} sexuelle Nachrichten in Folge")
        trigger = f"Inhaltssignal ({', '.join(why)}) + Intent {intent}"

    if trigger is None:
        decision["reason"] = f"kein ausreichendes Kaufinteresse (Intent {intent})"
        return decision

    # 2) Cooldown ist ein HARTES Limit und gilt fuer ALLE Signale (kein Bypass mehr).
    #    Verhindert PPV-Spam in angeheizten Chats, wo fast jede Nachricht ein
    #    Keyword/Signal enthaelt. Ist der Cooldown aktiv -> normaler Chat, kein PPV.
    if not cd_passed:
        mins = int(cd_seconds // 60)
        need_out = int(db.get_setting("ppv_cooldown_outbound", 2))
        decision["reason"] = (f"Kaufsignal ({trigger}), aber im Cooldown "
                              f"(min. {mins} Min + {need_out} eigene Nachrichten seit letztem PPV) "
                              f"-> kein PPV")
        return decision

    decision.update(send=True, reason=trigger)
    return decision


# ---------------------------------------------------------------- Set-Auswahl
def _score_folder(tags: str, preferences: list[str]) -> int:
    folder_tags = [t.strip().lower() for t in (tags or "").split(",") if t.strip()]
    if not preferences:
        return 0
    score = 0
    for pref in preferences:
        p = pref.lower()
        for ft in folder_tags:
            if p == ft or p in ft or ft in p:
                score += 1
                break
    return score


def pick_folder(candidates: list[dict[str, Any]], preferences: list[str],
                rng: Optional[random.Random] = None) -> Optional[dict[str, Any]]:
    """Waehlt aus den Kandidaten das beste Set: Tag-Match vor Zufall."""
    rng = rng or random
    if not candidates:
        return None
    if preferences:
        scored = [(_score_folder(c.get("tags", ""), preferences), c) for c in candidates]
        best = max(s for s, _ in scored)
        if best > 0:
            top = [c for s, c in scored if s == best]
            return rng.choice(top)
    return rng.choice(candidates)


def _row_media_kind(row: Any) -> str:
    """Medientyp eines Sets: 'image', 'video' oder 'mixed'. Fehlt die Spalte
    (sehr alte Datenbank), gilt 'image' - das war der bisherige Zustand."""
    try:
        return (row["media_kind"] or "image").strip().lower()
    except (IndexError, KeyError):
        return "image"


def kind_matches(set_kind: str, wanted: str) -> bool:
    """Passt der Medientyp eines Sets zum Wunsch des Fans?

    'mixed' passt immer - solche Sets enthalten beides. Ohne konkreten Wunsch
    kommt ebenfalls alles in Frage.
    """
    if not wanted or set_kind == "mixed":
        return True
    return set_kind == wanted


def select_set(user_uuid: str, preferences: list[str],
               rng: Optional[random.Random] = None,
               allow_request_only: bool = False,
               wanted_kind: str = "") -> Optional[dict[str, Any]]:
    """Waehlt ein noch nicht angebotenes/gekauftes, aktives PPV-Set.
    'Nur auf Anfrage'-Sets nur, wenn allow_request_only (explizite Anfrage).
    wanted_kind ('video'/'image') schraenkt auf den gewuenschten Medientyp ein."""
    # "angeboten" aus beiden Quellen: State-Liste UND tatsaechliche Angebots-Datensaetze
    offered = set(db.ppv_offered_sets(user_uuid)) | db.ppv_offered_folders(user_uuid)
    purchased = set(db.ppv_purchased_sets(user_uuid))
    candidates = []
    for row in db.enabled_ppv_folders():
        if row["name"] in offered or row["name"] in purchased:
            continue
        # "Nur auf Anfrage"-Sets nur bei expliziter Aufforderung
        if not allow_request_only and _row_request_only(row):
            continue
        # Wer nach einem Video fragt, soll kein Bild-Set bekommen
        if not kind_matches(_row_media_kind(row), wanted_kind):
            continue
        # Ordner-Tags + alle Bild-Tags (eigene + Fanvue-KI) fuer das Matching vereinen,
        # damit ein Treffer in irgendeinem Bild das ganze Set qualifiziert.
        all_tags = [t.strip() for t in (row["tags"] or "").split(",") if t.strip()]
        all_tags += db.folder_all_media_tags(row["name"])
        candidates.append({"name": row["name"], "tags": ", ".join(all_tags),
                           "price_cents": row["price_cents"],
                           "preview_media_uuid": row["preview_media_uuid"]})
    return pick_folder(candidates, preferences, rng)


# ------------------------------------------------------------------- Payload
def build_payload(folder: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Holt die Medien des Sets und baut das Sende-Payload zusammen."""
    max_media = int(db.get_setting("ppv_max_media_per_set", 30))
    try:
        # media_type="" -> ALLE Medien (Bilder UND Videos) werden angehaengt
        result = fanvue.list_folder_media(folder["name"], size=min(max_media, 50),
                                          media_type="")
    except fanvue.FanvueError:
        return None
    media = [m for m in result.get("data", []) if m.get("status") == "ready" and m.get("uuid")]
    if not media:
        return None
    all_uuids = [m["uuid"] for m in media]
    preview = folder.get("preview_media_uuid")
    if preview and preview in all_uuids:
        # Vorschaubild ist GRATIS-Teaser -> NICHT Teil des bezahlten Inhalts
        content = [u for u in all_uuids if u != preview]
        if not content:
            # Set enthaelt nur das Vorschaubild -> als bezahlten Inhalt senden, kein Gratis-Teaser
            content, preview = all_uuids, None
    else:
        content, preview = all_uuids, None  # kein Vorschaubild gesetzt
    return {
        "folder": folder["name"],
        "media_uuids": content[:max_media],
        "preview_uuid": preview,
        "price_cents": folder["price_cents"],
        "tags": folder.get("tags", ""),
    }
