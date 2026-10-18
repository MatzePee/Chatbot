# Fanvue: Sichtbarkeit nach Veröffentlichungsalter

## Einstellungen und Bedienung

Unter **Einstellungen → AutoPost → Fanvue-Sichtbarkeit nach Alter**:

- Nacht-Automatik standardmäßig **aus**, Alter standardmäßig **7 Tage**. Vorhandene gespeicherte Werte bleiben erhalten. Alter: ganze Zahl 1–36500; boolesche Werte, Fließkommazahlen und Zahlen-Strings werden serverseitig abgelehnt.
- „Sichtbarkeit speichern“ speichert ausschließlich diese beiden Felder, keine Zugangsdaten oder anderen Einstellungen.
- „Jetzt prüfen und umstellen“ fordert als Administrator einen Hintergrundlauf mit dem **gespeicherten** Alter an, auch bei ausgeschalteter Nacht-Automatik. HTTP 202 bedeutet angefordert, nicht abgeschlossen. „Status aktualisieren“ zeigt persistente Kontoergebnisse und den letzten Lauf. Notfall-Stopp oder Trockenlauf blockieren Änderungen; der manuelle Endpunkt meldet dafür HTTP 409.
- Der Hintergrundlauf verwendet dieselbe Logik im API-Prozess und in Celery. Es werden keine allgemeinen Veröffentlichungs-/Chatjobs gestartet.

## Umfang und Sicherheit

Alle von `GET /v1/posts?includeUnpublished=false&size=50` gelieferten Posts der **aktiven, korrekt verbundenen Fanvue-Kanäle** werden per Cursor durchlaufen, nicht nur lokal gespeicherte AutoPost-Posts. Die bestehende Anwendung verwaltet eine gemeinsame Fanvue-Verbindung über AutoChat; unpassend gebundene Kanäle werden mit Fehler gemeldet und nicht über ein fremdes Konto abgearbeitet. Mehrere Kanäle desselben Kontos werden einmal bearbeitet. Vor dem Durchlauf werden Token-Konto-ID und `GET /users/me` gegen die erwartete Konto-ID geprüft; `read:post` und `write:post` sind erforderlich.

Nur `audience=followers-and-subscribers` mit einem gültigen, zeitzonenbehafteten tatsächlichen `publishedAt <= Laufbeginn in UTC − X×24 Stunden` ist geeignet. `createdAt`, lokale Kalender-/Erstellzeiten und Sortierung werden nicht als Ersatz benutzt. Entwürfe, fehlende/ungültige Veröffentlichung, zukünftige Veröffentlichungen, zukünftiges/ungültiges `publishAt`, bereits abgelaufene Posts und bereits reine Abonnenten-Posts bleiben unangetastet. Die Prüfung wird nach einem frischen GET unmittelbar vor PATCH wiederholt.

Die **einzige Post-Mutation** lautet:

```http
PATCH /v1/posts/{uuid}
Content-Type: application/json

{"audience":"subscribers"}
```

Kein `publishAt: null`, kein Text, keine Medien-, Preis-, Ablauf- oder Collection-Felder. Kein Löschen, Neuveröffentlichen oder Reposten. Ein anschließendes GET muss dieselbe Post-UUID und `audience=subscribers` bestätigen, erst dann steigt „umgestellt“. Der Zähler bezeichnet im jeweiligen Lauf bestätigte Änderungen; bei Prozessabbruch oder unbestätigter Antwort kann eine bereits erfolgte Änderung nicht als bestätigt gezählt werden.

## 00:00 Europe/Berlin, Parallelität und Fehler

Beide Scheduler fragen alle 60 Sekunden eine **persistente Konto-/Berliner-Kalendertag-Sperre** ab. Damit liegt die Tagesgrenze bei 00:00 Europe/Berlin einschließlich Sommer-/Winterzeit; bestehende UTC-Celery-Zeitpläne werden nicht umgestellt. Nach einem Ausfall wird der heutige Slot beim nächsten Poll nachgeholt, keine vergangenen Tagesläufe als Serie. Ein erfolgreicher manueller Lauf erfüllt ebenfalls den heutigen Slot. Bei ausgeschalteter Automatik erfolgen keine automatischen Nachholungen.

Claims werden vor Providerverkehr committed. SQLite verwendet seine Schreibsperre, PostgreSQL Zeilensperren; ein zufälliges Besitz-Token verhindert, dass ein alter Worker den neueren Status überschreibt. Eine 5-Minuten-Lease wird vor jeder HTTP-Operation erneuert, eine abgelaufene Lease kann übernommen werden; ein alter Worker muss vor weiteren Operationen seine Besitzprüfung bestehen. Gleichzeitige manuelle/nächtliche Läufe überspringen ein laufendes Konto. Nach einem Crash ist ein manueller erneuter Versuch nach Lease-Ablauf möglich; bei eingeschalteter Automatik übernimmt der nächste Poll.

Netzwerkfehler und HTTP 429/500/502/503/504: maximal drei Versuche je Operation, 30 Sekunden HTTP-Timeout, exponentielle Pause bzw. `Retry-After` bis 30 Sekunden. Größere Wartezeiten werden als Fehler gemeldet, nicht ignoriert. Fehlgeschlagene Kontoläufe bleiben unvollständig und werden regulär nach 15 Minuten erneut versucht; ein manueller Lauf kann früher erneut angefordert werden, aber keine aktive Lease verdrängen. HTTP 401/403/410 beendet den Kontodurchlauf. Fehlerstatus enthält keine Provider-Rohantworten oder Tokens. Reine Cursor-Wiederholungen/ungültige Seiten werden explizit abgebrochen, Post-UUIDs pro Durchlauf dedupliziert.

Die offizielle API bietet hier keine dokumentierte Compare-and-swap-/ETag-Vorbedingung. Fremde gleichzeitige Änderungen zwischen GET und PATCH können deshalb nicht vollständig ausgeschlossen werden. Audience-only-PATCH bewahrt die anderen Felder auch dann. Cursor-Pagination ist laut Fanvue bei gleichzeitigem Pinning/Publizieren keine transaktionale Momentaufnahme; Deduplizierung ist enthalten, weitere Tagesläufe erfassen nachträgliche Änderungen.

## Offizielle API-Belege

- https://api.fanvue.com/docs/api-reference/update-a-post.md
- https://api.fanvue.com/docs/openapi.json — JSON Pointer `/paths/~1posts~1{uuid}/patch/requestBody/content/application~1json/schema/properties/audience`
- https://api.fanvue.com/docs/openapi-v1.json — derselbe Request-Pfad unter `/v1/posts/{uuid}`; `/components/schemas/PostAudience` enthält `subscribers` und `followers-and-subscribers`.
- v1 GET `/v1/posts`: `data`, **oberstes `nextCursor`**, `total`; `includeUnpublished=false`; `publishedAt` ist ausdrücklich die tatsächliche Veröffentlichung, nicht `createdAt`.
- PATCH-Dokumentation: alle Felder optional, nur mitgesendete Felder werden aktualisiert; erforderlicher Scope `write:post`.

Die vollständigen abgerufenen OpenAPI-Schemata und Markdown-Belege liegen im lokalen Arbeitsverzeichnis `research/`; sie sind keine aus Snippets abgeleiteten Annahmen.

## Verifikation und Freigabegrenze

Synthetische Testdatenbanken und gemockter Providerverkehr; kein Produktions-Lifespan, keine echten Fanvue-Posts/-Chats. Tests umfassen Altersgrenze, Zeitzonen/DST, Pagination/Deduplizierung, Kontoisolation, Claims in getrennten SQLite-Sitzungen, Lease-Ablauf/Besitzverlust, Scopes/Fehler und Readback, authentifizierte manuelle Route, Einstellungen und Offline-Chromium-Bedienung.

Ein Quellen-Upload aktiviert die Funktion **nicht** im laufenden Dienst. Keine automatische Aktivierung, kein Neustart, kein GitHub-Publish und keine Live-Sichtbarkeitsänderung sind Teil dieses Deployments. Quellbackup ist ausdrücklich **kein Datenbankbackup**.
