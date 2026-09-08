# GitHub-Upload und Update-Mechanik — Beschreibung zur Übernahme

Dieses Dokument beschreibt, wie im Projekt **Fanvue_Chatbot** das Veröffentlichen
nach GitHub („Upload") und das Einspielen neuer Versionen („Download"/Update)
gelöst ist. Es richtet sich an eine andere Claude-Code-Session, die dasselbe
Muster in einem anderen Projekt nachbauen soll.

Der Aufbau ist bewusst so gewählt, dass er für **selbst gehostete Python-Web-Apps
auf einem Linux-Server mit systemd** funktioniert, die von einer nicht-technischen
Person bedient werden. Zwei Anforderungen prägen jede Entscheidung:

1. Ein Update darf **niemals** Konfiguration oder Nutzdaten überschreiben.
2. Die Bedienerin klickt Knöpfe in einer Weboberfläche — sie öffnet keine Konsole.

---

## 1. Grundmodell in einem Satz

> Committen darf man beliebig. **Als Update gilt nur ein Git-Tag der Form `vX.Y.Z`.**

Das ist der Kern. Der Entwickler-Server pusht Commits, wann er will; bei den
laufenden Instanzen taucht erst dann ein Update auf, wenn ein Tag gesetzt wurde.
Damit braucht es keine Release-API, keinen Paketserver und keinen zweiten Kanal —
Git allein trägt Code *und* Versionsstand.

Daraus folgt der gesamte Rest:

| Frage | Antwort im Code |
|---|---|
| Welche Version läuft hier? | `git describe --tags` |
| Gibt es etwas Neueres? | `git fetch --tags` + höchstes `v*`-Tag numerisch vergleichen |
| Was hat sich geändert? | `git log <alt>..<neu> --pretty=%s` = Changelog |
| Wie installiere ich? | `git checkout -f <tag>` + Abhängigkeiten + Neustart |

---

## 2. Drei Ebenen, strikt getrennt

```
┌─ Weboberfläche (FastAPI, läuft als Dienst-Benutzer) ──────────────┐
│  /upload      → app/publisher.py   hochladen (schreibt ins Repo)  │
│  Dashboard    → app/updater.py     prüfen und anzeigen (nur lesen)│
└───────────────────────────┬───────────────────────────────────────┘
                            │  sudo -n (genau 3 erlaubte Argumente)
┌───────────────────────────▼───────────────────────────────────────┐
│  /usr/local/bin/fanvue-admin   (root)                             │
│    restart-service | reboot | update                              │
│    → sichert DB, wechselt auf das Tag, pip install, Dienst neu    │
└───────────────────────────────────────────────────────────────────┘
```

**Warum installiert der Webprozess nicht selbst?** Er würde sich beim
`git checkout` die eigenen Quelldateien unter den Füßen wegziehen und beim
`systemctl restart` die noch laufende HTTP-Antwort abwürgen. Außerdem bleibt die
sudo-Regel so auf drei feste Kommandos begrenzt statt auf „darf git und pip als
root".

---

## 3. Upload: `app/publisher.py`

Die Seite `/upload` zeigt geänderte Dateien, schlägt die nächste Versionsnummer
vor und hat einen Knopf „Veröffentlichen".

### 3.1 Ablauf von `publish(message, tag, do_push)` — die Reihenfolge ist tragend

```
1. guard()                     → Sicherheitsprüfung, BLOCKIERT bei Befund
2. git config user.name/email  → nur wenn hinterlegt
3. git add -A
4. git commit                  → nur wenn wirklich etwas vorgemerkt ist
5. git fetch origin <branch>
6. git rebase origin/<branch>  → falls "behind" > 0
7. git tag -a <tag>            ← ERST JETZT
8. git push origin HEAD:<branch>
9. git push origin <tag>
```

**Schritt 6 vor Schritt 7 ist der wichtigste Punkt der ganzen Datei.** Ein vor dem
Rebase gesetztes Tag hängt danach an einem Commit, der nicht mehr im Branch liegt.
Das Tag wird zwar gepusht, zeigt aber auf toten Code — und der Fehler fällt erst
auf, wenn jemand dieses Release installiert.

Schritt 5/6 überhaupt zu haben, ist die zweite Lehre: Sobald man einmal über die
GitHub-Weboberfläche eine README ändert, scheitert jeder spätere Push mit
`non-fast-forward`. Für eine Bedienerin ohne Git-Kenntnisse ist das eine Sackgasse.
Der Rebase löst das automatisch; nur bei echtem Konflikt bricht er ab und gibt
einen konkreten Befehl aus, statt die Git-Fehlermeldung durchzureichen.

### 3.2 Der Token wandert nie in Kommandozeile oder Konfiguration

Naheliegend, aber falsch: `https://user:token@github.com/...` als Remote-URL. Der
Token steht dann dauerhaft im Klartext in `.git/config`. Ebenso falsch: den Token
als Argument übergeben — jeder Benutzer sieht ihn per `ps`.

Gelöst über ein temporäres Askpass-Skript, das den Wert aus der **Umgebung des
Kindprozesses** liest:

```python
def _askpass_env() -> tuple[dict[str, str], Optional[str]]:
    token = str(db.get_setting("github_token", "") or "").strip()
    if not token:
        return {}, None
    user = str(db.get_setting("git_user_name", "") or "git").strip() or "git"
    fd, path = tempfile.mkstemp(prefix=".askpass-", suffix=".sh", dir=REPO_DIR)
    with os.fdopen(fd, "w") as fh:
        fh.write('#!/bin/sh\ncase "$1" in\n'
                 '  *[Uu]sername*) printf "%s\\n" "$GIT_ASKPASS_USER" ;;\n'
                 '  *) printf "%s\\n" "$GIT_ASKPASS_TOKEN" ;;\n'
                 'esac\n')
    os.chmod(path, stat.S_IRWXU)          # 0700, nur der Dienst-Benutzer
    return {"GIT_ASKPASS": path, "GIT_ASKPASS_USER": user,
            "GIT_ASKPASS_TOKEN": token}, path
```

Aufgeräumt wird im `finally`-Block, damit das Skript auch nach einem Fehler
verschwindet. Zusätzlich läuft jede git-Ausgabe durch `_scrub()`, das den Token und
`https://…@`-Muster durch `***` ersetzt — sonst landet er über eine Fehlermeldung
doch noch im Log oder in der Weboberfläche.

Alle git-Aufrufe setzen außerdem `GIT_TERMINAL_PROMPT=0` und `GIT_ASKPASS=true`.
Ohne das hängt ein Aufruf ohne Zugangsdaten ewig an einer Passwortabfrage, die
niemand sieht.

### 3.3 `guard()` — blockiert, warnt nicht

Ein einmal veröffentlichter Schlüssel lässt sich nicht zurückholen. Deshalb ist das
kein Hinweis, sondern ein Abbruch. Drei Prüfungen:

1. **`.gitignore` enthält `.env` und `data/`** — fehlt die Datei ganz, sofortiger Abbruch.
2. **Kein verbotener Pfad im Commit** — `^(\.env$|data/|\.venv/|exports/)`.
3. **Kein echter Schlüssel im Inhalt** aller getrackten und neuen Dateien:

```python
_SECRET_PATTERNS = re.compile(
    r"sk-or-v1-[A-Za-z0-9]{24}"          # OpenRouter
    r"|sk-[A-Za-z0-9]{32,}"              # OpenAI u.a.
    r"|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}" # Telegram-Bot-Token
    r"|ghp_[A-Za-z0-9]{30,}"             # GitHub PAT (klassisch)
    r"|github_pat_[A-Za-z0-9_]{30,}"     # GitHub PAT (fein granular)
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
```

Die Muster sind **eng** gefasst, damit Platzhalter in `.env.example`
(`dein-key-hier`) keinen Fehlalarm auslösen. Ein Guard, der ständig grundlos
blockiert, wird abgeschaltet — und dann schützt er gar nicht mehr.

### 3.4 `normalize_tag()` — das führende `v` ist keine Kosmetik

```python
def normalize_tag(tag: str) -> str:
    """'1.2.0' oder ' V1.2.0 ' -> 'v1.2.0'. Ungültiges -> ''."""
    raw = re.sub(r"^V", "v", (tag or "").strip())
    parsed = updater.parse_version(raw)
    return f"v{parsed[0]}.{parsed[1]}.{parsed[2]}" if parsed else ""
```

Der Updater sucht mit `git tag -l 'v*'`. Ein Tag `1.2.4` ohne `v` würde bei keiner
Instanz jemals als Update auftauchen — und der Fehler fällt erst Wochen später auf,
wenn sich jemand wundert, warum das Release nicht ankommt.

### 3.5 Zwei kleine Fallen in `status()`

**Porcelain-Ausgabe nicht mit festem Index schneiden.** Das Format ist `XY<leer>Pfad`,
aber X *oder* Y können selbst ein Leerzeichen sein (`" M pfad"` gegenüber `"M  pfad"`).
Ein `line[3:]` liefert dann `pp/main.py` statt `app/main.py`:

```python
code, path = line[:2].strip(), line[2:].lstrip()
if " -> " in path:                       # Umbenennung
    path = path.split(" -> ", 1)[1]
path = path.strip().strip('"')
```

**„Vorne/hinten" rechnet gegen den zuletzt geholten Stand.** `git rev-list
--left-right --count origin/<branch>...HEAD` sagt ohne vorheriges `fetch` nichts über
den echten Zustand auf GitHub aus. In der Anzeige ist das dokumentiert; beim Push
wird deshalb immer frisch geholt.

### 3.6 Verwaistes Tag aus einem Fehlversuch

Scheitert ein Upload nach dem Taggen, existiert das Tag lokal, zeigt nach dem
nächsten Rebase aber ins Leere. `_set_tag()` unterscheidet drei Fälle:

- Tag zeigt auf einen Commit **im aktuellen Branch** → in Ordnung, überspringen.
- Tag liegt **bereits auf GitHub** → Abbruch mit der Bitte um eine neue Nummer.
  (Ein veröffentlichtes Tag zu verschieben ist die schlimmste Variante — andere
  Instanzen haben es womöglich schon geholt.)
- Sonst → lokales Überbleibsel, wird gelöscht und auf HEAD neu gesetzt.

---

## 4. Download/Update: `app/updater.py` + `deploy/fanvue-admin`

### 4.1 Versionsvergleich numerisch, nicht als Text

```python
_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.]?(.+))?$")

def parse_version(tag):
    m = _TAG_RE.match((tag or "").strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
```

Als Zeichenkette verglichen wäre `v1.9.0` größer als `v1.10.0`. Das fällt beim
zehnten Release auf, also genau dann, wenn schon Instanzen draußen sind.

### 4.2 `check(fetch=True)`

Holt Tags, ermittelt das höchste, vergleicht mit dem installierten Stand und legt
als Changelog die Commit-Titel dazwischen bei (`git log --no-merges --pretty=%s`).
Das Ergebnis wird als JSON in der Einstellung `update_state` abgelegt.

Zwei getrennte Wege verhindern, dass der Seitenaufbau am Netzwerk hängt:

- `cached_state()` — liest nur die gespeicherte Antwort, **kein Netzwerk**. Das nutzt jeder Seitenaufruf.
- `check(fetch=True)` — echte Abfrage. Nur aus dem Hintergrund-Zyklus oder auf ausdrücklichen Knopfdruck.

`is_due()` drosselt den Hintergrund-Zyklus über den Zeitstempel der letzten Prüfung
(Standard: alle 6 Stunden). Der Poller ruft `update_check_cycle()` einfach in jedem
Durchlauf auf; die Drosselung sitzt in der Funktion, nicht beim Aufrufer.

Gemeldet wird per Telegram **nur beim ersten Auftauchen** einer Version
(`latest != before`), sonst käme alle sechs Stunden dieselbe Nachricht.

### 4.3 `install()` — die einzige Stelle mit sudo

```python
subprocess.run(["sudo", "-n", "/usr/local/bin/fanvue-admin", "update"],
               capture_output=True, timeout=300)
```

`-n` heißt: nie interaktiv nach einem Passwort fragen, lieber scheitern. Danach wird
`update_state` geleert, damit nach dem Neustart frisch geprüft wird.

### 4.4 Das Root-Helferskript `deploy/fanvue-admin`

Vier Schritte, jeder aus einem konkreten Grund:

**1. Sichern.** DB und `.env` liegen außerhalb der Versionierung und werden von git
nicht angefasst — aber ein Update kann Schema-Migrationen auslösen. `sqlite3
.backup` ist auch bei laufendem Zugriff sicher (WAL-Modus), mit `cp` als Rückfall.
Die letzten 10 Sicherungen bleiben.

**2. Neuestes Tag holen — und vorher prüfen, was drinsteckt:**

```bash
BAD="$(as_user git ls-tree -r --name-only "$TAG" \
       | grep -E '^(\.env|\.env\.local)$|^data/' || true)"
[ -n "$BAD" ] && { echo "ABBRUCH: Version $TAG würde deine Konfiguration überschreiben" >&2; exit 1; }
```

Das ist die Antwort auf „Updates dürfen nie Einstellungen überschreiben". Hätte
jemand versehentlich `.env` oder `data/` mit ins Repository committet, würde
`git checkout -f` genau diese Dateien überschreiben — Zugangsdaten und die komplette
Datenbank der Nutzerin wären weg. Das ist der einzige Weg, auf dem ein Update
Einstellungen zerstören könnte, also wird er vorher versperrt.

> **Fallstrick, in den ich gelaufen bin:** ein zu weites Muster wie `\.env` trifft
> auch `.env.example` — und die *gehört* ins Repository. Damit hätte der Guard
> jedes Update blockiert. Deshalb die Anker: `^(\.env|\.env\.local)$|^data/`.

**3. `git checkout -f "$TAG"`** — verwirft lokale Änderungen am Programmcode.
`data/` und `.env` sind per `.gitignore` ausgenommen und bleiben unangetastet.
Danach `pip install -r requirements.txt`, weil eine neue Version neue Pakete
brauchen kann.

**4. Neustart entkoppelt:**

```bash
systemd-run --no-block --collect --unit="fanvue-update-$$" systemctl restart "$SERVICE"
```

Ohne `systemd-run --no-block` bricht der Neustart den eigenen Aufruf ab, bevor er
zurückmelden kann. Die Bedienerin sieht dann einen Verbindungsfehler statt „Update
abgeschlossen".

**Wichtig:** git und pip laufen über `runuser -u "$SVC_USER"` als Dienst-Benutzer,
nicht als root. Sonst gehören die Dateien danach root und der Dienst kann sie nicht
mehr schreiben.

### 4.5 sudoers — Argumente einzeln aufführen

```
matze ALL=(root) NOPASSWD: /usr/local/bin/fanvue-admin restart-service, \
                           /usr/local/bin/fanvue-admin reboot, \
                           /usr/local/bin/fanvue-admin update
```

Nach `/etc/sudoers.d/fanvue-admin` mit Rechten `0440`. Stünde dort nur der
Skriptpfad ohne Argumente, dürfte **jedes beliebige** Argument übergeben werden —
die Beschränkung wäre wertlos. Das Skript selbst hat entsprechend ein `case` mit
Positivliste und `exit 1` im Default-Zweig.

---

## 5. Die Einstellungen

### Upload (Seite `/upload`, eigene Route `/upload/settings`)

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `git_remote_url` | `""` | z. B. `https://github.com/Name/Repo.git`. Wird beim Push gegen `origin` abgeglichen und bei Abweichung gesetzt. |
| `git_branch` | `main` | Zielbranch für Push und Rebase. |
| `git_user_name` | `""` | Commit-Autor **und** GitHub-Benutzername fürs Askpass. |
| `git_user_email` | `""` | Commit-Autor. |
| `github_token` | `""` | Personal Access Token mit Recht `repo`. |
| `git_commit_default` | — | Vorbelegung der Commit-Nachricht. |

Die Upload-Einstellungen stehen bewusst **nicht** im allgemeinen Einstellungs-Formular,
sondern auf `/upload` mit eigener Route. Grund: das Token-Feld.

> **Ein leeres Token-Feld bedeutet „unverändert lassen", nicht „löschen".** Sonst
> würde jedes versehentliche Speichern den Token entfernen. Zum bewussten Löschen
> gibt es `clear_token=1`.

```python
token = str(form.get("github_token", "")).strip()
if token:
    db.set_setting("github_token", token)
elif str(form.get("clear_token", "")) == "1":
    db.set_setting("github_token", "")
```

### Update (Abschnitt „Updates" in den Einstellungen)

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `update_check_enabled` | `True` | Hintergrundprüfung an/aus. |
| `update_check_interval_hours` | `6` | Prüfabstand. |
| `update_notify_telegram` | `True` | Neue Version per Telegram melden. |
| `update_state` | `""` | **Vom Programm gepflegt**, nicht bedienen. JSON mit `checked_at`, `current`, `latest`, `update_available`, `changelog`, `error`. |
| `app_base_url` | `""` | z. B. `http://192.168.20.16:8000` — für klickbare Links in Telegram-Meldungen. |

Installiert wird **nur auf Knopfdruck** im Dashboard, nie automatisch. Das ist eine
bewusste Produktentscheidung: ein selbsttätiges Update, das nachts eine laufende
Instanz umstellt, ist bei einer Person ohne Konsolenzugang nicht zu vertreten.

---

## 6. Oberfläche

- **Dashboard:** Karte mit installierter Version, Zustand aus `cached_state()`,
  Changelog-Liste und Knopf „Jetzt aktualisieren" (POST auf `/system/update`).
  `GET /api/update-check?force=1` erzwingt eine frische Abfrage.
- **`/upload`:** Liste der geänderten Dateien, Befunde von `guard()` in Rot,
  Versionsvorschläge aus `next_versions()` (patch/minor/major), Commit-Nachricht,
  Kästchen „hochladen" (aus = nur lokal committen und taggen).
  `GET /api/upload-status` liefert dasselbe als JSON zum Nachladen.
- Alle Formularaktionen antworten mit **303 Redirect** und Meldung im
  Query-String — sonst löst ein Neuladen der Seite den Upload erneut aus.

---

## 7. Fallstricke, die wirklich aufgetreten sind

Diese Liste ist der eigentliche Wert des Dokuments. Jeder Punkt hat Zeit gekostet.

1. **Tag vor dem Rebase gesetzt** → Tag zeigt auf einen Commit außerhalb des Branches. Reihenfolge: fetch → rebase → tag → push.
2. **Tag ohne `v`** angenommen → `git tag -l 'v*'` findet es nie, das Release kommt bei keiner Instanz an.
3. **Guard-Regex `\.env` zu weit** → traf `.env.example` und hätte jedes Update blockiert. Anker setzen: `^(\.env|\.env\.local)$`.
4. **Porcelain mit festem Index geschnitten** → `pp/main.py` statt `app/main.py`. `line[2:].lstrip()` nutzen.
5. **Kein `fetch` vor dem Push** → `non-fast-forward`, sobald jemand über die GitHub-Weboberfläche etwas ändert. Für Laien eine Sackgasse.
6. **`systemctl restart` direkt aufgerufen** → bricht die eigene HTTP-Antwort ab. `systemd-run --no-block --collect`.
7. **git als root laufen lassen** → Dateien gehören danach root, der Dienst kann nicht mehr schreiben. `runuser -u "$SVC_USER"`.
8. **sudoers ohne Argumente** → jedes Argument erlaubt, die Beschränkung ist wertlos.
9. **Textueller Versionsvergleich** → `v1.9.0 > v1.10.0`. Numerisch vergleichen.
10. **Leeres Token-Feld als „löschen" gewertet** → Token weg nach jedem Speichern.
11. **Setup-Skript über den SMB-Share ausgeführt** → Samba blockiert das Löschen von Git-Sperrdateien, git bricht mit `index.lock: File exists` ab. Solche Skripte gehören auf den Server, per SSH.
12. **Interaktive Rückfrage im Skript nur aus `/dev/tty` gelesen** → bei `curl … | bash` gilt stillschweigend die Vorgabe, hier also ungewollt „ja, hochladen". Prüfen mit `if { : </dev/tty; } 2>/dev/null` — die bloße Existenz der Datei genügt nicht, sie kann vorhanden und trotzdem nicht nutzbar sein.

---

## 8. Übernahme in ein anderes Projekt

**Direkt übernehmbar** (nur Bezeichner anpassen):

- `app/updater.py` — vollständig generisch bis auf `REPO_DIR` und den Skriptnamen in `install()`.
- `app/publisher.py` — generisch bis auf `_FORBIDDEN_PATHS` und `_SECRET_PATTERNS`.
- `deploy/fanvue-admin` + `.sudoers` — `REPO`, `SVC_USER`, `SERVICE` und den Skriptnamen ersetzen.

**Anzupassen:**

1. `_FORBIDDEN_PATHS` auf die Pfade des neuen Projekts (was darf nie hochgeladen werden?).
2. `_SECRET_PATTERNS` auf die dort tatsächlich verwendeten Anbieter-Schlüssel — eng halten.
3. Den `BAD`-Guard in `fanvue-admin` spiegelbildlich dazu (was darf ein Update nie überschreiben?).
4. Einstellungs-Speicher: hier eine `settings`-Tabelle in SQLite mit `db.get_setting/set_setting`. Bei anderer Ablage nur diese Aufrufe ersetzen.
5. Kein systemd? Dann Schritt 4 des Helferskripts ersetzen (z. B. `supervisorctl restart`) — die Entkopplung bleibt trotzdem nötig.

**Voraussetzungen auf dem Zielsystem:**

- Installation per `git clone` (kein Zip-Entpacken) — sonst ist `is_git_checkout()` falsch und es gibt kein Update. Es gibt einen Rückfall auf eine `VERSION`-Datei, aber nur für die Anzeige.
- `.gitignore` enthält mindestens `.env` und `data/`, sonst blockiert `guard()` sofort.
- `git`, `sqlite3` (optional), `systemd-run` vorhanden.

**Reihenfolge beim Nachbauen:**

1. `updater.py` (nur lesend, ungefährlich) → Versionsanzeige im Dashboard. Sofort testbar.
2. Helferskript + sudoers → Update-Knopf. **An einem Wegwerf-Repo testen**, nicht am Produktivstand.
3. `publisher.py` + `/upload` → zuletzt, weil hier Schreibzugriff und Token im Spiel sind.

---

## 9. Dateiübersicht

| Datei | Zweck |
|---|---|
| `app/updater.py` | Version lesen, Tags prüfen, Changelog, `install()` per sudo |
| `app/publisher.py` | Status, Guard, Askpass, `publish()` |
| `app/main.py` | Routen `/upload`, `/upload/settings`, `/upload/publish`, `/api/upload-status`, `/api/update-check`, `/system/update` |
| `app/poller.py` | `update_check_cycle()` — gedrosselte Hintergrundprüfung, Telegram-Meldung |
| `app/templates/upload.html` | Upload-Oberfläche |
| `deploy/fanvue-admin` | Root-Helfer: restart-service, reboot, update |
| `deploy/fanvue-admin.sudoers` | sudo-Regel, drei feste Argumente |
| `deploy/git-setup.sh` | Ersteinrichtung und erster Upload (einmalig, auf dem Server) |
| `deploy/install.sh`, `bootstrap.sh` | Installation auf frischem Ubuntu |
