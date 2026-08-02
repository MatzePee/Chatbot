# Fanvue Chatbot (MVP)

Ein lokal laufender Chatbot für Fanvue: er liest eingehende Chatnachrichten über die
Fanvue-API, generiert Antworten über OpenRouter und sendet sie – entweder nach
manueller Freigabe oder vollautomatisch. Steuerung über eine Web-GUI im Browser.

Bewusst als lokaler Web-Server gebaut (Python/FastAPI), damit derselbe Code später
1:1 auf einem Ubuntu-Server läuft – der „Port" ist dann nur ein Deployment.

## Funktionen

- **Zwei Modi, umschaltbar** – global und pro Chat: *Freigabe* (Bot schlägt vor, du gibst frei)
  oder *Auto* (Bot sendet selbst).
- **Freigabe-Queue** – Vorschläge ansehen, bearbeiten, neu generieren, freigeben oder verwerfen.
- **Master-Schalter / Kill-Switch** – alles mit einem Klick pausieren.
- **Pro-Fan-Steuerung** – Bot an/aus, eigener System-Prompt, Notizen (Fan-Gedächtnis).
- **Persona** – frei konfigurierbarer System-Prompt.
- **Guardrails** – Längenlimit, verbotene Wörter (blocken Auto-Send), Eskalations-Stichworte.
- **Menschenähnliches Verhalten** – zufällige Sende-Verzögerung, aktive Zeiten, Antwort-Cooldown.
- **OpenRouter** – Modell, Temperatur, Token-Limit, Verlaufstiefe frei wählbar.
- **Audit-Log** – jede Generierung und jeder Versand wird protokolliert.

## Voraussetzungen

- Python 3.10+
- Ein **Fanvue Creator-Account mit abgeschlossenem KYC** und einer angelegten OAuth-App
  (Client-ID, Client-Secret, Redirect-URI). Fanvue nutzt OAuth 2.0, **keine** API-Keys.
- Ein **OpenRouter-API-Key**.

## Fanvue-App einrichten

1. In deinem Fanvue-Creator-Bereich eine OAuth-App anlegen.
2. Als **Redirect-URI** exakt eintragen: `http://127.0.0.1:8000/oauth/callback`
3. Benötigte Scopes: `read:self`, `read:chat`, `write:chat`, `read:fan`, `offline_access`.
4. Client-ID und Client-Secret notieren.

## Start (macOS)

```bash
cd Fanvue_Chatbot
cp .env.example .env      # optional: Werte vorbelegen (SECRET_KEY setzen!)
./run.sh
```

Dann im Browser öffnen: <http://127.0.0.1:8000>

Alternativ manuell:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY="irgendeine-lange-zufallszeichenkette"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Erste Schritte in der GUI

1. **Einstellungen** öffnen → Fanvue Client-ID/Secret/Redirect-URI und OpenRouter-Key
   eintragen → speichern.
2. **„Mit Fanvue verbinden"** klicken → OAuth-Flow durchlaufen.
3. Persona/System-Prompt und Guardrails nach Wunsch anpassen.
4. Standard-Modus wählen (Freigabe empfohlen zum Start).
5. Auf dem **Dashboard** den Master-Schalter auf **Starten** stellen.
6. Eingehende Nachrichten erzeugen Entwürfe in der **Freigabe-Queue** (bzw. werden
   im Auto-Modus nach der Verzögerung automatisch gesendet).

## Wichtige Hinweise

- **Erst im Freigabe-Modus testen.** So siehst du, was der Bot schreiben würde, ohne
  dass etwas ungeprüft rausgeht.
- Der Bot antwortet nur, wenn die **letzte Nachricht vom Fan** kam, respektiert Cooldown
  und aktive Zeiten und erzeugt pro Fan nur einen offenen Entwurf gleichzeitig.
- Halte dich an die
  [Fanvue API Access & Usage Policy](https://legal.fanvue.com/api-policy) und die
  Plattform-Regeln (z.B. Kennzeichnung automatisierter/Team-Nachrichten, sofern gefordert).
- Client-Secret, Tokens und API-Keys liegen lokal in `data/bot.db`. Diese Datei nicht teilen.

## Später auf Ubuntu

Gleicher Code. Dort zusätzlich empfehlenswert:
- Redirect-URI auf die Server-Domain/HTTPS umstellen (in Fanvue-App **und** Einstellungen).
- Reverse-Proxy (nginx/Caddy) mit TLS vor uvicorn.
- systemd-Service für Autostart, GUI hinter Login/VPN absichern.

## Projektstruktur

```
app/
  main.py        FastAPI-App, Routen, OAuth-Callback
  db.py          SQLite-Layer (Settings, Tokens, Chats, Drafts, Logs)
  fanvue.py      Fanvue-API-Client + OAuth (PKCE, Token-Refresh)
  openrouter.py  Antwort-Generierung
  guardrails.py  Filter für aus- und eingehende Nachrichten
  poller.py      Hintergrund-Worker (Polling + Senden)
  templates/     HTML-Oberfläche
  static/        CSS
data/bot.db      wird beim ersten Start angelegt
```
