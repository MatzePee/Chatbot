# Bildeditor und Instagram-Varianten

Bilder lassen sich in der AutoPost-Bibliothek über das Stiftsymbol oder durch
Anklicken der großen Bildvorschau bearbeiten. Derselbe Editor ist an den
Bildansichten in Zuordnung, Matrix, Kalender/Postdialog, Dashboard-Vorschau und
Top-Posts erreichbar. Videos und animierte GIFs bleiben von der Bearbeitung ausgenommen.

Formate: frei, Original, 3:4, 4:5, 1:1 und 1,91:1. Der Ausschnitt lässt sich
verschieben und an den vier Ecken verändern. Pfeiltasten verschieben ihn ebenfalls;
mit Umschalt in größeren Schritten. Drehen, horizontal/vertikal spiegeln,
gerade richten, zentrieren und zurücksetzen sind verfügbar.

Beim Speichern gibt es zwei Ziele:

- **Nur Instagram-Kanäle:** Eine eigene Variante gilt für sämtliche
  Instagram-Kanäle dieses Bildes, unabhängig vom Versand über Buffer oder Upload-Post.
  Die gemeinsame Variante und die Originaldatei bleiben unverändert.
- **Alle Kanäle:** Der Zuschnitt gilt als gemeinsame Variante, auch für X,
  Fanvue und Telegram. Ein vorheriger Instagram-Zuschnitt wird dadurch ersetzt.

Das Original wird nicht überschrieben. „Gespeicherten Zuschnitt entfernen“
entfernt die Variante des gewählten Ziels. Instagram fällt dann auf die gemeinsame
Variante zurück; beim Entfernen der gemeinsamen Variante gelten wieder die Originale.
Bereits veröffentlichte oder extern übergebene Beiträge ändern sich nicht.
Noch lokal geplante Beiträge verwenden beim Versand die aktuelle Variante.

Die Bibliothekskacheln zeigen einen orangefarbenen Rahmen und ein Warnsymbol,
wenn ein einem Instagram-Kanal zugewiesenes Bild dort außerhalb von 3:4 bis
1,91:1 liegt. Ein vorhandener Instagram-Zuschnitt wird dabei berücksichtigt.
Die Statusmarkierung aktualisiert sich nach dem Speichern sofort. Die
Versandprüfung blockiert ungeeignete Bildformate; es werden keine weißen Ränder
mehr still hinzugefügt. Ein Rundungspixel an der Formatgrenze wird berücksichtigt.

Technisch liegen die Varianten in der additiven Tabelle `media_image_edit`
mit Charakterzuordnung und Fremdschlüssel zum ursprünglichen Bild. Dateien und
Vorschaubilder werden versioniert unter dem jeweiligen Bildordner gespeichert.
Die neue Tabelle wird vor der Charakter-Schemaprüfung angelegt. Bestehende
Bilddaten, IDs, Zuordnungen, Preise und Original-Hashes bleiben erhalten.
Nach Änderungen werden die passenden plattformseitigen Upload-Caches verworfen.
Zwei gleichzeitig geöffnete Editoren können einen neueren Zuschnitt nicht
unbemerkt mit einer alten Fassung überschreiben.
