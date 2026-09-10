# MP CreatorStudio

AutoChat und AutoPost (AutoPoster) in einer Anwendung.
Helle Oberfläche, abgerundete Karten und gemeinsame Navigation.

## Start am Mac

**`MP CreatorStudio starten.command` doppelklicken.** Der Browser öffnet den
**geschützten Mitlesemodus auf http://127.0.0.1:8765**. Der bisherige Server-Bot
kann gleichzeitig weiterlaufen.

- Live-Fanvue-Daten lesen und eingehende Nachrichten ansehen.
- Antwortvorschläge automatisch lokal erzeugen; unter „Freigabe“ ansehen und
  über „Test“ oder „Neu generieren“ ausprobieren. KI-Anfragen nutzen OpenRouter
  und verursachen die üblichen API-Kosten.
- Keine Nachrichten senden, keine Posts veröffentlichen, keine Lesebestätigungen.
- Keine OAuth-Anmeldung und keine Token-Erneuerung aus der lokalen Kopie.
- AutoPoster-Scheduler bleibt ausgeschaltet; Plattform-Schreibzugriffe sind
  zusätzlich direkt auf der HTTP-Transportebene gesperrt.

Der Server liefert über einen eingeschränkten SSH-Schlüssel ausschließlich seinen
aktuellen Access-Token samt Ablaufzeit. Er erneuert seine Tokens weiterhin selbst.
Der Schlüssel erlaubt keine Shell, Portweiterleitung oder Änderungen am Server.
Die lokale Konfiguration liegt in `data/preview.json`, der Schlüssel unter
`data/preview-ssh/`. Beides ist von GitHub ausgeschlossen. Ein SSH-Passwort wird
nicht gespeichert. Bei fehlendem Serverzugang bleiben Live-Abfragen aus; es gibt
keinen automatischen Wechsel in einen sendefähigen Modus.

Das Terminal bleibt geöffnet; mit Strg+C beenden. Ein zweiter Doppelklick öffnet die
bereits laufende geschützte Instanz. Falls Port 8765 belegt ist, erscheint eine
Fehlermeldung. Der Start weicht nicht auf eine produktive Instanz aus.

**Serverbetrieb:** `./run.sh` ohne `--preview` bzw. der bestehende systemd-Dienst
verwenden weiterhin den gespeicherten produktiven Betriebsmodus. Nur der
Doppelklick-Starter startet automatisch mit dem Sendeschutz. Der produktive
GitHub-Updateknopf und die bestehende `.env` bleiben erhalten. Der Update-Helfer
prüft neue Versionen vor dem Wechsel in einer getrennten Umgebung.

## Was erhalten bleibt

- AutoChat-Datenbank `data/bot.db`, einschließlich Chats, Nachrichten, Entwürfen,
  PPV-Daten, Einstellungen, OAuth-Tokens und GitHub-Einstellungen.
- Vorhandene `.env`, OAuth-Callback-Routen und API-Konfiguration des AutoChat.
- Repository und Tag-basierte GitHub-Update-Logik des Fanvue_Chatbot.
- Server-Einstiegspunkt `app.main:app` und Dienstname `fanvue-chatbot`.

Die Ausgangsordner wurden nicht verändert. Dieser Ordner enthält eine Kopie des
Datenbestands zum Erstellungszeitpunkt; spätere Änderungen der alten Instanz werden
nicht automatisch synchronisiert. Der vollständige Zeilenvergleich liegt lokal in
`data/migration-verification.json`.

## AutoPost

Über **AutoPost** öffnen. Enthalten sind Bibliothek, Zuordnung, Matrix,
Kalender, Kanäle, Personas und Posting-Einstellungen. Es nutzt denselben Port wie
der AutoChat. Die API liegt unter `/api/v1`, die Oberfläche unter `/autoposter/`.

Daten liegen getrennt in `data/autoposter/`; die kopierte Konfiguration in
`data/autoposter.env`. Dort tragen Variablennamen das Präfix `MP_AUTOPOSTER_`,
damit sie nicht versehentlich AutoChat-Einstellungen überschreiben.
Die vorhandenen AutoPoster-Medien, Kanal-Konfigurationen und Verschlüsselungsschlüssel
wurden lokal übernommen. Beim ersten Start bleibt AutoPost pausiert und im
Trockenlauf. Beides lässt sich in seinen Einstellungen ändern.

Fanvue nutzt im AutoPost automatisch die bestehende AutoChat-Anbindung:
Client-ID, Secret, API-Version und aktueller Access-Token kommen aus dem AutoChat.
Eine zweite Fanvue-Anmeldung entfällt. Fanvue-Kanäle müssen zum verbundenen
AutoChat-Konto gehören. Frühere Studio-Zugangsdaten bleiben gespeichert, werden
für Fanvue aber nicht mehr verwendet; X bleibt separat konfigurierbar.

Token-Erneuerungen laufen ausschließlich über den AutoChat. Im Mitlesemodus wird
weiterhin der aktuelle Server-Token per eingeschränktem SSH-Zugang gelesen.
Das Studio speichert keine Kopie des gemeinsamen Refresh-Tokens. Die vorhandenen
OAuth-Berechtigungen bleiben unverändert; die Zusammenführung erweitert sie nicht.
Fanvue wird oben unter **Einstellungen → Allgemein** verwaltet (`/settings/shared`).
Hier liegen auch die gemeinsamen Update-Einstellungen; **Version & GitHub** ist
links im selben Bereich erreichbar. Oben wechselt man zwischen **AutoChat**,
**AutoPost** und **Einstellungen**. Alle Bereichsseiten stehen in der linken
Navigation, die auf kleinen Bildschirmen über **Menü** geöffnet wird.
OpenRouter-Zugänge, Modelle, Prompts und Abläufe bleiben bereichsspezifisch:
**AutoChat-Einstellungen** unter `/settings`, **AutoPost-Einstellungen** unter
`/autoposter/#/settings`. Die getrennten Formulare erhalten beim Speichern die
Werte der anderen Bereiche. Datenbank, Token-Verwaltung und Updateweg bleiben erhalten.

## Personas für Text- und Medienposts

Unter AutoPost → Personas gibt es getrennte Bereiche **Text-Post** und
**Medien-Post**, jeweils mit eigenem Systemprompt und eigenen Beispielposts für
X und Fanvue. Der vorhandene Systemprompt bleibt als Text-Post-Prompt erhalten;
das neue Medien-Promptfeld ergänzt die Datenbank ohne Änderung vorhandener Werte.
Bestehende Beispiele behalten ihre Zuordnung mit bzw. ohne Bild.

Nur Textposts erhalten den Tagesrhythmus und das Tagesthema. Medienposts nutzen
die Bildbeschreibung und die Medien-Vorgaben ohne Tageszeitbezug. Ohne passende
Beispiele wird nicht mehr auf Beispiele der anderen Postart ausgewichen. Auch
bei noch fehlender Bildbeschreibung bleibt ein Medienpost ein Medienpost und
verwendet das Modell für Bildunterschriften. Änderungen gelten für künftig
neu generierte Texte; bestehende geplante Posts werden nicht automatisch umgeschrieben.

## Automatisches Befüllen je Kanal

Unter **AutoPost-Einstellungen → Planung** lassen sich einzelne Kanäle für das
Autobefüllen ein- und ausschalten. Diese Kanalschalter werden sofort gespeichert.
Zusätzlich muss der globale Hauptschalter **Automatisch nachplanen** aktiviert und
gespeichert sein. Pausierte Kanäle werden immer übersprungen. Sind alle Kanalschalter
aus, werden keine neuen Posts automatisch angelegt. Bestehende Posts und manuelles
Befüllen bleiben unverändert. Beim Update bleiben bestehende Kanäle für das
Autobefüllen ausgewählt; der bisherige Zustand des Hauptschalters bleibt erhalten.

Die öffentliche Basis-URL steht unter **Plattform-Vorgaben → Erweitert**. Sie bleibt
die Rückfalladresse für Verbindungen ohne eigene Callback-Adresse im Kanal und hat
keinen Einfluss auf die Planung.

## Update der produktiven Installation

Die Vorbereitung für den bestehenden Server ist abgeschlossen. Die Bedienung steht
in [PRODUKTIVSTART.md](PRODUKTIVSTART.md).

1. Lokal **Einstellungen → Version & GitHub** öffnen. Einen höheren `vX.Y.Z`-Tag
   wählen und **Committen und hochladen** anklicken. Dieser GitHub-Upload ist auch im
   geschützten Mitlesemodus erlaubt und durch einen Sitzungstoken geschützt.
2. Auf dem Server über den vorhandenen Update-Knopf nach der Version suchen und
   das Update einspielen. Ordner, Dienstname, AutoChat-Einstellungen, Datenbank und
   API-Verbindungen bleiben erhalten. Der Wechsel benötigt kurze Dienstneustarts.
3. AutoChat verwendet danach den bisherigen Betriebsmodus. AutoPost ist bereits
   einschließlich Einstellungen, Schlüsseln und Medien per SSH übernommen und
   bleibt zunächst pausiert und im Trockenlauf. Es kann später bewusst in seinen
   Einstellungen aktiviert werden.

Der neue Helfer installiert Abhängigkeiten in einer eigenen Umgebung und prüft
den Start mit Datenkopien, ohne Netzwerkzugriffe oder Hintergrundjobs. Erst nach
erfolgreicher Prüfung sichert er die aktuellen Daten und wechselt die Version.
Bei einem Startfehler stellt er Programmcode und Python-Umgebung zurück; aktuelle
Nachrichten und OAuth-Tokens werden dabei nicht durch alte Daten ersetzt.

Es ist kein zweiter Dienst erforderlich. Python 3.11–3.14 wird unterstützt; der
vorhandene Server wurde mit Python 3.13.3 geprüft. Die Updateknöpfe verwalten
Linux/systemd. Der lokale Doppelklick-Starter bleibt im Mitlesemodus.
Bei der Vorbereitung wurde kein GitHub-Release veröffentlicht und die laufende
Serverversion nicht gewechselt.

## Bestehende Installationen vorbereiten

Nach Veröffentlichung von **v2.0.2** kann die Updatefunktion einer bestehenden
Installation einmalig direkt über GitHub repariert werden:

```bash
curl -fsSL https://raw.githubusercontent.com/MatzePee/Chatbot/v2.0.2/deploy/fix-update.sh | sudo bash
```

Der Aufruf gehört ins Terminal auf dem jeweiligen Linux-Server. Dienstbenutzer,
Ordner, Dienstname und Port werden aus systemd ermittelt. Das Skript sichert und
installiert den Update-Helfer samt eng begrenzter sudo-Regel. Es ändert weder die
Programmdaten noch den laufenden Programmstand und startet den Bot nicht neu.
Anschließend wird das eigentliche Programmupdate in der Oberfläche gestartet.
Die kurze Anleitung steht in [ANLEITUNG_SABRINA.md](ANLEITUNG_SABRINA.md).

## Speicherbereinigung nach Updates

Nach einem erfolgreich abgeschlossenen Update bereinigt MP CreatorStudio die
älteren Update-Dateien automatisch, normalerweise innerhalb einer Minute.
Unter `data/releases/` bleiben die aktive Python-Umgebung und die unmittelbar
vorherige Umgebung für eine Rückkehr erhalten. Unter `data/backups/` werden die
letzten drei automatisch erzeugten `before-update-*`-Sicherungen behalten.
Ältere Vorbereitungsordner gescheiterter Updateversuche werden ebenfalls erst
nach einem erfolgreichen Update entfernt. Manuelle Sicherungen, aktuelle
Datenbanken, Medien, Zugangsdaten und unbekannte Ordner bleiben erhalten.

Die Bereinigung läuft als Dienstbenutzer und verwendet dieselbe Sperre wie der
Update-Helfer. Während eines Updates, nach einem Fehlschlag, bei unklaren
Versionsdaten und im lokalen Mitlesemodus wird nichts entfernt. Die tatsächlich
laufende Umgebung und das Rückfallziel werden vor jeder Bereinigung geprüft.
Ein Aufräumfehler stoppt den Bot nicht und löst kein Rollback aus.

Das funktioniert auch mit dem bereits installierten Update-Helfer: Nach der
Installation der neuen Programmversion ist kein erneutes Reparaturscript nötig.
Das Ergebnis steht in `data/update-cleanup.json` und im Dienstprotokoll. Der für
die Updatevorbereitung benötigte freie Platz (mindestens 1,5 GB) muss weiterhin
vor dem Update verfügbar sein; es wird nicht vor der erfolgreichen Prüfung
vorsorglich gelöscht.

## Sicherungen und Prüfung

Vor der Datenbankinitialisierung werden beide vorhandenen SQLite-Datenbanken mit
SQLite Online Backup (einschließlich WAL) gesichert, außerdem `.env` und
`data/autoposter.env`. Ablage: `data/backups/creatorpilot-*`. Diese Sicherungen
enthalten Zugangsdaten und bleiben durch `.gitignore` ausschließlich lokal.
Backups werden nicht automatisch gelöscht; bei häufigen Neustarts Speicher prüfen.
Medien liegen weiterhin im Datenordner und sind kein Teil dieser Datenbanksicherung.

Tests: `.venv/bin/python -m pytest -q`
Datenvergleich: `.venv/bin/python tools/verify_data.py ../Fanvue_Chatbot .`

Ab v2.0.3 sind die Paketversionen auch für Python 3.14 abgestimmt. Vorher konnte
die Updatevorbereitung bereits an `psycopg-binary==3.2.3` scheitern; weitere ältere
Vorgaben betrafen unter anderem asyncpg, Pydantic und Pillow. Die Anforderungen
werden aus der neuen Programmversion installiert. Eine bereits erfolgreiche
Helfer-Reparatur mit v2.0.2 muss deshalb nicht wiederholt werden.

Bei Änderungen an `requirements.txt` die Prüfung in einer **frischen virtuellen
Umgebung** für die betroffenen Python-Versionen ausführen: zuerst
`python -m pip install --only-binary=:all: -r requirements-dev.txt`, danach
`python -m pip check` und `python -m pytest -q`. Ein Test mit der bisherigen
Umgebung allein erkennt nicht, ob sich alle neuen Pakete installieren lassen.

Die Tests verwenden temporäre Datenbanken und simulierte APIs. Echte Nachrichten,
Posts, Bezahlaktionen oder ein produktives GitHub-Update wurden nicht ausgelöst.
Der eingeschränkte SSH-Lesezugang wurde separat geprüft.


## Fanvue-Kanäle aus der gemeinsamen Verbindung

Ab v2.0.5 ergänzt AutoPost beim Laden der Kanalliste automatisch **Fanvue · Subscriber**
und **Fanvue · Follower**, sobald Fanvue in den gemeinsamen Einstellungen eingerichtet
ist. Ohne abgeschlossene Anmeldung sind sie bereits sichtbar, aber noch nicht verbunden.
Beide nutzen ausschließlich die gemeinsame Verbindung; es werden keine zusätzlichen
OAuth-Tokens angelegt. Bilder und Kalenderplanung sind je Kanal getrennt. Die feste
Sichtbarkeit gilt auch beim automatischen Planen und beim Veröffentlichen.

Bestehende manuelle Kanäle samt Bildzuordnungen und Posts bleiben unverändert.
Die automatisch angelegten Kanäle können pausiert werden. Beim Wechsel des verbundenen
Fanvue-Kontos bleiben die bisherigen Kanäle an ihr altes Konto gebunden.

## Live-Ansicht und X-Kommentare

AutoChat und AutoPost aktualisieren sichtbare Seiten alle 15 Sekunden im Hintergrund.
Ungespeicherte Eingaben, offene Dialoge und ausgewählte Bilder werden geschützt;
inaktive Browser-Tabs pausieren. Die Vorschläge zeigen Ich/Fan mit unterschiedlichen
Hintergründen und Nachrichtenzeit in Europe/Berlin. Die Fanvue-Synchronisation
selbst behält ihren bisherigen Takt.

Unter AutoPost → Einstellungen → **Auto-Kommentar nach X-Bildposts** können
Text und Link gespeichert und aktiviert werden. Gilt für neue X-Bildposts und
Bildersets (einmal am ursprünglichen Post), nicht für Videos oder Fanvue.
Mitlesemodus und Trockenlauf senden keine Kommentare. Der Bildpost wird vor
Kommentarversand dauerhaft gespeichert. Bei unklarem Versandstatus wird nicht
blind wiederholt; der Status steht im Post-Editor und eine Meldung im Dashboard.
