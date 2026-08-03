# Fanvue Chatbot

Ein selbst gehosteter Chat-Assistent für Fanvue: beantwortet Nachrichten über
ein Sprachmodell, bietet passenden Content zum Kauf an und lässt sich über eine
Weboberfläche steuern.

## Installation

Auf einem frisch installierten Ubuntu genügt ein Befehl:

```bash
curl -fsSL https://raw.githubusercontent.com/MatzePee/Chatbot/main/deploy/bootstrap.sh -o /tmp/fanvue-install.sh && sudo bash /tmp/fanvue-install.sh
```

> Das Skript wird bewusst **erst heruntergeladen und dann ausgeführt**, statt
> es direkt in `sudo bash` zu leiten. Bei `curl | sudo bash` belegt das Skript
> selbst die Standardeingabe und `sudo` spannt zusätzlich ein eigenes
> Pseudo-Terminal auf — die Rückfragen bleiben dann je nach System stehen.

Ganz ohne Rückfragen geht es mit Vorgaben:

```bash
sudo env SVC_USER=fanvue INSTALL_DIR=/srv/fanvue/Fanvue_Chatbot PORT=8000 \
  bash /tmp/fanvue-install.sh
```
```bash
curl -fsSL https://raw.githubusercontent.com/MatzePee/Chatbot/main/deploy/bootstrap.sh -o /tmp/fanvue-install.sh && sudo bash /tmp/fanvue-install.sh
```

Das Skript fragt nach Benutzer, Verzeichnis und Port und richtet dann alles
ein: Systempakete, Dienst-Benutzer, virtuelle Umgebung, `.env` mit zufälligem
`SECRET_KEY`, Autostart per systemd, eine eng begrenzte sudo-Regel für
Neustart und Update sowie die Firewall-Freigabe.

Am Ende nennt es die Adresse der Oberfläche. Dort unter „Einstellungen" die
Fanvue- und OpenRouter-Zugangsdaten eintragen, in der Fanvue-App die
angezeigte Redirect-URI hinterlegen und auf „Mit Fanvue verbinden" klicken.

Eine bestimmte Version statt der neuesten:

```bash
curl -fsSL https://raw.githubusercontent.com/MatzePee/Chatbot/main/deploy/bootstrap.sh | sudo REF=v1.0.2 bash
```

Ein erneuter Aufruf repariert eine unvollständige Installation, ohne `.env`
oder die Datenbank anzutasten.

## Funktionsumfang

- **Antworten** über OpenRouter, mit Persona, Tagesrhythmus und Sprach-Anker
- **Freigabe-Queue**: Entwürfe prüfen, bearbeiten, freigeben — oder Auto-Modus
- **PPV-Verkauf**: erkennt Kaufinteresse und bietet passende Sets an
- **Reaktivierung** stiller Fans
- **Telegram-Meldung**, wenn ein Entwurf endgültig hängen bleibt
- **Updates** per Knopfdruck aus dem Dashboard

## Betrieb

| | |
|---|---|
| Oberfläche | `http://<server>:8000` |
| Dienst | `systemctl status fanvue-chatbot` |
| Protokoll | `journalctl -u fanvue-chatbot -f` |
| Daten | `data/bot.db` (SQLite, wird nicht versioniert) |
| Konfiguration | `.env` — alles Weitere in der Oberfläche |

## Updates

Als Version gilt ein Git-Tag der Form `vX.Y.Z`. Der Bot prüft alle sechs
Stunden auf neue Tags und zeigt sie auf dem Dashboard mit den Änderungen seit
der installierten Version an. Installiert wird nur auf Knopfdruck; vorher wird
die Datenbank gesichert.

## Sicherheitshinweis

Die Weboberfläche hat **keine Anmeldung**. Wer sie erreicht, kann den Dienst
neu starten, den Server rebooten und Updates einspielen. Nur im eigenen Netz
betreiben — oder einen Reverse-Proxy mit Passwortschutz und HTTPS davorsetzen.

## Weitere Dokumentation

- [DEPLOY.md](DEPLOY.md) — Einrichtung von Hand, systemd, sudo-Regel
- [WEITERGABE.md](WEITERGABE.md) — an eine zweite Person weitergeben,
  Release-Ablauf, Rückkehr auf eine ältere Version
