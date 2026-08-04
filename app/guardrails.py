"""Guardrail-Schicht: prueft generierte Antworten und eingehende Nachrichten,
bevor etwas gesendet oder automatisiert wird."""
from __future__ import annotations

import re
from datetime import datetime
from typing import NamedTuple, Optional

from . import db, persona_context

# Regieanweisungen / Platzhalter, die das LLM faelschlich in den Text schreibt
_RE_PPV_TAG = re.compile(r"\[[^\]\n]*(?:ppv|€|\$|preis|price)[^\]\n]*\]", re.I)
_RE_STAGE = re.compile(
    r"\([^)\n]*(?:send|sends|sending|schick|sendet|verschick|preview|vorschau|"
    r"image|bild|photo|foto|attach|anh[aä]ng|zeigt|shows)[^)\n]*\)", re.I)
_RE_ASTERISK = re.compile(r"\*[^*\n]{2,}\*")
_RE_POINTER = re.compile(r"[\U0001F449\U0001F447➡️]+\s*")  # 👉 👇 ➡ etc.

# Modell-Weigerungen / Meta-Kommentare, die NIEMALS an einen Fan gehen duerfen
# (z.B. "Ich kann keine explizite ... verfassen. Ich kann sie aber ...").
# WICHTIG: eng gefasst - ein blosses "I can't keep" / "ich kann nicht warten" im
# normalen Chat darf NICHT als Weigerung gelten. Es muss um das ERZEUGEN von
# Inhalt / Assistenz gehen (Verb wie write/create/help bzw. verfassen/schreiben).
_RE_REFUSAL = re.compile(
    r"("
    # Englisch: Modell weigert sich, Inhalt zu erzeugen / zu helfen
    r"\bi\s+(?:can'?t|cannot|can not|won'?t|will not|am unable to|'?m unable to|"
    r"am not able to|'?m not able to)\s+"
    r"(?:write|create|generate|produce|provide|make|assist|help|comply|complete|"
    r"continue|fulfil|fulfill|describe|caption|roleplay|do that|be part)"
    r"|\bas an ai\b|\bas a language model\b|\bi'?m an ai\b|\bi am an ai\b"
    r"|\bi'?m sorry,? but i\b|\bi cannot assist\b|\bi can'?t assist\b"
    r"|\bagainst (?:my|the) (?:content )?(?:policy|policies|guidelines)\b|\bcontent policy\b"
    # Deutsch: Weigerung, Inhalt zu erzeugen
    r"|ich kann keine?\b[^.\n]{0,90}\b(?:verfassen|schreiben|erstellen|generieren|"
    r"formulieren|liefern|anbieten)"
    r"|ich kann\b[^.\n]{0,50}\bnicht\b[^.\n]{0,25}\b(?:verfassen|schreiben|erstellen|"
    r"generieren|formulieren|helfen|liefern)"
    r"|ich darf (?:das |dir |dabei )?nicht\b|als (?:eine )?ki\b|als (?:ein )?sprachmodell\b"
    r"|nicht[- ]grafisch|es tut mir leid,? (?:aber|ich)"
    r")", re.I)
# Zitierte Beispiel-Caption in „...", "..." oder «...»
_RE_QUOTED = re.compile(r"[„\"“«]([^\"“”„«»]{10,})[\"”“»]")

# Erfundene Plattform-/Navigations-/Kaufhinweise. Fanvue hat keinen "Katalog",
# keine "Seite zum Stoebern", keinen "Medientyp"-Filter und keine Links – der
# Content kommt direkt im Chat als kaufbare Nachricht. Das Modell halluziniert
# solche Wege aber gern, wenn ein Fan fragt "wo/wie bekomme ich das".
_RE_FAKE_NAV = re.compile(
    r"("
    r"medientyp"
    r"|\bkatalog\b|\bcatalogue?\b"
    r"|st[öo]ber"
    r"|profilseite|startseite|auf meinem profil|auf meiner fanvue|fanvue[- ]seite"
    r"|on my (?:page|profile)|in my catalog"
    r"|schau\w*[^.\n]{0,20}auf (?:meiner|der|meine|deiner) seite"
    r"|(?:findest|freischalten|videos?)[^.\n]{0,20}auf (?:meiner|der) seite"
    r"|schick\w*[^.\n]{0,25}\blink\b|hier ist (?:der|dein|ein) link"
    r"|schicke? dir (?:einen|den|nen) link"
    r"|send you (?:a|the) link|here'?s the link"
    r")", re.I)


# Fremde Schriftsysteme, die in einem DE/EN-Chat nie vorkommen sollten
# (CJK: Chinesisch/Japanisch/Koreanisch, Kyrillisch, Arabisch, Hebraeisch, Thai).
_RE_FOREIGN_SCRIPT = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿가-힯"   # CJK + Kana + Hangul
    r"Ѐ-ӿ؀-ۿ֐-׿฀-๿]")   # Kyrill., Arab., Hebr., Thai


# --------------------------------------------------------- Zeit-Plausibilitaet
# Letztes Netz hinter dem Zeitkontext aus persona_context: Das Modell haelt sich
# meistens, aber nicht immer an den injizierten Block. Hier wird die FERTIGE
# Antwort gegengeprueft - "gute Nacht" um 10 Uhr morgens, "sitze in der Uni"
# am Samstagabend. Treffer blocken nicht, sie erzwingen nur manuelle Freigabe.

class TimeRule(NamedTuple):
    label: str
    pattern: re.Pattern[str]
    start: int                    # ab dieser Stunde plausibel
    end: int                      # bis (exkl.) dieser Stunde; Ueberlauf erlaubt
    activity_words: tuple[str, ...]  # passt eines davon zur Plan-Taetigkeit -> ok
    weekday_only: bool = False    # zusaetzlich: am Wochenende unplausibel
    tense_escape: bool = False    # bei Vergangenheits-/Zukunftsmarkern ignorieren


# Vergangenheit/Zukunft im selben Satz -> keine Aussage ueber das JETZT.
# ("morgen hab ich Uni", "war heute in der Vorlesung") - nur fuer Regeln, bei
# denen das realistisch vorkommt, sonst wuerde "guten Morgen" faelschlich greifen.
# BEWUSST nur TAG-Marker: schwache Marker wie "spaeter", "gleich" oder "bald"
# stehen staendig harmlos im Satz ("meld mich spaeter") und wuerden echte
# Widersprueche verschlucken.
_RE_TENSE = re.compile(
    r"\b(morgen|gestern|vorgestern|[uü]bermorgen|n[aä]chste[nrs]?|letzte[nrs]?|"
    r"war|warst|hatte|hatt?est|werde|wirst|wollte|wollt|"
    r"fr[uü]her|damals)\b", re.I)

_TIME_RULES: tuple[TimeRule, ...] = (
    TimeRule(
        label="Schlafengehen/Gute Nacht",
        pattern=re.compile(
            r"(gute\s*n8|gut[e]?\s+nacht\b|good\s*ni(?:ght|te)\b|"
            r"schlaf\s+(?:gut|sch[oö]n)|traeum\s+was|tr[aä]um\s+was|"
            r"geh(?:e)?\s+(?:jetzt\s+|dann\s+|gleich\s+)?(?:ins|zu)\s+bett|"
            r"ab\s+ins\s+bett|leg(?:e)?\s+mich\s+(?:jetzt\s+|gleich\s+)?(?:hin|schlafen)|"
            r"mach(?:e)?\s+mich\s+bettfertig|"
            r"(?:going|off|heading)\s+to\s+bed|time\s+for\s+bed)", re.I),
        start=21, end=4,
        activity_words=("schlaf", "bett", "nacht", "muede", "müde"),
    ),
    TimeRule(
        label="Aufwachen/Guten Morgen",
        pattern=re.compile(
            r"(gu?ten\s+morgen\b|good\s*morning\b|mor(?:gen|gi)n?\s*[!.:]|"
            r"(?:gerade|grad|eben)\s+(?:erst\s+)?(?:aufgewacht|aufgestanden|wach\s+geworden)|"
            r"bin\s+(?:gerade|grad|eben)\s+(?:erst\s+)?aufgestanden|"
            r"noch\s+(?:ganz\s+|voll\s+)?verschlafen|just\s+woke\s+up|"
            r"fr[uü]hst[uü]ck)", re.I),
        start=4, end=12,
        activity_words=("aufgestanden", "aufgewacht", "kaffee", "morgen",
                        "verschlafen", "fruehstueck", "frühstück"),
    ),
    TimeRule(
        label="Uni/Arbeit",
        pattern=re.compile(
            r"(in\s+der\s+(?:uni|vorlesung|schule|bibliothek|bib|arbeit)\b|"
            r"an\s+der\s+uni\b|auf\s+(?:der\s+)?arbeit\b|bei\s+der\s+arbeit\b|"
            r"im\s+(?:b[uü]ro|h[oö]rsaal|seminar|unterricht)\b|"
            r"(?:sitz|steck)(?:e)?\s+(?:gerade\s+|grad\s+|noch\s+)*(?:in|im)\s+"
            r"(?:der\s+)?(?:uni|vorlesung|meeting|b[uü]ro)|"
            r"\bat\s+(?:uni|work|the\s+office)\b|\bin\s+class\b|\bin\s+a\s+lecture\b)", re.I),
        start=7, end=20,
        activity_words=("uni", "vorlesung", "arbeit", "buero", "büro", "job",
                        "lernen", "schule", "kurs", "seminar", "office"),
        weekday_only=True,
        tense_escape=True,
    ),
    TimeRule(
        label="Mittagessen",
        pattern=re.compile(r"(mittagspause|mittagessen|\blunch\b)", re.I),
        start=11, end=15,
        activity_words=("mittag", "essen", "lunch", "pause"),
        tense_escape=True,
    ),
    TimeRule(
        label="Guten Abend",
        pattern=re.compile(r"(gu?ten\s+abend\b|good\s+evening\b)", re.I),
        start=16, end=24,
        activity_words=("abend", "couch", "feiern", "unterwegs"),
    ),
)


def _in_window(hour: int, start: int, end: int) -> bool:
    """Liegt die Stunde im Fenster? Ueberlauf ueber Mitternacht erlaubt."""
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def _sentence_around(text: str, pos: int) -> str:
    """Der Satz, in dem der Treffer steht - fuer die Zeitform-Pruefung."""
    start = max((text.rfind(ch, 0, pos) for ch in ".!?\n"), default=-1)
    ends = [p for p in (text.find(ch, pos) for ch in ".!?\n") if p != -1]
    return text[start + 1:min(ends) if ends else len(text)]


def check_time_consistency(text: str, dt: Optional[datetime] = None,
                           activity: Optional[str] = None) -> Optional[str]:
    """Prueft die Antwort auf Widersprueche zu Uhrzeit/Wochentag/Tagesplan.

    Rueckgabe: Beschreibung des Widerspruchs oder None.
    dt/activity sind Overrides fuer Tests; sonst wird beides live ermittelt.
    """
    if not text or not db.get_setting("time_guard_enabled", True):
        return None

    dt = dt or persona_context.now_local()
    if activity is None:
        rules = persona_context.parse_schedule(
            str(db.get_setting("persona_schedule", "") or ""))
        activity = persona_context.current_activity(dt, rules) if rules else None
    activity_lc = (activity or "").lower()

    is_weekend = dt.weekday() >= 5
    for rule in _TIME_RULES:
        match = rule.pattern.search(text)
        if not match:
            continue
        # Vergangenheit/Zukunft im selben Satz -> keine Aussage ueber jetzt
        if rule.tense_escape and _RE_TENSE.search(_sentence_around(text, match.start())):
            continue
        # Der Tagesplan hat Vorrang: passt die Taetigkeit, ist alles in Ordnung
        # (z.B. Nachtschicht-Persona darf um 6 Uhr "gute Nacht" sagen).
        if activity_lc and any(w in activity_lc for w in rule.activity_words):
            continue
        quote = match.group(0).strip()
        if not _in_window(dt.hour, rule.start, rule.end):
            return (f"Zeit-Widerspruch ({rule.label}): „{quote}“ um "
                    f"{dt.strftime('%H:%M')} Uhr")
        if rule.weekday_only and is_weekend:
            return (f"Zeit-Widerspruch ({rule.label}): „{quote}“ am "
                    f"{persona_context.WEEKDAYS_DE[dt.weekday()]}")
        # Bewusst KEIN Flag allein wegen abweichender Plan-Taetigkeit: der Plan
        # dient nur als Freispruch, nie als Ausloeser. Sonst wuerde jede
        # harmlose Formulierung ausserhalb des Plans in der Freigabe landen.
    return None


# --------------------------------------------------- Erfundene Preise/Zusagen
# Den Preis setzt IMMER die Engine und Fanvue zeigt ihn selbst an - die Persona
# hat nie einen Grund, eine Zahl zu nennen. Tut sie es doch, ist es erfunden.
# Genau so entstand "20 € mit meinem persoenlichen Gruss" fuer Content, den es
# gar nicht gibt: ein Versprechen, das niemand einloesen kann.
_RE_PRICE = re.compile(
    r"(?:[€$£]\s?\d{1,4}(?:[.,]\d{1,2})?)"                    # €20, $ 15.99
    r"|(?:\d{1,4}(?:[.,]\d{1,2})?\s?(?:[€$£]|eur\b|usd\b|euros?\b|dollars?\b))",  # 20 €, 15 Euro
    re.I)

# Ankuendigung, gleich Content zu schicken. Ohne tatsaechlichen Anhang ist das
# eine Zusage, die die Nachricht nicht einloest.
#
# Bewusst eng: Es muss entweder ein Content-Wort genannt sein oder das Objekt
# ein blosses Fuerwort ("soll ich es dir schicken?") - dann meint es Content.
# Ein "ich schick dir ein Kuesschen" ist dagegen harmlos und darf nicht in der
# Freigabe landen, sonst ist der Filter mehr im Weg als er nuetzt.
_CONTENT_WORT = (r"(?:video|clip|film|bild|bilder|foto|fotos|pic|pics|picture|photo|"
                 r"content|aufnahme|set|material)")
_RE_SEND_PROMISE = re.compile(
    # "soll ich es/das dir schicken?" - blosses Fuerwort als Objekt
    r"soll\s+ich\s+(?:es|das|ihn|sie)\b[^.?!]{0,24}(?:schicken|senden|zeigen)"
    r"|soll\s+ich\s+(?:dir\s+)?(?:das\s+|ein[en]?\s+)?" + _CONTENT_WORT +
    r"|(?:schicke?|sende|zeige?)\s+(?:ich\s+)?(?:dir\s+)?"
    r"(?:gleich|jetzt|nachher|sp[äa]ter|noch)?\s*(?:ein[en]?\s+|das\s+|die\s+|mein\s+)?" + _CONTENT_WORT +
    r"|(?:shall|should)\s+i\s+send\s+(?:it|you|them)"
    r"|i(?:'ll|\s+will)\s+send\s+(?:it|you|them)",
    re.I)


def finds_invented_price(text: str) -> Optional[str]:
    """Preisangabe im Text? Rueckgabe: die gefundene Stelle."""
    m = _RE_PRICE.search(text or "")
    return m.group(0).strip() if m else None


def finds_unbacked_promise(text: str) -> Optional[str]:
    """Ankuendigung, etwas zu schicken. Nur ohne echten Anhang problematisch."""
    m = _RE_SEND_PROMISE.search(text or "")
    return m.group(0).strip() if m else None


# ------------------------------------------------------- Ausgeplauderter Denkprozess
# Manche Modelle schreiben ihre Ueberlegungen in die Antwort statt ins dafuer
# vorgesehene Feld: "Wait, the fan is writing in German, so I need to reply in
# German only. ... I should match that energy". Das darf NIE zum Fan.
_RE_THINK_BLOCK = re.compile(r"<\s*(think|thinking|reasoning|scratchpad)\s*>.*?"
                             r"<\s*/\s*\1\s*>", re.I | re.S)
_RE_THINK_OPEN = re.compile(r"<\s*(?:think|thinking|reasoning|scratchpad)\s*>", re.I)

# Verraeterisch ist vor allem die DRITTE Person ueber den Fan - die Persona
# spricht ihn immer direkt an und wuerde nie "the fan" sagen.
_REASONING_SIGNALS = (
    re.compile(r"\bthe fan(?:'s)?\s+(?:is|was|wants|writes|is writing|asked|said|described)", re.I),
    re.compile(r"\b(?:der|die)\s+fan\s+(?:schreibt|will|fragt|beschreibt)", re.I),
    re.compile(r"\bi (?:need|should|have|want) to (?:reply|respond|answer|match|keep|acknowledge|write)", re.I),
    re.compile(r"\b(?:my|the) (?:reply|response|answer) should\b", re.I),
    re.compile(r"\bthe (?:current )?context (?:says|is|tells)\b", re.I),
    re.compile(r"\blet me (?:craft|write|think|reply|respond|keep)\b", re.I),
    re.compile(r"\b(?:okay|ok|alright|wait|hmm)[,.]\s+(?:so\s+)?(?:the|i|this)\b", re.I),
    re.compile(r"\bin german only\b|\bnur auf deutsch antworten\b", re.I),
    re.compile(r"\b(?:i'll|i will) (?:keep|make) (?:it|this) (?:short|warm|flirty|playful)", re.I),
)


def strip_think_blocks(text: str) -> str:
    """Entfernt <think>…</think>-Bloecke. Fehlt das schliessende Tag, wird ab
    dem oeffnenden alles verworfen - der Rest ist dann ohnehin Denkprozess."""
    if not text:
        return text
    text = _RE_THINK_BLOCK.sub("", text)
    m = _RE_THINK_OPEN.search(text)
    if m:
        text = text[:m.start()]
    return text.strip()


def looks_like_reasoning(text: str) -> Optional[str]:
    """Sieht der Text nach ausgeplaudertem Denkprozess aus?

    Rueckgabe: die verraeterische Stelle, sonst None. Bewusst mit mehreren
    Signalen und einer Mindestlaenge - eine kurze Flirt-Antwort soll nicht
    faelschlich haengenbleiben, nur weil zufaellig 'I should' vorkommt.
    """
    if not text:
        return None
    treffer = [m.group(0).strip() for m in
               (rx.search(text) for rx in _REASONING_SIGNALS) if m]
    if not treffer:
        return None
    # Ein einzelnes Signal reicht nur bei laengeren Texten (echte Antworten
    # sind kurz); zwei Signale sind immer verdaechtig.
    if len(treffer) >= 2 or len(text) > 400:
        return treffer[0][:80]
    return None


def looks_like_refusal(text: str) -> bool:
    """True, wenn der Text nach einer Modell-Weigerung/Meta-Antwort aussieht."""
    return bool(text and _RE_REFUSAL.search(text))


def has_foreign_script(text: str) -> bool:
    """True, wenn der Text fremde Schriftzeichen (z.B. Chinesisch) enthaelt –
    ein klares Zeichen fuer Sprach-Drift des Modells."""
    return bool(text and _RE_FOREIGN_SCRIPT.search(text))


def looks_like_fake_navigation(text: str) -> bool:
    """True, wenn der Text den Fan zu einer erfundenen Seite/Katalog/Link schickt."""
    return bool(text and _RE_FAKE_NAV.search(text))


def salvage_caption(text: str) -> str | None:
    """Bei einer Weigerung mit Beispiel ('..., etwa: „echte Caption") die eigentliche
    Bildunterschrift aus den Anfuehrungszeichen ziehen. None, wenn nichts Brauchbares."""
    matches = _RE_QUOTED.findall(text or "")
    if not matches:
        return None
    cand = max(matches, key=len).strip()
    if len(cand) >= 10 and not looks_like_refusal(cand):
        return cand
    return None


def _normalize_dashes(text: str) -> str:
    """Ersetzt KI-typische Gedankenstriche (Geviert-/Halbgeviertstrich — – ―) durch
    natuerlichere Zeichen. Normale Bindestriche (-) bleiben unangetastet."""
    if not text:
        return text
    # " Wort — Wort " / "Wort—Wort" -> Komma; am Satz-/Zeilenende einfach entfernen
    text = re.sub(r"\s*[—–―]\s*(?=[\wÄÖÜäöüß])", ", ", text)   # zwischen Woertern -> Komma
    text = re.sub(r"\s*[—–―]\s*", " ", text)                     # Rest (z.B. vor Emoji/Ende) -> Leerzeichen
    # Aufraeumen: doppelte Kommas/Leerzeichen, Leerzeichen vor Satzzeichen
    text = re.sub(r",\s*,+", ",", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    return text


def strip_artifacts(text: str) -> str:
    """Entfernt LLM-Platzhalter/Regieanweisungen wie '[PPV: ... 9.99€]',
    '(Sends a preview image ...)' oder '*schickt ein Bild*' aus dem Text.
    Solche Artefakte entstehen, wenn das Modell das Verkaufen 'erzaehlt',
    statt dass die Engine das Medium tatsaechlich anhaengt."""
    if not text:
        return text
    text = _RE_PPV_TAG.sub("", text)
    text = _RE_STAGE.sub("", text)
    text = _RE_ASTERISK.sub("", text)
    text = _RE_POINTER.sub("", text)
    text = _normalize_dashes(text)
    # Aufraeumen: leere Klammern, doppelte Leerzeichen/Zeilen
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _word_list(setting_key: str) -> list[str]:
    raw = db.get_setting(setting_key, "") or ""
    return [w.strip().lower() for w in raw.split(",") if w.strip()]


def check_outgoing(text: str, has_media: bool = False) -> tuple[str, str | None]:
    """Prueft eine generierte Antwort.

    Rueckgabe: (bereinigter_text, guardrail_note_oder_None).
    Wenn note gesetzt ist, sollte der Draft NICHT automatisch gesendet werden,
    sondern zur manuellen Freigabe.

    has_media: haengt der Nachricht tatsaechlich Content an? Ohne Anhang ist
    eine Ankuendigung wie "soll ich es dir schicken?" eine leere Zusage.
    """
    # Denk-Bloecke zuerst weg - danach bleibt oft eine brauchbare Antwort uebrig
    text = strip_artifacts(strip_think_blocks((text or "").strip()))
    if not text:
        return text, "Leere Antwort vom Modell"

    # Ausgeplauderter Denkprozess: darf NIE zum Fan. Neu generieren lassen.
    denk = looks_like_reasoning(text)
    if denk:
        return text, (f"Denkprozess des Modells statt Antwort („{denk}“) – "
                      f"bitte prüfen (kein Auto-Send)")

    # Modell-Weigerung / Meta-Text: darf NIE zum Fan. Wenn eine echte Beispiel-Caption
    # in Anfuehrungszeichen steckt, diese retten; sonst blockieren (manuelle Freigabe).
    if looks_like_refusal(text):
        salvaged = salvage_caption(text)
        if salvaged:
            text = salvaged
        else:
            return text, "Modell-Weigerung/Meta-Text erkannt – bitte prüfen (kein Auto-Send)"

    # Sprach-Drift: fremde Schriftzeichen (z.B. Chinesisch) -> nie automatisch senden.
    if has_foreign_script(text):
        return text, "Fremdsprachige Schriftzeichen erkannt (Sprach-Drift) – bitte prüfen (kein Auto-Send)"

    # Erfundener Plattform-/Navigationshinweis (Seite/Katalog/Medientyp/Link):
    # Fanvue funktioniert so nicht -> nie automatisch senden, manuell pruefen.
    if looks_like_fake_navigation(text):
        return text, "Erfundener Plattform-/Navigationshinweis erkannt – bitte prüfen (kein Auto-Send)"

    # Erfundener Preis: darf NIE automatisch rausgehen. Den Preis setzt die
    # Engine, Fanvue zeigt ihn selbst an - eine Zahl im Text ist immer erfunden
    # und der Fan koennte die Creatorin darauf festnageln.
    preis = finds_invented_price(text)
    if preis:
        return text, (f"Erfundene Preisangabe „{preis}“ im Text – Preise setzt die "
                      f"Engine, nie das Modell. Bitte prüfen (kein Auto-Send)")

    # Ankuendigung ohne Anhang: "soll ich es dir schicken?" bei einer Nachricht,
    # an der nichts haengt, ist ein Versprechen ins Leere.
    if not has_media:
        zusage = finds_unbacked_promise(text)
        if zusage:
            return text, (f"Kündigt „{zusage}“ an, es hängt aber kein Content an der "
                          f"Nachricht – bitte prüfen (kein Auto-Send)")

    # Zeit-Widerspruch (Uhrzeit/Wochentag/Tagesplan): nicht automatisch senden.
    # Der Text selbst bleibt unveraendert - er ist meist brauchbar und muss nur
    # an einer Stelle korrigiert werden.
    time_note = check_time_consistency(text)
    if time_note:
        return text, f"{time_note} – bitte prüfen (kein Auto-Send)"

    # Laengenlimit
    max_chars = int(db.get_setting("max_reply_chars", 500))
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip() + "…"

    # Verbotene Woerter
    lowered = text.lower()
    for word in _word_list("banned_words"):
        if word in lowered:
            return text, f"Verbotenes Wort erkannt: '{word}'"

    return text, None


def incoming_needs_escalation(text: str) -> str | None:
    """Prueft eine eingehende Fan-Nachricht auf Eskalations-Stichworte.
    Rueckgabe: das getroffene Stichwort oder None."""
    lowered = (text or "").lower()
    for kw in _word_list("escalation_keywords"):
        if kw in lowered:
            return kw
    return None
