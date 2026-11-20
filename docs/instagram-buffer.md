# Instagram über Buffer und ALL-INKL

Das CreatorStudio plant lokal. Zum Versandzeitpunkt lädt es nur die Medien des
jeweiligen Beitrags auf einen eigenen Webspace-Ordner und übergibt den Beitrag
mit `shareNow` und `schedulingType: automatic` an die Buffer GraphQL API.
Ein verbundenes professionelles Instagram-Konto in Buffer und ein Buffer API Key
sind erforderlich. Eine eigene Meta-App ist für diesen Weg nicht nötig.

## Einrichtung

1. Bei ALL-INKL eine HTTPS-Subdomain mit gültigem Zertifikat einrichten.
   Für Sally ist `https://sally.sallylarsen.net` im Formular vorbelegt.
2. Einen eigenen FTP-Zugang erstellen, der auf dasselbe Verzeichnis zeigt.
   Explizites FTP mit TLS (FTPS, normalerweise Port 21) verwenden. Kein Klartext-FTP.
3. Im Studio: Einstellungen → Kanäle → Kanal anlegen → Instagram über Buffer.
   Handle `@sally_in_nylons` eintragen. Der neue Kanal startet pausiert.
4. „Buffer & Webspace“ öffnen. Buffer API Key, FTPS-Host, Benutzer und Passwort
   eintragen. FTP-Verzeichnis bei passend eingerichteter Zugangsbeschränkung `/`.
5. „Zugang speichern & Instagram-Konten laden“, Zielkonto auswählen, anschließend
   „Speichern & Verbindung testen“. Der Test prüft das Konto und überträgt eine
   kleine Textdatei, liest sie über HTTPS und entfernt sie. Kein Instagram-Post.
6. Medien zuordnen, Persona und Posting-Regeln wählen. Danach Kanal fortsetzen.
   Automatische Planung separat aktivieren oder im Kalender „Instagram planen“.

API-Key und FTPS-Passwort werden verschlüsselt in den Kanal-Zugangsdaten gespeichert.
Die API gibt die Geheimnisse nicht zurück. Leere Passwortfelder behalten bestehende
Werte. Speichern setzt den Verbindungstest zurück und pausiert den Kanal.
Bestehende Zugangsdaten können bei offenen Versandvorgängen nicht ersetzt werden.
Pro Charakter und Kanal wird ein eigenes Instagram-Ziel festgelegt und geprüft.

## Medien und Ablauf

- Einzelbilder und Bildkarussells mit bis zu 10 Bildern; ein einzelnes MP4-Video
  als Reel. Gemischte Video-Karussells und Stories sind in dieser ersten Version
  nicht vorgesehen. Keine reinen Textposts.
- Text einschließlich Hashtags maximal 2200 Zeichen; Video maximal 50 MB.
  Die endgültige Formatprüfung erfolgt zusätzlich bei Buffer/Instagram.
- Bildkopien werden als JPEG aufbereitet. Hoch-/Querformate außerhalb des Bereichs
  4:5 bis 1,91:1 erhalten weiße Ränder, ohne Bildteile abzuschneiden.
  Originaldateien bleiben erhalten. Das Kanal-Wasserzeichen wird auf die Kopie gelegt.
- Kopien liegen öffentlich unter zufälligen Namen in
  `mp-creatorstudio/<Kanal-UUID>/<Post-UUID>/<Zufallsname>.jpg|mp4`.
  Nur für Instagram tatsächlich gesendete Medien verlassen die lokale Bibliothek.
- Nach Buffer-Annahme steht der Beitrag auf „publishing“. Erst Buffer-Status
  `sent` bestätigt die Veröffentlichung und verbucht die Mediennutzung.
  „Status prüfen“ im Post prüft sofort; ein stündlicher Job gleicht offene Beiträge
  ab. Der Job läuft sowohl im eingebauten Scheduler als auch über Celery.
- Sieben Tage nach bestätigtem Versand entfernt der Job die erzeugten Kopien.
  Bei geändertem Webspace-Ziel werden alte Pfade nicht am neuen Ziel gelöscht.
  Abgelehnte/unklare Vorgänge werden zur manuellen Prüfung aufbewahrt.
- Sobald übergeben, Änderungen direkt in Buffer durchführen. Lokales Editieren
  oder Löschen wäre irreführend und ist gesperrt. Bei Timeout oder einem Absturz
  nach Beginn des Versands niemals blind neu senden; zuerst in Buffer kontrollieren.
  Das Studio versucht unklar bestätigte Erstellungen nicht automatisch erneut.
- Ein Notfall-Stopp kann bereits an Buffer übergebene Beiträge nicht zurückholen.
  In diesem Fall unmittelbar auch in Buffer prüfen/stoppen.
- Trockenlauf überträgt keine Dateien und erstellt keinen Buffer-Beitrag.

Ein echter End-to-End-Test ist erst nach Eingabe der Zugangsdaten und mit
gültigem SSL-Zertifikat möglich. Ein GitHub-Upload überträgt den Quellcode auf
weitere Installationen; API-Key und FTPS-Zugang müssen dort separat eingerichtet werden.

## Referenzen

- [Buffer API Reference](https://developers.buffer.com/reference)
- [Buffer: Hosting media](https://developers.buffer.com/guides/hosting-media)
- [ALL-INKL: WinSCP-Verbindung mit TLS](https://all-inkl.com/wichtig/anleitungen/programme/ftp/winscp/verbinden-und-dateien-hochladen_452.html)
