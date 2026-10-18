# Dashboard: Problemmeldungen und X-Follower

## Verhalten

- Jede Meldung und jeder fehlgeschlagene Post zeigt den ursprünglichen Erstellungs-/Fehlversuchszeitpunkt in **Europe/Berlin**, deutsch formatiert. Alte ungültige/fehlende Werte heißen „Zeitpunkt unbekannt“. Naive historische Zeitwerte werden als UTC interpretiert.
- „Zur Kenntnis genommen“ blendet die Meldung dauerhaft aus. Es repariert keine Ursache, verändert keinen Poststatus und versendet nichts. Die Quittierung gilt installationsweit, wie die bisherigen gelesenen Benachrichtigungen; angemeldete Viewer dürfen eigene Übersichtsmeldungen ebenfalls quittieren (bestehende current_user-Regel).
- Bildvorrat-Warnungen bleiben für die laufende Knappheitsphase quittiert. Erst wenn eine Problems-Prüfung einen nicht mehr knappen/deaktivierten Kanal gesehen hat, kann eine neue Knappheitsphase erneut melden.
- Fehlgeschlagene Posts bleiben für genau ihren Fehlversuch (last_attempt_at, ersatzweise created_at) quittiert; ein späterer Fehlversuch erscheint wieder.
- Andere nicht-postbezogene Ereignisse werden durch identischen Titel, Text, Entität und Entitäts-ID wiedererkannt. Eine exakt gleiche quittierte Meldung bleibt ausgeblendet, auch bei wiederholter Erzeugung. Ein geänderter Titel/Text ist ausdrücklich eine neue Meldung. Für solche Ereignisse wird keine automatische neue Episode behauptet. Historische Benachrichtigungen bleiben erhalten.

## X-Follower

- Die Übersicht enthält pro X-Kanal aktuellen zuletzt gemessenen Stand sowie Stand und absolute Nettoänderung gegenüber gestern, vor 7 und vor 30 **Kalendertagen in Europe/Berlin**. Keine Postmetriken und keine geschätzten Baselines.
- Fehlende Baselines heißen „Keine Historie“. Ohne heutige Messung bleibt der letzte Stand mit „veraltet“ sichtbar; Wachstum ist dann unbekannt. Die Messzeit ist sichtbar.
- Speicherung erfolgt additiv als AppSetting `x_followers:<immutable X user id>` mit Tageshistorie (letzte Messung des Tages, maximal 400 Tage). Neue OAuth-Kontoverknüpfung mit anderer X-ID vermischt keine Historie. Kanäle desselben X-Kontos teilen absichtlich dieselbe Kontohistorie.
- Der vorhandene `refresh_metrics`-Job (alle 30 Minuten) versucht regulär alle 6 Stunden pro Konto, gemessen am letzten Versuch einschließlich Fehler. Dashboard-GETs und Browser-Aktualisieren lesen ausschließlich gespeicherte Werte, ohne X-Aufrufe.
- Zusätzlich prüft `collect_daily_followers` in Inprocess und Celery alle 60 Sekunden den Tages-Slot **06:00 Europe/Berlin** (Sommer 04:00 UTC, Winter 05:00 UTC). Der erste Lauf ab 06:00 umgeht einmal pro aktiver X-ID die Sechs-Stunden-Drossel. Tatsächlicher Start kann durch Polling, Warteschlange und vorherige Konten verzögert sein; kein Echtzeitversprechen.
- Start vor 06:00: warten. Start/Neustart danach: nur den heutigen noch offenen Slot nachholen; keine älteren Tage. Nach Slot-Claim keine erneute tägliche Zwangsmessung bei weiteren Polls. Fehler und Prozessabbruch verbrauchen den Slot ebenfalls; reguläre Wiederholungen nach sechs Stunden bleiben möglich. Bei Prozessabbruch vor Ergebnis bleibt „Messung begonnen – Ergebnis ausstehend“ sichtbar.
- Metrics- und Tagesjob teilen eine atomare Kontosperre (SQLite-Schreibsperre / PostgreSQL-Zeilensperre). Der zuerst laufende Job ab 06:00 beansprucht den Tages-Slot und `attempted_at` und bestätigt diese **vor** dem GET. Der gleichzeitig laufende andere Job überspringt das Konto; kein doppelter 06:00-Versuch. Ein regulärer Versuch knapp **vor** 06:00 ersetzt ausdrücklich nicht den Tages-Slot.
- Status: GET `/api/v1/jobs` zeigt im Inprocess-Modus `collect_daily_followers`, Pollintervall, Läufe und Resultat. GET `/api/v1/analytics/x-followers` zeigt in beiden Modi persistent pro Konto `attempted_at`, `daily_collection.time`, `timezone`, `last_slot`, `pending` und den Messstatus. `last_slot` bestätigt einen beanspruchten Versuch, nicht Erfolg; `pending` bedeutet heute ab 06:00 noch offen (bei abgeschaltetem Scheduler bleibt er offen). Keine manuelle Job-Auslösung zur reinen Prüfung verwenden.
- Abruf ausschließlich GET `/2/users/me?user.fields=public_metrics` über den bestehenden X-Adapter und vorhandenes Zugriffstoken; Antwort-ID muss zur gespeicherten X-ID passen. Keine Posts/DMs, keine Credential-Änderungen und keine zusätzliche Tokenrotation für Analytics. Abgelaufene Tokens warten auf den bestehenden autorisierten Verbindungs-/Refresh-Prozess.
- X-Tarif, users.read-Berechtigung, Rate-Limits oder ungültige Tokens können den Abruf verhindern. Letzte Messung bleibt erhalten; keine Provider-Fehlertexte/Token werden in Analytics gespeichert.
- Ohne laufenden Scheduler gibt es keine neuen Messungen. Historie entsteht erst nach Aktivierung; 7-/30-Tage-Werte können deshalb anfangs fehlen. Es gibt kein Backfilling.
- Die Slot-/Versuchsreservierung wird vor Netzwerkarbeit persistent bestätigt; Ergebnis und Tageshistorie danach separat gespeichert. PostgreSQL-Sperrpfad ist implementiert, aber diese Verifikation nutzt SQLite (inklusive gleichzeitiger separater Sessions mit Dateidatenbank).

## Betrieb / Rücknahme

Quellcode ist auf der SMB-Produktion abgelegt, **ohne Neustart oder Veröffentlichung**. Backend-Änderungen werden erst mit dem nächsten vom Betreiber autorisierten normalen Dienststart aktiv. Danach Browser neu laden; Modul-Versionen wurden erhöht. Der laufende Dienst wurde nicht getestet oder beeinflusst. Keine private `.env`, Runtime-Datenbank, Zugangsdaten oder Medien sind Bestandteil des Source-Backups.

Rücknahme: nur geänderte bestehende Quelldateien aus `Backup/Hermes_Dashboard_<UTC>` zurückkopieren, neue Dateien entfernen; keine Datenbank ersetzen. Additive AppSetting-Einträge dürfen bleiben (älterer Code ignoriert sie). Vor Rücknahme Betreiberänderungen vergleichen. Neustart/Veröffentlichung bleiben Betreiberentscheidung.

## Prüfung

```sh
python -m pytest tests/autoposter/test_dashboard_features.py tests/autoposter/test_problems.py tests/test_release_smoke.py -q
node tests/dashboard_features_ui.cjs
python -m pytest tests -q
```

Neue Featuretests wurden vor Implementierung fehlschlagend ausgeführt. Tests laufen auf isolierten Testdaten, X-HTTP wird durch MockTransport geprüft, UI durch einen Offline-DOM-Test. Release-Smoke-Tests führen den echten Verifier mit synthetischen Datenbanken und blockiertem Netzwerk/Workern aus und prüfen weiterhin, dass echte Datenmutationen abgelehnt werden. Kein Live-X-Abruf und kein visueller Produktions-Browsertest. Bekannte root-spezifische Fixturefehler in `test_repair_update` wurden nicht durch Lockerung des Sicherheitsguards umgangen.
