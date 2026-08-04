"""SQLite-Datenbank-Layer.

Bewusst mit dem Standard-Modul sqlite3 gehalten, damit keine zusaetzlichen
Abhaengigkeiten noetig sind. Die DB-Datei liegt im Projektordner (data/bot.db).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DATA_DIR, "bot.db")

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _connect()
    return _conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- OAuth-Tokens (einzeiliger Store, id=1)
CREATE TABLE IF NOT EXISTS tokens (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    access_token   TEXT,
    refresh_token  TEXT,
    expires_at     REAL,      -- unix ts, wann der access_token ablaeuft
    scope          TEXT,
    account_uuid   TEXT,
    account_handle TEXT,
    updated_at     REAL
);

-- Pro-Fan-Zustand und Overrides
CREATE TABLE IF NOT EXISTS chats (
    user_uuid            TEXT PRIMARY KEY,
    handle               TEXT,
    display_name         TEXT,
    bot_enabled          INTEGER DEFAULT 1,   -- Bot fuer diesen Chat aktiv?
    mode_override        TEXT,                -- NULL=global, 'approval' oder 'auto'
    persona_override     TEXT,                -- optionaler eigener System-Prompt
    notes                TEXT,                -- Fakten/Gedaechtnis ueber den Fan
    last_inbound_uuid    TEXT,                -- letzte verarbeitete eingehende Nachricht
    last_inbound_at      REAL,                -- Zeit der letzten Fan-Nachricht (fuer Reaktivierung)
    last_reactivation_at REAL,                -- letzte proaktive Reaktivierung
    last_seen_at         REAL,
    updated_at           REAL
);

-- Generierte Antworten (Entwuerfe). Auch Auto-Send laeuft ueber diese Tabelle.
CREATE TABLE IF NOT EXISTS drafts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_uuid         TEXT NOT NULL,
    handle            TEXT,
    display_name      TEXT,
    incoming_text     TEXT,                  -- worauf geantwortet wird
    generated_text    TEXT,                  -- roher LLM-Output
    edited_text       TEXT,                  -- vom User bearbeitete Fassung
    status            TEXT DEFAULT 'pending',-- pending|approved|sent|rejected|failed|blocked
    auto_send         INTEGER DEFAULT 0,     -- 1 = Auto-Modus, sendet automatisch faellig
    scheduled_send_at REAL,                  -- ab wann senden (Auto-Modus)
    guardrail_note    TEXT,                  -- Hinweis, falls Guardrail angeschlagen hat
    model             TEXT,
    error             TEXT,
    created_at        REAL,
    sent_at           REAL,
    sent_message_uuid TEXT,
    is_ppv            INTEGER DEFAULT 0,
    ppv_folder        TEXT,
    ppv_media_uuids   TEXT,                  -- JSON-Liste von Media-UUIDs
    ppv_price_cents   INTEGER,
    ppv_preview_uuid  TEXT,
    -- Selbstheilung wartender Drafts (Recheck-Zyklus)
    inbound_uuid      TEXT,                  -- Fanvue-UUID der beantworteten Fan-Nachricht
    regen_count       INTEGER DEFAULT 0,     -- wie oft bereits neu generiert wurde
    last_regen_at     REAL,                  -- letzte Neugenerierung
    last_check_at     REAL,                  -- letzte Aktualitaetspruefung
    stale_note        TEXT,                  -- gesetzt, wenn neuere Fan-Nachrichten vorliegen
    user_edited       INTEGER DEFAULT 0,     -- 1 = manuell bearbeitet, nie ueberschreiben
    notified_at       REAL                   -- wann per Telegram gemeldet (verhindert Doppelmeldung)
);

-- Audit-Log
CREATE TABLE IF NOT EXISTS logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL,
    level      TEXT,      -- info|warn|error
    category   TEXT,      -- poll|generate|send|oauth|guardrail|system
    message    TEXT,
    detail     TEXT
);

-- PPV: Ordner-Konfiguration (Fanvue-Vault-Ordner werden per NAME adressiert)
CREATE TABLE IF NOT EXISTS ppv_folders (
    name               TEXT PRIMARY KEY,
    enabled            INTEGER DEFAULT 0,   -- darf im Chat als PPV verkauft werden?
    price_cents        INTEGER DEFAULT 500, -- Verkaufspreis in Cent (min. 300 = $3)
    tags               TEXT DEFAULT '',     -- Ordner-Tags (kommagetrennt)
    preview_media_uuid TEXT,                -- Vorschaubild fuer die PPV-Nachricht
    request_only       INTEGER DEFAULT 0,   -- nur auf explizite Anfrage anbieten
    media_kind         TEXT DEFAULT 'image',-- 'image' | 'video' | 'mixed' (manuell gepflegt)
    thumbs_json        TEXT,                -- gecachte Vorschaubilder (JSON) fuer die Uebersicht
    thumbs_cached_at   REAL,                -- Zeitpunkt des Thumbnail-Caches
    updated_at         REAL
);

-- PPV: Verkaufs-Zustand pro Subscriber
CREATE TABLE IF NOT EXISTS ppv_state (
    user_uuid            TEXT PRIMARY KEY,
    last_ppv_at          REAL,
    outbound_since_ppv   INTEGER DEFAULT 0,
    unpurchased_streak   INTEGER DEFAULT 0,
    sexual_streak        INTEGER DEFAULT 0,
    offered_sets         TEXT DEFAULT '[]',   -- JSON-Liste angebotener Ordner
    purchased_sets       TEXT DEFAULT '[]',   -- JSON-Liste gekaufter Ordner
    last_purchase_marker TEXT,                -- letzter erkannter Kauf (ISO-String)
    updated_at           REAL
);

-- PPV: pro Medium (Bild) eigene Tags + Analyse-Status
CREATE TABLE IF NOT EXISTS ppv_media (
    media_uuid   TEXT PRIMARY KEY,
    folder_name  TEXT,
    tags         TEXT DEFAULT '',     -- eigene Tags (kommagetrennt)
    fanvue_tags  TEXT DEFAULT '',     -- von Fanvue-KI erkannte Tags (fuer Matching)
    analyzed     INTEGER DEFAULT 0,   -- 1 = per LLM analysiert
    media_type   TEXT,
    updated_at   REAL
);

-- PPV: einzelne Angebote (jede gesendete PPV-Nachricht) fuer Conversion-Tracking
CREATE TABLE IF NOT EXISTS ppv_offers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_uuid    TEXT,
    handle       TEXT,
    folder       TEXT,
    price_cents  INTEGER,
    media_count  INTEGER,
    message_uuid TEXT,                 -- UUID der gesendeten Fanvue-Nachricht
    sent_at      REAL,
    purchased    INTEGER DEFAULT 0,
    purchased_at REAL
);

-- OpenRouter-API-Kosten pro Aufruf (fuer Kosten-Auswertung)
CREATE TABLE IF NOT EXISTS api_costs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL,
    cost       REAL,      -- USD (OpenRouter-Credits)
    model      TEXT,
    category   TEXT       -- chat|caption|classifier|vision
);

-- Reaktivierung: welche Selfies wurden welchem Fan schon geschickt (kein Bild doppelt)
CREATE TABLE IF NOT EXISTS reactivation_sent (
    user_uuid   TEXT,
    media_uuid  TEXT,
    sent_at     REAL,
    PRIMARY KEY (user_uuid, media_uuid)
);

CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_ppv_media_folder ON ppv_media(folder_name);
CREATE INDEX IF NOT EXISTS idx_ppv_offers_user ON ppv_offers(user_uuid);
CREATE INDEX IF NOT EXISTS idx_api_costs_ts ON api_costs(ts);
"""


DEFAULT_SETTINGS: dict[str, Any] = {
    # Betriebsmodus
    "mode": "approval",                 # approval | auto  (global; pro Chat ueberschreibbar)
    "bot_running": False,               # Master-Schalter (Kill-Switch)
    # Polling
    "poll_interval_seconds": 60,        # wie oft nach neuen Nachrichten gesucht wird
    "chat_filter": "unread",            # unread | not_answered | ...
    "chat_custom_list_id": "",          # Prio-1-Gruppe: Fanvue-Liste (Custom List)
    "chat_custom_list_name": "",        # Anzeige/Merker fuer die GUI
    # Prio-2-Gruppe: seltener bearbeitet (spart Tokens bei Nur-Chattern)
    "prio2_custom_list_id": "",
    "prio2_custom_list_name": "",
    "prio2_interval_minutes": 30,       # Prio-2-Scan-Intervall
    "prio2_jitter_minutes": 30,         # zusaetzlicher Zufalls-Delay 1..X Minuten
    "max_chats_per_cycle": 10,          # wie viele Chats pro Zyklus max. bearbeitet werden
    "reply_cooldown_seconds": 120,      # min. Abstand zwischen zwei Antworten an denselben Fan
    # Aktive Zeiten (lokale Serverzeit, 24h)
    "active_hours_enabled": False,
    "active_hour_start": 8,
    "active_hour_end": 23,
    # Menschenaehnliche Sende-Verzoegerung (Auto-Modus)
    "send_delay_min_seconds": 20,
    "send_delay_max_seconds": 90,
    # OpenRouter
    "openrouter_api_key": "",
    "openrouter_model": "openai/gpt-4o-mini",   # Chat-Modell (Antworten) – hier das unzensierte
    "classifier_model": "",                 # Analyse/Intent (leer = Chat-Modell); smartes Modell empfohlen
    "ppv_caption_model": "",                # PPV-Bildunterschriften (leer = Chat-Modell)
    "vision_api_key": "",                   # optionaler eigener Key fuer die Bildanalyse
    "vision_model": "openai/gpt-4o-mini",   # multimodales Modell fuer die Bildanalyse
    "vision_prompt": (
        "Analysiere den Bildinhalt und gib 5-10 kurze, praegnante Schlagworte "
        "(Tags) zurueck, die den Inhalt beschreiben. Nur die Tags, kommagetrennt, "
        "ohne Nummerierung und ohne ganze Saetze."
    ),
    # --- Eingehende Fan-Bilder: analysieren statt sofort PPV ---
    "incoming_image_enabled": True,     # Fan-Bild per Vision-LLM analysieren und darauf eingehen
    "incoming_image_react_prompt": (
        "Der Fan hat dir gerade ein Bild geschickt. Beschreibung des Bildes: \"{beschreibung}\". "
        "Gehe in deiner Antwort natuerlich und persoenlich darauf ein, als haettest du es dir "
        "gerade angeschaut. Kein Verkauf, kein Angebot – reagiere einfach echt auf das, was zu sehen ist."
    ),
    "incoming_image_woman_prompt": (
        "Der Fan hat ein Bild geschickt, auf dem eine Frau zu sehen ist. Du kannst NICHT sicher "
        "wissen, ob das ein Bild von dir selbst ist oder von jemand anderem. Bestaetige oder "
        "verneine daher NIEMALS, dass du das bist. Reagiere warm, aber ausweichend und neugierig "
        "(z.B. 'oh, wie kommst du darauf 😏' oder 'spannend, erzaehl mir mehr'), ohne dich auf "
        "die Identitaet festzulegen. Kein Verkauf, kein Angebot."
    ),
    # --- PPV Auto-Selling-Engine ---
    "ppv_enabled": False,               # Master-Schalter fuer automatisches PPV-Anbieten
    "ppv_use_llm_classifier": True,     # Intent/Interesse per LLM bewerten
    "ppv_intent_threshold": 4,          # Intent-Score >= X -> Kaufinteresse
    "ppv_min_fan_messages": 4,          # Aufwaermphase: so viele Fan-Nachrichten vor PPV
                                        # (ausser bei eindeutiger Bildanfrage/Foto)
    "ppv_sexual_streak_trigger": 3,     # X sexuelle Nachrichten in Folge -> Signal
    "ppv_cooldown_minutes": 8,          # normaler Cooldown
    "ppv_cooldown_outbound": 2,         # zusaetzlich: min. so viele Outbound-Nachrichten
    "ppv_cooldown_long_minutes": 30,    # verlaengerter Cooldown
    "ppv_unpurchased_threshold": 3,     # nach X ungekauften PPV -> langer Cooldown
    "ppv_caption_use_vision": False,    # PPV-Captions ueber das Vision-Modell/-Key generieren
    "ppv_block_on_distress": True,      # kein PPV, wenn der Fan emotional verletzlich ist
    "ppv_max_media_per_set": 30,        # max. Medien pro PPV-Nachricht
    "ppv_thumb_cache_hours": 6,         # wie lange Vorschaubilder gecacht werden
    "ppv_keywords": (
        "noch mehr,hast du mehr,mehr sehen,mehr davon,send me pics,send pics,send me more,"
        "zeig mir was,zeig mir mehr,kaufen,take my money,strip,get naked,show me,show me more,"
        "i want more,gib mir mehr,ausziehen,nackt sehen,i need more,want to see more"
    ),
    "ppv_bodyparts_keywords": (
        "heels,legs,beine,ass,arsch,po,tits,titten,boobs,brueste,pussy,muschi,feet,fuesse,"
        "cock,schwanz,dick,nipple,nippel,booty,curves"
    ),
    "ppv_pic_request_keywords": (
        "kann ich deine bilder sehen,schick mal was,wo sind deine pics,wo sind deine bilder,"
        "zeig deine bilder,bilder sehen,can i see your,where are your pics,send something,"
        "show me your,pics,foto,fotos,picture,pictures,bilder,ich sehe nichts,kann nichts sehen,"
        "can't see,cant see"
    ),
    # Wunsch nach einem VIDEO bzw. ausdruecklich nach FOTOS. Steuert, welche
    # Sets ueberhaupt in Frage kommen - siehe Medientyp auf der PPV-Seite.
    "ppv_video_keywords": (
        "video,videos,clip,clips,filmchen,film,movie,vid,vids,bewegtbild,"
        "vidoe,viedeo,vidio,mach mal ein video,schick mir ein video"
    ),
    "ppv_photo_keywords": (
        "foto,fotos,bild,bilder,pic,pics,picture,pictures,photo,photos,"
        "selfie,selfies,aufnahme,aufnahmen"
    ),
    "ppv_no_match_prompt": (
        "Der Fan wuenscht sich ausdruecklich {wunsch}, davon ist aktuell aber nichts "
        "Passendes mehr verfuegbar. Biete NICHTS anderes an und haenge nichts an. "
        "Geh im Chat kurz und charmant darauf ein, vertroeste ihn ehrlich auf spaeter "
        "(z.B. dass gerade etwas Neues entsteht) und halte die Stimmung. Erfinde keine "
        "konkreten Zusagen und nenne keinen Termin."
    ),
    "ppv_freecontent_keywords": (
        "gratis,kostenlos,umsonst,for free,free content,geschenkt,kostenfrei,free pic,free pics,"
        "ohne bezahlen"
    ),
    "ppv_sales_prompt": (
        "Du verkaufst jetzt exklusiven Content, der der Nachricht bereits als kaufbarer Anhang "
        "beiliegt. Ton: premium und selektiv, niemals verzweifelt. Schreibe eine kurze, "
        "verfuehrerische Bildunterschrift (1-2 Saetze), in der Ich-Form, passend zur Stimmung, "
        "gern mit einem Emoji – so als waere der Content schon sichtbar. Verwende NICHT das Wort "
        "'Set' (es kann auch nur ein einzelnes Bild sein). Keine Preisangabe, keine Platzhalter/Tags, "
        "keine Regieanweisungen in Klammern, keine Sternchen-Aktionen. "
        "Beispiel-Stil (nicht woertlich): 'Extra fuer dich rausgesucht... ich zeig das echt selten 😏'"
    ),
    "ppv_freecontent_prompt": (
        "Der Fan moechte Gratis-Content. Lehne freundlich, aber bestimmt ab und leite spielerisch auf "
        "bezahlten Content um. Niemals gratis anbieten oder nachgeben. Beispiele (nicht woertlich): "
        "'Neugierig bist du ja... aber die guten Sachen gibt es nicht gratis, Babe 😇' / "
        "'haha wenn du das willst, musst du schon ein bisschen investieren 😏'"
    ),
    "temperature": 0.9,
    "max_tokens": 800,   # hoeher, damit Reasoning-Modelle (gpt-5.x) nicht abgeschnitten werden
    "history_messages": 15,             # wie viele vergangene Nachrichten in den Prompt
    "queue_context_messages": 2,        # wie viele vorherige Nachrichten in der Freigabe-Queue anzeigen
    "generation_retries": 3,            # so oft bei LEERER Modell-Antwort neu generieren
    "generation_retry_delay": 60,       # Pause (s) zwischen den Neuversuchen (nur Hintergrundbetrieb)
    # --- Selbstheilung wartender Drafts ---
    "draft_recheck_enabled": True,      # wartende Drafts regelmaessig pruefen
    "draft_recheck_interval_seconds": 180,   # Pruefabstand (Standard: 3 Minuten)
    "draft_max_regen": 10,              # max. automatische Neugenerierungen pro Draft
    "draft_regen_on_stale": True,       # veraltete Drafts automatisch neu erzeugen
                                        # (manuell bearbeitete NIE - dort nur ein Hinweis)
    # --- Veroeffentlichen nach GitHub (Seite /upload) ---
    "git_remote_url": "",               # z.B. https://github.com/MatzePee/Chatbot.git
    "git_branch": "main",
    "git_user_name": "",                # Name fuer Commits (auch GitHub-Benutzername)
    "git_user_email": "",
    "github_token": "",                 # Personal Access Token mit Recht 'repo'
    "git_commit_default": "Aktueller Stand",
    # --- Update-Pruefung (Git-Tags) ---
    "update_check_enabled": True,       # regelmaessig auf neue Versionen pruefen
    "update_check_interval_hours": 6,   # Pruefabstand
    "update_notify_telegram": True,     # neue Version auch per Telegram melden
    "update_state": "",                 # JSON: letztes Pruefergebnis (vom Programm gepflegt)
    # --- Telegram-Benachrichtigungen ---
    "telegram_enabled": False,          # Master-Schalter
    "telegram_bot_token": "",           # von @BotFather
    "telegram_chat_id": "",             # eigene Chat-ID (per Knopfdruck ermittelbar)
    "app_base_url": "",                 # z.B. http://192.168.20.16:8000 - fuer Links in Meldungen
    # Persona / System-Prompt
    "system_prompt": (
        "Du bist die Chat-Persona einer Creatorin auf Fanvue. Antworte kurz, "
        "warm und in der Ich-Form, wie in einem privaten Direktchat. Stelle "
        "gelegentlich Rueckfragen, um das Gespraech am Laufen zu halten. Bleibe "
        "in deiner Rolle und erwaehne niemals, dass du eine KI bist."
    ),
    # --- Zeit- und Situationskontext (gegen Uhrzeit-/Wochentag-Fehler) ---
    "time_context_enabled": True,       # Datum/Uhrzeit/Tagesplan in den Prompt injizieren
    "timestamps_in_history": True,      # relative Zeitmarken an die Verlaufsnachrichten
    "time_guard_enabled": True,         # Antwort auf Zeit-Widersprueche pruefen (kein Auto-Send)
    "persona_timezone": "Europe/Berlin",  # Zeitzone der PERSONA (nicht des Servers)
    "persona_schedule": (
        "# Tagesrhythmus der Persona. Eine Regel pro Zeile:\n"
        "#   <Tage> <von>-<bis> <Aktivitaet>\n"
        "# Tage: Mo | Mo-Fr | Mo,Mi,Fr | taeglich    Zeiten: 9 oder 09:30\n"
        "# Bei mehreren Treffern gewinnt die spezifischste Regel\n"
        "# (wenigste Tage, dann kuerzestes Zeitfenster).\n"
        "# Der Plan sollte LUECKENLOS sein - fuer nicht abgedeckte Zeiten bekommt\n"
        "# das Modell die Anweisung, vage zu bleiben.\n"
        "taeglich 00:30-08:30  schlaefst tief und fest\n"
        "taeglich 08:30-09:30  gerade aufgestanden, Kaffee, noch verschlafen\n"
        "Mo-Fr 09:30-16:00  in der Uni, Vorlesungen und Lernen\n"
        "Mo-Fr 16:00-18:00  im Gym oder beim Einkaufen, unterwegs\n"
        "Mo-Fr 18:00-00:30  zuhause, entspannt auf der Couch\n"
        "Sa,So 09:30-13:00  gemuetlicher Wochenendstart, ausgeschlafen\n"
        "Sa,So 13:00-19:00  Freizeit, Freundinnen treffen, Content drehen\n"
        "Sa,So 19:00-00:30  Abend zuhause, Serie und Handy\n"
        "So 19:00-00:30  ruhiger Sonntagabend, frueh muede\n"
        "Fr 21:00-03:00  unterwegs, feiern mit den Maedels\n"
        "Sa 21:00-03:00  unterwegs, feiern mit den Maedels\n"
    ),
    # Guardrails
    "max_reply_chars": 500,
    "banned_words": "",                 # kommagetrennt; blockt Draft, wenn enthalten
    "escalation_keywords": "",          # kommagetrennt; Fan-Nachricht -> nicht auto-antworten, melden
    # Fanvue OAuth (koennen auch aus .env vorbelegt werden)
    "fanvue_client_id": "",
    "fanvue_client_secret": "",
    "fanvue_redirect_uri": "http://127.0.0.1:8000/oauth/callback",
    "fanvue_api_version": "2025-06-26",
    # --- Anti-AI: Namens-/Wiederholungs-Filter + natuerlichere Antworten ---
    "anti_ai_enabled": True,
    "anti_ai_petnames": (
        "schatz,schatzi,suesser,suesse,maus,maeuschen,engel,baer,liebling,schnucki,spatz,"
        "hasi,puppe,kleiner,babe,baby,honey,love,darling,sweetheart,gorgeous,angel,boo,bae,sugar,cutie"
    ),
    "anti_ai_rules": (
        "Wirke natuerlich und menschlich: Variiere die Laenge deiner Antworten, beende nicht "
        "jede Nachricht mit einer Frage, spiegle den Fan nicht, und beginne nicht mit Fuellwoertern "
        "wie 'hihi', 'hm', 'mhm', 'aha', 'oh ja'. Wiederhole keinen Kosenamen und nicht den Namen "
        "des Fans, den du gerade eben schon benutzt hast."
    ),
    # --- Trinkgeld-Dank ---
    "tip_thanks_enabled": True,          # bei einem Trinkgeld automatisch bedanken
    "tip_thanks_prompt": (
        "Klinge echt geruehrt und verspielt, nicht floskelhaft. Halte es kurz (1-2 Saetze) "
        "und beziehe dich ruhig auf die Stimmung eures Chats."
    ),
    # --- Proaktive Reaktivierung stiller Fans ---
    "reactivation_enabled": False,
    "reactivation_inactive_hours": 14,   # ab wie vielen STUNDEN Stille reaktivieren
    "reactivation_cooldown_days": 14,    # min. Abstand zwischen zwei Reaktivierungen pro Fan
    "reactivation_max_per_cycle": 3,     # max. Reaktivierungen pro Durchlauf
    "reactivation_delay_min_minutes": 15,  # Zufalls-Delay vor dem Senden (Auto), Untergrenze
    "reactivation_delay_max_minutes": 45,  # Zufalls-Delay vor dem Senden (Auto), Obergrenze
    "reactivation_folder": "",           # optionaler Vault-Ordner mit Selfies (leer = nur Text)
    "reactivation_prompt": (
        "Melde dich locker und persoenlich beim Fan, der laenger nichts geschrieben hat. Kurz, "
        "warm, ohne Vorwurf, kein Verkauf. Kein 'lange nichts gehoert'-Klischee. "
        "WICHTIG: Knüpfe konkret an EUER LETZTES THEMA an (das, worueber ihr zuletzt "
        "geschrieben habt) oder an etwas, das der Fan ueber sich erzaehlt hat, damit es "
        "echt persoenlich wirkt und nicht wie eine Standard-Nachricht. Nutze auch, was du "
        "ueber den Fan weisst (Notizen)."
    ),
}


def _migrate(conn: sqlite3.Connection) -> None:
    """Fuegt fehlende Spalten zu bestehenden Tabellen hinzu (einfache Migration)."""
    wanted = {
        "drafts": {
            "is_ppv": "INTEGER DEFAULT 0",
            "ppv_folder": "TEXT",
            "ppv_media_uuids": "TEXT",
            "ppv_price_cents": "INTEGER",
            "ppv_preview_uuid": "TEXT",
            "inbound_uuid": "TEXT",
            "regen_count": "INTEGER DEFAULT 0",
            "last_regen_at": "REAL",
            "last_check_at": "REAL",
            "stale_note": "TEXT",
            "user_edited": "INTEGER DEFAULT 0",
            "notified_at": "REAL",
        },
        "ppv_folders": {
            "preview_media_uuid": "TEXT",
            "request_only": "INTEGER DEFAULT 0",
            "media_kind": "TEXT DEFAULT 'image'",
            "thumbs_json": "TEXT",
            "thumbs_cached_at": "REAL",
        },
        "ppv_media": {
            "fanvue_tags": "TEXT DEFAULT ''",
        },
        "chats": {
            "last_inbound_at": "REAL",
            "last_reactivation_at": "REAL",
        },
    }
    for table, cols in wanted.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for col, ddl in cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                # Einmaliger Startwert fuer den Medientyp: Sets, die im Namen
                # oder in den Tags "video" tragen, sind mit hoher Sicherheit
                # Videos. Das erspart es, alle vorhandenen Sets von Hand
                # durchzugehen - aendern laesst sich jedes davon jederzeit.
                if table == "ppv_folders" and col == "media_kind":
                    conn.execute(
                        "UPDATE ppv_folders SET media_kind = 'video' "
                        "WHERE lower(name) LIKE '%video%' OR lower(name) LIKE '%clip%' "
                        "   OR lower(',' || COALESCE(tags,'') || ',') LIKE '%,video,%'")
    conn.commit()


def init_db() -> None:
    with _lock:
        conn = get_conn()
        conn.executescript(SCHEMA)
        _migrate(conn)
        # Defaults setzen, falls noch nicht vorhanden
        for key, value in DEFAULT_SETTINGS.items():
            row = conn.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?)",
                    (key, json.dumps(value)),
                )
        conn.commit()
        _seed_from_env()


def _seed_from_env() -> None:
    """Uebernimmt Startwerte aus Umgebungsvariablen, falls die Settings noch leer sind."""
    mapping = {
        "fanvue_client_id": "FANVUE_CLIENT_ID",
        "fanvue_client_secret": "FANVUE_CLIENT_SECRET",
        "fanvue_redirect_uri": "FANVUE_REDIRECT_URI",
        "openrouter_api_key": "OPENROUTER_API_KEY",
        "openrouter_model": "OPENROUTER_MODEL",
    }
    for setting_key, env_key in mapping.items():
        env_val = os.environ.get(env_key)
        if env_val and not get_setting(setting_key):
            set_setting(setting_key, env_val)


# ---------------------------------------------------------------- Settings API
def get_setting(key: str, default: Any = None) -> Any:
    with _lock:
        row = get_conn().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return DEFAULT_SETTINGS.get(key, default)
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return default


def set_setting(key: str, value: Any) -> None:
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
        conn.commit()


def all_settings() -> dict[str, Any]:
    with _lock:
        rows = get_conn().execute("SELECT key, value FROM settings").fetchall()
    result = dict(DEFAULT_SETTINGS)
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            pass
    return result


# ------------------------------------------------------------------ Token API
def save_tokens(access_token: str, refresh_token: str, expires_at: float,
                scope: str = "", account_uuid: str = "", account_handle: str = "") -> None:
    with _lock:
        conn = get_conn()
        conn.execute(
            """INSERT INTO tokens(id, access_token, refresh_token, expires_at, scope,
                                  account_uuid, account_handle, updated_at)
               VALUES(1, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   access_token=excluded.access_token,
                   refresh_token=excluded.refresh_token,
                   expires_at=excluded.expires_at,
                   scope=excluded.scope,
                   account_uuid=COALESCE(NULLIF(excluded.account_uuid,''), tokens.account_uuid),
                   account_handle=COALESCE(NULLIF(excluded.account_handle,''), tokens.account_handle),
                   updated_at=excluded.updated_at""",
            (access_token, refresh_token, expires_at, scope,
             account_uuid, account_handle, time.time()),
        )
        conn.commit()


def get_tokens() -> Optional[sqlite3.Row]:
    with _lock:
        return get_conn().execute("SELECT * FROM tokens WHERE id = 1").fetchone()


def clear_tokens() -> None:
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM tokens WHERE id = 1")
        conn.commit()


# ------------------------------------------------------------------- Chat API
def upsert_chat(user_uuid: str, handle: str = "", display_name: str = "") -> sqlite3.Row:
    with _lock:
        conn = get_conn()
        conn.execute(
            """INSERT INTO chats(user_uuid, handle, display_name, last_seen_at, updated_at)
               VALUES(?, ?, ?, ?, ?)
               ON CONFLICT(user_uuid) DO UPDATE SET
                   handle=COALESCE(NULLIF(excluded.handle,''), chats.handle),
                   display_name=COALESCE(NULLIF(excluded.display_name,''), chats.display_name),
                   last_seen_at=excluded.last_seen_at""",
            (user_uuid, handle, display_name, time.time(), time.time()),
        )
        conn.commit()
        return conn.execute("SELECT * FROM chats WHERE user_uuid = ?", (user_uuid,)).fetchone()


def get_chat(user_uuid: str) -> Optional[sqlite3.Row]:
    with _lock:
        return get_conn().execute("SELECT * FROM chats WHERE user_uuid = ?", (user_uuid,)).fetchone()


def get_chat_by_handle(handle: str) -> Optional[sqlite3.Row]:
    with _lock:
        return get_conn().execute(
            "SELECT * FROM chats WHERE lower(handle) = lower(?) "
            "OR lower(display_name) = lower(?) LIMIT 1", (handle, handle)).fetchone()


def list_chats() -> list[sqlite3.Row]:
    with _lock:
        return get_conn().execute(
            "SELECT * FROM chats ORDER BY last_seen_at DESC"
        ).fetchall()


def due_reactivation_chats(now: float, inactive_seconds: float,
                           cooldown_seconds: float, limit: int) -> list[sqlite3.Row]:
    """Aktive Chats, deren letzte Fan-Nachricht laenger als inactive_seconds her ist
    und die innerhalb des Cooldowns nicht schon reaktiviert wurden."""
    with _lock:
        return get_conn().execute(
            "SELECT * FROM chats WHERE bot_enabled = 1 "
            "AND last_inbound_at IS NOT NULL AND last_inbound_at <= ? "
            "AND (last_reactivation_at IS NULL OR last_reactivation_at <= ?) "
            "ORDER BY last_inbound_at ASC LIMIT ?",
            (now - inactive_seconds, now - cooldown_seconds, limit),
        ).fetchall()


def update_chat(user_uuid: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _lock:
        conn = get_conn()
        conn.execute(f"UPDATE chats SET {cols} WHERE user_uuid = ?",
                     (*fields.values(), user_uuid))
        conn.commit()


# ------------------------------------------------------------------ Draft API
def create_draft(**fields: Any) -> int:
    fields.setdefault("created_at", time.time())
    cols = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            f"INSERT INTO drafts({cols}) VALUES({placeholders})", tuple(fields.values())
        )
        conn.commit()
        return int(cur.lastrowid)


def get_draft(draft_id: int) -> Optional[sqlite3.Row]:
    with _lock:
        return get_conn().execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()


def update_draft(draft_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _lock:
        conn = get_conn()
        conn.execute(f"UPDATE drafts SET {cols} WHERE id = ?", (*fields.values(), draft_id))
        conn.commit()


def list_drafts(status: Optional[str] = None, limit: int = 100) -> list[sqlite3.Row]:
    with _lock:
        conn = get_conn()
        if status:
            return conn.execute(
                "SELECT * FROM drafts WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM drafts ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()


def due_auto_drafts(now: float) -> list[sqlite3.Row]:
    """Auto-Drafts, die gesendet werden duerfen."""
    with _lock:
        return get_conn().execute(
            "SELECT * FROM drafts WHERE status = 'pending' AND auto_send = 1 "
            "AND scheduled_send_at IS NOT NULL AND scheduled_send_at <= ? "
            "ORDER BY scheduled_send_at ASC",
            (now,),
        ).fetchall()


def drafts_due_for_recheck(now: float, interval_seconds: float,
                           limit: int = 25) -> list[sqlite3.Row]:
    """Wartende Drafts, deren Aktualitaetspruefung faellig ist.

    Aeltester Check zuerst, damit bei vielen Drafts trotzdem jeder drankommt.
    NULL in last_check_at (= noch nie geprueft) sortiert zuerst.
    """
    with _lock:
        return get_conn().execute(
            "SELECT * FROM drafts WHERE status = 'pending' "
            "AND (last_check_at IS NULL OR last_check_at <= ?) "
            "ORDER BY last_check_at IS NOT NULL, last_check_at ASC LIMIT ?",
            (now - interval_seconds, limit),
        ).fetchall()


def count_open_drafts() -> int:
    with _lock:
        row = get_conn().execute(
            "SELECT COUNT(*) AS c FROM drafts WHERE status = 'pending'").fetchone()
    return int(row["c"] if row else 0)


def has_open_draft(user_uuid: str) -> bool:
    with _lock:
        row = get_conn().execute(
            "SELECT 1 FROM drafts WHERE user_uuid = ? AND status IN ('pending','approved') LIMIT 1",
            (user_uuid,),
        ).fetchone()
    return row is not None


def get_open_draft(user_uuid: str) -> Optional[sqlite3.Row]:
    """Der (neueste) offene Entwurf eines Fans, oder None."""
    with _lock:
        return get_conn().execute(
            "SELECT * FROM drafts WHERE user_uuid = ? AND status IN ('pending','approved') "
            "ORDER BY created_at DESC LIMIT 1",
            (user_uuid,),
        ).fetchone()


def reactivation_sent_media(user_uuid: str) -> set[str]:
    """Menge der Selfies, die diesem Fan bereits als Reaktivierung geschickt wurden."""
    with _lock:
        rows = get_conn().execute(
            "SELECT media_uuid FROM reactivation_sent WHERE user_uuid = ?", (user_uuid,)
        ).fetchall()
    return {r["media_uuid"] for r in rows}


_API_COSTS_DDL = ("CREATE TABLE IF NOT EXISTS api_costs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                  "ts REAL, cost REAL, model TEXT, category TEXT)")


def add_api_cost(cost: float, model: str = "", category: str = "") -> None:
    """Schreibt die Kosten eines OpenRouter-Aufrufs (USD) mit Zeitstempel.
    Absturzsicher – ein Kosten-Fehler darf den Bot nie stoppen."""
    try:
        c = float(cost or 0)
    except (TypeError, ValueError):
        return
    if c <= 0:
        return
    try:
        with _lock:
            conn = get_conn()
            conn.execute(_API_COSTS_DDL)   # Tabelle sicherstellen
            conn.execute(
                "INSERT INTO api_costs (ts, cost, model, category) VALUES (?, ?, ?, ?)",
                (time.time(), c, model or "", category or ""),
            )
            conn.commit()
    except sqlite3.Error:
        pass


def api_cost_between(start_ts: float, end_ts: float) -> float:
    """Summe der OpenRouter-Kosten (USD) im Zeitraum [start, end). Absturzsicher -> 0.0."""
    try:
        with _lock:
            conn = get_conn()
            conn.execute(_API_COSTS_DDL)   # Tabelle sicherstellen (verhindert 'no such table')
            row = conn.execute(
                "SELECT COALESCE(SUM(cost), 0) AS c FROM api_costs WHERE ts >= ? AND ts < ?",
                (start_ts, end_ts),
            ).fetchone()
        return float(row["c"] or 0) if row else 0.0
    except sqlite3.Error:
        return 0.0


def add_reactivation_sent(user_uuid: str, media_uuids: list[str]) -> None:
    """Merkt sich, dass diese Selfies an den Fan gesendet wurden (kein Bild doppelt)."""
    if not media_uuids:
        return
    now = time.time()
    with _lock:
        conn = get_conn()
        conn.executemany(
            "INSERT OR IGNORE INTO reactivation_sent (user_uuid, media_uuid, sent_at) "
            "VALUES (?, ?, ?)",
            [(user_uuid, mu, now) for mu in media_uuids if mu],
        )
        conn.commit()


def last_sent_at(user_uuid: str) -> Optional[float]:
    with _lock:
        row = get_conn().execute(
            "SELECT MAX(sent_at) AS t FROM drafts WHERE user_uuid = ? AND status = 'sent'",
            (user_uuid,),
        ).fetchone()
    return row["t"] if row and row["t"] is not None else None


def count_drafts(status: str) -> int:
    with _lock:
        row = get_conn().execute(
            "SELECT COUNT(*) AS c FROM drafts WHERE status = ?", (status,)
        ).fetchone()
    return int(row["c"]) if row else 0


# -------------------------------------------------------------------- Log API
def log(level: str, category: str, message: str, detail: str = "") -> None:
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO logs(ts, level, category, message, detail) VALUES(?, ?, ?, ?, ?)",
            (time.time(), level, category, message, detail),
        )
        conn.commit()


def list_logs(limit: int = 200) -> list[sqlite3.Row]:
    with _lock:
        return get_conn().execute(
            "SELECT * FROM logs ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()


# ------------------------------------------------------------ Report-Auswertungen
def _scalar(sql: str, params: tuple) -> int:
    with _lock:
        row = get_conn().execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def sent_message_count(start: float, end: float) -> int:
    return _scalar("SELECT COUNT(*) FROM drafts WHERE status='sent' AND sent_at>=? AND sent_at<?",
                   (start, end))


def offers_sent_count(start: float, end: float) -> int:
    return _scalar("SELECT COUNT(*) FROM ppv_offers WHERE message_uuid IS NOT NULL "
                   "AND message_uuid!='' AND sent_at>=? AND sent_at<?", (start, end))


def purchases_count(start: float, end: float) -> int:
    return _scalar("SELECT COUNT(*) FROM ppv_offers WHERE purchased=1 "
                   "AND purchased_at>=? AND purchased_at<?", (start, end))


def revenue_between(start: float, end: float) -> int:
    return _scalar("SELECT SUM(price_cents) FROM ppv_offers WHERE purchased=1 "
                   "AND purchased_at>=? AND purchased_at<?", (start, end))


def activity_by_user(start: float, end: float) -> dict[str, dict]:
    """Pro Fan: Nachrichten (gesendet), PPV-Angebote (Bot), PPV-Käufe im Zeitraum."""
    out: dict[str, dict] = {}
    def _slot(u: str) -> dict:
        return out.setdefault(u, {"messages": 0, "offers": 0, "purchases": 0, "revenue_cents": 0})
    with _lock:
        conn = get_conn()
        for r in conn.execute("SELECT user_uuid, COUNT(*) c FROM drafts "
                              "WHERE status='sent' AND sent_at>=? AND sent_at<? GROUP BY user_uuid",
                              (start, end)):
            _slot(r["user_uuid"])["messages"] = int(r["c"])
        for r in conn.execute("SELECT user_uuid, COUNT(*) c FROM ppv_offers "
                              "WHERE message_uuid IS NOT NULL AND message_uuid!='' "
                              "AND sent_at>=? AND sent_at<? GROUP BY user_uuid", (start, end)):
            _slot(r["user_uuid"])["offers"] = int(r["c"])
        for r in conn.execute("SELECT user_uuid, COUNT(*) c, SUM(price_cents) rev FROM ppv_offers "
                              "WHERE purchased=1 AND purchased_at>=? AND purchased_at<? "
                              "GROUP BY user_uuid", (start, end)):
            slot = _slot(r["user_uuid"])
            slot["purchases"] = int(r["c"])
            slot["revenue_cents"] = int(r["rev"] or 0)
    return out


def offer_folders_by_user() -> dict[str, dict]:
    """Pro Fan: {offered: set(folders), purchased: set(folders)} aus allen ppv_offers."""
    out: dict[str, dict] = {}
    with _lock:
        rows = get_conn().execute(
            "SELECT user_uuid, folder, MAX(purchased) AS p FROM ppv_offers "
            "GROUP BY user_uuid, folder").fetchall()
    for r in rows:
        slot = out.setdefault(r["user_uuid"], {"offered": set(), "purchased": set()})
        if r["folder"]:
            slot["offered"].add(r["folder"])
            if r["p"]:
                slot["purchased"].add(r["folder"])
    return out


def chat_name_map() -> dict[str, tuple]:
    """{user_uuid: (handle, display_name)} fuer alle erfassten Chats."""
    with _lock:
        rows = get_conn().execute("SELECT user_uuid, handle, display_name FROM chats").fetchall()
    return {r["user_uuid"]: (r["handle"] or "", r["display_name"] or "") for r in rows}


def last_inbound_map() -> dict[str, float]:
    with _lock:
        rows = get_conn().execute(
            "SELECT user_uuid, last_inbound_at FROM chats WHERE last_inbound_at IS NOT NULL").fetchall()
    return {r["user_uuid"]: r["last_inbound_at"] for r in rows}


def set_conversion() -> list[dict]:
    """Pro Set: Angebote, Kaeufe, Umsatz, Conversion (aus ppv_offers)."""
    with _lock:
        rows = get_conn().execute(
            "SELECT folder, COUNT(*) offered, "
            "SUM(CASE WHEN purchased=1 THEN 1 ELSE 0 END) purchased, "
            "SUM(CASE WHEN purchased=1 THEN price_cents ELSE 0 END) revenue "
            "FROM ppv_offers WHERE folder IS NOT NULL AND folder!='' GROUP BY folder").fetchall()
    out = []
    for r in rows:
        offered = int(r["offered"] or 0)
        purchased = int(r["purchased"] or 0)
        out.append({"folder": r["folder"], "offered": offered, "purchased": purchased,
                    "revenue_cents": int(r["revenue"] or 0),
                    "conversion": (purchased / offered * 100) if offered else 0.0})
    return out


def non_buyers(min_offered: int = 3) -> list[dict]:
    """Fans mit vielen Angeboten aber 0 Kaeufen."""
    with _lock:
        rows = get_conn().execute(
            "SELECT user_uuid, COUNT(*) offered FROM ppv_offers GROUP BY user_uuid "
            "HAVING SUM(CASE WHEN purchased=1 THEN 1 ELSE 0 END)=0 AND COUNT(*)>=? "
            "ORDER BY offered DESC", (min_offered,)).fetchall()
    return [{"user_uuid": r["user_uuid"], "offered": int(r["offered"])} for r in rows]


def buyers_with_spend() -> list[dict]:
    """Fans mit >=1 Kauf: Gesamtumsatz + Anzahl Kaeufe."""
    with _lock:
        rows = get_conn().execute(
            "SELECT user_uuid, "
            "SUM(CASE WHEN purchased=1 THEN price_cents ELSE 0 END) revenue, "
            "SUM(CASE WHEN purchased=1 THEN 1 ELSE 0 END) purchases "
            "FROM ppv_offers GROUP BY user_uuid "
            "HAVING SUM(CASE WHEN purchased=1 THEN 1 ELSE 0 END)>0").fetchall()
    return [{"user_uuid": r["user_uuid"], "revenue_cents": int(r["revenue"] or 0),
             "purchases": int(r["purchases"])} for r in rows]


def revenue_kpis() -> dict:
    with _lock:
        row = get_conn().execute(
            "SELECT COUNT(*) offers, "
            "SUM(CASE WHEN purchased=1 THEN 1 ELSE 0 END) purchases, "
            "SUM(CASE WHEN purchased=1 THEN price_cents ELSE 0 END) revenue, "
            "COUNT(DISTINCT CASE WHEN purchased=1 THEN user_uuid END) buyers "
            "FROM ppv_offers").fetchone()
    offers = int(row["offers"] or 0)
    purchases = int(row["purchases"] or 0)
    revenue = int(row["revenue"] or 0)
    buyers = int(row["buyers"] or 0)
    return {"offers": offers, "purchases": purchases, "revenue_cents": revenue, "buyers": buyers,
            "avg_per_buyer_cents": int(revenue / buyers) if buyers else 0,
            "avg_price_cents": int(revenue / purchases) if purchases else 0,
            "conversion": (purchases / offers * 100) if offers else 0.0}


# --------------------------------------------------------------- PPV Folder API
def get_ppv_folder(name: str) -> Optional[sqlite3.Row]:
    with _lock:
        return get_conn().execute(
            "SELECT * FROM ppv_folders WHERE name = ?", (name,)
        ).fetchone()


def upsert_ppv_folder(name: str, **fields: Any) -> None:
    fields["updated_at"] = time.time()
    with _lock:
        conn = get_conn()
        existing = conn.execute("SELECT 1 FROM ppv_folders WHERE name = ?", (name,)).fetchone()
        if existing is None:
            conn.execute("INSERT INTO ppv_folders(name) VALUES(?)", (name,))
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE ppv_folders SET {cols} WHERE name = ?",
                         (*fields.values(), name))
        conn.commit()


def list_ppv_folders() -> dict[str, sqlite3.Row]:
    """Als Dict {name: row} fuer schnellen Merge mit der Live-Ordnerliste."""
    with _lock:
        rows = get_conn().execute("SELECT * FROM ppv_folders").fetchall()
    return {r["name"]: r for r in rows}


def enabled_ppv_folders() -> list[sqlite3.Row]:
    with _lock:
        return get_conn().execute(
            "SELECT * FROM ppv_folders WHERE enabled = 1 ORDER BY name"
        ).fetchall()


# ---------------------------------------------------------------- PPV Media API
def get_ppv_media(media_uuid: str) -> Optional[sqlite3.Row]:
    with _lock:
        return get_conn().execute(
            "SELECT * FROM ppv_media WHERE media_uuid = ?", (media_uuid,)
        ).fetchone()


def upsert_ppv_media(media_uuid: str, **fields: Any) -> None:
    fields["updated_at"] = time.time()
    with _lock:
        conn = get_conn()
        existing = conn.execute(
            "SELECT 1 FROM ppv_media WHERE media_uuid = ?", (media_uuid,)
        ).fetchone()
        if existing is None:
            conn.execute("INSERT INTO ppv_media(media_uuid) VALUES(?)", (media_uuid,))
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE ppv_media SET {cols} WHERE media_uuid = ?",
                         (*fields.values(), media_uuid))
        conn.commit()


def list_ppv_media(folder_name: str) -> dict[str, sqlite3.Row]:
    with _lock:
        rows = get_conn().execute(
            "SELECT * FROM ppv_media WHERE folder_name = ?", (folder_name,)
        ).fetchall()
    return {r["media_uuid"]: r for r in rows}


def folder_all_media_tags(folder_name: str) -> list[str]:
    """Vereinigt die selbst vergebenen Bild-Tags eines Ordners zu einer Tag-Liste.
    Damit qualifiziert ein Treffer in irgendeinem Bild das gesamte Set.
    Fanvue-KI-Tags werden bewusst NICHT beruecksichtigt (nur Anzeige)."""
    with _lock:
        rows = get_conn().execute(
            "SELECT tags FROM ppv_media WHERE folder_name = ?", (folder_name,)
        ).fetchall()
    tags: set[str] = set()
    for r in rows:
        for t in (r["tags"] or "").split(","):
            t = t.strip().lower()
            if t:
                tags.add(t)
    return sorted(tags)


# ----------------------------------------------------------------- PPV State API
def get_ppv_state(user_uuid: str) -> sqlite3.Row:
    with _lock:
        conn = get_conn()
        row = conn.execute("SELECT * FROM ppv_state WHERE user_uuid = ?", (user_uuid,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO ppv_state(user_uuid, updated_at) VALUES(?, ?)",
                         (user_uuid, time.time()))
            conn.commit()
            row = conn.execute("SELECT * FROM ppv_state WHERE user_uuid = ?", (user_uuid,)).fetchone()
        return row


def update_ppv_state(user_uuid: str, **fields: Any) -> None:
    get_ppv_state(user_uuid)  # sicherstellen, dass die Zeile existiert
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _lock:
        conn = get_conn()
        conn.execute(f"UPDATE ppv_state SET {cols} WHERE user_uuid = ?",
                     (*fields.values(), user_uuid))
        conn.commit()


def ppv_offered_sets(user_uuid: str) -> list[str]:
    row = get_ppv_state(user_uuid)
    try:
        return json.loads(row["offered_sets"] or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


def ppv_purchased_sets(user_uuid: str) -> list[str]:
    row = get_ppv_state(user_uuid)
    try:
        return json.loads(row["purchased_sets"] or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


def add_ppv_offered(user_uuid: str, folder_name: str) -> None:
    sets = ppv_offered_sets(user_uuid)
    if folder_name not in sets:
        sets.append(folder_name)
        update_ppv_state(user_uuid, offered_sets=json.dumps(sets))


def add_ppv_purchased(user_uuid: str, folder_name: str) -> None:
    sets = ppv_purchased_sets(user_uuid)
    if folder_name and folder_name not in sets:
        sets.append(folder_name)
        update_ppv_state(user_uuid, purchased_sets=json.dumps(sets))


def remove_ppv_purchased(user_uuid: str, folder_name: str) -> None:
    sets = ppv_purchased_sets(user_uuid)
    if folder_name in sets:
        sets.remove(folder_name)
        update_ppv_state(user_uuid, purchased_sets=json.dumps(sets))


def reset_ppv_offered(user_uuid: str) -> None:
    update_ppv_state(user_uuid, offered_sets="[]")


def remove_ppv_offered(user_uuid: str, folder_name: str) -> None:
    sets = ppv_offered_sets(user_uuid)
    if folder_name in sets:
        sets.remove(folder_name)
        update_ppv_state(user_uuid, offered_sets=json.dumps(sets))


# --------------------------------------------------------------- PPV Offer API
def create_ppv_offer(user_uuid: str, handle: str, folder: str, price_cents: int,
                     media_count: int, message_uuid: str) -> int:
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO ppv_offers(user_uuid, handle, folder, price_cents, media_count, "
            "message_uuid, sent_at, purchased) VALUES(?, ?, ?, ?, ?, ?, ?, 0)",
            (user_uuid, handle, folder, price_cents, media_count, message_uuid, time.time()),
        )
        conn.commit()
        return int(cur.lastrowid)


def create_purchased_offer(user_uuid: str, handle: str, folder: str, price_cents: int,
                           purchased_at: float) -> None:
    """Legt ein bereits als gekauft markiertes Angebot an (fuer CSV-Import)."""
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO ppv_offers(user_uuid, handle, folder, price_cents, media_count, "
            "message_uuid, sent_at, purchased, purchased_at) VALUES(?, ?, ?, ?, 0, '', ?, 1, ?)",
            (user_uuid, handle, folder, price_cents, purchased_at, purchased_at))
        conn.commit()


def has_offer_for_folder(user_uuid: str, folder: str) -> bool:
    with _lock:
        row = get_conn().execute(
            "SELECT 1 FROM ppv_offers WHERE user_uuid = ? AND folder = ? LIMIT 1",
            (user_uuid, folder)).fetchone()
    return row is not None


def list_ppv_offers(user_uuid: str, limit: int = 100) -> list[sqlite3.Row]:
    with _lock:
        return get_conn().execute(
            "SELECT * FROM ppv_offers WHERE user_uuid = ? ORDER BY sent_at DESC LIMIT ?",
            (user_uuid, limit),
        ).fetchall()


def open_ppv_offers(user_uuid: str) -> list[sqlite3.Row]:
    """Noch nicht als gekauft markierte Angebote mit Message-UUID."""
    with _lock:
        return get_conn().execute(
            "SELECT * FROM ppv_offers WHERE user_uuid = ? AND purchased = 0 "
            "AND message_uuid IS NOT NULL AND message_uuid != ''",
            (user_uuid,),
        ).fetchall()


def mark_ppv_offer_purchased(offer_id: int, purchased_at: float) -> None:
    with _lock:
        conn = get_conn()
        conn.execute("UPDATE ppv_offers SET purchased = 1, purchased_at = ? WHERE id = ?",
                     (purchased_at, offer_id))
        conn.commit()


def mark_offer_purchased_by_folder(user_uuid: str, folder: str, purchased_at: float) -> None:
    """Markiert das juengste offene Angebot dieses Sets als gekauft (manuelle Markierung)."""
    with _lock:
        conn = get_conn()
        row = conn.execute(
            "SELECT id FROM ppv_offers WHERE user_uuid = ? AND folder = ? AND purchased = 0 "
            "ORDER BY sent_at DESC LIMIT 1", (user_uuid, folder)).fetchone()
        if row:
            conn.execute("UPDATE ppv_offers SET purchased = 1, purchased_at = ? WHERE id = ?",
                         (purchased_at, row["id"]))
            conn.commit()


def set_folder_offers_purchased(user_uuid: str, folder: str, purchased: bool,
                                purchased_at: Optional[float] = None) -> None:
    """Setzt den Kauf-Status ALLER Angebote eines Sets fuer diesen Fan."""
    with _lock:
        conn = get_conn()
        conn.execute(
            "UPDATE ppv_offers SET purchased = ?, purchased_at = ? WHERE user_uuid = ? AND folder = ?",
            (1 if purchased else 0, purchased_at if purchased else None, user_uuid, folder))
        conn.commit()


def ppv_offered_folders(user_uuid: str) -> set:
    """Alle Ordner, die diesem Fan schon angeboten wurden (aus ppv_offers)."""
    with _lock:
        rows = get_conn().execute(
            "SELECT DISTINCT folder FROM ppv_offers WHERE user_uuid = ?", (user_uuid,)).fetchall()
    return {r["folder"] for r in rows if r["folder"]}


def folder_offer_dates(user_uuid: str) -> dict:
    """Je Ordner: juengstes Angebots- und Kaufdatum -> {folder: {offered_at, purchased_at}}."""
    with _lock:
        rows = get_conn().execute(
            "SELECT folder, MAX(sent_at) AS offered_at, "
            "MAX(CASE WHEN purchased = 1 THEN purchased_at END) AS purchased_at "
            "FROM ppv_offers WHERE user_uuid = ? GROUP BY folder", (user_uuid,)).fetchall()
    return {r["folder"]: {"offered_at": r["offered_at"], "purchased_at": r["purchased_at"]}
            for r in rows}


def delete_offers_for_folder(user_uuid: str, folder: str) -> None:
    """Loescht alle Angebots-Datensaetze eines Sets fuer diesen Fan (Reset)."""
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM ppv_offers WHERE user_uuid = ? AND folder = ?",
                     (user_uuid, folder))
        conn.commit()


def ensure_offer(user_uuid: str, handle: str, folder: str, price_cents: int = 0) -> None:
    """Stellt sicher, dass es mind. einen Angebots-Datensatz fuer (Fan, Set) gibt."""
    if not has_offer_for_folder(user_uuid, folder):
        create_ppv_offer(user_uuid, handle, folder, price_cents, 0, "")


def ppv_offer_stats(user_uuid: str) -> dict[str, Any]:
    with _lock:
        row = get_conn().execute(
            "SELECT COUNT(*) AS offered, "
            "SUM(CASE WHEN purchased = 1 THEN 1 ELSE 0 END) AS purchased, "
            "SUM(CASE WHEN purchased = 1 THEN price_cents ELSE 0 END) AS revenue_cents "
            "FROM ppv_offers WHERE user_uuid = ?", (user_uuid,)).fetchone()
    offered = row["offered"] or 0
    purchased = row["purchased"] or 0
    return {"offered": offered, "purchased": purchased,
            "revenue_cents": row["revenue_cents"] or 0,
            "conversion": (purchased / offered * 100) if offered else 0.0}
