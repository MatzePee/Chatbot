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
- Der vorhandene `refresh_metrics`-Job sammelt aktive X-Konten mit vorhandener externer Konten-ID. Pro Konto wird höchstens alle 6 Stunden erneut versucht; auch Fehler werden gecacht. Dashboard-GETs und Browser-Aktualisieren lesen ausschließlich gespeicherte Werte, ohne X-Aufrufe.
- Abruf ausschließlich GET `/2/users/me?user.fields=public_metrics` über den bestehenden X-Adapter und vorhandenes Zugriffstoken; Antwort-ID muss zur gespeicherten X-ID passen. Keine Posts/DMs, keine Credential-Änderungen und keine zusätzliche Tokenrotation für Analytics. Abgelaufene Tokens warten auf den bestehenden autorisierten Verbindungs-/Refresh-Prozess.
- X-Tarif, users.read-Berechtigung, Rate-Limits oder ungültige Tokens können den Abruf verhindern. Letzte Messung bleibt erhalten; keine Provider-Fehlertexte/Token werden in Analytics gespeichert.
- Ohne laufenden Metrics-Scheduler gibt es keine neuen Messungen. Historie entsteht erst nach Aktivierung; 7-/30-Tage-Werte können deshalb anfangs fehlen. Es gibt kein Backfilling.
- Der Cache ist persistent, aber keine verteilte Sperre: gleichzeitig gestartete Metrics-Jobs verschiedener Prozesse könnten denselben ersten Abruf doppelt ausführen. Regulärer Einzel-Scheduler-Betrieb vermeidet dies.

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
