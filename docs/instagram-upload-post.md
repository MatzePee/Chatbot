# Instagram über Upload-Post

Diese Alternative zu Buffer überträgt Dateien direkt vom lokalen Studio an
Upload-Post. Der Anbieter stellt die Dateien für Instagram bereit. Für diesen
Weg werden weder ALL-INKL/FTPS noch eine öffentliche Medien-Subdomain gebraucht.

## Einrichten

1. Bei Upload-Post ein Profil anlegen, das Instagram-Creator-/Business-Konto
   verbinden und einen API-Key erstellen.
2. Einstellungen → Kanäle → Kanal anlegen → **Instagram über Upload-Post**.
   Den tatsächlichen Instagram-Handle eintragen. Der Kanal startet pausiert.
3. **Upload-Post-Zugang** öffnen. API-Key eingeben und **Key speichern & Profile
   laden** anklicken. Schlüssel werden verschlüsselt gespeichert und nicht an
   die Oberfläche zurückgegeben. Leere Eingabe behält den vorhandenen Key.
4. Passendes Profil auswählen, dann **Speichern & Verbindung testen**. Der
   Test liest ausschließlich das Profil und das verbundene Instagram-Konto.
   Er veröffentlicht nichts und verbraucht keinen Medienupload.
5. Persona wählen und Medien diesem Kanal zuordnen. Einen bisherigen Buffer-Kanal
   für dasselbe Instagram-Konto pausieren. Den neuen Kanal fortsetzen und zuerst
   einen einzelnen Beitrag im Kalender testen; automatische Planung separat aktivieren.

Ein Upload-Post-Profilname ist nicht der Instagram-Handle. Der Test prüft den
Handle und bindet zusätzlich die numerische Instagram-Konto-ID. Wird beim
Anbieter ein anderes Konto verbunden, blockiert das Studio den Versand.

## Versand und Status

- Bilder, Bildkarussells bis 10 Bilder und einzelne MP4-Videos als Reels.
  Die vorhandenen Instagram-Medienregeln gelten weiterhin.
- Bildkopien werden einschließlich Kanal-Wasserzeichen als JPEG vorbereitet;
  die Originale bleiben unverändert. Videos werden als MP4 hochgeladen.
- Der Kalender bleibt lokal. Erst zum Versandzeitpunkt erfolgt ein binärer
  Multipart-Upload an `/api/upload_photos` bzw. `/api/upload`.
- Vor dem externen Aufruf wird eine stabile Vorgangs-ID gespeichert. Sie wird
  als `request_id` und `Idempotency-Key` verwendet. Nach einem Timeout lässt
  sich der Status anhand dieser ID prüfen, ohne nochmals zu veröffentlichen.
- Erst eine ausdrückliche erfolgreiche Instagram-Bestätigung zählt als
  veröffentlicht und verbucht die Mediennutzung. Eine bloße Upload-Annahme,
  ein übersprungenes Konto oder der Erfolg einer anderen Plattform reicht nicht.
- **Status prüfen** im Post aktualisiert sofort. Ein Job prüft offene
  Upload-Post-Vorgänge alle fünf Minuten (eingebauter Scheduler und Celery).
- Bei unklarem Versand bleiben erneutes Senden, Änderungen und Löschen gesperrt,
  bis der Vorgang geklärt ist. Keine blinden Wiederholungen. Anbieterfehler und
  Monatskontingente werden angezeigt. Trockenlauf/Notfall-Stopp senden nichts.

Buffer-Verbindungen und ihre bestehenden Posts werden nicht automatisch
umgestellt. Für Upload-Post wird ein neuer Kanal angelegt. Ein echter Test kann
erst nach Eintragen des persönlichen API-Keys erfolgen. Quellcode und
Zugangsdaten bleiben getrennt; jede weitere Installation richtet den Key selbst ein.

Technischer Hinweis: Das bestehende Instagram-Versandjournal heißt aus Gründen
der Rückwärtskompatibilität weiterhin `buffer_delivery`; sein Feld `provider`
unterscheidet `buffer` und `upload_post`. Bestehende Einträge ohne provider gelten
als Buffer. Die Statusroute `/buffer-status` bedient beide Anbieter.

Referenzen:

- https://docs.upload-post.com/api/upload-photo/
- https://docs.upload-post.com/api/upload-video/
- https://docs.upload-post.com/api/upload-status/
- https://docs.upload-post.com/api/user-profiles/
