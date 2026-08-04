"""OpenRouter-Anbindung fuer die Antwort-Generierung.

OpenRouter ist OpenAI-kompatibel: POST /api/v1/chat/completions.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from . import db, persona_context

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(Exception):
    pass


def build_messages(system_prompt: str, history: list[dict[str, Any]], me_uuid: str,
                   fan_notes: str = "") -> list[dict[str, str]]:
    """Baut die OpenAI-kompatible messages-Liste aus dem Chatverlauf.

    history: Liste von Fanvue-Nachrichten (alt -> neu), jeweils mit
             sender.uuid und text. Eigene Nachrichten -> assistant, Fan -> user.

    Zusaetzlich wird - falls aktiviert - der aktuelle Zeit-/Situationskontext
    ans Ende des System-Prompts gehaengt (siehe persona_context). Ohne diesen
    Block hat das Modell keinerlei Zeitinformation und raet die Tageszeit.
    """
    messages: list[dict[str, str]] = []
    sys = system_prompt
    if fan_notes.strip():
        sys += f"\n\nWichtige Fakten ueber diesen Fan (beachten):\n{fan_notes.strip()}"

    # Zeitmarken im Verlauf nur, wenn aktiviert UND die API Zeitstempel liefert
    with_stamps = bool(db.get_setting("timestamps_in_history", True))
    now = persona_context.now_local()

    if with_stamps:
        sys += ("\n\nIm Verlauf stehen vor den Fan-Nachrichten eckige Zeitmarken wie "
                "[vor 2 Std.] oder [gestern 21:14]. Sie zeigen, wie lange die Nachricht "
                "her ist. Beruecksichtige sie (z.B. keine Begruessung wie am Morgen, "
                "wenn die Nachricht von gestern Abend stammt), gib sie aber NIEMALS in "
                "deiner Antwort aus.")

    # Zeit-/Situationskontext ganz ans Ende - je naeher am Gespraech, desto
    # zuverlaessiger haelt sich das Modell daran.
    context_block = persona_context.build_context_block(now)
    if context_block:
        sys += "\n\n" + context_block

    messages.append({"role": "system", "content": sys})

    for msg in history:
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        sender_uuid = (msg.get("sender") or {}).get("uuid", "")
        role = "assistant" if sender_uuid == me_uuid else "user"
        if with_stamps and role == "user":
            stamp = persona_context.relative_time(
                persona_context.message_timestamp(msg), now)
            if stamp:
                text = f"[{stamp}] {text}"
        messages.append({"role": role, "content": text})
    return messages


def resolve_model(task: str) -> tuple[str, str]:
    """Liefert (Modell, API-Key) fuer eine Aufgabe. Leere Aufgaben-Modelle fallen
    auf das Standard-Chat-Modell zurueck, damit nichts konfiguriert werden MUSS.
    Aufgaben: 'chat', 'caption', 'classifier', 'vision'."""
    okey = db.get_setting("openrouter_api_key", "")
    base = db.get_setting("openrouter_model", "openai/gpt-4o-mini")
    if task == "classifier":
        return (db.get_setting("classifier_model", "") or base, okey)
    if task == "caption":
        m = db.get_setting("ppv_caption_model", "")
        if m:
            return (m, okey)
        # Rueckwaertskompatibel: alte Vision-Umleitung fuer Captions
        if db.get_setting("ppv_caption_use_vision", False):
            return (db.get_setting("vision_model", "openai/gpt-4o-mini"),
                    db.get_setting("vision_api_key", "") or okey)
        return (base, okey)
    if task == "vision":
        return (db.get_setting("vision_model", "openai/gpt-4o-mini"),
                db.get_setting("vision_api_key", "") or okey)
    return (base, okey)  # 'chat'


def _record_cost(data: dict, model: str, category: str) -> None:
    """Schreibt die von OpenRouter gemeldeten Kosten (usage.cost, USD) in die DB."""
    try:
        cost = ((data or {}).get("usage") or {}).get("cost")
        if cost:
            db.add_api_cost(float(cost), model, category)
    except (TypeError, ValueError, AttributeError):
        pass


def generate(messages: list[dict[str, str]], model: str = "", api_key: str = "",
             category: str = "chat") -> str:
    """Generiert Text. Ohne Angabe wird das Standard-Chat-Modell + OpenRouter-Key
    verwendet; model/api_key koennen pro Aufgabe ueberschrieben werden."""
    api_key = api_key or db.get_setting("openrouter_api_key", "")
    model = model or db.get_setting("openrouter_model", "openai/gpt-4o-mini")
    if not api_key:
        raise OpenRouterError("Kein OpenRouter-API-Key hinterlegt")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": float(db.get_setting("temperature", 0.9)),
        "max_tokens": int(db.get_setting("max_tokens", 300)),
        "usage": {"include": True},   # OpenRouter liefert dann usage.cost mit
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # Von OpenRouter empfohlene (optionale) Attribution-Header:
        "HTTP-Referer": "http://localhost",
        "X-Title": "Fanvue Chatbot",
    }
    # Netzwerkfehler (Timeout, SSL-Handshake, DNS ...) als OpenRouterError
    # kapseln. Sonst fliegen sie an generate_retry vorbei, das nur
    # OpenRouterError abfaengt - ein voruebergehender Timeout fuehrte dann zu
    # GAR KEINEM Wiederholungsversuch, und die Fan-Nachricht blieb unbeantwortet.
    try:
        resp = httpx.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
    except Exception as exc:  # noqa: BLE001 - httpx.TimeoutException, SSLError, ...
        raise OpenRouterError(f"Netzwerkfehler bei OpenRouter: {exc}") from exc
    if resp.status_code != 200:
        raise OpenRouterError(f"OpenRouter-Fehler [{resp.status_code}]: {resp.text}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise OpenRouterError(f"Antwort war kein JSON: {resp.text[:200]}") from exc
    _record_cost(data, model, category)
    # _extract_content behandelt content=None (Reasoning-Modelle), Listen-Content
    # und Refusals robust – statt hart .strip() auf evtl. None aufzurufen.
    text = _extract_content(data)
    _log_if_suspicious(data, text, model, category)
    return text


def _log_if_suspicious(data: dict, text: str, model: str, category: str) -> None:
    """Protokolliert die Rohantwort, wenn das Ergebnis unbrauchbar aussieht.

    Ohne das laesst sich hinterher nicht mehr sagen, WARUM eine Antwort leer
    oder nur ein Wortfetzen war: brach das Modell am Token-Limit ab, hat es
    gefiltert, oder kam wirklich nur das zurueck? Genau diese Frage stand bei
    Entwuerfen wie 'dilemma (1)' oder 'Mmm,' im Raum.
    """
    stripped = (text or "").strip()
    if len(stripped) >= 25:
        return
    try:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        info = {
            "finish_reason": choice.get("finish_reason"),
            "native_finish_reason": choice.get("native_finish_reason"),
            "laenge": len(stripped),
            "text": stripped[:120],
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            # Denk-Tokens zaehlen als Output und gehen von max_tokens ab
            "reasoning_tokens": details.get("reasoning_tokens"),
            "reasoning_feld": len(str(msg.get("reasoning") or "")),
            "max_tokens": int(db.get_setting("max_tokens", 300)),
            "modell": model,
        }
        db.log("warn", "generate",
               f"Auffällig kurze Antwort ({len(stripped)} Zeichen) von {model}",
               " · ".join(f"{k}={v}" for k, v in info.items() if v not in (None, "")))
    except Exception:  # noqa: BLE001 - Diagnose darf nie etwas kaputt machen
        pass


def generate_retry(messages: list[dict[str, str]], model: str = "", api_key: str = "",
                   attempts: int = 3, delay_seconds: float = 0.0,
                   category: str = "chat") -> str:
    """Wie generate(), wiederholt aber bei LEERER Antwort (Reasoning-Modelle liefern
    manchmal nichts) bis zu `attempts` Mal. Zwischen den Versuchen wird `delay_seconds`
    gewartet (0 = keine Pause; sinnvoll nur im Hintergrundbetrieb). Gibt "" zurueck,
    wenn alle Versuche leer bleiben – der Aufrufer leitet dann zur manuellen Freigabe."""
    attempts = max(1, attempts)
    for i in range(attempts):
        netzfehler = False
        try:
            text = generate(messages, model=model, api_key=api_key, category=category)
        except OpenRouterError as exc:
            text = ""
            netzfehler = "Netzwerkfehler" in str(exc)
            db.log("warn", "generate", f"Generierung-Versuch {i + 1}/{attempts} fehlgeschlagen",
                   str(exc)[:200])
        if text and text.strip():
            return text
        if i < attempts - 1:
            # Zwei sehr verschiedene Faelle:
            #  - LEERE Antwort: das Modell hat geantwortet, nur ohne Inhalt
            #    (Reasoning-Modelle). Da lohnt eine laengere Pause.
            #  - NETZWERKFEHLER: jeder Versuch laeuft schon bis zu 60s ins
            #    Timeout. Mit der langen Pause wuerde ein einziger haengender
            #    Chat den ganzen Poll-Zyklus minutenlang blockieren und alle
            #    anderen Fans warten lassen. Deshalb kurz antesten und sonst
            #    aufgeben - der Recheck-Zyklus versucht es spaeter erneut,
            #    ohne den Betrieb aufzuhalten.
            if netzfehler:
                if i >= 1:
                    db.log("warn", "generate",
                           "Netzwerk weiterhin gestört – Entwurf geht zur späteren "
                           "Wiederholung in die Queue", "")
                    break
                wait = 2.0
            else:
                wait = max(0.0, delay_seconds)
            db.log("info", "generate",
                   f"{'Netzwerkfehler' if netzfehler else 'Leere Antwort'} – "
                   f"neuer Versuch {i + 2}/{attempts}" + (f" in {int(wait)}s" if wait >= 1 else ""), "")
            if wait:
                time.sleep(wait)
    return ""


def analyze_image(image_url: str, instruction: str = "") -> list[str]:
    """Laesst ein multimodales Modell das Bild beschreiben und gibt Tags zurueck.
    Nutzt einen eigenen Vision-API-Key, falls hinterlegt, sonst den Haupt-Key."""
    api_key = db.get_setting("vision_api_key", "") or db.get_setting("openrouter_api_key", "")
    if not api_key:
        raise OpenRouterError("Kein OpenRouter-API-Key hinterlegt")
    model = db.get_setting("vision_model", "openai/gpt-4o-mini")
    instruction = instruction or db.get_setting("vision_prompt", "")
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }],
        "temperature": 0.3,
        "max_tokens": 200,
        "usage": {"include": True},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",
        "X-Title": "Fanvue Chatbot",
    }
    resp = httpx.post(OPENROUTER_URL, headers=headers, json=payload, timeout=90)
    if resp.status_code != 200:
        raise OpenRouterError(f"Bildanalyse-Fehler [{resp.status_code}]: {resp.text}")
    data = resp.json()
    _record_cost(data, model, "vision")
    return parse_tags(_extract_content(data))


def analyze_incoming_image(image_url: str) -> dict:
    """Analysiert ein vom Fan geschicktes Bild.
    Rueckgabe: {"woman": bool, "nude": bool, "penis": bool, "description": str}.
    - woman = erwachsene weibliche Person prominent im Bild (dann kann der Bot nicht
      wissen, ob es die Creatorin selbst ist -> vorsichtig antworten).
    - nude  = nackte/explizit sexuelle weibliche Darstellung (Brueste/Genital sichtbar).
    - penis = ein Penis ist zu sehen.
    nude/penis gelten als Kaufsignal (PPV). Bei Fehlern: woman=True (vorsichtige
    Default-Annahme), nude/penis=False, leere Beschreibung."""
    import json as _json
    api_key = db.get_setting("vision_api_key", "") or db.get_setting("openrouter_api_key", "")
    if not api_key or not image_url:
        return {"woman": True, "nude": False, "penis": False, "description": ""}
    model = db.get_setting("vision_model", "openai/gpt-4o-mini")
    instruction = (
        "Du bekommst ein Bild, das ein Fan an eine Creatorin einer Adult-Plattform geschickt hat. "
        "Antworte AUSSCHLIESSLICH mit kompaktem JSON, ohne Erklaerung, im Format: "
        '{"woman": true|false, "nude": true|false, "penis": true|false, "description": "..."}. '
        "woman=true, wenn eine erwachsene weibliche Person deutlich/prominent im Bild zu sehen "
        "ist (Gesicht oder Koerper), sonst false. "
        "nude=true, wenn eine weibliche Person nackt oder explizit sexuell dargestellt ist "
        "(nackte Brueste oder Genitalien sichtbar), sonst false. "
        "penis=true, wenn ein Penis zu sehen ist, sonst false. "
        "description = eine kurze, sachliche Beschreibung des Bildinhalts auf DEUTSCH (1 Satz)."
    )
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }],
        "temperature": 0,
        "max_tokens": 150,
        "usage": {"include": True},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
               "HTTP-Referer": "http://localhost", "X-Title": "Fanvue Chatbot"}
    try:
        resp = httpx.post(OPENROUTER_URL, headers=headers, json=payload, timeout=90)
        if resp.status_code != 200:
            return {"woman": True, "nude": False, "penis": False, "description": ""}
        full = resp.json()
        _record_cost(full, model, "vision")
        content = _extract_content(full)
        if content.startswith("```"):
            content = content.strip("`")
            content = content[content.find("{"):content.rfind("}") + 1]
        data = _json.loads(content)
        return {"woman": bool(data.get("woman", True)),
                "nude": bool(data.get("nude", False)),
                "penis": bool(data.get("penis", False)),
                "description": str(data.get("description", "") or "").strip()}
    except (OpenRouterError, ValueError, KeyError, TypeError):
        # Konnte nicht analysiert werden -> vorsichtige Annahme: koennte eine Frau sein
        return {"woman": True, "nude": False, "penis": False, "description": ""}


def _extract_content(data: dict) -> str:
    """Holt den Text-Content robust aus einer OpenRouter-Antwort.
    Behandelt content=None, Listen-Content (multimodal) und Refusals."""
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenRouterError(f"Unerwartete OpenRouter-Antwort: {data}") from exc
    msg = choice.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):  # manche Modelle liefern eine Content-Teile-Liste
        content = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    if not content:  # None oder leer -> ggf. Refusal
        refusal = msg.get("refusal")
        if refusal:
            raise OpenRouterError(f"Modell hat den Inhalt abgelehnt: {refusal}")
        return ""
    return str(content).strip()


def classify_message(text: str, notes: str = "") -> dict:
    """Bewertet eine Fan-Nachricht auf Kaufinteresse. Gibt ein Dict zurueck:
    {sexual_energy: bool, intent_score: 1-5, describes_fantasy: bool, preferences: [str]}.
    preferences werden IMMER als englische Tags geliefert (passend zu den PPV-Tags),
    auch wenn der Fan Deutsch schreibt. notes = gespeicherte Fan-Notizen (Vorlieben).
    Bei Fehlern wird ein leeres/neutrales Dict zurueckgegeben."""
    import json as _json

    model, api_key = resolve_model("classifier")
    if not api_key or not (text or "").strip():
        return {}
    system = (
        "Du bewertest die Nachricht eines Fans an eine Creatorin auf einer Adult-Plattform "
        "hinsichtlich echtem Kaufinteresse an ihrem Bild-/Videocontent. "
        "Antworte AUSSCHLIESSLICH mit kompaktem JSON, ohne Erklaerung, im Format: "
        '{"sexual_energy": true|false, "intent_score": 1-5, "describes_fantasy": true|false, '
        '"content_request": true|false, "emotional_distress": true|false, "preferences": ["tag", ...]}. '
        "content_request=true, wenn der Fan EXPLIZIT fragt, ob du bestimmten Content/Bilder/Videos "
        "hast, oder darum bittet, etwas Bestimmtes zu sehen (z.B. 'hast du content wo...', "
        "'zeig mir...', 'kann ich ... sehen', 'schick mir...'). Sonst false. "
        "emotional_distress=true, wenn der Fan gerade seelisch verletzlich ist oder eine emotionale "
        "Ausnahmesituation schildert: Traurigkeit, Einsamkeit, Weinen, Schmerz, Angst, Depression, "
        "Trennung/Liebeskummer, Verlust/Trauer, Krise, Ueberforderung, Selbstzweifel oder Andeutungen "
        "von Selbstverletzung. In solchen Momenten ist KEIN Verkauf angebracht. Sonst false. "
        "Bewerte STRENG und konservativ. Wichtige Regeln: "
        "Witze, Sprueche, Provokationen, Off-Topic (z.B. Drogen, Party, allgemeine Vulgaritaet) "
        "oder Aussagen ueber DRITTE sind KEIN Kaufinteresse -> intent_score 1 und sexual_energy=false. "
        "sexual_energy=true nur bei echtem sexuellem/romantischem Interesse, das der Creatorin "
        "SELBST gilt. describes_fantasy=true nur, wenn der Fan ernsthaft ein Szenario MIT der "
        "Creatorin beschreibt, nicht bei beilaeufigen Vulgaritaeten. "
        "intent_score: 1=Smalltalk/kein Kaufinteresse, 3=deutliches Interesse an ihr, "
        "5=will explizit mehr/Bilder/Videos von ihr sehen. "
        "preferences=konkret genannte oder aus den Fan-Notizen bekannte Vorlieben/Koerperteile/"
        "Szenerien. WICHTIG: preferences IMMER als kurze ENGLISCHE Schlagworte ausgeben "
        "(z.B. 'Fuesse' -> feet, 'Absaetze' -> heels, 'Dessous' -> lingerie), damit sie zu den "
        "englischen Content-Tags passen. Leer, wenn keine erkennbar."
    )
    user_content = text[:1000]
    if (notes or "").strip():
        user_content += f"\n\n[Bekannte Vorlieben des Fans aus Notizen: {notes.strip()[:300]}]"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
        "max_tokens": 150,
        "usage": {"include": True},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
               "HTTP-Referer": "http://localhost", "X-Title": "Fanvue Chatbot"}
    try:
        resp = httpx.post(OPENROUTER_URL, headers=headers, json=payload, timeout=45)
        if resp.status_code != 200:
            return {}
        full = resp.json()
        _record_cost(full, model, "classifier")
        content = full["choices"][0]["message"]["content"].strip()
        # eventuelle Code-Fences entfernen
        if content.startswith("```"):
            content = content.strip("`")
            content = content[content.find("{"):content.rfind("}") + 1]
        data = _json.loads(content)
        return {
            "sexual_energy": bool(data.get("sexual_energy", False)),
            "intent_score": int(data.get("intent_score", 1) or 1),
            "describes_fantasy": bool(data.get("describes_fantasy", False)),
            "content_request": bool(data.get("content_request", False)),
            "emotional_distress": bool(data.get("emotional_distress", False)),
            "preferences": [str(p).strip().lower() for p in (data.get("preferences") or []) if str(p).strip()],
        }
    except Exception:  # noqa: BLE001
        return {}


def parse_tags(text: str) -> list[str]:
    """Wandelt eine LLM-Antwort in eine saubere Tag-Liste (kommagetrennt)."""
    # Zeilenumbrueche und Aufzaehlungszeichen zu Kommas normalisieren
    for ch in ["\n", ";", "•", "- ", "*"]:
        text = text.replace(ch, ",")
    seen: list[str] = []
    for raw in text.split(","):
        tag = raw.strip().strip(".").lstrip("0123456789. ").strip().lower()
        if tag and tag not in seen:
            seen.append(tag)
    return seen[:15]
