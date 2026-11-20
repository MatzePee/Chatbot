"""Standard-Inhalt für den Doku-Reiter (Markdown). Über die GUI editierbar;
der bearbeitete Text wird in der DB unter dem Setting 'doku_markdown' gespeichert."""

DEFAULT_DOKU = """# Installationsanleitung (Ubuntu-Server)

Diese Anleitung beginnt an dem Punkt, an dem das Programm bereits in ein
Verzeichnis auf dem Server kopiert wurde – im Beispiel `/srv/fanvue/Fanvue_Chatbot`.

## 1. Benötigte Software installieren

```
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
python3 --version   # sollte 3.10 oder neuer sein
```

## 2. Konfiguration anlegen (.env)

```
cd /srv/fanvue/Fanvue_Chatbot
cp .env.example .env
nano .env
```

In der `.env` mindestens setzen:

- `HOST=0.0.0.0`  – damit die GUI im Netzwerk erreichbar ist
- `PORT=8000`
- `SECRET_KEY=...`  – z.B. mit `openssl rand -hex 32` erzeugen
- `FANVUE_REDIRECT_URI=http://127.0.0.1:8000/oauth/callback`

## 3. Erststart (baut die virtuelle Umgebung)

```
chmod +x run.sh
./run.sh
```

`run.sh` legt beim ersten Start die virtuelle Umgebung an und installiert alle
Pakete. Läuft die App, im Browser `http://SERVER-IP:8000` öffnen und danach mit
`Strg+C` wieder stoppen.

## 4. Rechte an den Dienst-Benutzer übergeben

Der Autostart-Dienst läuft als Benutzer `matze`. Falls du den Erststart als root
gemacht hast, einmal die Rechte übergeben:

```
sudo chown -R matze:matze /srv/fanvue/Fanvue_Chatbot
```

## 5. Autostart per systemd einrichten

```
sudo cp deploy/fanvue-chatbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fanvue-chatbot
systemctl status fanvue-chatbot
```

Der Bot startet nun automatisch beim Booten und wird bei Absturz neu gestartet.

## 6. System-Buttons aktivieren (Dienst-/Server-Neustart)

Damit die Buttons im Dashboard funktionieren:

```
sudo cp deploy/fanvue-admin /usr/local/bin/fanvue-admin
sudo chown root:root /usr/local/bin/fanvue-admin
sudo chmod 755 /usr/local/bin/fanvue-admin
sudo cp deploy/fanvue-admin.sudoers /etc/sudoers.d/fanvue-admin
sudo chmod 440 /etc/sudoers.d/fanvue-admin
sudo visudo -c
```

## 7. Mit Fanvue verbinden

Fanvue erlaubt `http://`-Redirects nur für `127.0.0.1`, nicht für eine LAN-IP.
Für die einmalige Verbindung deshalb einen SSH-Tunnel vom eigenen Rechner öffnen:

```
ssh -L 8000:localhost:8000 matze@SERVER-IP
```

Dann im Browser **http://127.0.0.1:8000** öffnen, unter Einstellungen die
Redirect-URI auf `http://127.0.0.1:8000/oauth/callback` setzen (identisch in der
Fanvue-App hinterlegen) und „Mit Fanvue verbinden". Danach kann der Tunnel wieder
geschlossen werden; die Sitzung bleibt über den Refresh-Token aktiv.

## Betrieb

- Dienst neu starten: `sudo systemctl restart fanvue-chatbot`
- Dienst stoppen: `sudo systemctl stop fanvue-chatbot`
- Logs ansehen: `journalctl -u fanvue-chatbot -f`
- Firewall (falls ufw aktiv): `sudo ufw allow 8000/tcp`

## Hinweise

- Die Datenbank liegt lokal unter `data/bot.db` und wird beim ersten Start erzeugt.
- Die App **nicht** gleichzeitig auf einem anderen Rechner starten – nur auf dem Server.
- Die Web-GUI hat keine Anmeldung. Für Zugriff über das LAN hinaus einen
  Reverse-Proxy mit Login und HTTPS davorsetzen.
"""
