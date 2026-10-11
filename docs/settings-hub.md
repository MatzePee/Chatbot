# Einheitlicher Einstellungsbereich

Der obere Button **Einstellungen** öffnet `/settings/shared`. Dort stehen echte Einstellungsbereiche **Gemeinsam**, **AutoChat** und **AutoPost** im selben Rahmen und mit denselben Formular-, Karten-, Schaltflächen- und Navigationstokens bereit. Kein iframe und keine reine Link-Sammlung. Die Änderung betrifft nicht das Dashboard.

## Vollständigkeit und Kompatibilität

`settings-completeness.json` inventarisiert die unveränderten Ausgangsseiten vor dem Umbau: sämtliche HTML-Eingaben, Auswahllisten, Textfelder, Schaltflächen, Formularaktionen, Werkzeuglinks, die separat eingebundenen Programm-Updateaktionen sowie AutoPost-Eingaben und API-Aktionen. Die Tests vergleichen die alten Kontrollidentitäten mit den neuen Vorlagen. AutoPost verwendet seinen ursprünglichen, hashgesicherten Renderer unverändert; ein Offline-Chromium-Test vergleicht zusätzlich alle tatsächlich gerenderten Kontrollen, Schaltflächen, Links, Zustände und vorausgefüllten Werte mit einem eigenständig gerenderten Ausgangsbild.

- `/settings/shared?section=shared`: Fanvue-OAuth und Zugangsdaten, Telegram-Token/Chat-ID/Basis-URL mit Chat-ID-Ermittlung und Testnachricht, Updateprüfung/Installation und Updateoptionen, Veröffentlichung-Link, Dienststeuerung.
- `/settings/shared?section=autochat`: sämtliche Modell-, Vision-, Persona-, Zeit-, Guardrail-, Fanbild-, Trinkgeld-, Betriebs-, Prio-, Queue-, PPV-, Reaktivierungs-, Gedächtnis- und Telegram/Stichwortoptionen einschließlich Vorschauwerkzeugen und Auf-/Zuklappen.
- `/settings/shared?section=autopost`: Trockenlauf/Notfall-Stopp, X-Bildkommentar, OpenRouter-Key/Modelle/Filter/Budget/Test/Modellaktualisierung/Schlüsselentfernung, Plattformvorgaben, Kanaltests, Planung einschließlich direkter Kanalschalter, Kostenanzeige, Bibliothekswartung, bestätigtes Kalenderleeren und Systeminformationen.
- `/settings` und vorhandene AutoChat-Anker bleiben verwendbar und zeigen den integrierten AutoChat-Bereich.
- `/autoposter/#/settings` bleibt als kompatibler Einstieg erhalten und führt nach der bestehenden AutoPost-Anmeldung zum integrierten AutoPost-Bereich. Kanallinks bleiben richtige AutoPost-Kanallinks.
- Bestehende gemeinsame Anker `#fanvue`, `#telegram`, `#updates` bleiben verwendbar.

## Speichern, Geheimnisse und Berechtigungen

Die bestehenden Formaktionen und AutoPost-API-Endpunkte bleiben erhalten. Gemeinsame Formulare speichern nur ihre jeweilige Gruppe; AutoChat speichert weiterhin seine eigenen booleschen Felder mit der bisherigen Checkbox-Semantik. AutoPost-Karten speichern unabhängig; die ausdrücklich als sofortig beschriebenen Kanalschalter behalten ihr Verhalten. Tabwechsel über GET speichern nichts. Nicht gespeicherte Eingaben erhalten eine browserseitige Warnung vor dem Verlassen.

Leere oder vollständig maskierte AutoChat/Fanvue/Telegram-Geheimnisse überschreiben keinen gespeicherten Schlüssel. Zum absichtlichen Löschen gibt es explizite zusätzliche Lösch-Checkboxen in der jeweiligen Speichergruppe. AutoPost behält seinen bisherigen `clearSecret`-Bestätigungsdialog und sendet leere Schlüssel nicht im Speicherpayload. Gespeicherte Fanvue-Listen und ihre Namen bleiben auch bei nicht erreichbarer Fanvue-Auswahl erhalten.

Bestehende serverseitige Admin-/Editor-/Mitleseprüfungen bleiben unverändert. Der Hub übernimmt AutoPost-Benutzer ausschließlich aus dessen bestehender Auth-API. Fehlende Anmeldung zeigt einen Anmeldungseinstieg; es wird kein Benutzer erfunden und keine Berechtigung umgangen. Die bestehenden Aktionsschaltflächen bleiben vorhanden, werden bei den Tests aber nur gegen synthetische Offline-Antworten verwendet.

## Prüfung ohne produktive Nebenwirkungen

```sh
python -m pytest tests/test_settings_hub.py tests/test_shared_settings.py tests/test_system_page.py tests/test_admin_permissions.py tests/test_preview.py tests/test_release_smoke.py tests/autoposter/test_dashboard_features.py -q
SETTINGS_RENDER_OUTPUT=/tmp/settings-render python -m pytest tests/test_settings_hub.py -q
PLAYWRIGHT_MODULE=/path/to/playwright SETTINGS_RENDER_OUTPUT=/tmp/settings-render node tests/settings_hub_ui.cjs
python -m pytest tests -q
```

Die Python-Tests verwenden disposable Datenbanken und keinen Application-Lifespan. Chromium erhält ausschließlich synthetische HTML/API-Testdaten, erfüllt alle Requests offline und scheitert bei unbekannten Requests. Es prüft alle AutoPost-Kontrollen, unabhängige Payloads, geheime Leerwerte, abgebrochene Löschaktionen, Navigation ohne Speichern, verschachtelungsfreie Formulare, aktive Seitenleiste und Desktop/Mobil-Rendering. Es startet keinen produktiven Server oder Scheduler und erstellt keine echten Posts/Nachrichten.

Vorlagen und statische Dateien können früher sichtbar werden als der geänderte Python-Router. **Eine vollständige Aktivierung erfordert den separat autorisierten regulären Dienstneustart.** Das Bereitstellen der Quelldateien führt selbst keinen Neustart, keine Veröffentlichung und keine Änderung produktiver Einstellungen oder Daten aus.
