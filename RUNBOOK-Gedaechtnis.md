# Runbook: Gesprächsgedächtnis für den AutoChat

**Stand:** 06.09.2026 · **Status:** umgebaut auf zwei Schichten, noch nicht scharf

---

## 0. Änderung vom 06.09.2026 — zwei Schichten statt drei

> **Dieser Abschnitt sticht alles Folgende, wo es sich widerspricht.** Abschnitt 3
> („Drei Gedächtnisschichten") und die Settings-Tabelle in Abschnitt 5 beschreiben
> den Stand bis v1.4. Der Rest des Runbooks — Phasen, Kostenrechnung, Risiken,
> Abnahmeverfahren — gilt unverändert.

**Was sich geändert hat und warum:** Drei gleichrangige Listen (profile/notes/loops)
haben in der Praxis nicht getragen. Das Modell durfte seine Schlagworte selbst
erfinden und legte Fächer wie „Stockings" und „Colors" neben „Name" an — danach ließ
sich weder prüfen noch zusammenführen, was es schon wusste. Und `notes` verfiel nur
nach Anzahl, nicht nach Alter: bei einem stillen Fan stand monatealter Smalltalk als
„zuletzt besprochen" im Prompt.

### Die zwei Schichten

| | **Langzeit** (`long`) | **Kurzzeit** (`short`) |
|---|---|---|
| Inhalt | Stichworte in **festen Fächern** | datierte Gesprächsnotizen |
| Fächer | name · alter · herkunft · beruf · familie · vorlieben · grenzen · sonstiges | — |
| Form | `Elektriker`, nicht „Er arbeitet als Elektriker" | ein Satz je Gesprächstag |
| Verfall | **nie** | Alter **und** Anzahl, was zuerst greift |
| Spalte | `chat_memory.long_json` | `chat_memory.short_json` |

**Offene Fäden** sind keine eigene Schicht mehr, sondern eine Kurzzeit-Notiz mit
`open: 1`. Sie überleben den Verfall und speisen weiterhin die Reaktivierung.
`memory.open_loops()` bleibt als Name erhalten, damit vorhandene Aufrufer laufen.

### Hochstufen — die Regel, an der das Ganze hängt

Kurzzeit verfällt, Langzeit nicht. Steht in einer Notiz etwas Dauerhaftes (sein Name,
sein Beruf, sein Hund), **muss** das Modell es beim Verdichten zusätzlich als Stichwort
nach `long` schreiben. Was nicht hochgestuft wird, ist nach dem Verfall endgültig weg.
Das ist Regel 6 im Prompt in `openrouter.consolidate_memory()`. Übersieht das Modell
etwas, gibt es je Notiz den Knopf **„↑ merken"** in der Oberfläche.

### Schalter je Fan — dreistufig

`chats.memory_mode`: `''` folgt dem globalen Schalter · `'on'` immer an · `'off'` immer aus.

`'on'` wirkt **auch dann, wenn das Gedächtnis global aus ist**. Genau dafür ist der
Schalter da: die Funktion an einer Handvoll Fans erproben, ohne sie für alle
scharfzuschalten. Zu bedienen unter *Subs → 🧠 Gedächtnis*. Ein Fan auf `'off'` kostet
auch keinen Verdichtungs-Aufruf — das filtert schon `db.memory_candidates()`.

Der Schalter greift an drei Stellen: `poller._fan_memory()` (Antwort-Prompt),
`poller._open_loop_hint()` (Reaktivierung) und `memory.cycle()` (Verdichtung).

### Umstellung vorhandener Daten

Läuft **beim Lesen**, nicht per SQL (`memory._upgrade_v1`): sobald ein Fan das erste
Mal angefasst wird, wandert `profile` → `long` (Fach `sonstiges`), `notes` → `short`,
offene `loops` → `short` mit `open: 1`. Die alten Spalten bleiben unverändert stehen,
ein Rückbau auf v1.4 findet seine Daten also vor.

Die alten Fakten landen zunächst alle in `sonstiges`. Das ist Absicht: raten wäre
schlimmer als einsortieren. Beim nächsten Verdichten sortiert das Modell sie in die
richtigen Fächer, oder man zieht sie von Hand um.

### Geänderte Settings

| Neu | Standard | Bedeutung |
|---|---|---|
| `memory_max_long` | 14 | Stichworte je Fan |
| `memory_max_short` | 12 | Notizen je Fan |
| `memory_short_max_age_days` | 21 | danach verfällt eine Notiz |

Entfallen: `memory_max_profile`, `memory_max_notes`, `memory_max_loops`,
`memory_loop_max_age_days`. Die drei erstgenannten Schlüssel bleiben in
`DEFAULT_SETTINGS` stehen, werden aber nicht mehr gelesen.

---

## 0b. Änderung vom 06.09.2026 — Reaktivierung hörte nie auf

**Kein Gedächtnis-Thema, aber im selben Zug behoben.** `due_reactivation_chats()`
fragte nur: *letzte Fan-Nachricht älter als X* **und** *letzte Reaktivierung älter als
Cooldown*. Antwortet ein Fan nie, wird seine letzte Nachricht nicht jünger — er war
also nach **jedem** Cooldown erneut fällig. Für immer.

Gemessen am 06.09.2026 in der Live-DB: 1190 Reaktivierungs-Drafts, **183 von 255 Fans**
mit drei oder mehr Anschreiben, zwölf Fans mit elf. Ein Fan, der seit 51 Tagen
schweigt, wurde weiter alle fünf Tage angeschrieben.

**Zwei neue Bremsen:**

| Setting | Standard | Wirkung |
|---|---|---|
| `reactivation_max_attempts` | 3 | so oft ohne Antwort, dann Ruhe |
| `reactivation_max_silence_days` | 60 | länger still = gar nicht mehr |

Getragen von `chats.reactivation_streak`: hoch bei jeder angesetzten Reaktivierung
(auch bei der manuellen), zurück auf 0, sobald der Fan **selbst** schreibt
(`poller._process_chat`). Damit ist es kein endgültiges Aus, sondern eine Pause, bis
der Fan sich meldet.

**Beim ersten Start füllt die Migration den Zähler aus der Historie** — sonst bekämen
ausgerechnet die 139 schon ausgereizten Fans nochmal drei Versuche. Simulation gegen
die echte DB: 266 dauerhaft fällige Fans → 127, und bis Anfang November auf 0.

Zwei Nebenbefunde, ebenfalls behoben:

* **236 Drafts standen auf `failed`** mit `[400] Invalid user UUID` — diese Fans gibt
  es bei Fanvue nicht mehr. Der Bot generierte für sie trotzdem alle fünf Tage weiter.
  Jetzt setzt `_send_draft()` bei dieser Fehlermeldung `bot_enabled = 0`.
* **`reactivation_inactive_days = 3`** lag als Leiche in den Settings und wurde von
  keinem Code gelesen — wirksam war `reactivation_inactive_hours`. Die Migration
  löscht den Schlüssel.

---

## 1. Ziel

Der Bot soll sich an frühere Gespräche erinnern, ohne dass der komplette Chatverlauf bei jeder Antwort ans LLM geht. Statt hunderter Nachrichten wandert ein **kompakter, gepflegter Gedächtnisblock** in den System-Prompt — aufgebaut aus verdichteten Gesprächsnotizen.

**Ausgangslage im Code:**

| Was | Wo | Heute |
|---|---|---|
| Verlauf | `poller._fetch_history()` | letzte 15 Nachrichten, live aus der Fanvue-API |
| Fan-Wissen | `chats.notes` | Freitext, **handgepflegt** |
| Injektion | `openrouter.build_messages()` | `notes` → „Wichtige Fakten über diesen Fan" |
| Speicher | — | **keine Nachrichten lokal gespeichert** |

Alles vor den letzten 15 Nachrichten ist heute unwiederbringlich weg. Genau diese Lücke schließt das Feature.

---

## 2. Grundentscheidungen — bitte freigeben oder ändern

| # | Frage | Vorschlag | Alternative |
|---|---|---|---|
| E1 | Nachrichten lokal speichern? | **Ja**, neue Tabelle `messages`, vom Poller nebenbei befüllt (keine Extra-API-Calls) | Nachts pro Fan die API paginieren — langsam, Rate-Limit-Risiko |
| E2 | Auslöser der Verdichtung | **Ereignisgesteuert** (genug neue Nachrichten *oder* Chat ruhig) + nächtlicher Sweep als Netz | Fester 24-h-Cron für alle Chats |
| E3 | Verhältnis zu `chats.notes` | **Getrennt.** Handnotizen bleiben unangetastet, Automatik schreibt in neue Felder. Beide werden injiziert | Automatik überschreibt `notes` — würde deine Handarbeit löschen |
| E4 | Modell für die Verdichtung | Eigener Task `memory`, Setting `memory_model` = **`openai/gpt-5.6-luna`** (euer Klassifizierer-Modell, 15× günstiger als DeepSeek — siehe 5.1) | Immer das Chat-Modell |
| E5 | Gedächtnis pro Persona getrennt? | **Nein** (eine Persona pro Instanz, `user_uuid` reicht als Schlüssel) | Zusätzlicher Persona-Schlüssel — nur nötig bei Mehrfach-Accounts |
| E6 | Aufbewahrung roher Nachrichten | 90 Tage, dann automatisch löschen | Unbegrenzt (DB wächst) |
| E7 | Phase 2 (Echtzeit-Extraktion) jetzt? | **Später** — Phase 1+3 liefern den Großteil des Effekts. Der Klassifizierer läuft heute nur bei `ppv_enabled` und müsste erst entkoppelt werden | Sofort mitbauen |

> **E5 und E7 sind die einzigen, bei denen ich mir ohne deine Antwort unsicher bin.**

---

## 3. Architektur

```
Fanvue-API
    │
    ▼
poller._process_chat ──► messages (SQLite)      [Phase 0]
    │                        │
    │                        ▼
    │                 memory._consolidate()      [Phase 3]
    │                 (LLM, günstig, selten)
    │                        │
    │                        ▼
    │                  chat_memory               [Phase 1]
    │                  ├─ profile   stabile Fakten
    │                  ├─ notes     rollende Gesprächsnotizen
    │                  └─ loops     offene Fäden
    │                        │
    ▼                        ▼
openrouter.build_messages ◄──┘  + chats.notes (Hand)
    │
    ▼
System-Prompt (harte Zeichengrenze!) → LLM-Antwort
                             │
                             └──► _create_reactivation_draft [Phase 4]
```

### Drei Gedächtnisschichten

| Schicht | Inhalt | Lebensdauer | Max. im Prompt |
|---|---|---|---|
| **Profil** | Job, Stadt, Beziehung, Vorlieben, No-Gos, Kaufverhalten | dauerhaft, wird ergänzt statt ersetzt | 6 Einträge |
| **Notizen** | datierte Einzeiler zu Gesprächen | rollend, ältere verfallen | 5 neueste |
| **Offene Fäden** | Dinge mit Fälligkeit, auf die man zurückkommt | bis erledigt | 3 offene |

Die offenen Fäden sind der Hebel für die Reaktivierung: „Wie war das Gespräch am Montag?" statt generischem „Wo bist du?".

---

## 4. Phasen

Jede Phase ist einzeln lauffähig und einzeln abnehmbar. **Nach jeder Phase: deployen, beobachten, dann weiter.**

---

### Phase 0 — Nachrichten lokal speichern

**Ziel:** Grundlage für alles Weitere. Ohne lokale Nachrichten kann der Verdichter nur mit 15 Nachrichten arbeiten oder muss die API quälen.

**Dateien:** `app/db.py`, `app/poller.py`

**db.py — SCHEMA ergänzen:**

```sql
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_uuid  TEXT NOT NULL,
    msg_uuid   TEXT UNIQUE,          -- Fanvue-UUID, verhindert Duplikate
    direction  TEXT,                 -- 'in' | 'out'
    text       TEXT,
    has_media  INTEGER DEFAULT 0,
    msg_type   TEXT,                 -- z.B. 'TIP'
    sent_at    REAL,                 -- Zeitstempel laut Fanvue
    seen_at    REAL                  -- wann lokal erfasst
);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_uuid, sent_at);
```

**db.py — neue Funktionen:**

```python
def store_messages(user_uuid: str, messages_chrono: list, me_uuid: str) -> int
    # INSERT OR IGNORE über msg_uuid; gibt Anzahl neu gespeicherter zurück
def messages_since(user_uuid: str, after_id: int, limit: int = 80) -> list[sqlite3.Row]
def last_message_id(user_uuid: str) -> int
def purge_old_messages(days: int) -> int
```

**poller.py:** in `_process_chat()` direkt nach `messages_chrono = list(reversed(messages))` (Zeile ~165) `db.store_messages(...)` aufrufen. Ebenso in `_fetch_history()` und nach erfolgreichem Senden in `_send_draft()`, damit auch eigene Nachrichten sicher landen.

**Neue Settings:** `messages_store_enabled` (True), `messages_retention_days` (90)

**Abnahme:**
- Nach einem Poll-Zyklus stehen Nachrichten in der Tabelle, `direction` stimmt.
- Zweiter Zyklus über denselben Chat erzeugt **keine** Duplikate.
- DB-Größe nach 24 h prüfen.

**Rollback:** `messages_store_enabled = False`. Tabelle stört nicht.

---

### Phase 1 — Gedächtnis speichern und injizieren

**Ziel:** Das Gedächtnis existiert, ist im Prompt, ist auf der Subs-Seite einsehbar und von Hand editierbar. **Noch ohne LLM** — erst mal nur die Leitungen legen und manuell testen.

**Dateien:** `app/db.py`, `app/memory.py` *(neu)*, `app/openrouter.py`, `app/poller.py`, `app/main.py`, `templates/chats.html`, `templates/settings.html`

**db.py — SCHEMA:**

```sql
CREATE TABLE IF NOT EXISTS chat_memory (
    user_uuid    TEXT PRIMARY KEY,
    profile_json TEXT DEFAULT '[]',   -- stabile Fakten
    notes_json   TEXT DEFAULT '[]',   -- rollende Gesprächsnotizen
    loops_json   TEXT DEFAULT '[]',   -- offene Fäden
    last_run_at  REAL,                -- letzte Verdichtung
    last_msg_id  INTEGER DEFAULT 0,   -- bis wohin verdichtet (messages.id)
    dirty_count  INTEGER DEFAULT 0,   -- neue Fan-Nachrichten seit Verdichtung
    updated_at   REAL
);

CREATE TABLE IF NOT EXISTS memory_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_uuid TEXT, ts REAL,
    action    TEXT,     -- 'add' | 'update' | 'expire' | 'manual'
    layer     TEXT,     -- 'profile' | 'note' | 'loop'
    content   TEXT,
    model     TEXT
);
```

**Datenformate (JSON-Listen):**

```jsonc
// profile
[{"t": "job", "v": "Elektriker in Rotterdam", "at": 1786000000, "src": "auto"}]
// notes
[{"d": "2026-08-14", "v": "Streit mit dem Chef, war frustriert"}]
// loops
[{"v": "Vorstellungsgespräch am Montag", "due": "2026-08-24", "at": 1786000000, "done": 0}]
```

`src: "manual"` markiert Einträge, die du selbst angelegt hast — **die darf die Automatik nie überschreiben oder löschen.**

**Migration:** neue Tabellen in `SCHEMA`; `_migrate()` bleibt für die Spalten-Nachrüstung wie gehabt.

**Neues Modul `app/memory.py`:**

```python
def get(user_uuid: str) -> dict            # {"profile": [...], "notes": [...], "loops": [...]}
def save(user_uuid: str, mem: dict, model: str = "") -> None   # schreibt + memory_log
def render_block(user_uuid: str, max_chars: int = 0) -> str    # Prompt-Text, gekürzt
def mark_dirty(user_uuid: str, n: int = 1) -> None
def add_manual(user_uuid: str, layer: str, value: str) -> None
def close_loop(user_uuid: str, index: int) -> None
```

**`render_block()` — Priorität beim Kürzen:** Profil → offene Fäden → Notizen (neueste zuerst). Harte Grenze `memory_max_chars` (Default **1200**). Ausgabeform:

```
Was du über diesen Fan weißt:
- Elektriker in Rotterdam, geschieden, Hund Bruno
- mag Dessous, keine Füße
Offen (darauf kannst du zurückkommen):
- Vorstellungsgespräch am Montag (24.08.)
Zuletzt besprochen:
- 14.08.: Streit mit dem Chef
- 12.08.: plant Urlaub in Spanien
```

**openrouter.py:** `build_messages(system_prompt, history, me_uuid, fan_notes="", fan_memory="")` — `fan_memory` **nach** `fan_notes` in den System-Prompt (Handnotizen haben Vorrang, Gedächtnis ist Ergänzung).

**poller.py:** in `_process_chat()` bei `fan_notes = chat["notes"] or ""` (Zeile ~211) ergänzen:
```python
fan_memory = memory.render_block(user_uuid) if db.get_setting("memory_enabled", False) else ""
```
und durch `_generate(...)` bis `build_messages(...)` durchreichen. `_generate()` bekommt einen Parameter `fan_memory: str = ""`.

**UI `settings.html`:** neuer Abschnitt „Gedächtnis" mit allen Settings aus Abschnitt 5.

#### UI auf der Subs-Seite (`templates/chats.html`) — dritter Aufklapp-Block

Pro Fan-Karte kommt **nach** „📊 Fanvue Daten" (endet Zeile 167) ein dritter Block im exakt gleichen Muster wie die beiden vorhandenen:

```html
<details class="settings">
  <summary>🧠 Gedächtnis</summary>
  ...
</details>
```

Die vorhandene CSS-Klasse `details.settings` (Zeile 184–190) greift automatisch — kein neues Styling nötig, der Block sieht aus wie „Einstellungen" und „Fanvue Daten".

**Inhalt, drei Abschnitte untereinander:**

```
🧠 Gedächtnis                                    [Jetzt aktualisieren]
─────────────────────────────────────────────────────────────────────
Profil                                    zuletzt verdichtet: vor 4 Std.
  • Elektriker in Rotterdam                        seit 02.08.  [Hand] ✕
  • geschieden, Hund namens Bruno                  seit 09.08.        ✕
  • mag Dessous, keine Füße                        seit 12.08.        ✕
  [+ Fakt hinzufügen ______________________]

Offene Fäden
  • Vorstellungsgespräch am Montag       fällig 24.08.   [erledigt] ✕
  [+ Faden hinzufügen ______________________]

Zuletzt besprochen
  14.08.  Streit mit dem Chef, war frustriert                        ✕
  12.08.  plant Urlaub in Spanien                                    ✕

▸ Was der Bot davon sieht (1180 / 1200 Zeichen)
```

**Details:**

- **`[Hand]`-Marker** an Einträgen mit `src: "manual"` — die rührt die Automatik nie an (E3).
- **`✕`** löscht einen einzelnen Eintrag, **`[erledigt]`** setzt einen Faden auf `done: 1`.
- **„Jetzt aktualisieren"** stößt die Verdichtung für diesen Fan sofort an (ab Phase 3; vorher ausgegraut).
- **„Was der Bot davon sieht"** — ein verschachteltes `<details>`, das den fertigen Prompt-Block aus `memory.render_block()` **wörtlich** zeigt, mit Zeichenzähler gegen `memory_max_chars`. Das ist die ehrlichste Kontrolle: du siehst genau, was beim Modell ankommt, und ob etwas wegen der Grenze abgeschnitten wird.
- **Leerer Zustand:** „Noch kein Gedächtnis für diesen Fan." Ist `memory_enabled = False`, zusätzlich: „Gedächtnis ist global ausgeschaltet — Einträge werden gespeichert, aber nicht an das Modell übergeben." (Verlinkung nach `/settings#memory`.)
- **Bestehenden Hinweistext anpassen:** Zeile 166 verweist heute auf das Feld „Notizen / Gedächtnis" unter „Einstellungen" — dort ergänzen, dass das automatische Gedächtnis im neuen Block steht. Das Handfeld selbst bleibt unverändert unter „Einstellungen" (E3).

**Backend dazu:**

- `main.py` Route `/chats` (Zeile ~983): im `enriched`-Dict pro Fan `"memory": memory.get(m["uuid"])` und `"memory_block": memory.render_block(m["uuid"])` ergänzen.
  *Achtung Performance:* das sind zwei DB-Zugriffe pro Fan-Karte. Bei großen Listen stattdessen **einmal** alle `chat_memory`-Zeilen laden (`memory.get_many(uuids)`) und zuordnen — wie es `_insights_for()` schon vormacht.
- Neue Routen:
  - `POST /chats/{user_uuid}/memory/add` — Felder `layer` (`profile`/`loop`/`note`), `value`, optional `due` → legt Eintrag mit `src: "manual"` an
  - `POST /chats/{user_uuid}/memory/delete` — Feld `layer`, `index`
  - `POST /chats/{user_uuid}/memory/close` — Feld `index` (Faden erledigt)
  - `POST /chats/{user_uuid}/memory/run` — Verdichtung sofort (Phase 3)
  
  Alle mit Redirect zurück auf `/chats?msg=...`, wie die bestehende `/chats/{uuid}/update`-Route (Zeile 1236).

**Abnahme:**
- Über die Testseite (`/test`) einen Fan mit gepflegtem Gedächtnis prüfen: Block steht im Prompt, Antwort nimmt Bezug darauf.
- `memory_max_chars` auf 200 setzen → Block wird korrekt gekürzt, Profil überlebt, Notizen fallen zuerst weg. Der Zeichenzähler in der UI zeigt die Kürzung an.
- `memory_enabled = False` → Prompt exakt wie vorher (Diff prüfen!), UI zeigt den Hinweis.
- Handnotizen in `chats.notes` unverändert.
- **Subs-Seite:** dritter Block „🧠 Gedächtnis" klappt auf, zeigt alle drei Schichten; Hinzufügen/Löschen/Erledigt funktionieren und landen im `memory_log`; „Was der Bot davon sieht" stimmt mit dem echten Prompt aus `/test` überein.
- Ladezeit der Subs-Seite mit voller Fan-Liste vorher/nachher vergleichen.

**Rollback:** `memory_enabled = False`.

---

### Phase 2 — *(optional, siehe E7)* Echtzeit-Extraktion

**Ziel:** Wichtige Fakten landen **sofort** im Gedächtnis statt erst bei der nächsten Verdichtung.

**Ansatz:** `openrouter.classify_message()` bekommt ein zusätzliches JSON-Feld `"memory": ["kurzer Fakt", ...]` (max. 2 Einträge, nur wörtlich Gesagtes). Grenzkosten: wenige Output-Tokens auf einem Call, der ohnehin läuft.

**Hürde:** Der Klassifizierer läuft heute nur unter `if db.get_setting("ppv_enabled", False)` (poller.py ~Zeile 240). Entweder entkoppeln (eigener, günstiger Call bei jeder Fan-Nachricht) oder Phase 2 zurückstellen. **Vorschlag: zurückstellen**, bis Phase 3 im Betrieb bewertet ist.

---

### Phase 3 — Verdichtung (das eigentliche Feature)

**Ziel:** Neue Nachrichten werden zu Gedächtnis verdichtet — ereignisgesteuert, günstig, inkrementell.

**Dateien:** `app/memory.py`, `app/openrouter.py`, `app/poller.py`

**openrouter.py:**
- `resolve_model()` um Task `"memory"` erweitern → Setting `memory_model`, leer = Chat-Modell.
- Neue Funktion:
```python
def consolidate_memory(current: dict, new_messages: list[dict], lang: str = "de") -> dict
```
  - `temperature = 0`, `max_tokens ≈ 600`, `response_format` JSON falls das Modell es kann
  - Kosten über `_record_cost(..., category="memory")` → taucht automatisch in eurer Kostenstatistik auf
  - Bei Parse-Fehler: `{}` zurück, altes Gedächtnis bleibt unangetastet

**Prompt-Regeln (streng, das ist der kritische Teil):**

1. Nur festhalten, was der Fan **wörtlich gesagt** hat. Nichts folgern, nichts ausschmücken.
2. Bestehende Fakten **nur ersetzen**, wenn der Fan explizit widerspricht — sonst ergänzen.
3. Einträge mit `"src": "manual"` **niemals** verändern oder löschen.
4. Maximal 12 Profil-Fakten, 10 Notizen, 5 offene Fäden — beim Überlauf das Älteste/Unwichtigste streichen.
5. Erledigte Fäden auf `done: 1` setzen, wenn das Thema abgeschlossen wurde.
6. Keine Zahlungsdaten, keine Klarnamen Dritter, keine Gesundheitsdiagnosen (siehe Abschnitt 6).
7. Ausgabe: ausschließlich JSON im vorgegebenen Schema.

**Eingabe pro Lauf:** aktuelles Gedächtnis (JSON) + neue Nachrichten seit `last_msg_id` (max. 60 Nachrichten / 4000 Zeichen, sonst die neuesten). Klein und billig.

**Auslöser (`poller._memory_cycle_if_due()`, nach dem Muster von `_prio2_cycle_if_due()`):**

Ein Chat wird verdichtet, wenn `dirty_count >= 1` **und** eine der Bedingungen greift:

| Bedingung | Default |
|---|---|
| `dirty_count >= memory_trigger_messages` | 20 neue Fan-Nachrichten |
| Chat ruhig: `now - last_inbound_at >= memory_quiet_hours` | 2 h |
| Nächtlicher Sweep zur Stunde `memory_sweep_hour` | 4 Uhr |

Zusätzlich immer: `now - last_run_at >= memory_min_interval_hours` (6 h) und `memory_max_per_cycle` (5) Chats pro Durchlauf, damit ein Nachholstau nicht auf einmal durchschlägt. Auswahl der fälligen Chats **`ORDER BY last_run_at ASC`**, damit ein Vielschreiber die anderen nicht aushungert.

Einhängen in `_loop()` neben `reactivation_cycle()`. `dirty_count` wird in `_process_chat()` bei jeder neuen Fan-Nachricht hochgezählt, in `memory.save()` zurückgesetzt.

**Betrieb ohne Prio-Listen (`chat_custom_list_id` leer):**

Der Auslöser hängt an `_process_chat()`, nicht an der Gruppenauswahl — das Gedächtnis greift also unverändert, egal ob über Prio-Listen oder über alle unbeantworteten Chats gearbeitet wird. Zwei Folgen, die dann aber zu bedenken sind:

1. **Jeder Fan bekommt ein Gedächtnis**, auch der, der einmal „hi" schreibt und nie wieder. Das kostet einen LLM-Call und erzeugt Datenmüll. Gegenmittel: Setting **`memory_min_fan_messages`** (Default **5**) — unterhalb dieser Zahl gespeicherter Fan-Nachrichten wird nicht verdichtet. Die Nachrichten werden trotzdem mitgeschrieben, das Gedächtnis entsteht rückwirkend, sobald die Schwelle fällt.
2. **`memory_min_interval_hours` ist der eigentliche Kostenhebel**, nicht `memory_max_per_cycle`. Bei 6 h sind es maximal 4 Verdichtungen pro Fan und Tag. Bei euren Volumina ist das unkritisch (siehe 5.1) — bei deutlich mehr aktiven Fans auf 12 h stellen. Nach der ersten Woche `api_costs` mit `category='memory'` prüfen und nachjustieren.

**Abnahme:**
- Manueller Testknopf „Gedächtnis jetzt aktualisieren" pro Chat (Route `POST /chats/{uuid}/memory/run`) — vor/nach im `memory_log` vergleichen.
- **Halluzinations-Stichprobe:** 10 Chats verdichten, jeden Fakt gegen die Rohnachrichten prüfen. **Abbruchkriterium: mehr als 1 erfundener Fakt in 10 Chats → Prompt nachschärfen, nicht produktiv schalten.**
- Handnotizen und `src: "manual"`-Einträge nach der Verdichtung unverändert.
- Kosten: `api_costs` mit `category='memory'` nach 24 h gegen die Erwartung prüfen.

**Rollback:** `memory_consolidate_enabled = False` — Injektion (Phase 1) läuft weiter.

---

### Phase 4 — Reaktivierung nutzt offene Fäden

**Ziel:** Reaktivierung mit konkretem Anknüpfungspunkt statt Floskel.

**Dateien:** `app/poller.py`

In `_create_reactivation_draft()` (Zeile ~937): den ältesten offenen Faden holen und in den `structure`-Block einbauen — Punkt 2 („Bezug aufs letzte Thema") wird konkret:

```
2) Nimm KURZ Bezug auf: "{faden}". Frag beiläufig und warm nach, wie es
   ausgegangen ist. Kein Verkauf. Wenn das Thema unpassend wirkt, ignoriere es.
```

Zusätzlich `render_block()` als `fan_memory` an `_generate()` durchreichen.

**Später optional:** Ein Faden mit fälligem `due`-Datum *löst* die Reaktivierung aus, statt nur den Text zu füttern — dann wird aus `due_reactivation_chats()` zusätzlich eine Fälligkeitsprüfung auf offene Fäden.

**Abnahme:** 5 Reaktivierungs-Drafts im Freigabe-Modus prüfen — Bezug korrekt, nicht aufdringlich, keine erfundenen Details.

---

### Phase 5 — *(optional, später)* Wörtlicher Rückgriff per Volltextsuche

Wenn der Fan sagt „weißt du noch, das mit meinem Bruder?", holt eine SQLite-**FTS5**-Suche über `messages` die echten alten Zeilen in den Prompt. Kein LLM, keine Embeddings, keine laufenden Kosten. Setzt Phase 0 voraus. Vektor-DB/RAG halte ich für euren Maßstab für überdimensioniert.

---

## 5. Neue Settings (alle in `DEFAULT_SETTINGS`, alle in `settings.html`)

| Key | Default | Zweck |
|---|---|---|
| `messages_store_enabled` | `True` | Phase 0 an/aus |
| `messages_retention_days` | `90` | Rohnachrichten aufbewahren |
| `memory_enabled` | `False` | **Hauptschalter Injektion** |
| `memory_consolidate_enabled` | `False` | **Hauptschalter Verdichtung** |
| `memory_model` | `openai/gpt-5.6-luna` | günstiges Modell, siehe 5.1 |
| `memory_max_chars` | `1200` | harte Prompt-Grenze |
| `memory_trigger_messages` | `20` | neue Nachrichten bis zur Verdichtung |
| `memory_quiet_hours` | `2` | Stille bis zur Verdichtung |
| `memory_min_interval_hours` | `6` | Mindestabstand pro Chat — **Hauptkostenhebel** |
| `memory_min_fan_messages` | `5` | ab wie vielen Fan-Nachrichten überhaupt verdichtet wird |
| `memory_max_per_cycle` | `3` | Chats pro Durchlauf |
| `memory_sweep_hour` | `4` | Stunde des Sicherheits-Sweeps |
| `memory_max_profile` / `_notes` / `_loops` | `12` / `10` / `5` | gespeicherte Einträge |

Beide Hauptschalter starten auf **False** — nichts ändert sich, bis du bewusst einschaltest.

### 5.1 Dimensionierung — gerechnet mit euren echten Zahlen

Gemessen in `data/bot.db` am 19.08.2026:

| Kennzahl | Wert |
|---|---|
| Fans mit eigener Nachricht, letzte 7 Tage | **66** |
| Fans mit Aktivität (inkl. Reaktivierungen), 7 Tage | 144 |
| davon mit ≥ 5 Antworten | **48** · mit ≥ 20: 25 |
| Antworten je Fan (7 Tage) | Median **2** · Ø 24,8 · **Max 456** |
| Chat-Calls | 615 / Tag · Ø **$0,00197** je Call |
| Chat-Kosten | **$1,32 / Tag** ≈ $40 / Monat |
| System-Prompt-Basis | 9.672 Zeichen ≈ 2.400 Tokens |
| Kontext je Call | ≈ 3.200 Tokens (Prompt + 15 Nachrichten) |
| Chat-Modell | `deepseek/deepseek-v4-pro` |
| Klassifizierer | `openai/gpt-5.6-luna`, **$0,000135** je Call |

**Was das Gedächtnis kostet:**

| Posten | Rechnung | Kosten |
|---|---|---|
| Injektion (jede Antwort) | 1.200 Zeichen ≈ 300 Tokens auf 3.200 → **+9 %** Kontext | **≈ $0,12 / Tag · $3,70 / Monat** |
| Verdichtung | ~45 Läufe/Tag mit `gpt-5.6-luna` | **≈ $0,01 / Tag · $0,30 / Monat** |
| **Summe** | | **≈ $4 / Monat auf $40 Bestand (+10 %)** |

**Daraus die konkreten Empfehlungen — die Defaults aus der Tabelle oben werden damit:**

- **`memory_model = openai/gpt-5.6-luna`** (statt leer). Das ist euer Klassifizierer-Modell und **15× günstiger** als DeepSeek — und Verdichtung ist eine strukturierte JSON-Aufgabe, genau sein Profil. Das Chat-Modell dafür zu nehmen wäre reine Geldverschwendung.
- **`memory_min_interval_hours = 6`** (nicht 12). Bei diesen Volumina sind die Verdichtungskosten Rauschen — Frische ist mehr wert als die eingesparten Cent.
- **`memory_min_fan_messages = 5`** bestätigt: von 144 Fans mit Aktivität haben nur **48** fünf oder mehr Antworten. Die Schwelle filtert also rund zwei Drittel weg, ohne dass ein relevanter Fan verloren geht.
- **`memory_max_per_cycle = 3`** reicht. Der Poller läuft alle 60 s → 180 Verdichtungen/Stunde Kapazität bei ~45 Bedarf/Tag.
- **`memory_max_chars = 1200`** ist die richtige Größe. Zum Vergleich: 2.000 Zeichen wären +15 % Kontext statt +9 %, also ~$6/Monat. Machbar, aber der Nutzen der zusätzlichen 800 Zeichen ist gering — die wichtigsten Fakten stehen ohnehin oben.

**Ausreißer beachten:** Ein Fan hatte **456 Antworten in 7 Tagen** (~65/Tag). Der reißt die Nachrichtenschwelle mehrmals täglich. `memory_min_interval_hours` deckelt ihn auf 4 Verdichtungen/Tag — ohne diese Bremse wären es allein für ihn über 20.

**Speicherbedarf Phase 0:** ~1.500 Nachrichten/Tag × ~200 Zeichen ≈ 0,3 MB/Tag → bei 90 Tagen Aufbewahrung ~27 MB. Die DB ist heute 36 MB. Unkritisch.

---

## 6. Risiken und Gegenmaßnahmen

| Risiko | Warum kritisch | Gegenmaßnahme |
|---|---|---|
| **Erfundene Fakten** | Ein falscher Fakt geht in *jede* künftige Antwort ein — der Bot behauptet wochenlang Unsinn | Temperatur 0, „nur Wörtliches", `memory_log` als Audit-Spur, editierbare UI, Stichprobe als Abnahmekriterium |
| Prompt wächst unbemerkt | Kosten pro Antwort steigen schleichend | `memory_max_chars` hart, Kürzung nach Priorität |
| Automatik löscht Handarbeit | Deine Pflege wäre umsonst | `chats.notes` bleibt unberührt, `src: "manual"` ist tabu |
| Verdichtung stürzt ab / API-Fehler | Poller darf nie stehenbleiben | Alles in try/except, bei Fehler altes Gedächtnis behalten, `db.log("error", "memory", ...)` |
| Rate Limits | viele Chats gleichzeitig fällig | `memory_max_per_cycle` |
| **Datenschutz** | Es entstehen Persönlichkeitsprofile über zahlende Kunden — teils Gesundheit, Beziehungen, Sexualität | Ausschlussliste im Prompt (keine Zahlungsdaten, keine Diagnosen, keine Klarnamen Dritter), Retention für Rohnachrichten, Gedächtnis wird beim Löschen des Chats mitgelöscht |

---

## 7. Aufwand

| Phase | Aufwand | Nutzen |
|---|---|---|
| 0 — Nachrichten speichern | klein | Grundlage, sofort Auswertungen möglich |
| 1 — Speichern + Injektion + UI | mittel | **größter spürbarer Effekt** |
| 3 — Verdichtung | mittel | Automatik, kein Handbetrieb |
| 4 — Reaktivierung | klein | hoher Effekt pro Aufwand |
| 2 / 5 — optional | mittel | Feinschliff |

Phase 0+1 allein liefern schon ein funktionierendes Gedächtnis — nur eben von Hand gepflegt. Phase 3 nimmt dir die Handarbeit ab.

---

## 8. Deployment (je Phase)

1. Änderungen im Share `/Volumes/fanvue/Fanvue_Chatbot` machen und committen
2. Auf dem Ubuntu-CT `192.168.20.16` unter `/srv/fanvue/Fanvue_Chatbot` aktualisieren
3. `sudo systemctl restart fanvue-chatbot`
4. **Vorher:** `data/*.db` sichern — die Migration legt Tabellen an
5. Erst mit Hauptschaltern auf `False` deployen, dann im Freigabe-Modus an einem Chat testen, dann breit

---

## 9. Nicht im Scope

- Vektor-Datenbank / Embeddings / RAG
- Gedächtnis über mehrere Personas hinweg (siehe E5)
- Automatisches Nachladen alter Nachrichten aus der Fanvue-API (nur laufend Mitgeschriebenes)
- Änderungen an PPV-Logik, Guardrails oder Sende-Mechanik

---

## 10. Freigabe

- [ ] Grundentscheidungen E1–E7 bestätigt oder geändert
- [ ] Phasenreihenfolge und Umfang bestätigt
- [ ] Defaults aus Abschnitt 5 bestätigt
- [ ] Start mit Phase: ______
