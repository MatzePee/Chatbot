# Deployment auf dem Ubuntu-Server

Der Code liegt auf dem Server unter `/srv/fanvue/Fanvue_Chatbot` (zugleich der
SMB-Share `fanvue`). Der Server führt die App lokal aus; der Mac bearbeitet nur
die Dateien über den Share.

## 1. Einmalige Einrichtung

```bash
cd /srv/fanvue/Fanvue_Chatbot

# .env anlegen und ausfüllen
cp .env.example .env
nano .env
```

In der `.env` mindestens setzen:

```
HOST=0.0.0.0
PORT=8000
SECRET_KEY=<z.B. mit  openssl rand -hex 32  erzeugen>
FANVUE_REDIRECT_URI=http://192.168.20.16:8000/oauth/callback
```

Virtuelle Umgebung anlegen + Pakete installieren, App einmal testweise starten:

```bash
chmod +x run.sh
./run.sh
```

Im Browser vom Mac aus `http://192.168.20.16:8000` öffnen. Läuft es, mit `Strg+C`
wieder stoppen und den Autostart einrichten.

> Wichtig: In der Fanvue-App die Redirect-URI auf
> `http://192.168.20.16:8000/oauth/callback` setzen (identisch zur .env).

## 2. Autostart per systemd

```bash
# Service-Datei installieren
sudo cp /srv/fanvue/Fanvue_Chatbot/deploy/fanvue-chatbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fanvue-chatbot

# Status / Logs
systemctl status fanvue-chatbot
journalctl -u fanvue-chatbot -f
```

Der Bot startet nun automatisch beim Booten und wird bei Absturz neu gestartet.

## 3. Nach Code-Änderungen neu starten

Wenn ich (über den Share) Dateien geändert habe:

```bash
sudo systemctl restart fanvue-chatbot
```

## 4. System-Buttons im Dashboard aktivieren (Neustart + Update)

Damit die Buttons „Dienst neu starten", „Server neu starten" und
„Update installieren" funktionieren, braucht der Dienst-Benutzer `matze` das
Recht, genau diese drei Aktionen per sudo auszuführen. Einmalig einrichten:

> Vorher oben in `deploy/fanvue-admin` prüfen, ob `REPO`, `SVC_USER` und
> `SERVICE` zur eigenen Installation passen.

```bash
# Helferskript installieren (Root, ausführbar)
sudo cp /srv/fanvue/Fanvue_Chatbot/deploy/fanvue-admin /usr/local/bin/fanvue-admin
sudo chown root:root /usr/local/bin/fanvue-admin
sudo chmod 755 /usr/local/bin/fanvue-admin

# Eng begrenzte sudo-Regel installieren
sudo cp /srv/fanvue/Fanvue_Chatbot/deploy/fanvue-admin.sudoers /etc/sudoers.d/fanvue-admin
sudo chmod 440 /etc/sudoers.d/fanvue-admin
sudo visudo -c   # Syntax prüfen -> sollte "parsed OK" melden
```

Test (als matze):
```bash
sudo -u matze sudo -n /usr/local/bin/fanvue-admin restart-service
```
Startet der Dienst neu, sind die Buttons einsatzbereit.

> Sicherheitshinweis: Die Web-GUI hat keine Anmeldung. Jeder im LAN, der die GUI
> erreicht, kann damit den Dienst/Server neu starten und ein Update einspielen.
> Für Zugriff über das LAN hinaus unbedingt einen Reverse-Proxy mit Login/HTTPS
> davorsetzen — sonst ist der Update-Knopf ein offener Weg, fremden Code auf dem
> Server auszuführen.

## 5. Updates

Die Versionsanzeige und der Update-Knopf sitzen oben auf dem Dashboard. Als
Version gilt nur ein Git-Tag der Form `vX.Y.Z`; der Bot prüft alle 6 Stunden auf
neue Tags (Intervall in den Einstellungen änderbar).

Voraussetzung: Die Installation muss per `git clone` erfolgt sein und Lesezugriff
auf das Repository haben. Einrichtung und Release-Ablauf stehen in
[WEITERGABE.md](WEITERGABE.md).

## Firewall (falls ufw aktiv)

```bash
sudo ufw allow 8000/tcp
```

## Hinweise

- Die Datenbank liegt lokal unter `data/bot.db` (wird beim ersten Start erzeugt).
- Die App **nicht** gleichzeitig auf dem Mac starten – nur auf dem Server.
- Für Zugriff von außerhalb des LAN später einen Reverse-Proxy (Caddy/nginx) mit
  HTTPS vor Port 8000 setzen.
