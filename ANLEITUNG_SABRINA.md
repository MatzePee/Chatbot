# Sabrina: Update auf MP CreatorStudio

Du brauchst einmalig einen Befehl auf deinem bisherigen Linux-Server. Das Skript erkennt deinen Dienstbenutzer, Programmordner, Dienstnamen und Port automatisch. Dateien von Hand bearbeiten oder ein ZIP übertragen ist nicht nötig.

**Voraussetzung:** Matze hat **v2.0.2 einschließlich Versions-Tag** nach GitHub hochgeladen. Vorher ist der folgende Download noch nicht verfügbar.

Verwende den unten stehenden Befehl mit **v2.0.2** auch dann, wenn du die Reparatur mit v2.0.1 bereits versucht hast. Die neue Fassung korrigiert die Berechtigungsprüfung für sudo-rs; der alte Downloadlink lädt weiterhin die alte Fassung.

## 1. Auf deinem Server anmelden

Öffne deine bisher verwendete SSH-Verbindung. Verwende deinen eigenen Serverzugang. Der nächste Befehl gehört in das Terminal **auf dem Server**, auf dem dein Bot läuft.

## 2. Diesen Befehl kopieren und ausführen

```bash
curl -fsSL https://raw.githubusercontent.com/MatzePee/Chatbot/v2.0.2/deploy/fix-update.sh | sudo bash
```

Wenn sudo nach einem Passwort fragt, gib dein eigenes Serverpasswort ein. Während der Eingabe werden keine Zeichen angezeigt.

Das Skript zeigt die erkannte Installation an, prüft die Voraussetzungen, sichert den bisherigen Update-Helfer und die sudo-Regel und richtet die Updatefunktion ein. Der laufende Bot wird dabei nicht gestoppt. Deine Datenbank, API-Zugänge, Einstellungen und dein Programmstand bleiben unverändert.

Am Ende muss **„Updatefunktion repariert“** erscheinen. Weitere Eingaben oder Anpassungen sind bei einer eindeutig erkannten Standardinstallation nicht nötig.

## 3. Im Browser aktualisieren

Öffne deine bisherige Bot-Oberfläche, suche nach Updates und starte das Update. Das Skript selbst hat noch kein Programmupdate ausgelöst. Du musst keinen eigenen GitHub-Upload machen.

Der Update-Helfer installiert die höchste veröffentlichte stabile Version aus deinem bereits eingerichteten Repository. Er prüft neue Abhängigkeiten und den Start zunächst getrennt vom laufenden Bot. Erst danach sichert er die aktuellen Datenbanken und Konfigurationen und startet den Dienst für den Versionswechsel kurz neu. Das kann einige Minuten dauern.

Danach heißt die Oberfläche **MP CreatorStudio**:

- **AutoChat** übernimmt deine bisherigen Einstellungen und API-Verbindungen. War dein Bot aktiv, arbeitet er anschließend weiter; war er pausiert, bleibt er pausiert.
- **AutoPost** startet bei einer neuen Einrichtung leer, pausiert und im Trockenlauf. Für Fanvue verwendet es dieselbe Verbindung wie AutoChat.
- Matzes Zugangsdaten, Nachrichten, Medien und Kanaleinstellungen werden nicht mitgeliefert.

Öffne deine normale Serveradresse anschließend neu, ohne einen alten `?sys_err=…`-Zusatz. Unter **System** siehst du den Updatezustand.

## Falls eine Fehlermeldung erscheint

Bei einer fehlenden Voraussetzung oder nicht eindeutig erkannten Installation beendet sich das Skript mit einer Erklärung. Es installiert kein Betriebssystem- oder Python-Upgrade automatisch. Lass die konkrete Meldung prüfen, bevor du fortfährst.

Findet es mehrere Bot-Dienste, zeigt es deren Namen an. Dann kann die zuständige Person den gewünschten Dienst ausdrücklich mit `--service DIENSTNAME` auswählen. Bei einer üblichen Einzelinstallation ist das nicht nötig.

Die Reparatur sichert vorhandene Helfer und Berechtigungen unter `/var/backups/mp-creatorstudio/fix-update-*`. Falls ihre abschließende Berechtigungsprüfung scheitert, stellt sie diese Dateien wieder her. Die spätere Programmaktualisierung sichert vorhandene Datenbanken und Konfigurationen unter `data/backups/before-update-*` und versucht bei einem Startfehler, den vorherigen Code samt Python-Umgebung wieder zu starten.

Ab v2.0.2 nennt ein Fehler während der Einrichtung den betroffenen Schritt und die Ursache. Diese Meldung wird außerdem als `diagnose.txt` im angegebenen Sicherungsordner gespeichert. Falls eine Datei nicht wiederhergestellt werden konnte, wird ihr Pfad ausdrücklich genannt.

Gib für Unterstützung nur die Fehlermeldung weiter, keine Passwörter, Tokens oder Datenbankdateien. Nach erfolgreicher einmaliger Einrichtung verwendest du wieder den normalen Updateknopf.
