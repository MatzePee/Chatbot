"""Hintergrund-Worker.

Zwei Aufgaben in einem Thread-Loop:
1. poll_cycle(): neue eingehende Nachrichten finden und Antwort-Entwuerfe erzeugen.
2. send_due_cycle(): faellige Auto-Drafts senden (mit menschenaehnlicher Verzoegerung).

Der Loop laeuft immer; ob tatsaechlich gearbeitet wird, haengt am Master-Schalter
(setting 'bot_running') und den aktiven Zeiten.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from datetime import datetime
from typing import Any

from . import db, fanvue, guardrails, namefilter, notify, openrouter, ppv_engine, updater

_thread: threading.Thread | None = None
_stop = threading.Event()
_status: dict[str, Any] = {"last_poll": None, "last_error": None, "cycles": 0}


# ---------------------------------------------------------------- Hilfsfunktionen
def status() -> dict[str, Any]:
    st = dict(_status)
    st["alive"] = bool(_thread and _thread.is_alive())
    last = st.get("last_poll")
    st["stale_seconds"] = (time.time() - last) if last else None
    return st


def _within_active_hours() -> bool:
    if not db.get_setting("active_hours_enabled", False):
        return True
    start = int(db.get_setting("active_hour_start", 8))
    end = int(db.get_setting("active_hour_end", 23))
    hour = datetime.now().hour
    if start <= end:
        return start <= hour < end
    # ueber Mitternacht (z.B. 22 -> 6)
    return hour >= start or hour < end


def _effective_mode(chat: Any) -> str:
    if chat and chat["mode_override"] in ("approval", "auto"):
        return chat["mode_override"]
    return db.get_setting("mode", "approval")


def _me_uuid() -> str:
    """Eigene Konto-Kennung. Holt sie nach, falls sie fehlt - ohne sie kann der
    Bot eigene Nachrichten nicht von Fan-Nachrichten unterscheiden."""
    try:
        return fanvue.account_uuid()
    except Exception:  # noqa: BLE001
        tokens = db.get_tokens()
        return (tokens["account_uuid"] if tokens else "") or ""


_UNSET = object()


# ------------------------------------------------------------------ Kernlogik
def poll_cycle(custom_list_id=_UNSET) -> None:
    """Ein Durchlauf: unbeantwortete Chats einer Gruppe holen und Drafts erzeugen.
    custom_list_id=_UNSET -> Prio-1-Liste aus den Einstellungen; sonst die uebergebene."""
    if not fanvue.is_connected():
        return
    filter_ = db.get_setting("chat_filter", "unread")
    if custom_list_id is _UNSET:
        custom_list_id = db.get_setting("chat_custom_list_id", "")
    max_chats = int(db.get_setting("max_chats_per_cycle", 10))
    me_uuid = _me_uuid()

    result = fanvue.list_chats(filter_=filter_, size=min(max_chats, 50),
                               custom_list_id=custom_list_id)
    chats = result.get("data", [])
    for entry in chats[:max_chats]:
        user = entry.get("user", {})
        user_uuid = user.get("uuid")
        if not user_uuid:
            continue
        handle = user.get("handle", "")
        display_name = user.get("displayName", "")
        db.upsert_chat(user_uuid, handle, display_name)
        try:
            _process_chat(user_uuid, handle, display_name, me_uuid)
        except fanvue.FanvueError as exc:
            db.log("error", "poll", f"Chat {handle or user_uuid} fehlgeschlagen", str(exc))
        # kleine Pause zwischen Chats, freundlich zur API
        time.sleep(0.4)


def _process_chat(user_uuid: str, handle: str, display_name: str, me_uuid: str) -> None:
    chat = db.get_chat(user_uuid)
    if chat and not chat["bot_enabled"]:
        return
    # Offener Draft? Normalerweise nicht doppelt generieren. Ausnahme: eine noch nicht
    # gesendete, geplante REAKTIVIERUNG wird verworfen, sobald der Fan selbst schreibt
    # (dann antworten wir stattdessen normal auf seine neue Nachricht).
    open_draft = db.get_open_draft(user_uuid)
    reactivation_to_cancel = None
    if open_draft is not None:
        if _is_reactivation(open_draft):
            reactivation_to_cancel = open_draft
        else:
            return

    # Cooldown gegen zu haeufiges Antworten
    cooldown = int(db.get_setting("reply_cooldown_seconds", 120))
    last = db.last_sent_at(user_uuid)
    if last and (time.time() - last) < cooldown:
        return

    history_n = int(db.get_setting("history_messages", 15))
    msg_result = fanvue.list_messages(user_uuid, size=max(history_n, 5), mark_as_read=False)
    messages = msg_result.get("data", [])
    if not messages:
        return

    # API liefert neueste zuerst -> chronologisch drehen
    messages_chrono = list(reversed(messages))
    last_msg = messages_chrono[-1]
    last_sender = (last_msg.get("sender") or {}).get("uuid", "")

    # Nur antworten, wenn die letzte Nachricht vom Fan kam
    if last_sender == me_uuid:
        return
    last_uuid = last_msg.get("uuid")
    if chat and chat["last_inbound_uuid"] == last_uuid:
        return  # schon verarbeitet

    # Neue Fan-Nachricht liegt vor -> eine noch eingeplante Reaktivierung verwerfen,
    # damit nicht kurz danach doch noch ein "wo bist du?" rausgeht.
    if reactivation_to_cancel is not None:
        db.update_draft(reactivation_to_cancel["id"], status="rejected",
                        error="Fan hat zwischenzeitlich geantwortet")
        db.log("info", "generate",
               f"Reaktivierung verworfen (Fan wieder aktiv) für {handle or user_uuid}", "")

    incoming_text = (last_msg.get("text") or "").strip()
    has_photo = (bool(last_msg.get("hasMedia")) and last_msg.get("mediaType") == "image"
                 and last_sender != me_uuid)
    # Trinkgeld? Fanvue markiert Tips mit type == "TIP" (Betrag in pricing.USD.price, Cent)
    is_tip = str(last_msg.get("type") or "").upper() == "TIP"
    tip_cents = 0
    if is_tip:
        try:
            tip_cents = int(((last_msg.get("pricing") or {}).get("USD") or {}).get("price") or 0)
        except (TypeError, ValueError):
            tip_cents = 0
    # Zeitstempel der letzten Fan-Nachricht (fuer die proaktive Reaktivierung)
    db.update_chat(user_uuid, last_inbound_at=time.time())

    # Eskalations-Check auf die eingehende Nachricht
    escalation = guardrails.incoming_needs_escalation(incoming_text)

    # Persona bestimmen (globaler oder Chat-spezifischer System-Prompt)
    system_prompt = db.get_setting("system_prompt", "")
    fan_notes = ""
    if chat:
        if chat["persona_override"]:
            system_prompt = chat["persona_override"]
        fan_notes = chat["notes"] or ""

    # Anti-AI: verbotene Woerter aus der letzten eigenen Nachricht bestimmen
    banned: list[str] = []
    if db.get_setting("anti_ai_enabled", True):
        last_out = ""
        for m in reversed(messages_chrono):
            if (m.get("sender") or {}).get("uuid", "") == me_uuid:
                last_out = m.get("text") or ""
                break
        banned = namefilter.extract_banned(last_out, display_name or handle)

    # --- Eingehendes Fan-Bild analysieren (einmal) ---
    # Ergebnis fliesst sowohl in die PPV-Entscheidung (explizites Bild = Kaufsignal)
    # als auch in die normale Chat-Antwort (Bild beschreiben / vorsichtig bei Frau).
    img_analysis: dict[str, Any] | None = None
    if has_photo and db.get_setting("incoming_image_enabled", True):
        img_analysis = openrouter.analyze_incoming_image(fanvue.message_image_url(last_msg))
    explicit_image = bool(img_analysis and (img_analysis.get("nude") or img_analysis.get("penis")))

    # --- Trinkgeld: sich bedanken (kein PPV, kein Verkauf) ---
    if is_tip and db.get_setting("tip_thanks_enabled", True):
        thanks = db.get_setting("tip_thanks_prompt", "")
        amount = f" von ${tip_cents/100:.2f}" if tip_cents else ""
        system_prompt = system_prompt + (
            f"\n\nWICHTIG: Der Fan hat dir gerade ein TRINKGELD{amount} geschickt. "
            f"Bedanke dich warm, persoenlich und in deinem Stil dafuer. Kein Verkauf, "
            f"kein Angebot, keine Gegenfrage nach mehr Geld. {thanks}")

    # --- PPV Auto-Selling ---
    if db.get_setting("ppv_enabled", False) and not is_tip:
        _detect_purchase(user_uuid, messages_chrono, me_uuid)
        state_row = db.get_ppv_state(user_uuid)
        state = {k: state_row[k] for k in
                 ("last_ppv_at", "outbound_since_ppv", "unpurchased_streak", "sexual_streak")}
        classifier: dict[str, Any] = {}
        if db.get_setting("ppv_use_llm_classifier", True):
            # fan_notes einbeziehen -> Vorlieben (z.B. "liebt Fuesse") fliessen als
            # englische Tags in die Set-Auswahl ein
            classifier = openrouter.classify_message(incoming_text, fan_notes)
        # Anzahl bisheriger Fan-Nachrichten (fuer die Aufwaermphase)
        fan_msg_count = sum(1 for m in messages_chrono
                            if (m.get("sender") or {}).get("uuid", "") != me_uuid)
        decision = ppv_engine.evaluate(state, incoming_text, has_photo, classifier,
                                       time.time(), fan_msg_count,
                                       explicit_image=explicit_image)
        db.update_ppv_state(user_uuid, sexual_streak=decision["sexual_streak"])
        if decision["send"] and not escalation:
            wanted_kind = decision.get("wanted_kind", "")
            folder = ppv_engine.select_set(user_uuid, decision["preferences"],
                                           allow_request_only=decision.get("request_unlock", False),
                                           wanted_kind=wanted_kind)
            # Zweitmeinung MIT Gespraechsverlauf, bevor wirklich verkauft wird.
            # Erst an dieser Stelle, damit der Aufruf nur faellt, wenn wirklich
            # ein Set bereitsteht - und das konkrete Set mitgegeben werden kann.
            if folder and db.get_setting("ppv_confirm_enabled", True):
                pruef = openrouter.confirm_ppv(
                    messages_chrono, me_uuid, decision.get("reason", ""),
                    set_name=folder["name"], price_cents=folder.get("price_cents", 0))
                # Bei einer Stoerung bewusst KEIN Verkauf: ein Set kann jedem Fan
                # nur einmal angeboten werden, ein verbranntes ist dauerhaft weg.
                if not pruef["ok"] and not (
                        not pruef["geprueft"] and db.get_setting("ppv_confirm_fail_open", False)):
                    db.log("info", "generate",
                           f"PPV abgelehnt durch Zweitmeinung ({handle or user_uuid})",
                           f"{decision.get('reason', '')} → {pruef['grund']}"
                           + ("" if pruef["geprueft"] else " (Prüfung war nicht möglich)"))
                    folder = None          # normal weiterchatten, Set bleibt erhalten

            if folder:
                payload = ppv_engine.build_payload(folder)
                if payload:
                    # Nur als erledigt markieren, wenn wirklich ein Entwurf entstand.
                    # Sonst geht die Nachricht bei einem Timeout dauerhaft verloren.
                    if _create_ppv_draft(user_uuid, handle, display_name, incoming_text,
                                         messages_chrono, me_uuid, system_prompt, fan_notes,
                                         payload, decision, chat, banned):
                        db.update_chat(user_uuid, last_inbound_uuid=last_uuid)
                    else:
                        db.log("warn", "generate",
                               f"PPV-Entwurf für {handle} fehlgeschlagen – "
                               f"nächster Durchlauf versucht es erneut", "")
                    return
                db.log("warn", "generate", f"PPV-Set '{folder['name']}' hat keine Medien", "")
            else:
                db.log("info", "generate",
                       f"PPV getriggert, aber kein Set mehr uebrig fuer {handle or user_uuid}"
                       + (f" (gewuenscht: {wanted_kind})" if wanted_kind else ""),
                       decision["reason"])
                # Ausdruecklicher Wunsch, aber nichts Passendes da: NICHTS anderes
                # anbieten, sondern im Chat ehrlich vertroesten. Sonst bekaeme der
                # Fan auf "schick mir ein Video" ein Bild-Set - genau das soll nicht
                # passieren.
                if wanted_kind:
                    wunsch = "ein Video" if wanted_kind == "video" else "Fotos"
                    hinweis = db.get_setting("ppv_no_match_prompt", "")
                    if hinweis:
                        system_prompt = system_prompt + "\n\n" + hinweis.replace("{wunsch}", wunsch)
        elif decision.get("free_request"):
            # Gratis-Anfrage -> normaler Chat, aber mit Umleit-Instruktion
            system_prompt = system_prompt + "\n\n" + db.get_setting("ppv_freecontent_prompt", "")

    # --- Eingehendes Fan-Bild: im Chat darauf eingehen ---
    # Ein explizites Bild (nackte Frau/Penis) wurde bereits als PPV-Signal behandelt;
    # kam kein PPV zustande, wird hier normal darauf reagiert.
    if img_analysis is not None:
        desc = img_analysis.get("description", "") or "ein Bild"
        if img_analysis.get("woman") and not explicit_image:
            # Angezogene/erkennbare Frau: Bot kann nicht wissen, ob das die Creatorin
            # selbst ist -> vorsichtig/ausweichend antworten.
            system_prompt = system_prompt + "\n\n" + \
                db.get_setting("incoming_image_woman_prompt", "")
            db.log("info", "image",
                   f"Fan-Bild mit Person erkannt ({handle or user_uuid}) -> vorsichtige Antwort",
                   desc)
        else:
            react = db.get_setting("incoming_image_react_prompt", "")
            system_prompt = system_prompt + "\n\n" + react.replace("{beschreibung}", desc)
            db.log("info", "image", f"Fan-Bild analysiert ({handle or user_uuid})", desc)

    # Generieren (mit Anti-AI-Regeln + Namens-Filter)
    try:
        generated = _generate(system_prompt, messages_chrono, me_uuid, fan_notes, banned)
    except Exception as exc:  # noqa: BLE001 – nie den ganzen Poll-Zyklus abbrechen
        # BEWUSST OHNE last_inbound_uuid: Die Nachricht darf NICHT als erledigt
        # gelten. Ein Netzwerk-Timeout ist voruebergehend - wird hier markiert,
        # bekommt der Fan nie eine Antwort, weil der naechste Durchlauf die
        # Nachricht fuer bereits verarbeitet haelt.
        db.log("error", "generate",
               f"Generierung fehlgeschlagen ({handle}) – wird beim nächsten Durchlauf erneut versucht",
               str(exc))
        return

    clean_text, note = guardrails.check_outgoing(generated, has_media=False)
    mode = _effective_mode(chat)

    # Entscheiden: Auto-Send oder manuelle Freigabe?
    # -> Eskalation oder Guardrail-Note erzwingen immer manuelle Freigabe.
    auto = (mode == "auto") and not escalation and not note
    status_val = "pending"
    scheduled = None
    guardrail_note = note
    if escalation:
        guardrail_note = (guardrail_note + " | " if guardrail_note else "") + \
            f"Eskalations-Stichwort '{escalation}' -> bitte manuell pruefen"

    if auto:
        delay_min = int(db.get_setting("send_delay_min_seconds", 20))
        delay_max = int(db.get_setting("send_delay_max_seconds", 90))
        delay = random.randint(min(delay_min, delay_max), max(delay_min, delay_max))
        scheduled = time.time() + delay

    draft_id = db.create_draft(
        user_uuid=user_uuid,
        handle=handle,
        display_name=display_name,
        incoming_text=incoming_text,
        generated_text=generated,
        edited_text=clean_text,
        status=status_val,
        auto_send=1 if auto else 0,
        scheduled_send_at=scheduled,
        guardrail_note=guardrail_note,
        model=db.get_setting("openrouter_model", ""),
        inbound_uuid=last_uuid,
    )
    db.update_chat(user_uuid, last_inbound_uuid=last_uuid)
    db.log("info", "generate",
           f"Draft #{draft_id} fuer {handle or user_uuid} erstellt "
           f"({'auto' if auto else 'freigabe'})",
           incoming_text[:200])


# Sprach-Erkennung (leichtgewichtige Heuristik): Deutsch vs. Englisch
_DE_WORDS = {
    "ich", "und", "nicht", "ist", "das", "dass", "dir", "dich", "mein", "meine", "wie",
    "was", "schon", "aber", "auch", "noch", "immer", "engel", "schatz", "kuss", "wenn",
    "hab", "habe", "bist", "sehr", "mich", "weil", "danke", "gerne", "du", "wir", "ein",
    "eine", "der", "die", "den", "mit", "für", "auf", "so", "nur", "mal", "heute", "morgen",
}
_EN_WORDS = {
    "the", "you", "and", "your", "love", "would", "have", "that", "this", "with", "what",
    "know", "feel", "i", "me", "my", "we", "are", "was", "for", "how", "when", "so",
    "just", "want", "like", "much", "she", "her", "night", "here", "there", "always",
}
_FR_WORDS = {
    "je", "tu", "il", "elle", "nous", "vous", "ils", "le", "la", "les", "un", "une", "des",
    "du", "et", "est", "ne", "pas", "que", "qui", "pour", "avec", "mais", "sur", "dans",
    "tout", "bien", "comme", "aussi", "très", "moi", "toi", "ton", "ta", "tes", "mon", "ma",
    "mes", "salut", "bonjour", "coucou", "ça", "oui", "merci", "aime", "veux", "suis",
    "fais", "quoi", "plus", "toujours", "beaucoup", "chérie", "bisous", "envie", "es", "ai",
}


def _fan_language(messages_chrono: list, me_uuid: str) -> str:
    """Grobe Erkennung der Fan-Sprache aus den letzten Fan-Nachrichten: 'de', 'en' oder 'fr'."""
    texts = [(m.get("text") or "") for m in messages_chrono
             if (m.get("sender") or {}).get("uuid", "") != me_uuid]
    sample = " ".join(texts[-5:]).lower()
    if not sample.strip():
        return "en"
    words = re.findall(r"[^\W\d_]+", sample, re.UNICODE)
    de = sum(1 for w in words if w in _DE_WORDS)
    en = sum(1 for w in words if w in _EN_WORDS)
    fr = sum(1 for w in words if w in _FR_WORDS)
    # Starke Schrift-Marker: deutsche Umlaute -> Deutsch; franzoesische Akzente -> Franzoesisch
    if re.search(r"[äöüß]", sample):
        de += 3
    if re.search(r"[éèêàçùâîïôûœ]", sample):
        fr += 2
    scores = {"de": de, "en": en, "fr": fr}
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "en"


def _generate(system_prompt: str, messages_chrono: list, me_uuid: str, fan_notes: str,
              banned: list[str], task: str = "chat", retry_delay: float | None = None) -> str:
    """Generiert eine Antwort inkl. Anti-AI-Regeln, Namens-Filter und Sprach-Anker.
    Bei einer Wiederholung verbotener Woerter wird EINMAL neu generiert.
    task steuert das genutzte Modell ('chat' oder 'caption').
    retry_delay: Pause (s) zwischen Neuversuchen bei leerer Antwort; None = aus den
    Einstellungen (Hintergrundbetrieb), 0 = keine Pause (interaktiv, z.B. Test)."""
    model, api_key = openrouter.resolve_model(task)
    retries = int(db.get_setting("generation_retries", 3))
    if retry_delay is None:
        retry_delay = float(db.get_setting("generation_retry_delay", 60))
    sys = system_prompt
    if db.get_setting("anti_ai_enabled", True):
        rules = db.get_setting("anti_ai_rules", "")
        if rules:
            sys = sys + "\n\n" + rules
    # Sprach-Anker ganz ans Ende (Recency) – konkrete Sprache vorgeben, damit das
    # Modell nicht ins Deutsche/Chinesische o.ae. abdriftet.
    lang = _fan_language(messages_chrono, me_uuid)
    lang_name = {"de": "German", "fr": "French"}.get(lang, "English")
    sys = sys + (f"\n\nLANGUAGE — STRICT AND MOST IMPORTANT: The fan is writing in {lang_name}. "
                 f"Write your ENTIRE reply ONLY in {lang_name}. Do not switch languages, do not "
                 f"mix languages, and NEVER use any other language or writing system "
                 f"(never Chinese, Japanese, Korean, Cyrillic, Arabic, etc.), no matter what "
                 f"language these instructions are written in.")
    # Bis zu `retries` Versuche gegen leere Antworten (Reasoning-Modelle), mit Pause.
    text = openrouter.generate_retry(
        openrouter.build_messages(sys, messages_chrono, me_uuid, fan_notes),
        model=model, api_key=api_key, attempts=retries, delay_seconds=retry_delay,
        category=task)
    if text and banned:
        viol = namefilter.violations(text, banned)
        if viol:
            sys2 = sys + ("\n\nVerwende folgende Woerter NICHT in deiner Antwort: "
                          + ", ".join(viol) + ".")
            regen = openrouter.generate_retry(
                openrouter.build_messages(sys2, messages_chrono, me_uuid, fan_notes),
                model=model, api_key=api_key, attempts=retries, delay_seconds=retry_delay,
                category=task)
            if regen and regen.strip():
                text = regen
            db.log("info", "generate", "Namens-Filter: Wiederholung vermieden", ", ".join(viol))
    return text


def _create_ppv_draft(user_uuid: str, handle: str, display_name: str, incoming_text: str,
                      messages_chrono: list, me_uuid: str, system_prompt: str, fan_notes: str,
                      payload: dict, decision: dict, chat: Any,
                      banned: list[str] | None = None) -> bool:
    """Erzeugt einen PPV-Entwurf (Verkaufston + Media/Preis/Vorschau).

    Rueckgabe: True, wenn ein Entwurf entstand. Nur dann darf die Nachricht
    als beantwortet markiert werden."""
    sales_prompt = db.get_setting("ppv_sales_prompt", "")
    content_context = payload.get("tags") or "exklusiver Content"
    media_count = len(payload.get("media_uuids") or [])
    menge = "genau EIN einzelnes Bild" if media_count <= 1 else "mehrere Bilder"
    sys = (f"{system_prompt}\n\n{sales_prompt}\n\n"
           f"WICHTIG – SPRACHE: Antworte in DERSELBEN Sprache, die der Fan in seinen letzten "
           f"Nachrichten verwendet (z.B. Englisch, wenn der Fan Englisch schreibt). Wechsle "
           f"NIEMALS die Sprache – auch nicht, weil diese Anweisung oder ein Beispiel auf Deutsch ist. "
           f"Der Content ist der Nachricht bereits als KAUFBARER ANHANG beigefuegt "
           f"(Medium + Preis werden automatisch angehaengt). Es handelt sich um {menge}. "
           f"Inhalt/Stimmung: {content_context}. "
           f"Schreibe NUR eine kurze, verfuehrerische Bildunterschrift dazu (1-2 Saetze), so als "
           f"waere der Content schon da. Verwende NIEMALS das Wort 'Set' (es kann auch nur ein "
           f"einzelnes Bild sein) und behaupte keine Menge, die nicht stimmt. "
           f"STRIKT VERBOTEN: eckige Klammern oder Platzhalter wie '[PPV ...]', jede Preisangabe, "
           f"Regieanweisungen in Klammern wie '(sendet ein Bild)' oder '(sends a preview)', "
           f"Sternchen-Aktionen. Frage NICHT 'willst du es sehen' und kuendige nichts an – der "
           f"Content haengt bereits an der Nachricht.")
    caption_model, _ = openrouter.resolve_model("caption")
    try:
        generated = _generate(sys, messages_chrono, me_uuid, fan_notes, banned or [],
                              task="caption")
    except openrouter.OpenRouterError as exc:
        db.log("error", "generate", f"PPV-Text fehlgeschlagen ({handle})", str(exc))
        return False
    # Sicherheitsnetz: 'Set' -> 'PPV' (impliziert faelschlich mehrere Bilder)
    generated = re.sub(r"(?i)\bsets?\b", "PPV", generated)
    clean_text, note = guardrails.check_outgoing(generated, has_media=True)
    mode = _effective_mode(chat)
    auto = (mode == "auto") and not note
    scheduled = None
    if auto:
        delay_min = int(db.get_setting("send_delay_min_seconds", 20))
        delay_max = int(db.get_setting("send_delay_max_seconds", 90))
        scheduled = time.time() + random.randint(min(delay_min, delay_max), max(delay_min, delay_max))
    guard = f"PPV · {decision.get('reason', '')} · Set '{payload['folder']}' · " \
            f"${payload['price_cents']/100:.2f} · {len(payload['media_uuids'])} Medien"
    if note:
        guard = note + " | " + guard
    draft_id = db.create_draft(
        user_uuid=user_uuid, handle=handle, display_name=display_name,
        incoming_text=incoming_text, generated_text=generated, edited_text=clean_text,
        status="pending", auto_send=1 if auto else 0, scheduled_send_at=scheduled,
        guardrail_note=guard, model=caption_model,
        is_ppv=1, ppv_folder=payload["folder"],
        ppv_media_uuids=json.dumps(payload["media_uuids"]),
        ppv_price_cents=payload["price_cents"], ppv_preview_uuid=payload["preview_uuid"],
        inbound_uuid=(messages_chrono[-1].get("uuid") if messages_chrono else None),
    )
    db.log("info", "generate",
           f"PPV-Draft #{draft_id} fuer {handle or user_uuid} ({'auto' if auto else 'freigabe'})",
           decision.get("reason", ""))
    return True


def _detect_purchase(user_uuid: str, messages_chrono: list, me_uuid: str) -> None:
    """Gleicht offene PPV-Angebote per Message-UUID mit gekauften Nachrichten ab.
    Markiert das passende Set als gekauft und setzt Cooldown/Streak zurueck."""
    open_offers = db.open_ppv_offers(user_uuid)
    if not open_offers:
        return
    # Nachrichten-UUID -> purchasedAt (nur bezahlte, gekaufte)
    purchased_map = {}
    for m in messages_chrono:
        if m.get("uuid") and m.get("purchasedAt"):
            purchased_map[m["uuid"]] = m["purchasedAt"]
    any_purchase = False
    for offer in open_offers:
        pa = purchased_map.get(offer["message_uuid"])
        if pa:
            db.mark_ppv_offer_purchased(offer["id"], time.time())
            db.add_ppv_purchased(user_uuid, offer["folder"])
            any_purchase = True
            db.log("info", "send",
                   f"PPV-Kauf erkannt: Set '{offer['folder']}' von {offer['handle'] or user_uuid}",
                   "")
    if any_purchase:
        db.update_ppv_state(
            user_uuid,
            unpurchased_streak=0,
            last_ppv_at=None,  # Cooldown sofort zuruecksetzen
            outbound_since_ppv=int(db.get_setting("ppv_cooldown_outbound", 2)),
        )


def send_due_cycle() -> None:
    """Sendet faellige Auto-Drafts."""
    if not fanvue.is_connected():
        return
    if not _within_active_hours():
        return
    for draft in db.due_auto_drafts(time.time()):
        _send_draft(draft["id"])
        time.sleep(0.5)


def _check_stale_before_send(draft: Any) -> str | None:
    """Direkt vor dem Senden pruefen, ob der Fan zwischenzeitlich geschrieben hat.

    Rueckgabe: Hinweistext, wenn NICHT gesendet werden soll (der Draft wurde
    dann bereits neu eingeplant bzw. markiert), sonst None.
    Netzwerkfehler fuehren bewusst NICHT zum Abbruch - lieber senden als haengen.
    """
    draft_id = draft["id"]
    try:
        messages_chrono = _fetch_history(draft["user_uuid"])
    except Exception:  # noqa: BLE001
        return None
    newer = _newer_fan_messages(draft, messages_chrono, _me_uuid())
    if not newer:
        db.update_draft(draft_id, last_check_at=time.time(), stale_note=None)
        return None

    handle = draft["handle"] or draft["user_uuid"]
    stale = (f"Veraltet beim Senden: {len(newer)} neue Fan-Nachricht"
             f"{'en' if len(newer) != 1 else ''} eingegangen")
    max_regen = int(db.get_setting("draft_max_regen", 10))
    count = draft["regen_count"] or 0
    edited = _draft_is_edited(draft)

    # Senden stoppen; wenn erlaubt, sofort mit dem neuen Verlauf neu generieren.
    db.update_draft(draft_id, scheduled_send_at=None, stale_note=stale)
    if db.get_setting("draft_regen_on_stale", True) and not edited and count < max_regen:
        regenerate_draft(draft_id, reason=stale)
        db.log("info", "send", f"Senden gestoppt, Draft #{draft_id} neu generiert ({handle})", stale)
    else:
        why = ("manuell bearbeitet" if edited else
               f"Limit von {max_regen} Neuversuchen erreicht" if count >= max_regen
               else "Auto-Neugenerierung deaktiviert")
        db.update_draft(draft_id, auto_send=0, stale_note=f"{stale} ({why})")
        db.log("warn", "send",
               f"Senden gestoppt, Draft #{draft_id} veraltet ({handle}) – {why}", stale)
    return stale


def _send_draft(draft_id: int) -> bool:
    """Sendet einen Draft (Auto oder nach Freigabe). Rueckgabe: Erfolg."""
    draft = db.get_draft(draft_id)
    if not draft or draft["status"] not in ("pending", "approved"):
        return False
    # Sicherheitsnetz: eine geplante Reaktivierung NICHT senden, wenn der Fan seit
    # Erstellung des Drafts geschrieben hat (er ist wieder aktiv).
    if _is_reactivation(draft):
        ch = db.get_chat(draft["user_uuid"])
        li = ch["last_inbound_at"] if ch else None
        if li and draft["created_at"] and li > draft["created_at"]:
            db.update_draft(draft_id, status="rejected",
                            error="Fan hat zwischenzeitlich geantwortet")
            db.log("info", "send",
                   f"Reaktivierung verworfen (Fan wieder aktiv) {draft['handle'] or draft['user_uuid']}", "")
            return False
    # Sicherheitsnetz 2: Ist die Antwort noch aktuell? Hat der Fan seit dem Erzeugen
    # weitergeschrieben, wuerde eine Antwort auf die ALTE Nachricht rausgehen.
    # -> Senden abbrechen und mit dem vollstaendigen Verlauf neu generieren.
    if not _is_reactivation(draft) and db.get_setting("draft_recheck_enabled", True):
        stale_note = _check_stale_before_send(draft)
        if stale_note:
            return False

    text = (draft["edited_text"] or draft["generated_text"] or "").strip()
    if not text:
        db.update_draft(draft_id, status="failed", error="Leerer Text")
        return False
    user_uuid = draft["user_uuid"]
    is_ppv = bool(draft["is_ppv"])
    try:
        # Medien anhaengen, sobald welche hinterlegt sind (PPV = bezahlt, sonst gratis)
        media = json.loads(draft["ppv_media_uuids"]) if draft["ppv_media_uuids"] else []
        resp = fanvue.send_message(
            user_uuid, text,
            price_cents=draft["ppv_price_cents"] if is_ppv else None,
            media_uuids=media or None,
            media_preview_uuid=draft["ppv_preview_uuid"] if is_ppv else None,
        )
        db.update_draft(
            draft_id,
            status="sent",
            sent_at=time.time(),
            sent_message_uuid=resp.get("messageUuid", ""),
            error=None,
        )
        # PPV-State pflegen
        if is_ppv:
            state = db.get_ppv_state(user_uuid)
            db.update_ppv_state(
                user_uuid,
                last_ppv_at=time.time(),
                outbound_since_ppv=0,
                unpurchased_streak=(state["unpurchased_streak"] or 0) + 1,
            )
            db.add_ppv_offered(user_uuid, draft["ppv_folder"])
            # Angebot fuer das Conversion-Tracking festhalten
            media = json.loads(draft["ppv_media_uuids"] or "[]")
            db.create_ppv_offer(
                user_uuid, draft["handle"] or "", draft["ppv_folder"],
                draft["ppv_price_cents"] or 0, len(media),
                resp.get("messageUuid", ""),
            )
            db.log("info", "send",
                   f"PPV gesendet an {draft['handle'] or user_uuid} "
                   f"(Set '{draft['ppv_folder']}', ${(draft['ppv_price_cents'] or 0)/100:.2f})",
                   text[:200])
        else:
            state = db.get_ppv_state(user_uuid)
            db.update_ppv_state(user_uuid,
                                outbound_since_ppv=(state["outbound_since_ppv"] or 0) + 1)
            # Reaktivierungs-Selfies merken, damit kein Bild doppelt geschickt wird
            if media and _is_reactivation(draft):
                db.add_reactivation_sent(user_uuid, media)
            db.log("info", "send", f"Gesendet an {draft['handle'] or user_uuid}", text[:200])
        return True
    except fanvue.FanvueError as exc:
        db.update_draft(draft_id, status="failed", error=str(exc))
        db.log("error", "send", f"Senden fehlgeschlagen (Draft #{draft_id})", str(exc))
        return False


def send_draft_now(draft_id: int) -> bool:
    """Von der GUI aufgerufen: sofort senden (Freigabe)."""
    return _send_draft(draft_id)


# --------------------------------------------------- CSV-Kauf-Import (Hintergrund)
_import_lock = threading.Lock()
_import_state: dict[str, Any] = {"running": False, "total": 0, "done": 0, "offers": 0,
                                 "purchased_subs": 0, "matched_folders": 0, "csv_purchased": 0,
                                 "unresolved": 0, "error": None, "started_at": None,
                                 "finished_at": None, "check_api": False}


def import_status() -> dict[str, Any]:
    return dict(_import_state)


def start_purchase_import(rows: list, check_api: bool = True) -> bool:
    """Startet den CSV-Import als Hintergrund-Job. False, wenn schon einer laeuft."""
    with _import_lock:
        if _import_state["running"]:
            return False
        _import_state.update(running=True, total=0, done=0, offers=0, purchased_subs=0,
                             matched_folders=0, csv_purchased=0, unresolved=0, error=None,
                             started_at=time.time(), finished_at=None, check_api=check_api)
    threading.Thread(target=_purchase_import_worker, args=(rows, check_api),
                     name="csv-import", daemon=True).start()
    return True


def _purchase_import_worker(rows: list, check_api: bool) -> None:
    try:
        me_uuid = _me_uuid()
        # Zeilen pro Subscriber gruppieren (handle -> uuid aufloesen)
        by_user: dict[str, dict] = {}
        for r in rows:
            uuid = r.get("user_uuid", "")
            handle = r.get("handle", "")
            if not uuid and handle:
                chat = db.get_chat_by_handle(handle)
                uuid = chat["user_uuid"] if chat else ""
            if not uuid:
                _import_state["unresolved"] += 1
                continue
            by_user.setdefault(uuid, {"handle": handle, "rows": []})["rows"].append(r)
        _import_state["total"] = len(by_user)

        index: dict[str, set] = {}
        if check_api and fanvue.is_connected():
            try:
                index = fanvue.media_folder_index(ppv_only=True)
            except Exception as exc:  # noqa: BLE001
                _import_state["error"] = f"Ordner-Index fehlgeschlagen: {exc}"

        cooldown_out = int(db.get_setting("ppv_cooldown_outbound", 2))
        for uuid, info in by_user.items():
            handle = info["handle"]
            db.upsert_chat(uuid, handle, "")
            # Offers anlegen und in der CSV bereits als gekauft markierte Sets uebernehmen
            csv_folders = set()
            for r in info["rows"]:
                db.create_ppv_offer(uuid, handle, r["folder"], r.get("price_cents", 0), 0, "")
                db.add_ppv_offered(uuid, r["folder"])  # -> wird nicht erneut angeboten
                _import_state["offers"] += 1
                if r.get("purchased"):
                    csv_folders.add(r["folder"])
            for folder in csv_folders:
                db.add_ppv_purchased(uuid, folder)
                db.set_folder_offers_purchased(uuid, folder, True, time.time())
                _import_state["csv_purchased"] += 1
            # Kaufhistorie ueber die API abgleichen
            api_folders = set()
            if check_api and index and fanvue.is_connected():
                try:
                    for p in fanvue.collect_purchased_ppv(uuid, me_uuid):
                        for folder, uuids in index.items():
                            if p["media_uuids"] & uuids:
                                api_folders.add(folder)
                                break
                except Exception:  # noqa: BLE001
                    pass
            for folder in api_folders:
                db.add_ppv_purchased(uuid, folder)
                db.set_folder_offers_purchased(uuid, folder, True, time.time())
                _import_state["matched_folders"] += 1
            if csv_folders or api_folders:
                db.update_ppv_state(uuid, unpurchased_streak=0, last_ppv_at=None,
                                    outbound_since_ppv=cooldown_out)
                _import_state["purchased_subs"] += 1
            _import_state["done"] += 1
            time.sleep(0.15)  # freundlich zur API
        db.log("info", "system",
               f"CSV-Import fertig: {_import_state['offers']} Offers, "
               f"{_import_state['matched_folders']} Käufe via API, "
               f"{_import_state['csv_purchased']} via CSV, {_import_state['unresolved']} ohne Fan")
    except Exception as exc:  # noqa: BLE001
        _import_state["error"] = str(exc)
        db.log("error", "system", "CSV-Import-Job fehlgeschlagen", str(exc))
    finally:
        _import_state["running"] = False
        _import_state["finished_at"] = time.time()


# ------------------------------------------------------------ Reaktivierung
_REACTIVATION_MARK = "(proaktive Reaktivierung)"

# Opener-Vibes fuer Reaktivierungen (nur Beispiele fuer den Ton). Pro Nachricht werden
# einige zufaellig gezogen, damit nicht jede Reaktivierung gleich beginnt. WICHTIG: die
# Beispiele muessen in der SPRACHE DES FANS sein, sonst zieht das Modell in die falsche
# Sprache (deutsche Beispiele bei englischem Fan -> Sprach-Drift).
_REACT_POOL = {
    "de": {
        "greets_name": ["Hey", "Hi", "Na", "Mhm"],
        "greets_noname": ["Hey", "Hi", "Na", "Hey du"],
        "openers": [
            "du warst so still in letzter Zeit...",
            "wo steckst du denn gerade?",
            "bist du online?",
            "ich musste eben an dich denken...",
            "ich hab gerade an unser Gespraech gedacht...",
            "hier war's irgendwie zu ruhig ohne dich...",
            "hast du mich etwa ein bisschen vergessen?",
            "ich wollte dir nur schnell Hallo sagen...",
            "du gehst mir gerade nicht aus dem Kopf...",
            "na, alles gut bei dir?",
            "ich hab mich gefragt, was du so treibst...",
            "mir ist grad so nach dir...",
        ],
    },
    "en": {
        "greets_name": ["Hey", "Hi", "Hey there"],
        "greets_noname": ["Hey", "Hi", "Hey you"],
        "openers": [
            "you've been so quiet lately...",
            "where have you been hiding?",
            "are you around?",
            "I was just thinking about you...",
            "I was just thinking about our chat...",
            "it's been way too quiet without you...",
            "did you forget about me a little?",
            "just wanted to say hi...",
            "you're kind of stuck in my head right now...",
            "hey, how have you been?",
            "I was wondering what you're up to...",
            "I'm kind of in the mood for you right now...",
        ],
    },
    "fr": {
        "greets_name": ["Hey", "Coucou", "Salut"],
        "greets_noname": ["Hey", "Coucou", "Salut"],
        "openers": [
            "tu es bien silencieux ces derniers temps...",
            "où est-ce que tu te caches?",
            "tu es là?",
            "je pensais justement à toi...",
            "je repensais à notre conversation...",
            "c'était trop calme sans toi...",
            "tu m'aurais un peu oubliée?",
            "je voulais juste te dire coucou...",
            "tu me trottes dans la tête là...",
            "alors, tout va bien?",
            "je me demandais ce que tu deviens...",
            "j'ai un peu envie de toi là...",
        ],
    },
}


def _reactivation_examples(greet_name: str, lang: str = "en") -> str:
    """Baut aus dem sprachpassenden Pool ein paar zufaellige Opener-Beispiele."""
    pool = _REACT_POOL.get(lang, _REACT_POOL["en"])
    openers = random.sample(pool["openers"], 3)
    out = []
    for o in openers:
        if greet_name:
            g = random.choice(pool["greets_name"])
            out.append(f"'{g} {greet_name}, {o}'")
        else:
            g = random.choice(pool["greets_noname"])
            out.append(f"'{g}, {o}'")
    return ", ".join(out)


def _is_reactivation(draft: Any) -> bool:
    """True, wenn ein Draft eine (noch nicht gesendete) Reaktivierung ist."""
    try:
        return (draft["incoming_text"] or "") == _REACTIVATION_MARK
    except (KeyError, IndexError, TypeError):
        return False


def reactivation_cycle() -> None:
    """Schreibt inaktive Fans proaktiv wieder an (mit Cooldown)."""
    if not db.get_setting("reactivation_enabled", False):
        return
    if not fanvue.is_connected() or not _within_active_hours():
        return
    now = time.time()
    inactive_s = float(db.get_setting("reactivation_inactive_hours", 14)) * 3600
    cooldown_s = float(db.get_setting("reactivation_cooldown_days", 14)) * 86400
    limit = int(db.get_setting("reactivation_max_per_cycle", 3))
    chats = db.due_reactivation_chats(now, inactive_s, cooldown_s, limit)
    if not chats:
        return
    folder = (db.get_setting("reactivation_folder", "") or "").strip()
    me_uuid = _me_uuid()
    for chat in chats:
        uuid = chat["user_uuid"]
        if db.has_open_draft(uuid):
            continue
        try:
            _create_reactivation_draft(chat, me_uuid, folder)
        except Exception as exc:  # noqa: BLE001
            db.log("error", "generate", f"Reaktivierung fehlgeschlagen ({chat['handle'] or uuid})",
                   str(exc))
        db.update_chat(uuid, last_reactivation_at=now)
        time.sleep(0.4)


def _reactivation_media(folder: str, user_uuid: str) -> list[str]:
    """Waehlt EIN Selfie aus dem Ordner, das dieser Fan noch NICHT bekommen hat.
    Sind alle Bilder des Ordners schon geschickt, wird kein Bild angehaengt (nur Text),
    damit sich kein Bild wiederholt."""
    if not folder:
        return []
    try:
        result = fanvue.list_folder_media(folder, size=50, media_type="image")
    except fanvue.FanvueError:
        return []
    imgs = [m["uuid"] for m in result.get("data", [])
            if m.get("status") == "ready" and m.get("uuid")]
    already = db.reactivation_sent_media(user_uuid)
    remaining = [u for u in imgs if u not in already]
    if not remaining:
        if imgs:
            db.log("info", "generate",
                   f"Reaktivierung: alle {len(imgs)} Selfies aus '{folder}' schon geschickt "
                   f"an {user_uuid} -> nur Text", "")
        return []
    return [random.choice(remaining)]


def _create_reactivation_draft(chat: Any, me_uuid: str, folder: str,
                               manual: bool = False) -> None:
    uuid = chat["user_uuid"]
    handle = chat["handle"] or ""
    system_prompt = chat["persona_override"] or db.get_setting("system_prompt", "")
    fan_notes = chat["notes"] or ""
    # Persoenliche Anrede: NUR ein echter Vorname aus dem Anzeigenamen. NIEMALS das
    # Handle/@Username. Sieht der Anzeigename nach einem Handle aus (Ziffern, -, _, @,
    # sehr lang) oder ist er identisch mit dem Handle, wird KEIN Name verwendet.
    raw_name = (chat["display_name"] or "").strip()
    handle_l = (handle or "").strip().lower()
    looks_real = bool(raw_name and len(raw_name) <= 20
                      and not re.search(r"[\d_\-@]", raw_name)
                      and raw_name.lower() != handle_l)
    greet_name = raw_name if looks_real else ""
    if greet_name:
        name_hint = f" Sprich den Fan dabei DIREKT mit seinem Namen an: '{greet_name}'."
    else:
        name_hint = (" Es ist KEIN echter Vorname bekannt: verwende dann GAR KEINEN Namen "
                     "und keine namentliche Anrede.")
    # Verlauf holen – auch zur Sprach-Erkennung, damit die Beispiel-Opener in der
    # SPRACHE DES FANS sind (sonst zieht das Modell in die falsche Sprache).
    try:
        result = fanvue.list_messages(uuid, size=int(db.get_setting("history_messages", 15)),
                                      mark_as_read=False)
        messages_chrono = list(reversed(result.get("data", [])))
    except fanvue.FanvueError:
        messages_chrono = []
    lang = _fan_language(messages_chrono, me_uuid)
    examples = _reactivation_examples(greet_name, lang)
    # Struktur fest vorgeben: erst begruessen/Opener MIT ANREDE, dann Bezug aufs letzte Thema.
    structure = (
        "DIES IST EINE RE-ENGAGEMENT-NACHRICHT, weil der Fan eine Weile still/offline war. "
        "Setze NICHT einfach die vorherige (Rollenspiel-)Szene fort, als waere keine Zeit "
        "vergangen, und steige NICHT mitten in die Handlung ein. "
        "Verwende NIEMALS den @Username / das Handle / einen Benutzernamen als Anrede oder "
        "im Text. "
        "STRUKTUR (genau so): "
        "1) Beginne mit einer kurzen, warmen persoenlichen BEGRUESSUNG, die auffaengt, "
        "dass der Fan still war oder dass du an ihn denkst." + name_hint +
        " VARIIERE den Einstieg – benutze NICHT jedes Mal dieselbe Formulierung. "
        "Die folgenden sind nur zufaellige Ton-Beispiele (nicht woertlich kopieren, in der "
        "Sprache des Fans): " + examples + ". "
        "2) Danach nimm KURZ und beilaeufig Bezug auf euer letztes Thema, um es wieder "
        "aufleben zu lassen. Kurz, warm, kein Verkauf, keine explizite Fortsetzung der Szene, "
        "keine Standard-Floskel."
    )
    sys = (system_prompt + "\n\n" + db.get_setting("reactivation_prompt", "")
           + "\n\n" + structure)
    banned = []
    if db.get_setting("anti_ai_enabled", True):
        last_out = ""
        for m in reversed(messages_chrono):
            if (m.get("sender") or {}).get("uuid", "") == me_uuid:
                last_out = m.get("text") or ""
                break
        banned = namefilter.extract_banned(last_out, chat["display_name"] or handle)
        # Der Anrede-Name soll in der Reaktivierung ausdruecklich vorkommen duerfen
        if greet_name:
            banned = [b for b in banned if b != greet_name.lower()]
    generated = _generate(sys, messages_chrono, me_uuid, fan_notes, banned)
    media = _reactivation_media(folder, uuid)
    clean_text, note = guardrails.check_outgoing(generated, has_media=bool(media))
    mode = _effective_mode(chat)
    # Manuell ausgeloest -> immer in die Freigabe (kein Delay), damit der Creator
    # die Nachricht sofort pruefen und senden kann.
    auto = (not manual) and (mode == "auto") and not note
    scheduled = None
    if auto:
        # eigener, laengerer Zufalls-Delay fuer Reaktivierungen (Minuten)
        dmin = int(db.get_setting("reactivation_delay_min_minutes", 15))
        dmax = int(db.get_setting("reactivation_delay_max_minutes", 45))
        lo, hi = (min(dmin, dmax), max(dmin, dmax))
        scheduled = time.time() + random.randint(lo * 60, max(hi, lo) * 60)
    tag = "Reaktivierung (manuell)" if manual else "Reaktivierung"
    draft_id = db.create_draft(
        user_uuid=uuid, handle=handle, display_name=chat["display_name"],
        incoming_text=_REACTIVATION_MARK, generated_text=generated,
        edited_text=clean_text, status="pending", auto_send=1 if auto else 0,
        scheduled_send_at=scheduled,
        guardrail_note=(tag + (" | " + note if note else "")),
        model=db.get_setting("openrouter_model", ""),
        ppv_media_uuids=json.dumps(media) if media else None,
    )
    db.log("info", "generate",
           f"Reaktivierungs-Draft #{draft_id} fuer {handle or uuid} "
           f"({'manuell/freigabe' if manual else ('auto' if auto else 'freigabe')}"
           f"{', +Bild' if media else ''})", "")


_next_prio2_at = 0.0


def _prio2_cycle_if_due() -> None:
    """Bearbeitet die Prio-2-Gruppe seltener (Intervall + Zufalls-Jitter)."""
    global _next_prio2_at
    prio2_list = db.get_setting("prio2_custom_list_id", "")
    if not prio2_list:
        return
    now = time.time()
    if now < _next_prio2_at:
        return
    poll_cycle(custom_list_id=prio2_list)
    interval = float(db.get_setting("prio2_interval_minutes", 30)) * 60
    jitter_max = float(db.get_setting("prio2_jitter_minutes", 30))
    jitter = random.randint(60, max(60, int(jitter_max * 60)))
    _next_prio2_at = time.time() + interval + jitter
    db.log("info", "poll", f"Prio-2-Gruppe bearbeitet · naechster Lauf in "
           f"{int((interval + jitter)/60)} Min", "")


# ------------------------------------------- Selbstheilung wartender Drafts
# Zwei Probleme, die Drafts in der Freigabe-Queue haengen lassen:
#   1. Der Fan hat inzwischen weitergeschrieben -> die Antwort passt nicht mehr.
#   2. Das Modell hat nichts/Unbrauchbares geliefert (leer, fremde Schrift,
#      Weigerung) -> der Draft ist unsendbar und bleibt einfach liegen.
# recheck_pending_cycle() laeuft regelmaessig ueber alle wartenden Drafts und
# behebt beides, begrenzt durch draft_max_regen.

# Guardrail-Notizen, die sich durch eine Neugenerierung heilen LASSEN.
_REGENERABLE_NOTES = (
    "leere antwort",
    "fremdsprachige schriftzeichen",
    "modell-weigerung",
    "erfundener plattform",
    "zeit-widerspruch",
)
# Diese Notiz braucht menschliche Augen - nie automatisch neu generieren.
_HUMAN_ONLY_NOTES = ("eskalations-stichwort",)

# Guardrails, die einen Entwurf blockieren, aber NICHT durch eine
# Neugenerierung heilbar sind - hier hilft nur Handarbeit.
_MANUAL_ONLY_NOTES = (
    "verbotenes wort",
    "erfundene preisangabe",
    "kündigt",          # "Kuendigt ... an, es haengt aber kein Content an"
    "kuendigt",
)

# Alles, was ueberhaupt eine Blockade darstellt. Notizen wie "PPV · Bildanfrage
# ..." oder "Reaktivierung" sind dagegen rein informativ - solche Entwuerfe
# warten normal auf die Freigabe und sind NICHT festgefahren.
_BLOCKING_NOTES = _REGENERABLE_NOTES + _HUMAN_ONLY_NOTES + _MANUAL_ONLY_NOTES


def _stuck_reason(draft: Any, max_regen: int) -> Optional[str]:
    """Warum kommt dieser Entwurf ohne Zutun nicht mehr weiter?

    Rueckgabe: Klartext-Grund, oder None wenn er sich noch selbst loest bzw.
    ganz normal auf die Freigabe wartet.

    Wichtig: Die Meldung darf NICHT allein am erreichten Neuversuch-Limit
    haengen. Blockaden, die gar nicht erst wiederholt werden (Eskalation,
    verbotenes Wort, erfundener Preis), erreichen dieses Limit nie - und
    blieben damit fuer immer stumm in der Queue liegen.
    """
    note = (draft["guardrail_note"] or "").strip()
    low = note.lower()

    if any(k in low for k in _HUMAN_ONLY_NOTES):
        return "Eskalations-Stichwort – wird bewusst nie automatisch beantwortet"
    if any(k in low for k in _MANUAL_ONLY_NOTES):
        return note
    if _is_broken(draft):
        if (draft["regen_count"] or 0) >= max_regen:
            return f"{max_regen} automatische Neuversuche ohne Erfolg"
        return None                      # wird noch wiederholt
    if any(k in low for k in _BLOCKING_NOTES):
        return note                      # blockiert, aber nicht wiederholbar
    return None                          # informative Notiz oder gar keine


def _is_broken(draft: Any) -> bool:
    """Draft unsendbar, aber durch Neugenerierung reparierbar?"""
    note = (draft["guardrail_note"] or "").lower()
    if any(k in note for k in _HUMAN_ONLY_NOTES):
        return False
    if not (draft["generated_text"] or "").strip():
        return True
    return any(k in note for k in _REGENERABLE_NOTES)


def _draft_is_edited(draft: Any) -> bool:
    """Hat der Mensch den Text angefasst? Dann nie ueberschreiben."""
    try:
        if draft["user_edited"]:
            return True
    except (IndexError, KeyError):  # Spalte fehlt in sehr alten DBs
        pass
    return False


def _fetch_history(user_uuid: str) -> list:
    """Aktueller Chatverlauf (alt -> neu) direkt aus Fanvue."""
    history_n = int(db.get_setting("history_messages", 15))
    result = fanvue.list_messages(user_uuid, size=max(history_n, 5), mark_as_read=False)
    return list(reversed(result.get("data", [])))


def _newer_fan_messages(draft: Any, messages_chrono: list, me_uuid: str) -> list:
    """Fan-Nachrichten, die NACH der vom Draft beantworteten Nachricht kamen.

    Ohne gespeicherte inbound_uuid (Drafts aus der Zeit vor diesem Feature)
    wird auf einen Textvergleich mit der letzten Fan-Nachricht ausgewichen.
    """
    if not messages_chrono:
        return []
    anchor = None
    try:
        anchor = draft["inbound_uuid"]
    except (IndexError, KeyError):
        anchor = None

    if anchor:
        idx = next((i for i, m in enumerate(messages_chrono)
                    if m.get("uuid") == anchor), None)
        if idx is None:
            return []  # Anker nicht im geladenen Fenster -> keine Aussage moeglich
        tail = messages_chrono[idx + 1:]
    else:
        # Fallback: letzte Fan-Nachricht mit incoming_text vergleichen
        old = (draft["incoming_text"] or "").strip()
        fan_msgs = [m for m in messages_chrono
                    if (m.get("sender") or {}).get("uuid", "") != me_uuid]
        if not fan_msgs or not old:
            return []
        idx = next((i for i, m in enumerate(messages_chrono)
                    if (m.get("text") or "").strip() == old
                    and (m.get("sender") or {}).get("uuid", "") != me_uuid), None)
        if idx is None:
            return []
        tail = messages_chrono[idx + 1:]

    return [m for m in tail if (m.get("sender") or {}).get("uuid", "") != me_uuid]


def regenerate_draft(draft_id: int, reason: str = "", interactive: bool = False) -> bool:
    """Erzeugt den Text eines wartenden Drafts neu - mit FRISCHEM Verlauf.

    Nutzt bewusst denselben _generate()-Pfad wie die Erstgenerierung, damit
    Anti-AI-Regeln, Namens-Filter und Sprach-Anker auch hier greifen.
    Ein PPV-Anhang bleibt erhalten; nur die Bildunterschrift wird neu getextet.
    interactive=True -> keine Wartepausen zwischen Modell-Versuchen (GUI).
    Rueckgabe: True, wenn brauchbarer Text erzeugt wurde.
    """
    draft = db.get_draft(draft_id)
    if not draft or draft["status"] != "pending":
        return False

    user_uuid = draft["user_uuid"]
    handle = draft["handle"] or user_uuid
    me_uuid = _me_uuid()
    now = time.time()

    try:
        messages_chrono = _fetch_history(user_uuid)
    except fanvue.FanvueError as exc:
        db.log("error", "generate", f"Regenerate: Verlauf nicht ladbar ({handle})", str(exc))
        db.update_draft(draft_id, last_check_at=now)
        return False
    if not messages_chrono:
        db.update_draft(draft_id, last_check_at=now)
        return False

    chat = db.get_chat(user_uuid)
    system_prompt = db.get_setting("system_prompt", "")
    fan_notes = ""
    if chat:
        if chat["persona_override"]:
            system_prompt = chat["persona_override"]
        fan_notes = chat["notes"] or ""

    banned: list[str] = []
    if db.get_setting("anti_ai_enabled", True):
        last_out = ""
        for m in reversed(messages_chrono):
            if (m.get("sender") or {}).get("uuid", "") == me_uuid:
                last_out = m.get("text") or ""
                break
        banned = namefilter.extract_banned(
            last_out, draft["display_name"] or draft["handle"] or "")

    is_ppv = bool(draft["is_ppv"])
    task = "caption" if is_ppv else "chat"
    if is_ppv:
        system_prompt = (f"{system_prompt}\n\n{db.get_setting('ppv_sales_prompt', '')}\n\n"
                         "Der Content haengt der Nachricht bereits als kaufbarer Anhang an. "
                         "Schreibe NUR eine kurze Bildunterschrift (1-2 Saetze). Keine "
                         "Preisangabe, keine Platzhalter, keine Regieanweisungen.")

    # Letzte Fan-Nachricht als neuer Bezugspunkt
    last_msg = messages_chrono[-1]
    last_fan = next((m for m in reversed(messages_chrono)
                     if (m.get("sender") or {}).get("uuid", "") != me_uuid), None)

    try:
        generated = _generate(system_prompt, messages_chrono, me_uuid, fan_notes, banned,
                              task=task, retry_delay=0 if interactive else None)
    except Exception as exc:  # noqa: BLE001 - nie den Zyklus abbrechen
        db.log("error", "generate", f"Regenerate fehlgeschlagen ({handle})", str(exc))
        db.update_draft(draft_id, last_check_at=now, last_regen_at=now,
                        regen_count=(draft["regen_count"] or 0) + 1)
        return False

    if is_ppv:
        generated = re.sub(r"(?i)\bsets?\b", "PPV", generated)
    clean_text, note = guardrails.check_outgoing(generated, has_media=is_ppv)
    count = (draft["regen_count"] or 0) + 1
    ok = bool((generated or "").strip()) and not note

    fields: dict[str, Any] = {
        "generated_text": generated,
        "guardrail_note": note,
        "regen_count": count,
        "last_regen_at": now,
        "last_check_at": now,
        "stale_note": None,
        "error": None,
    }
    # Wieder brauchbar? Dann die Melde-Sperre loesen, damit ein spaeterer
    # erneuter Blocker wieder auf dem Handy landet.
    if ok:
        fields["notified_at"] = None
    # Bezugspunkt mitziehen, damit der Draft nicht sofort wieder als veraltet gilt
    if last_fan is not None:
        fields["incoming_text"] = (last_fan.get("text") or "").strip()
    fields["inbound_uuid"] = last_msg.get("uuid")
    # Bearbeiteten Text NIE ueberschreiben (Nutzerentscheidung)
    if not _draft_is_edited(draft):
        fields["edited_text"] = clean_text

    # Auto-Send NEU bewerten statt den alten Wert zu uebernehmen.
    #
    # Grund: Schlaegt bei der ERSTgenerierung ein Guardrail an, wird auto_send
    # auf 0 gesetzt. Wuerde man das hier nur fortschreiben, bliebe ein Entwurf
    # dauerhaft in der Freigabe haengen - auch wenn der zweite Versuch
    # einwandfrei ist. Ein misslungener erster Anlauf soll die Antwort aber
    # nicht fuer immer aufhalten.
    #
    # Zwei Faelle bleiben bewusst in der Freigabe:
    #   - Eskalations-Stichwort: braucht menschliche Augen, egal wie sauber
    #     der Text aussieht.
    #   - Vom Menschen bearbeitet: wer den Text angefasst hat, will ihn auch
    #     selbst freigeben.
    #   - Manuell angestossen: wer in der Queue auf "Neu generieren" klickt,
    #     sitzt gerade davor und will den Text ansehen, nicht 30 Sekunden
    #     spaeter ueberrascht werden.
    escalated = any(k in (draft["guardrail_note"] or "").lower()
                    for k in _HUMAN_ONLY_NOTES)
    if (ok and not interactive and _effective_mode(chat) == "auto"
            and not escalated and not _draft_is_edited(draft)):
        delay_min = int(db.get_setting("send_delay_min_seconds", 20))
        delay_max = int(db.get_setting("send_delay_max_seconds", 90))
        fields["auto_send"] = 1
        fields["scheduled_send_at"] = now + random.randint(
            min(delay_min, delay_max), max(delay_min, delay_max))

    db.update_draft(draft_id, **fields)
    db.log("info" if ok else "warn", "generate",
           f"Draft #{draft_id} neu generiert (Versuch {count}) für {handle}"
           + ("" if ok else f" – weiterhin blockiert: {note}"),
           reason)
    return ok


def recheck_pending_cycle() -> None:
    """Prueft wartende Drafts auf Aktualitaet und repariert kaputte.

    Laeuft im normalen Poll-Loop, arbeitet aber nur Drafts ab, deren letzte
    Pruefung laenger als draft_recheck_interval_seconds zurueckliegt.
    """
    if not db.get_setting("draft_recheck_enabled", True):
        return
    if not fanvue.is_connected():
        return

    interval = float(db.get_setting("draft_recheck_interval_seconds", 180))
    max_regen = int(db.get_setting("draft_max_regen", 10))
    regen_on_stale = bool(db.get_setting("draft_regen_on_stale", True))
    me_uuid = _me_uuid()
    now = time.time()

    for draft in db.drafts_due_for_recheck(now, interval):
        draft_id = draft["id"]
        handle = draft["handle"] or draft["user_uuid"]
        count = draft["regen_count"] or 0
        try:
            messages_chrono = _fetch_history(draft["user_uuid"])
        except fanvue.FanvueError as exc:
            db.update_draft(draft_id, last_check_at=time.time())
            db.log("warn", "poll", f"Recheck: Verlauf nicht ladbar ({handle})", str(exc))
            continue

        newer = _newer_fan_messages(draft, messages_chrono, me_uuid)
        broken = _is_broken(draft)

        # --- Fall 1: veraltet (Fan hat weitergeschrieben) ---
        if newer:
            preview = " | ".join((m.get("text") or "").strip()[:80]
                                 for m in newer if (m.get("text") or "").strip())
            stale = (f"Veraltet: {len(newer)} neue Fan-Nachricht"
                     f"{'en' if len(newer) != 1 else ''} seit dieser Antwort"
                     + (f" – „{preview[:200]}“" if preview else ""))
            edited = _draft_is_edited(draft)
            if regen_on_stale and not edited and count < max_regen:
                # Auto-Send stoppen, solange neu generiert wird
                db.update_draft(draft_id, scheduled_send_at=None, stale_note=stale)
                regenerate_draft(draft_id, reason=stale)
            else:
                why = ("manuell bearbeitet – bitte selbst entscheiden" if edited else
                       f"Limit von {max_regen} Neuversuchen erreicht" if count >= max_regen
                       else "Auto-Neugenerierung deaktiviert")
                db.update_draft(draft_id, last_check_at=time.time(),
                                scheduled_send_at=None,
                                stale_note=f"{stale} ({why})")
                db.log("info", "poll", f"Draft #{draft_id} veraltet ({handle}): {why}", stale)
            time.sleep(0.4)
            continue

        # --- Fall 2: reparierbar kaputt -> neuen Versuch starten ---
        if broken and count < max_regen:
            regenerate_draft(draft_id, reason=f"Auto-Retry: {draft['guardrail_note'] or 'leerer Text'}")
            time.sleep(0.4)
            continue

        # --- Fall 3: festgefahren? ---
        # Bewusst NICHT nur bei erreichtem Neuversuch-Limit: Blockaden, die gar
        # nicht wiederholt werden (Eskalation, verbotenes Wort, erfundener
        # Preis), erreichen dieses Limit nie und blieben sonst stumm liegen.
        grund = _stuck_reason(draft, max_regen)
        db.update_draft(draft_id, last_check_at=time.time(),
                        **({} if newer else {"stale_note": None}))
        if grund and not draft["notified_at"]:
            # Genau einmal melden: notified_at ist der Riegel dagegen, dass
            # derselbe Draft alle 3 Minuten erneut aufs Handy kommt.
            db.update_draft(draft_id, notified_at=time.time(), error=grund)
            db.log("warn", "generate",
                   f"Draft #{draft_id} ({handle}) hängt fest: {grund}",
                   draft["guardrail_note"] or "")
            try:
                notify.notify_blocked_draft(db.get_draft(draft_id), max_regen, grund)
            except Exception as exc:  # noqa: BLE001 - nie den Zyklus abbrechen
                db.log("error", "notify", "Telegram-Meldung fehlgeschlagen", str(exc))


# ------------------------------------------------------------- Update-Pruefung
def update_check_cycle() -> None:
    """Prueft in grossem Abstand, ob eine neue Version veroeffentlicht wurde.

    Laeuft im normalen Loop mit, drosselt sich aber selbst ueber den
    Zeitstempel der letzten Pruefung (Standard: alle 6 Stunden).
    """
    try:
        if not updater.is_due():
            return
        before = updater.cached_state().get("latest")
        state = updater.check(fetch=True)
        if state.get("error"):
            db.log("warn", "update", "Update-Pruefung fehlgeschlagen", str(state["error"])[:300])
            return
        if not state.get("update_available"):
            return
        latest = state.get("latest")
        db.log("info", "update",
               f"Neue Version verfuegbar: {latest} (installiert: {state.get('current')})",
               " · ".join(state.get("changelog", [])[:5]))
        # Nur beim ERSTEN Auftauchen melden, nicht bei jeder Pruefung erneut
        if latest and latest != before and db.get_setting("update_notify_telegram", True):
            _notify_update(state)
    except Exception as exc:  # noqa: BLE001 - nie den Loop abbrechen
        db.log("error", "update", "Update-Pruefung abgebrochen", str(exc))


def _notify_update(state: dict) -> None:
    import html as _html
    lines = [
        "🆕 <b>Neue Version verfügbar</b>",
        "",
        f"Installiert: <b>{_html.escape(str(state.get('current', '?')))}</b>",
        f"Verfügbar:  <b>{_html.escape(str(state.get('latest', '?')))}</b>",
    ]
    changes = state.get("changelog") or []
    if changes:
        lines.append("\n<b>Änderungen:</b>")
        lines += [f"• {_html.escape(c)}" for c in changes[:8]]
        if len(changes) > 8:
            lines.append(f"… und {len(changes) - 8} weitere")
    lines.append("\nInstallieren über den Knopf auf dem Dashboard.")
    notify.send("\n".join(lines))


# ---------------------------------------------------------------- Loop-Steuerung
def _loop() -> None:
    """Hauptschleife des Workers.

    ALLES steht im Schutzbereich - auch das Lesen des Intervalls und das
    Warten. Frueher lagen beide ausserhalb: warf `db.get_setting` (etwa bei
    kurzzeitig gesperrter SQLite-Datei) oder das Logging im Fehlerzweig, verliess
    die Schleife den Thread lautlos. Der Bot stand dann still, waehrend das
    Dashboard weiter "laeuft" anzeigte.
    """
    while not _stop.is_set():
        interval = 60.0
        try:
            if db.get_setting("bot_running", False):
                if _within_active_hours():
                    poll_cycle()  # Prio 1: jeder Zyklus
                    _prio2_cycle_if_due()
                    reactivation_cycle()
                    # Wartende Drafts pflegen (veraltet? kaputt?) - drosselt sich
                    # selbst ueber last_check_at, laeuft also faktisch alle 3 Min.
                    recheck_pending_cycle()
                send_due_cycle()  # faellige Sends auch ausserhalb aktiver Zeiten? -> nein, geprueft drin
                # Unabhaengig von den aktiven Zeiten und vom Fanvue-Login:
                # betrifft nur das Programm selbst, nicht den Chatbetrieb.
                update_check_cycle()
                _status["last_error"] = None
            _status["last_poll"] = time.time()
            _status["cycles"] += 1
        except fanvue.NotAuthenticated as exc:
            _status["last_error"] = str(exc)
        except BaseException as exc:  # noqa: BLE001 - der Thread darf NIE sterben
            _status["last_error"] = str(exc)
            try:
                db.log("error", "system", "Poller-Fehler", str(exc))
            except Exception:  # noqa: BLE001 - Logging darf nicht mitreissen
                pass
        # Intervall lesen und warten - ebenfalls abgesichert
        try:
            interval = max(15.0, float(db.get_setting("poll_interval_seconds", 60)))
        except Exception:  # noqa: BLE001
            interval = 60.0
        try:
            _stop.wait(interval)
        except Exception:  # noqa: BLE001
            time.sleep(interval)
    _status["stopped_at"] = time.time()


# ------------------------------------------------------------------- Waechter
# Selbst eine sturzsichere Schleife hilft nicht gegen einen Thread, der aus
# anderen Gruenden verschwindet. Der Waechter ist bewusst winzig: er prueft nur,
# ob der Worker noch lebt, und holt ihn zurueck.
_watchdog: threading.Thread | None = None
_WATCHDOG_INTERVAL = 60.0


def is_alive() -> bool:
    return bool(_thread and _thread.is_alive())


def _watchdog_loop() -> None:
    while not _stop.is_set():
        try:
            if not _stop.is_set() and not is_alive():
                db.log("error", "system",
                       "Worker war gestoppt – wird automatisch neu gestartet",
                       f"letzter Durchlauf: {_status.get('last_poll')} · "
                       f"letzter Fehler: {_status.get('last_error')}")
                _status["restarts"] = int(_status.get("restarts", 0)) + 1
                _start_thread()
                try:
                    from . import notify
                    notify.send(
                        "⚠️ <b>Bot-Worker war stehengeblieben</b>\n\n"
                        "Er wurde automatisch neu gestartet. Nachrichten in der "
                        "Zwischenzeit werden jetzt nachgeholt.")
                except Exception:  # noqa: BLE001
                    pass
        except BaseException:  # noqa: BLE001 - der Waechter erst recht nicht
            pass
        _stop.wait(_WATCHDOG_INTERVAL)


def _start_thread() -> None:
    global _thread
    _thread = threading.Thread(target=_loop, name="fanvue-poller", daemon=True)
    _thread.start()


def start() -> None:
    global _watchdog
    if is_alive():
        return
    _stop.clear()
    _start_thread()
    if not (_watchdog and _watchdog.is_alive()):
        _watchdog = threading.Thread(target=_watchdog_loop, name="fanvue-watchdog",
                                     daemon=True)
        _watchdog.start()
    db.log("info", "system", "Worker gestartet")


def stop() -> None:
    _stop.set()
    db.log("info", "system", "Worker gestoppt")
