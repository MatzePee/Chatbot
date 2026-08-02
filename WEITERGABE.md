# Weitergabe an eine zweite Person

Ziel: Deine Freundin betreibt eine **eigene, vollständig getrennte Instanz**.
Geteilt wird nur der Programmcode über GitHub — niemals Zugangsdaten,
niemals die Datenbank.

---

## Was geteilt wird und was nicht

| | Geteilt über GitHub | Bleibt bei jedem für sich |
|---|---|---|
| Programmcode (`app/`, `deploy/`, `run.sh`) | ✅ | |
| `.env.example` (Vorlage ohne Werte) | ✅ | |
| `.env` mit API-Keys | | ✅ eigene Datei |
| `data/bot.db` (Chats, Subscriber, Verkäufe) | | ✅ eigene Datenbank |
| Fanvue-OAuth-App | | ✅ eigene App |
| OpenRouter-Konto | | ✅ eigener Key |

Die `.gitignore` sorgt bereits dafür, dass `.env`, `data/` und `.venv/` gar
nicht erst in Git landen. **Vor dem ersten Push einmal kontrollieren:**

```bash
cd /srv/fanvue/Fanvue_Chatbot
git status --short          # hier dürfen .env und data/ NICHT auftauchen
git check-ignore -v .env data/bot.db     # muss beide als ignoriert melden
```

> **Wichtig:** Jede Person braucht eine **eigene Fanvue-Creator-App**. Die
> Redirect-URI ist an die jeweilige Server-Adresse gebunden — eine gemeinsam
> genutzte App funktioniert nicht und würde außerdem beide Konten verbinden.

---

## 1. Repository erstmalig befüllen (bei dir)

> **Auf dem Server ausführen, nicht über den SMB-Share vom Mac.** Samba
> blockiert das Löschen von Git-Sperrdateien; `git add` bricht dort mit
> „unable to index file" oder „index.lock: File exists" ab.

```bash
ssh matze@192.168.20.16
cd /srv/fanvue/Fanvue_Chatbot
bash deploy/git-setup.sh
```

Das Skript prüft **vor** dem Commit, dass weder `.env` noch `data/` noch
irgendein echter API-Key mitgeht, und bricht im Zweifel ab. Danach fragt es
nach Commit-Nachricht, Versions-Tag und ob hochgeladen werden soll.

Bei HTTPS fragt GitHub nach Benutzername und Passwort — als Passwort ein
**Personal Access Token** verwenden (github.com → Settings → Developer
settings → Personal access tokens, Berechtigung `repo`).

Von Hand ginge es auch so:

```bash
git init && git add -A
git status --short          # Kontrolle: keine .env, kein data/
git commit -m "Erste Fassung"
git branch -M main
git remote add origin https://github.com/MatzePee/Chatbot.git
git push -u origin main
git tag -a v1.0.0 -m "Erste Version" && git push --tags
```

---

## 2. Zugang für ihren Server einrichten (Deploy Key)

Das Repository ist privat. Damit ihr Server Updates ziehen kann, braucht er
einen **Deploy Key** — ein Schlüssel nur für dieses eine Repository, nur
lesend. Besser als ein persönliches Zugangstoken: er gilt für nichts anderes
und lässt sich jederzeit einzeln entziehen.

**Auf ihrem Server:**

```bash
sudo -u matze ssh-keygen -t ed25519 -C "fanvue-server" -f ~/.ssh/id_fanvue -N ""
cat ~/.ssh/id_fanvue.pub          # diesen Text kopieren
```

**Bei dir auf GitHub:** Repository → *Settings* → *Deploy keys* →
*Add deploy key* → Text einfügen, **Schreibzugriff NICHT anhaken**.

**Zurück auf ihrem Server**, damit git den Schlüssel benutzt:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/id_fanvue
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh -T git@github.com          # einmal bestätigen; "successfully authenticated" ist richtig
```

---

## 3. Erstinstallation auf ihrem Server

Auf einem **frisch installierten Ubuntu** genügt ein einziger Befehl:

```bash
curl -fsSL https://raw.githubusercontent.com/MatzePee/Chatbot/main/deploy/bootstrap.sh | sudo bash
```

Der Befehl holt das Repository, wählt automatisch die **neueste markierte
Version** und startet die Installation. Eine bestimmte Version geht auch:

```bash
curl -fsSL https://raw.githubusercontent.com/MatzePee/Chatbot/main/deploy/bootstrap.sh | sudo REF=v1.0.2 bash
```

Das Skript fragt nach Benutzer, Verzeichnis, Port und optional den Schlüsseln
und richtet dann selbstständig ein:

- Systempakete (`python3`, `python3-venv`, `git`, `sqlite3`)
- Dienst-Benutzer, falls noch nicht vorhanden — **nie root**
- virtuelle Umgebung und alle Python-Pakete
- `.env` mit zufällig erzeugtem `SECRET_KEY`, Rechte 600
- Autostart per systemd
- die eng begrenzte sudo-Regel für Neustart und Update
- Firewall-Freigabe, falls `ufw` aktiv ist

Am Ende prüft es, ob die Anwendung antwortet, und nennt die Adresse der
Oberfläche. Ein zweiter Lauf repariert eine unvollständige Installation,
ohne `.env` oder die Datenbank anzutasten.

Falls Schlüssel beim Installieren übersprungen wurden: Oberfläche öffnen,
unter „Einstellungen" eintragen und speichern.

> Die **Redirect-URI in ihrer Fanvue-App** muss exakt der Adresse entsprechen,
> die das Skript am Ende ausgibt — z. B.
> `http://192.168.20.50:8000/oauth/callback`.

---

## 4. Dein Ablauf für ein Update

Nur ein **Tag** macht einen Stand zu einer Version. Du kannst also beliebig
committen, ohne dass bei ihr etwas aufpoppt:

```bash
git add -A
git commit -m "Zeitkontext für Tageszeiten ergänzt"
git push

# ... weitere Commits ...

# Wenn ein Stand fertig getestet ist:
git tag -a v1.1.0 -m "Zeitkontext und Telegram-Meldungen"
git push --tags
```

Ab dem Push des Tags erscheint bei ihr innerhalb von 6 Stunden auf dem
Dashboard „Version 1.1.0 ist verfügbar" samt Liste der Commit-Titel seit
ihrer Version. Deshalb lohnen sich verständliche Commit-Nachrichten — sie
sind ihr Änderungsprotokoll.

**Versionsnummern** nach dem üblichen Schema `vHAUPT.NEBEN.KORREKTUR`:

- `v1.0.1` — Fehlerbehebung
- `v1.1.0` — neue Funktion
- `v2.0.0` — etwas, das nach dem Update Handarbeit erfordert

---

## 5. Was beim Update auf ihrem Server passiert

1. Datenbank und `.env` werden gesichert nach `data/backups/` (die letzten 10 bleiben)
2. Prüfung, dass die neue Version weder `.env` noch `data/` mitbringt — sonst Abbruch
3. `git fetch` und Wechsel auf das neueste Tag
4. `pip install -r requirements.txt` — falls neue Pakete dazugekommen sind
5. Dienst startet neu; fehlende Datenbankspalten legt das Programm selbst an

### Ihre Einstellungen bleiben erhalten

Alles, was sie einstellt, liegt in `data/bot.db` — Persona und System-Prompt,
Tagesrhythmus, API-Schlüssel, PPV-Regeln, Telegram-Daten sowie pro Fan die
Notizen und die abweichende Persona. Dieser Ordner steht in `.gitignore` und
wird von einem Update nicht angefasst; dasselbe gilt für `.env`.

Auch wenn du in einer neuen Version einen **Standardwert änderst**, bleibt ihr
eigener Wert bestehen: Beim Start werden nur Einstellungen angelegt, die es
noch gar nicht gibt. Neue Optionen kommen also dazu, vorhandene bleiben.

Was ein Update **verwirft**, sind Änderungen am Programmcode selbst — an
Dateien unter `app/`, `deploy/` und den Vorlagen. Auf ihrem Server sollte
deshalb niemand Dateien bearbeiten; alles Einstellbare gehört in die
Oberfläche.

> `deploy/sally_persona.txt` ist nur eine **Vorlage zum Hineinkopieren** und
> wird vom Programm nie gelesen. Die tatsächlich benutzte Persona steht in den
> Einstellungen und damit in der Datenbank.

### Wenn ein Update Probleme macht

```bash
cd /srv/fanvue/Fanvue_Chatbot
git tag -l 'v*' --sort=-v:refname       # verfügbare Versionen
git checkout -f v1.0.0                  # eine Version zurück
sudo systemctl restart fanvue-chatbot

# Falls auch die Datenbank zurück muss:
sudo systemctl stop fanvue-chatbot
cp data/backups/bot-<datum>.db data/bot.db
sudo systemctl start fanvue-chatbot
```

---

## Sicherheitshinweis

Die Weboberfläche hat **keine Anmeldung**. Wer sie erreicht, kann den Dienst
neu starten, den Server rebooten und jetzt auch ein Update einspielen. Solange
das nur im heimischen Netz läuft, ist das vertretbar.

Sobald die Oberfläche aus dem Internet erreichbar sein soll: unbedingt einen
Reverse-Proxy mit Passwortschutz und HTTPS davorsetzen (Caddy genügt dafür mit
wenigen Zeilen). Ohne das wäre der Update-Knopf ein offener Weg, fremden Code
auf dem Server auszuführen.
