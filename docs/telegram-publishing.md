# Telegram: eigene geplante Kanalbeiträge

Telegram ist eine unabhängige AutoPost-Plattform, keine Weiterleitung von X. Ein eigener Kanal erhält eigene Posts/Queue, Medienzuordnungen und Nutzungsverlauf, Zeitzone, Zeitfenster, Freigabe, Abstände, Persona und **Telegram-Textvorgaben** unter Kanäle → Einstellungen. Telegram erscheint separat im Kalender mit „Telegram planen“. Bibliotheksmedien können weiterhin unabhängig auf X/Fanvue verwendet werden. Telegram meldet keine erfundenen Follower- oder Postmetriken.

## Sichere Einrichtung

1. Kanäle → Kanal anlegen → Telegram-Kanal. Neue Kanäle sind **pausiert**, automatische Planung pro Kanal ist **aus**, Verbindung ist **nicht eingerichtet**.
2. Beim eigenen Bot in BotFather ein Token erstellen; Bot im Zielkanal als Administrator mit **Nachrichten posten** hinzufügen. Der Bot braucht keine eingehenden Updates. Bestehende Webhooks/Update-Consumer bleiben unangetastet.
3. Bot-Zugang → Bot-Token und öffentlichen `@kanalnamen` oder private numerische `-100…` Kanal-ID speichern. Tokens liegen ausschließlich im vorhandenen verschlüsselten Kanal-Zugangsspeicher. Leere/maskierte Eingaben erhalten das gespeicherte Token. GET liefert nur `has_bot_token`, niemals das Token.
4. „Testen“ ruft ausschließlich `getMe`, `getChat`, `getChatMember` auf: Bot-ID und Kanal-ID werden getrennt geprüft; Kanal muss vom Typ `channel` sein; Bot benötigt Administratorstatus und `can_post_messages=true`. **Es wird keine Testnachricht gesendet.** Speichern allein bestätigt keine Verbindung. Token-/Zielwechsel erfordert einen erneuten Test. „Trennen“ entfernt den lokalen Publikationszugang, widerruft aber nicht das Token in BotFather.
5. Erst nach bewusster Einrichtung bei Bedarf Kanal fortsetzen und automatische Planung in den AutoPost-Einstellungen aktivieren. Allgemeine Pause/Trockenlauf, Freigabe und bestehende Planerregeln gelten unverändert. Manuell: Kalender → Post anlegen → Telegram-Kanal wählen, Text/Medien auswählen, prüfen, speichern/freigeben oder „Jetzt veröffentlichen“.

Der Publikationsbot verwendet **nicht** die globale Telegram-Benachrichtigungsverbindung, nicht den Hermes-Bot und keine X-/Fanvue-Zugangsdaten. Auf demselben Bot vorhandene Webhooks werden nicht abgefragt/geändert. Im gemeinsamen Einstellungsbereich „Telegram“ bleibt die bisherige Benachrichtigungsfunktion unverändert; Kanalpublikation wird unter Kanäle konfiguriert.

## Tatsächlich implementierter Umfang / harte Grenzen

- Plaintext-Nachricht: maximal **4096 UTF-16-Einheiten**, inklusive Hashtags. HTML/Markdown wird wörtlich gesendet, ohne Parse-Modus.
- Einzelnes Foto mit Caption: JPEG oder PNG, maximal **10.000.000 Byte**; Breite+Höhe höchstens **10000**, Seitenverhältnis höchstens **20:1**. Prüfung erfolgt auch auf den tatsächlichen Uploadbytes nach dem optionalen Bildwasserzeichen.
- Einzelnes Video mit Caption: lokale **MP4**, maximal **50.000.000 Byte**. Bibliotheksimport akzeptiert einen **H.264-Videostream**, optional eine **AAC-Audiospur**, höchstens **16.777.216 Bildpixel**. Import benötigt `ffmpeg` und `ffprobe` auf dem App-Server. Fehlende Werkzeuge/andere Codecs/defekte Dateien werden ausdrücklich abgelehnt; es gibt keine heimliche Transkodierung. Metadaten/Kapitel werden beim Remux entfernt, Thumbnail wird aus erstem Frame erzeugt.
- Foto-/Video-Album (auch gemischt): **2–10 Medien**, zusammen maximal **100.000.000 Byte** (bewusst engeres lokales Sicherheitslimit). Reihenfolge bleibt erhalten. Die vollständige gemeinsame Caption steht nur am **ersten** Albumteil, keine Caption-Duplikation. Ein einzelnes Medium nutzt sendPhoto/sendVideo, kein ungültiges Ein-Medien-Album.
- Medien-Caption: maximal **1024 UTF-16-Einheiten** inklusive Hashtags. Text wird **nicht gekürzt**, ein zu großes manuelles Album **nicht in Einzelposts/Threads zerlegt**. Überlange/fehlende/mehrfach ausgewählte oder inkompatible Medien werden abgelehnt, nicht still weggelassen.
- Bibliothek: MP4-Upload, Standbild-Thumbnails, Wiedergabe in der Mediendetailansicht. Posteditor-Vorschau für Telegram zeigt Thumbnails/erste Frames. KI-Beschreibung eines Videos analysiert **nur den ersten Frame**, ausdrücklich als Video-Standbild gekennzeichnet, nicht Bewegung/Audio/ganzen Clip. Kein Videowasserzeichen; vorhandene Wasserzeichenregeln gelten nur für Fotos.
- Kein GIF/WebP als Telegram-Foto, keine Dokumente, Live Photos, Audio-only, Geschichten, bezahlte Medien, automatische Pins, Fanvue-Preise/Zielgruppen oder X-Reply-Threads. Unsupported options werden abgelehnt. `allow_paid_broadcast=false` wird immer ausdrücklich gesendet; keine Käufe/Stars-Ausgaben.
- Text-/Bildgenerierung nutzt Telegram-Regeln und das jeweils korrekte Caption-/Textbudget. Telegram-Textvorgaben ergänzen nur diesen Kanal; Persona kann unabhängig ausgewählt werden. Für API-basierte Kanalanlage ohne UI kann die Bildanzahl-Policy enger sein; Telegram-Hardlimit bleibt immer 10.

## Bestätigung und Duplikatvermeidung

Nur `ok=true` plus zurückgemeldete passende kanonische `chat.id` und vollständige gültige Message-IDs gelten als Erfolg. Alle Album-IDs und die kanonische Kanal-ID werden in `generation_meta.telegram_delivery` behalten, zusätzlich kommaseparierte IDs in `external_post_id`. Öffentlicher Link: `https://t.me/<username>/<message_id>`; privater Link: `https://t.me/c/<id-ohne--100>/<message_id>` (nur für Kanalmitglieder zugänglich). Öffentliche Links verwenden den zuletzt geprüften Benutzernamen; bei Namenswechsel erneut testen.

Ein atomarer lokaler Compare-and-set-Claim wird **vor dem Senderequest committed**. Abbruch/Timeout/Serverfehler, unpassende Antwort oder unvollständiges Album werden als **unklar** behandelt; bestätigte Teil-IDs bleiben erhalten. Ein Crash kann `pending` hinterlassen; auch das blockiert weiteren Versand. Derselbe Post wird dann auch mit force nicht blind erneut gesendet. Der normale Auto-Retry-Job überspringt alle Telegram-Fehler; bei bestätigter Ablehnung kann nach Prüfung/Korrektur bewusst manuell erneut veröffentlicht werden. Die Auto-Retry-Checkbox bleibt als gemeinsame Policy erhalten, für Telegram gilt diese Sicherheitsausnahme. Bei unklarem Versand zuerst Zielkanal manuell prüfen; es gibt absichtlich keinen automatischen „Status zurücksetzen und erneut senden“-Knopf.

**Bot API bietet weder einen generischen GET eines ausgehenden Posts anhand ID noch einen Idempotency-Key.** API-Antworten sind Versandbestätigungen, kein späterer Live-Readback. Keine Exactly-once-Garantie über Provider-/DB-Ausfälle hinweg; im Zweifel wird lieber gestoppt als doppelt veröffentlicht. Ein neuer, bewusst duplizierter Post ist eine separate Veröffentlichung und muss entsprechend geprüft werden.

## Kompatibilität / Auslieferung

Die vorhandene additive Schema-Synchronisierung ergänzt nur `posting_policy.text_guidelines TEXT NOT NULL DEFAULT ''`. Bestehende Werte bleiben erhalten; kein neues Bot-Token in Konfigurationsdateien, keine bestehende Produktionsdatenbank in diesem Arbeitsschritt geöffnet oder migriert. Migration wurde auf synthetischen Altbeständen und mit dem bestehenden Release-Smoke geprüft. Quellcode-Auslieferung allein aktiviert keinen laufenden Python-Prozess. Neustart/GitHub-Publikation/Live-Posting benötigen gesonderte Freigabe. Ohne Bot-Zugang ist die Funktion sicher getrennt.

## Referenz / Offline-Prüfung

Offizielle Bot API, abgerufen bei Implementierung: https://core.telegram.org/bots/api/ und Methoden `sendmessage.md`, `sendphoto.md`, `sendvideo.md`, `sendmediagroup.md`, `getchatmember.md` unter https://core.telegram.org/bots/api/methods/ .

Automatisierte Tests: `pytest tests/autoposter/test_telegram*.py tests/autoposter/test_planner_generation.py`; MockTransport prüft exakte URL/HTTP-Methode, JSON/Multipart und Album-Attachments. Vier echte API-Route-Durchläufe auf synthetischer DB decken Text/Foto/Video/gemischtes Album ab, ohne Lifespan/Worker. Chromium `tests/telegram_ui.cjs` und `tests/settings_hub_ui.cjs` erfüllen sämtliche Requests offline aus synthetischen Fixtures. Keine echten Provider-Nachrichten, keine echten Zugangsdaten und kein Live-Verbindungstest ausgeführt.
