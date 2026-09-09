# MP CreatorStudio produktiv starten

## Produktiver Stand vom 9. September 2026

Das Update auf **v2.0.0** wurde erfolgreich durchgeführt. AutoChat läuft produktiv
mit den bisherigen Einstellungen und bestehender Fanvue-Verbindung. AutoPost ist
übernommen und bleibt pausiert sowie im Trockenlauf. Beide Datenbanken wurden vor
dem Wechsel gesichert und die Sicherungen geprüft.

Beim ersten Updateversuch fehlte in der alten sudo-Regel die passwortlose Freigabe
für `fanvue-admin update`. Diese wurde ergänzt; die Prüfung kontrolliert nun die
Passwortfreiheit aller drei erlaubten Aktionen ausdrücklich. Der Updateversuch
wurde danach erfolgreich wiederholt. Ein weiterer Upload ist für diese Reparatur
nicht erforderlich.

## Vorbereitung vor dem Produktivwechsel

Der bisherige Bot lief auf `192.168.20.16:8000` als Dienst `fanvue-chatbot` unter
`/srv/fanvue/Fanvue_Chatbot`, Version `v1.6.2`. Adresse, Verzeichnis und Dienstname
bleiben auch nach dem Wechsel auf MP CreatorStudio erhalten.

- GitHub-Zugang lokal per Push-Trockenlauf und serverseitiges Lesen geprüft.
- Neuer Update-Helfer installiert, bisheriger Helfer unter
  `/var/backups/mp-creatorstudio/` gesichert. Die sudo-Regel erlaubt genau Update und die beiden Neustartaktionen ohne Passwort.
- Abhängigkeiten auf dem Server unter Python 3.13.3 separat installiert und geprüft.
- Produktionsstart mit Kopien der aktuellen Daten getestet. Alle 15 bestehenden
  AutoChat-Tabellen und 25 AutoPost-Tabellen blieben unverändert. Gespeicherte
  AutoPost-Schlüssel wurden ohne Netzwerkzugriff erfolgreich entschlüsselt.
- AutoPost per SSH übernommen: 2 Kanäle, 8 Bilder, 1 Persona und alle Einstellungen
  und Schlüssel. 41 Dateien per SHA-256 geprüft; AutoPost bleibt pausiert und im
  Trockenlauf. Es gab keine geplanten Posts in der übertragenen Datenbank.
- Die produktive AutoChat-Datenbank und `.env` wurden nicht ersetzt. GitHub enthält
  ausschließlich Programmdateien; private Daten bleiben auf den jeweiligen Geräten.

## Ablauf für weitere Veröffentlichungen

Den Doppelklick-Starter verwenden. Unter **Einstellungen → Version & GitHub** eine
neue Version größer als `v2.0.0` wählen, zum Beispiel `v2.0.1`, eine kurze Beschreibung
wie „MP CreatorStudio mit AutoChat und AutoPost“ eintragen und **Committen und hochladen**
anklicken. Die Erfolgsmeldung muss sowohl Programmcode als auch Versions-Tag bestätigen.

Der GitHub-Upload ist im lokalen Mitlesemodus erlaubt. Nachrichten, Posts,
Lesebestätigungen und Fanvue-Token-Erneuerungen bleiben dort gesperrt. Eine bestehende
Versionsnummer wird nie auf einen anderen Commit umgebogen. Meldet die Oberfläche,
dass nur der Tag-Upload gescheitert ist, den unveränderten Stand mit derselben Version
nochmals veröffentlichen.

## 2. Auf dem Server aktualisieren

Im Browser `http://192.168.20.16:8000` öffnen, über die vorhandene Updatefunktion
nach der neuen Version suchen und das Update starten. Die Vorbereitung kann mehrere
Minuten dauern. Der alte Bot läuft während der Paketinstallation und Vorprüfung weiter.
Beim eigentlichen Wechsel wird der Dienst kurz gestoppt und neu gestartet.

Danach erscheint MP CreatorStudio. AutoChat übernimmt den bisherigen aktiven Zustand
und Betriebsmodus aus der Serverdatenbank. Die lokale Testkopie bleibt im Mitlesemodus.
Unter **Einstellungen → Allgemein** zeigt die neue Oberfläche den Updatefortschritt.
Beim ersten Update kann die alte Oberfläche den neuen Fortschrittsbereich noch nicht
anzeigen; nach dem Neustart ist er verfügbar.

AutoPost enthält bereits die übernommenen Kanäle und Medien. Fanvue verwendet die
bestehende AutoChat-Anbindung. AutoPost bleibt pausiert und im Trockenlauf, bis diese
beiden Einstellungen ausdrücklich geändert werden. Die Übernahme ist eine Momentaufnahme;
spätere lokale AutoPost-Änderungen werden nicht automatisch zum Server synchronisiert.

## Sicherung und Fehlerbehandlung

Vor dem Versionswechsel sichert der Helfer beide SQLite-Datenbanken einschließlich
WAL sowie `.env` und `data/autoposter.env` unter
`data/backups/before-update-*`. Medien verbleiben unverändert unter `data/autoposter/media`.
Die bisherigen Programmstände bleiben über Git, die Python-Umgebungen unter
`data/releases/` verfügbar. Sicherungen und Umgebungen werden nicht automatisch gelöscht.

Schlägt die Vorbereitung fehl, bleibt die alte Version aktiv. Schlägt der neue Start
fehl, versucht der Helfer automatisch, den vorherigen Code und die vorherige Umgebung
zu starten. Dabei werden keine alten Nachrichten oder OAuth-Tokens zurückgespielt.
Ein fehlgeschlagener Wiederanlauf wird ausdrücklich als Fehler angezeigt.

Für eine technische Prüfung auf dem Server:

```bash
systemctl status fanvue-chatbot
journalctl -u fanvue-update -n 100 --no-pager
cat /srv/fanvue/Fanvue_Chatbot/data/update-status.json
```

Der private Testzugang per SSH bleibt auf das Lesen des aktuellen Fanvue-Access-Tokens
beschränkt. Er ist von GitHub ausgeschlossen und erfordert nach dem Update keine neue
Fanvue-Anmeldung.
