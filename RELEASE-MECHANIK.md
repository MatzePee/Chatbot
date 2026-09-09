# GitHub-Upload und Serverupdate

MP CreatorStudio verwendet weiterhin das Repository und den Dienst des bisherigen
Fanvue_Chatbot. Nur veröffentlichte stabile Tags der Form `vX.Y.Z` gelten als Release.
Die konkrete Bedienung des vorbereiteten Servers steht in [PRODUKTIVSTART.md](PRODUKTIVSTART.md).

## Upload

`app/publisher.py` prüft private Pfade und bekannte Schlüsselformate vor dem Vormerken,
vor dem Commit und nach einem GitHub-Abgleich. `.env`, `data/`, Python-Umgebungen und
temporäre Zugangshilfen gehören nicht ins Repository. Die Prüfung erfasst auch bereits
versionierte, unveränderte private Dateien.

Der Upload committet den Stand, holt den Zielbranch, führt bei Bedarf einen Rebase aus,
setzt danach das Versions-Tag und pusht Branch und Tag. Gleichzeitige Uploads sind
innerhalb des Prozesses gesperrt. Ein bestehendes Tag wird nur für denselben Commit
wiederverwendet. Scheitert der Tag-Push, meldet die Oberfläche den Teilfehler und erlaubt
einen erneuten Versuch mit derselben unveränderten Version.

GitHub-URLs dürfen keine eingebetteten Zugangsdaten enthalten. Ein vorübergehendes
Askpass-Skript liest den Token aus der Prozessumgebung und wird anschließend entfernt.
Die POST-Routen `/upload/settings` und `/upload/publish` benötigen einen Sitzungstoken.
Nur diese zusätzlichen GitHub-Aktionen sind im lokalen Mitlesemodus freigegeben; die
Sperren für Plattformänderungen gelten weiterhin.

## Update auf Linux/systemd

Die Oberfläche ruft weiterhin `sudo -n /usr/local/bin/fanvue-admin update` auf.
Der root-eigene Wrapper startet den root-eigenen Supervisor
`/usr/local/libexec/mp-creatorstudio-update` als separate systemd-Aufgabe. Git,
Paketinstallation, Datenkopien und Programmprüfungen laufen als Dienstbenutzer.
Die sudo-Regel erlaubt weiterhin nur Update, Dienstneustart und Serverneustart.

Der Supervisor in `deploy/creatorstudio_update.py`:

1. Sperrt parallele Updates und verlangt einen sauberen Server-Checkout.
2. Ermittelt das höchste tatsächlich veröffentlichte stabile Tag, prüft dessen
   Identität, Python-Version und freien Speicher. Ein Downgrade wird abgewiesen.
3. Entpackt ausschließlich Programmcode nach `data/releases/`, erstellt dort eine
   separate Python-Umgebung und installiert und prüft die Abhängigkeiten.
4. Startet `tools/release_smoke.py` gegen temporäre Kopien der vorhandenen Daten.
   Netzwerk und Hintergrundjobs sind dabei abgeschaltet. Tabellen, Einstellungen,
   Schlüssel und vorhandene Medien werden geprüft.
5. Setzt einen Wartungsmarker und stoppt erst jetzt den bisherigen Dienst. Sichert
   beide Datenbanken und Konfigurationen mit SQLite Online Backup.
6. Wechselt Code und `.venv`, startet mit Wartungsmarker und prüft Anwendung,
   Git-Revision, Produktionsmodus und Versandblockade über die lokale Health-Route.
7. Startet ohne Wartungsmarker und prüft, ob der AutoChat-Worker bereit ist.
   Der in der Datenbank gespeicherte aktive/pausierte Zustand bleibt erhalten.
8. Stellt bei Startfehlern Code und Umgebung zurück und prüft den Wiederanlauf.
   Laufende Nachrichten und rotierende OAuth-Tokens werden niemals automatisch durch
   eine alte Datenbanksicherung ersetzt.

Zustand und Sicherungspfad stehen in `data/update-status.json`; die neue Oberfläche
fragt den Stand alle fünf Sekunden über `/api/deployment-status` ab. Details stehen
im systemd-Journal der Aufgabe `fanvue-update`. Die bisherige AutoChat-Datenstruktur
ist kompatibel; künftige inkompatible Migrationen benötigen eine gesonderte Strategie.

## Installation des Helfers

Für den bestehenden Server ist `deploy/install-update-helper.sh` vorgesehen. Es
sichert den bisherigen Helfer, installiert Wrapper und Supervisor als root und
belässt Dienst, Daten und sudo-Regel unverändert. Es führt keinen Versionswechsel aus.
`deploy/install.sh` enthält dieselben Helfer für eine Neuinstallation.

Der verifizierte Bestandsserver nutzt Port 8000 und Python 3.13.3. Der Supervisor
prüft diesen Port; bei einem anderen Server müssen Zielpfad, Dienstbenutzer,
Dienstname und Health-Port vorab angepasst werden. Aktuelle Daten, Konfigurationen
und Medien werden nicht über GitHub verteilt.
