# Telegram: Gratis und Pay im gleichen Zielkanal

## Einrichten

1. Unter Einstellungen → Konten & Kanäle den Telegram-Gratis-Kanal anlegen bzw. öffnen. Bot-Zugang speichern und testen.
2. Auf „Pay-Kanal anlegen“ klicken. Der neue Planungsbereich teilt den verschlüsselt gespeicherten Bot und denselben Telegram-Zielkanal. Er startet pausiert; automatische Planung ist aus.
3. Den Pay-Kanal testen. Telegram muss für dieses Ziel `can_send_paid_media` bestätigen. Es wird keine Testnachricht veröffentlicht.
4. In den Kanaleinstellungen den Rhythmus festlegen: Gratis üblicherweise täglich, Pay alle drei Tage ab dem gewählten Startdatum. Die Anzahl „Posts pro Tag“ gilt pro aktivem Rhythmustag. Uhrzeit und Zeitfenster bleiben pro Kanal einstellbar.
5. Bilder bzw. Sets ausdrücklich dem gewünschten Kanal zuordnen und anschließend den Kanal aktivieren. Automatisches Nachplanen bei Bedarf separat einschalten.

Änderungen am gemeinsamen Bot-Zugang und das Trennen der Verbindung betreffen beide Planungsbereiche. Die Planung, Persona, Medienzuordnung und Freigaben bleiben getrennt.

## Preise und eigene Sets

- Ein Bild in der Bibliothek öffnen und „Telegram-Einzelpreis in Sternen“ setzen. Erlaubt sind ganze Zahlen von 1 bis 25000. Ein leeres Feld schließt das Bild aus der Einzelbild-Pay-Planung aus.
- Mehrere Bilder markieren und „Set bilden“ wählen. Namen, Bildreihenfolge und einen eigenen Telegram-Setpreis angeben. Dies ist der Gesamtpreis; Einzelbildpreise werden weder benötigt noch addiert.
- Sets über das Zahnrad in der Bibliothek bearbeiten und als ganzes Set dem Pay-Kanal zuordnen. Für Pay sind 2–10 JPEG-/PNG-Bilder oder MP4-Videos erlaubt. Sets werden vollständig und in ihrer Reihenfolge verkauft, niemals gekürzt oder aufgeteilt.
- Eine Setzuordnung verkauft dessen Mitglieder nicht automatisch einzeln. Für einen zusätzlichen Einzelverkauf sind eine direkte Bildzuordnung und ein Einzelpreis erforderlich.
- Im Pay-Kanal kann die Auswahl auf Einzelbilder, Sets oder beides begrenzt werden. Die automatische Planung wählt zufällig unter verfügbaren Angeboten; ein Set zählt dabei als ein Angebot.

## Planen und Freigeben

Der Kalender zeigt den gespeicherten Sternepreis. Bei „Post anlegen“ kann ein freies Pay-Angebot manuell ausgewählt oder automatisch zufällig bestimmt werden. Ein bewusst manuell angelegter Einzeltermin darf vom Dreitagesrhythmus abweichen. Zeitraumplanung und automatisches Auffüllen berücksichtigen den Rhythmus.

Preis und Set-Inhalt werden beim Planen gespeichert. Änderungen an einem Preis oder einer Set-Zusammenstellung setzen betroffene offene Pay-Posts auf „zu prüfen“. Im Posteditor „Set und Preis übernehmen“ wählen, danach erneut freigeben. Bereits veröffentlichte Preise bleiben erhalten.

Pay-Posts werden ausschließlich über Telegram `sendPaidMedia` verschickt. Bei fehlendem Preis, fehlenden Rechten oder ungültigem Inhalt wird kein kostenloser Ersatzpost versandt. Unklare Versandantworten werden nicht automatisch wiederholt; zuerst den Telegram-Zielkanal prüfen.

## Updates

Die neuen Datenbankfelder werden beim normalen Programmstart ergänzend angelegt. Bestehende Telegram-Kanäle bleiben Gratis-Kanäle; vorhandene Daten und Preise anderer Plattformen werden nicht umgerechnet. Die Erweiterung wird über das normale GitHub-Update verteilt und benötigt kein separates Sabrina-Skript.

Telegram-Referenz: https://core.telegram.org/bots/api#sendpaidmedia


MP4-Videos (H.264, optional AAC, maximal 50 MB pro Datei) lassen sich auch einzeln zum Sternepreis verkaufen. Gemischte Pay-Sets aus Bildern und Videos behalten ihren Gesamtpreis und die Reihenfolge. Telegram-Sets dürfen insgesamt höchstens 100 MB enthalten. Die Auswahl „Einzelbilder und Einzelvideos“ verwendet denselben bisherigen Einstellungswert wie „Nur Einzelbilder“.
