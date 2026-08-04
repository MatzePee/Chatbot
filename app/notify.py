"""Benachrichtigungen nach aussen (aktuell: Telegram).

Zweck: melden, wenn in der Freigabe-Queue etwas endgueltig haengt - also ein
Entwurf, der sich nach allen automatischen Neuversuchen nicht mehr selbst
repariert und ohne Zutun eines Menschen nie rausgeht.

Telegram Bot API (Stand Bot API 10.2, Juli 2026):
    POST https://api.telegram.org/bot<TOKEN>/sendMessage
    Felder: chat_id, text, parse_mode

Einrichtung durch den Nutzer:
  1. In Telegram @BotFather anschreiben -> /newbot -> Token kopieren
  2. Den eigenen neuen Bot einmal anschreiben (sonst darf er nicht antworten)
  3. Token in den Einstellungen hinterlegen, Chat-ID per Knopfdruck ermitteln

Grundsatz: Benachrichtigungen sind NIE kritisch. Jeder Fehler wird geloggt und
geschluckt - ein nicht erreichbares Telegram darf den Poller nie aufhalten.
"""
from __future__ import annotations

import html
import time
from typing import Any, Optional

import httpx

from . import db

API_BASE = "https://api.telegram.org"
_TIMEOUT = 15.0


class TelegramError(Exception):
    pass


def is_configured() -> bool:
    return bool(db.get_setting("telegram_bot_token", "").strip()
                and str(db.get_setting("telegram_chat_id", "")).strip())


def _api(method: str, payload: dict[str, Any], token: str = "") -> dict[str, Any]:
    """Ruft eine Bot-API-Methode auf. Wirft TelegramError bei Fehlern."""
    token = (token or db.get_setting("telegram_bot_token", "")).strip()
    if not token:
        raise TelegramError("Kein Telegram-Bot-Token hinterlegt")
    try:
        resp = httpx.post(f"{API_BASE}/bot{token}/{method}", json=payload, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - Netzwerk, DNS, TLS ...
        raise TelegramError(f"Telegram nicht erreichbar: {exc}") from exc
    try:
        data = resp.json()
    except ValueError as exc:
        raise TelegramError(f"Unerwartete Antwort [{resp.status_code}]: {resp.text[:200]}") from exc
    if not data.get("ok"):
        # description ist die sprechende Fehlermeldung von Telegram
        raise TelegramError(f"Telegram-Fehler [{data.get('error_code', resp.status_code)}]: "
                            f"{data.get('description', 'unbekannt')}")
    return data.get("result") or {}


def send(text: str, chat_id: str = "", token: str = "", silent: bool = False) -> bool:
    """Schickt eine Nachricht. Rueckgabe: True bei Erfolg.

    Fehler werden geloggt, nicht geworfen - Aufrufer sollen sich nie darum
    kuemmern muessen. Fuer die Testfunktion in den Einstellungen wird
    send_or_raise() genutzt, dort will man die Fehlermeldung sehen.
    """
    try:
        send_or_raise(text, chat_id=chat_id, token=token, silent=silent)
        return True
    except TelegramError as exc:
        db.log("error", "notify", "Telegram-Benachrichtigung fehlgeschlagen", str(exc))
        return False


def send_or_raise(text: str, chat_id: str = "", token: str = "", silent: bool = False) -> None:
    chat_id = str(chat_id or db.get_setting("telegram_chat_id", "")).strip()
    if not chat_id:
        raise TelegramError("Keine Telegram-Chat-ID hinterlegt")
    _api("sendMessage", {
        "chat_id": chat_id,
        "text": text[:4000],          # Telegram-Limit: 4096 Zeichen
        "parse_mode": "HTML",
        "disable_notification": bool(silent),
        "link_preview_options": {"is_disabled": True},
    }, token=token)


def get_me(token: str = "") -> dict[str, Any]:
    """Prueft das Token und liefert die Bot-Infos (username usw.)."""
    return _api("getMe", {}, token=token)


def discover_chat_id(token: str = "") -> tuple[str, str]:
    """Ermittelt die Chat-ID aus den letzten Updates des Bots.

    Der Nutzer muss dem Bot vorher EINMAL geschrieben haben. Rueckgabe:
    (chat_id, beschreibung). Bei nichts Brauchbarem: ("", Hinweistext).
    """
    updates = _api("getUpdates", {"limit": 20, "timeout": 0}, token=token)
    if not isinstance(updates, list):
        return "", "Unerwartete Antwort von Telegram."
    for upd in reversed(updates):  # neueste zuerst
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = ((upd.get(key) or {}).get("chat")) or {}
            if chat.get("id"):
                name = (chat.get("title") or chat.get("username")
                        or " ".join(filter(None, [chat.get("first_name"),
                                                  chat.get("last_name")])) or "Chat")
                return str(chat["id"]), f"{name} ({chat.get('type', '?')})"
    return "", ("Keine Nachricht gefunden. Schreibe deinem Bot in Telegram einmal "
                "kurz „hallo“ und versuche es dann erneut.")


# ------------------------------------------------------------- Nachrichtenbau
def _queue_link() -> str:
    base = str(db.get_setting("app_base_url", "") or "").strip().rstrip("/")
    return f"\n\n👉 {html.escape(base)}/queue" if base else ""


def _esc(value: Any, limit: int = 200) -> str:
    return html.escape(str(value or "")[:limit])


def notify_blocked_draft(draft: Any, max_regen: int, grund: str = "") -> bool:
    """Meldet einen Entwurf, der ohne Zutun nicht mehr weiterkommt.

    grund: warum er festhaengt (Eskalation, verbotenes Wort, Limit erreicht ...).
    Ohne Angabe wird der klassische Fall 'alle Neuversuche erfolglos' gemeldet.
    """
    if not db.get_setting("telegram_enabled", False) or not is_configured():
        return False

    who = draft["display_name"] or draft["handle"] or draft["user_uuid"]
    reason = (draft["guardrail_note"] or "").strip() or "Modell lieferte keinen Text"
    incoming = (draft["incoming_text"] or "").strip()
    waiting = ""
    if draft["created_at"]:
        minutes = int((time.time() - float(draft["created_at"])) / 60)
        waiting = (f"\n⏱ Wartet seit {minutes} Min."
                   if minutes < 120 else f"\n⏱ Wartet seit {minutes // 60} Std.")

    versuche = int(draft["regen_count"] or 0)
    lines = [
        "⚠️ <b>Entwurf hängt in der Freigabe-Queue</b>",
        "",
        f"👤 <b>{_esc(who, 80)}</b>",
        f"🚫 {_esc(grund or reason, 300)}",
    ]
    # Guardrail-Notiz nur zusaetzlich zeigen, wenn sie etwas Neues sagt
    if grund and reason and reason[:40] not in grund:
        lines.append(f"📝 {_esc(reason, 300)}")
    if versuche:
        lines.append(f"↻ {versuche} von {max_regen} automatischen Neuversuchen verbraucht")
    lines.append("Hier passiert ohne dich nichts mehr.")
    if incoming:
        lines.append(f"\n💬 Fan schrieb: „{_esc(incoming, 300)}“")
    if waiting:
        lines.append(waiting)

    ok = send("\n".join(lines) + _queue_link())
    if ok:
        db.log("info", "notify", f"Telegram: Draft #{draft['id']} als blockiert gemeldet", reason)
    return ok
