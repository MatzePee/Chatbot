# MP4-Videos auf allen AutoPost-Kanälen

Die Bibliothek unterstützt MP4 mit H.264-Video und optional AAC-Ton, maximal 50 MB pro Datei. ffmpeg und ffprobe müssen auf dem Server installiert sein. Videos werden unverändert hochgeladen; die vorhandenen Bildwasserzeichen gelten weiterhin nur für Bilder.

- **X:** Pro Post ein Video ohne weitere Medien. Der vorhandene Upload in Dateiteilen verwendet `tweet_video` und wartet auf die bestätigte Verarbeitung. Gemischte Bild-/Video-Posts werden vor dem Upload abgelehnt. Beim automatischen Planen werden Videos separat behandelt. Die Plattform prüft zusätzlich die Berechtigungen und Laufzeitgrenzen des jeweiligen X-Kontos.
- **Fanvue Follower und Subscriber:** Videos können zugeordnet, geplant und über den bisherigen Multipart-Upload veröffentlicht werden. Der Versand wartet bei Videos ausdrücklich auf `ready`.
- **Telegram Gratis:** Die bisherige Video-Unterstützung bleibt erhalten.
- **Telegram Pay:** Einzelvideos erhalten in der Bibliothek einen Sternepreis. Sets dürfen 2–10 Bilder und Videos enthalten und behalten den eigenen Gesamtpreis sowie ihre Reihenfolge. Das gesamte Set darf höchstens 100 MB groß sein. Ohne Preis keine Pay-Planung; keine kostenlose Ersatzveröffentlichung.

Im Posteditor öffnet ein Klick auf eine Medienminiatur die Vorschau, einschließlich Videoplayer. Vorschaubilder in Kalender und Dashboard verwenden bei Videos das erzeugte Standbild. Die KI-Beschreibung basiert weiterhin auf diesem Standbild und analysiert nicht den gesamten Videoablauf.

Die vorhandenen Bilder-Posts, Freigaben und Telegram-Preissicherungen bleiben erhalten. Es werden keine neuen Datenbankfelder benötigt. Auslieferung erfolgt über das reguläre GitHub-Update.

Geprüfte offizielle Referenzen:
- https://docs.x.com/x-api/media/quickstart/media-upload-chunked
- https://api.fanvue.com/docs/tutorials/working-with-media
- https://core.telegram.org/bots/api#inputpaidmediavideo
