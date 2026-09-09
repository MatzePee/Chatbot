"""OpenRouter-Client mit Persona-Prompting, JSON-Zwang und Kostenbremse."""
from __future__ import annotations

import base64
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config import settings
from autoposter.models import LlmUsage, Persona, PersonaExample, Post, PostStatus
from autoposter.services import rhythm
from autoposter.services.appconfig import current as cfg

PLATFORM_RULES = {
    "x": (
        "Plattform X (Twitter). Maximal 280 Zeichen inklusive Hashtags. Kurz, pointiert, "
        "kein Roman, keine Aufzählungen, maximal zwei Zeilenumbrüche."
    ),
    "fanvue": (
        "Plattform Fanvue. Bis 5000 Zeichen, aber 2-5 Sätze wirken am besten. Direkte, "
        "persönliche Ansprache der Abonnenten, ein klarer Call-to-Action ist erlaubt."
    ),
}


# Ohne Steuerung wählt ein Modell bei gleichem Prompt immer denselben Einstieg.
# Ein zufällig gezogener Blickwinkel je Aufruf sorgt für Abwechslung, ohne die
# Persona zu verwässern.
OPENING_ANGLES = [
    "eine Beobachtung aus dem Moment heraus",
    "ein Detail aus dem Bild, das sonst niemand bemerkt",
    "eine Frage an die Leserinnen und Leser",
    "ein kurzer Gedanke, fast beiläufig",
    "eine kleine Selbstironie",
    "eine Sinneswahrnehmung: Geräusch, Licht, Temperatur",
    "ein Satzfragment als Einstieg, dann ein vollständiger Satz",
    "eine Ortsangabe oder Uhrzeit als Einstieg",
    "ein Widerspruch zwischen Plan und Wirklichkeit",
    "etwas, das gerade eben passiert ist",
    "eine leise Vorfreude auf etwas",
    "ein Zitat von sich selbst, halb ernst gemeint",
]


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class LlmResult:
    variants: List[Dict[str, Any]]
    model: str
    cost_usd: float
    raw: Dict[str, Any]


#: Denk-Blöcke hybrider Modelle. Qwen3, DeepSeek-R1 und Verwandte schreiben ihre
#: Überlegungen in solche Marken – landen die im Post, steht dort plötzlich
#: „Okay, the user wants a short caption…". Ein Systemprompt allein verhindert
#: das nicht zuverlässig, deshalb wird hier zusätzlich gefiltert.
_THINK_BLOCK = re.compile(
    r"<\s*(think|thinking|reasoning|analysis)\s*>.*?<\s*/\s*\1\s*>",
    re.S | re.I,
)
#: Abgeschnittener oder unverschlossener Block: alles bis zum Ende verwerfen.
_THINK_OPEN = re.compile(r"<\s*(think|thinking|reasoning|analysis)\s*>.*", re.S | re.I)
#: Manche Modelle (u. a. Kimi) benutzen Sonderzeichen statt spitzer Klammern.
#: Erst das Paar samt Inhalt entfernen …
_THINK_UNI_BLOCK = re.compile(
    r"[◁◀]\s*think\s*[▷▶].*?[◁◀]\s*/\s*think\s*[▷▶]", re.S | re.I
)
#: … dann ein offen gebliebenes Anfangszeichen samt allem, was folgt …
_THINK_UNI_OPEN = re.compile(r"[◁◀]\s*think\s*[▷▶].*", re.S | re.I)
#: … und zuletzt vereinzelt übrig gebliebene Marken.
_THINK_UNI_MARK = re.compile(r"[◁◀]\s*/?\s*think\s*[▷▶]", re.I)


#: Wendungen, mit denen ein Modell ÜBER die Aufgabe redet, statt sie zu lösen.
#: Sally schreibt „Rain the whole way home." – nicht „We need to generate 3 posts".
_META_OPENERS = re.compile(
    r"^\s*(?:okay|ok|alright|sure|got it|hmm|let me|let's|first|so,|now,|right,"
    r"|we need to|we should|i need to|i should|i'll|i will|the user|the post"
    r"|the goal|the task|here (?:is|are)|assistant|als erstes|zunächst|ich soll"
    r"|der nutzer|wir brauchen|zuerst)\b",
    re.I,
)
#: Formulierungen, die in einem Post nichts zu suchen haben, egal wo sie stehen.
_META_PHRASES = re.compile(
    r"\b(in character as|the user wants|word count|character limit|max_chars"
    r"|json|variant\s*\d|first person|per the instructions|as instructed"
    r"|laut anweisung|in der ich-form)\b",
    re.I,
)


def looks_like_reasoning(text: str, max_chars: int) -> bool:
    """Redet der Text über die Aufgabe, statt der Post zu sein?

    Nötig, weil Modelle ohne Denk-Marken einfach in Prosa losüberlegen. Das
    fällt durch jeden Marken-Filter und landete bisher als 3000-Zeichen-Post im
    Kalender. Drei Anzeichen, jedes für sich schon aussagekräftig:
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    # 1. Ein Text, der das Limit um ein Vielfaches reißt, war nie als Post gemeint.
    if len(stripped) > max(max_chars * 2, 600):
        return True
    # 2. Spricht ausdrücklich über Aufgabe, Format oder Vorgaben.
    if _META_PHRASES.search(stripped):
        return True
    # 3. Denk-Eröffnung – aber NUR zusammen mit Überlänge. „Okay so the library
    #    closes at 22:00 now" ist ein völlig normaler Post, und „First coffee of
    #    the day" auch. Das Eröffnungswort allein sagt nichts; erst wenn danach
    #    mehr kommt, als in einen Post passt, redet das Modell über die Aufgabe.
    return bool(_META_OPENERS.search(stripped)) and len(stripped) > max_chars


#: Reste des Antwortschemas, die manche Modelle wörtlich zurückgeben.
_PLACEHOLDERS = {
    "...", "…", "text", "tag", "hier_der_fertige_post", "hier_ein_schlagwort",
    "your text here", "post text", "string",
}


def is_placeholder(text: str) -> bool:
    """Hat das Modell das Beispiel aus dem Prompt abgeschrieben?"""
    bare = (text or "").strip().strip("\"'<>[]{}").lower()
    return not bare or bare in _PLACEHOLDERS


def strip_reasoning(text: str) -> str:
    """Denk-Blöcke entfernen und das Ergebnis aufräumen."""
    if not text:
        return ""
    cleaned = _THINK_BLOCK.sub(" ", text)
    cleaned = _THINK_UNI_BLOCK.sub(" ", cleaned)
    cleaned = _THINK_UNI_OPEN.sub(" ", cleaned)
    cleaned = _THINK_UNI_MARK.sub(" ", cleaned)
    # Ein offener Block ohne Schluss heißt: Das Modell hat mitten im Denken
    # aufgehört. Was danach kommt, ist kein fertiger Text.
    cleaned = _THINK_OPEN.sub(" ", cleaned)
    return cleaned.strip()


def content_of(payload: Dict[str, Any]) -> str:
    """Den Text aus einer OpenRouter-Antwort holen — auch wenn keiner da ist.

    `message.content` ist **nullable**. Das passiert regelmäßig und nicht nur
    im Fehlerfall:

    * Denkende Modelle legen ihre Ausgabe in `reasoning` ab und lassen
      `content` leer, wenn das Token-Budget schon beim Denken aufgebraucht war.
    * Bei einer Inhaltssperre kommt eine Antwort ohne Text zurück.
    * Ein Abbruch mit `finish_reason: length` liefert ebenfalls null.

    Vorher stand hier `payload[...]["content"]` und direkt danach `.strip()` —
    entsprechend endete jeder dieser Fälle in
    „'NoneType' object has no attribute 'strip'", einer Meldung, die über die
    Ursache nichts verrät.
    """
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return ""
    for key in ("content", "reasoning", "reasoning_content"):
        value = message.get(key)
        # Manche Anbieter liefern content als Liste von Blöcken.
        if isinstance(value, list):
            value = " ".join(
                str(part.get("text", ""))
                for part in value
                if isinstance(part, dict)
            )
        if isinstance(value, str):
            cleaned = strip_reasoning(value)
            if cleaned:
                return cleaned
    return ""


def empty_answer_reason(payload: Dict[str, Any]) -> str:
    """Erklärt in einem Satz, warum keine Ausgabe kam."""
    try:
        finish = str(payload["choices"][0].get("finish_reason") or "")
    except (KeyError, IndexError, TypeError):
        finish = ""
    model = str(payload.get("_model") or "das Modell")
    hints = {
        "length": (
            "Das Token-Budget war aufgebraucht, bevor Text entstand. Bei "
            "denkenden Modellen geht es fürs Denken drauf — ein einfacheres "
            "Modell unter Einstellungen → Modelle hilft."
        ),
        "content_filter": "Die Anfrage wurde von einer Inhaltssperre abgewiesen.",
        "error": "Der Anbieter meldete einen Fehler.",
    }
    tail = hints.get(finish, "")
    return (
        f"{model} hat keinen Text geliefert"
        + (f" (finish_reason: {finish})" if finish else "")
        + (". " + tail if tail else ".")
    )


def _extract_json(text: Optional[str]) -> Dict[str, Any]:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


async def monthly_cost(db: AsyncSession) -> float:
    start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    value = (
        await db.execute(
            select(func.coalesce(func.sum(LlmUsage.cost_usd), 0)).where(LlmUsage.created_at >= start)
        )
    ).scalar_one()
    return float(value or 0)


async def _recent_texts(db: AsyncSession, channel_id, limit: int = 30) -> List[str]:
    """Texte, gegen die sich ein neuer Post abgrenzen muss.

    Bewusst nicht nur veröffentlichte: Beim Planen eines ganzen Monats ist noch
    nichts veröffentlicht. Ohne die geplanten Texte bekäme jeder Aufruf denselben
    Prompt – und dasselbe Modell liefert dann brav immer denselben Satzanfang.
    """
    rows = (
        await db.execute(
            select(Post.body_text)
            .where(
                Post.channel_id == channel_id,
                Post.body_text.isnot(None),
                Post.body_text != "",
                Post.status.notin_([PostStatus.cancelled.value, PostStatus.skipped.value]),
            )
            .order_by(func.coalesce(Post.published_at, Post.scheduled_at).desc())
            .limit(limit)
        )
    ).scalars().all()
    return [t for t in rows if t]


def _openers(texts: Sequence[str], words: int = 3) -> List[str]:
    """Die ersten Wörter je Text – daran hängt die gefühlte Wiederholung."""
    seen: List[str] = []
    for text in texts:
        opener = " ".join(text.split()[:words]).strip(" ,.:;–-")
        if opener and opener.lower() not in {s.lower() for s in seen}:
            seen.append(opener)
    return seen


async def _few_shots(
    db: AsyncSession,
    persona_id,
    platform: str,
    limit: int = 8,
    *,
    with_image: Optional[bool] = None,
) -> List[str]:
    """Beispielposts als Stilvorlage.

    Beispiele mit hinterlegter Bildbeschreibung gehören zu Bildposts, solche
    ohne zu reinen Textposts – eine Frage an die Community sieht anders aus als
    eine Bildunterschrift. Passen keine, werden alle genommen; eine Stilvorlage
    ist immer noch besser als keine.
    """
    stmt = select(PersonaExample).where(
        PersonaExample.persona_id == persona_id,
        PersonaExample.platform == platform,
        PersonaExample.rating >= 0,
    )
    rows = (
        await db.execute(stmt.order_by(PersonaExample.rating.desc(), PersonaExample.created_at.desc()))
    ).scalars().all()
    if not rows:
        return []

    if with_image is not None:
        fitting = [r for r in rows if bool((r.image_description or "").strip()) == with_image]
        if fitting:
            rows = fitting

    # Aus dem passenden Vorrat zufällig ziehen: sonst bekommt jeder Post
    # dieselben fünf Beispiele und damit dieselbe Schablone.
    pool = list(rows)
    if len(pool) > limit:
        pool = random.sample(pool, limit)
    return [r.text for r in pool]


def build_system_prompt(persona: Optional[Persona], platform: str) -> str:
    if not persona:
        return (
            "Du schreibst Social-Media-Texte. " + PLATFORM_RULES.get(platform, "")
        )
    emoji = {
        "none": "Verwende keine Emojis.",
        "sparse": "Höchstens ein Emoji, oft gar keins.",
        "liberal": "Emojis sind willkommen, aber nicht mehr als drei.",
    }.get(persona.emoji_policy, "Höchstens ein Emoji.")

    parts = [
        f"Du schreibst als '{persona.name}' in der Ich-Form.",
        persona.bio.strip(),
        f"Tonalität: {persona.tone_guidelines.strip()}" if persona.tone_guidelines else "",
        f"Sprache der Ausgabe: {persona.language}.",
        emoji,
        PLATFORM_RULES.get(platform, ""),
        (
            "Verbotene Themen (niemals erwähnen): "
            + ", ".join(persona.forbidden_topics)
            if persona.forbidden_topics
            else ""
        ),
        persona.system_prompt.strip(),
        "Schreibe niemals über dich als KI. Keine Anführungszeichen um den Text. "
        "Keine Erklärungen, nur der fertige Post-Text.",
    ]
    return "\n\n".join(p for p in parts if p)


# --------------------------------------------------------------------------- #
# Modellkatalog und Verbindungstest
# --------------------------------------------------------------------------- #
_models_cache: Dict[str, Any] = {"fetched_at": 0.0, "models": []}
MODELS_TTL = 3600.0


async def list_models(db: AsyncSession, *, refresh: bool = False) -> Dict[str, Any]:
    """Modelle bei OpenRouter abfragen. Ohne Schlüssel geht es auch – der
    Katalog ist öffentlich –, dann fehlen nur die persönlichen Preise."""
    import time

    if not refresh and _models_cache["models"] and time.time() - _models_cache["fetched_at"] < MODELS_TTL:
        return {"models": _models_cache["models"], "cached": True}

    base = settings.openrouter_base_url.rstrip("/")
    headers = {}
    key = cfg("openrouter_api_key")
    if key:
        headers["Authorization"] = "Bearer {}".format(key)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.get(f"{base}/models", headers=headers)
            if resp.status_code >= 400:
                return {"models": [], "error": f"OpenRouter {resp.status_code}: {resp.text[:200]}"}
            data = resp.json().get("data", [])
    except httpx.HTTPError as exc:
        return {"models": [], "error": f"OpenRouter nicht erreichbar: {exc}"}

    models = []
    for entry in data:
        pricing = entry.get("pricing") or {}
        models.append(
            {
                "id": entry.get("id"),
                "name": entry.get("name") or entry.get("id"),
                "context_length": entry.get("context_length"),
                "prompt_price": pricing.get("prompt"),
                "completion_price": pricing.get("completion"),
                # Grobe Einordnung, damit die Auswahl im UI filterbar bleibt.
                "vision": "image" in str(
                    (entry.get("architecture") or {}).get("input_modalities", "")
                ).lower()
                or "vision" in str(entry.get("id", "")).lower(),
                "free": str(pricing.get("prompt", "1")) in ("0", "0.0", "-1"),
            }
        )
    models.sort(key=lambda m: str(m["id"]))
    _models_cache["models"] = models
    _models_cache["fetched_at"] = time.time()
    return {"models": models, "cached": False}


async def test_connection(db: AsyncSession, *, model: Optional[str] = None) -> Dict[str, Any]:
    """Kurzer Live-Test: Schlüssel gültig? Modell antwortet?"""
    key = cfg("openrouter_api_key")
    if not key:
        return {"ok": False, "message": "Kein API-Schlüssel hinterlegt."}

    chosen = model or cfg("openrouter_model_text")
    base = settings.openrouter_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            resp = await client.post(
                f"{base}/chat/completions",
                headers={
                    "Authorization": "Bearer {}".format(key),
                    "HTTP-Referer": cfg("public_base_url"),
                    "X-Title": "AutoPoster",
                    "Content-Type": "application/json",
                },
                content=json.dumps(
                    {
                        "model": chosen,
                        "messages": [{"role": "user", "content": "Antworte nur mit: OK"}],
                        "max_tokens": 5,
                        "temperature": 0,
                    }
                ),
            )
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Netzwerkfehler: {exc}"}

    if resp.status_code == 401:
        return {"ok": False, "message": "Schlüssel wurde abgelehnt (401)."}
    if resp.status_code >= 400:
        return {"ok": False, "message": f"OpenRouter {resp.status_code}: {resp.text[:200]}"}

    payload = resp.json()
    answer = ""
    try:
        answer = content_of(payload)[:60]
    except (KeyError, IndexError, TypeError):
        pass
    return {
        "ok": True,
        "model": chosen,
        "message": f"Verbindung steht. Antwort: {answer or '(leer)'}",
        "cost_usd": float((payload.get("usage") or {}).get("total_cost", 0) or 0),
    }


class LlmClient:
    def __init__(self) -> None:
        self.base = settings.openrouter_base_url.rstrip("/")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": "Bearer {}".format(cfg("openrouter_api_key")),
            "HTTP-Referer": cfg("public_base_url"),
            "X-Title": "AutoPoster",
            "Content-Type": "application/json",
        }

    async def _call(
        self,
        db: AsyncSession,
        *,
        task: str,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.9,
        max_tokens: int = 900,
        json_mode: bool = True,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
        no_reasoning: bool = False,
    ) -> Dict[str, Any]:
        if not cfg("openrouter_api_key"):
            raise RuntimeError("OPENROUTER_API_KEY ist nicht gesetzt")
        if await monthly_cost(db) >= cfg("openrouter_monthly_budget_usd"):
            raise BudgetExceeded(
                "Monatsbudget von {} USD ausgeschöpft".format(cfg("openrouter_monthly_budget_usd"))
            )

        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Strafterme gegen Wortwiederholungen. Nicht jedes Modell kennt sie,
        # OpenRouter reicht sie nur an die Modelle weiter, die sie unterstützen.
        if frequency_penalty:
            body["frequency_penalty"] = frequency_penalty
        if presence_penalty:
            body["presence_penalty"] = presence_penalty
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        # Denkmodelle verbrauchen ihr Token-Budget fürs Nachdenken und liefern
        # dann keinen Text mehr. Für einen Zweizeiler ist das nicht nur teuer,
        # sondern der Grund, warum Posts leer blieben. „effort: none" schaltet
        # das Denken ab, „exclude" hält die Denkspur aus der Antwort heraus.
        if no_reasoning:
            body["reasoning"] = {"effort": "none", "exclude": True}

        models_to_try = [model, cfg("openrouter_model_fallback")]
        last_error: Optional[Exception] = None

        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.llm_timeout_seconds)) as client:
            for candidate in models_to_try:
                body["model"] = candidate
                try:
                    resp = await client.post(
                        f"{self.base}/chat/completions",
                        headers=self._headers(),
                        content=json.dumps(body),
                    )
                    # Bei manchen Modellen ist das Denken Pflicht; die lehnen
                    # „effort: none" mit 400 ab. Dann ohne den Wunsch erneut
                    # fragen, statt den Kanal ganz ausfallen zu lassen.
                    if (
                        resp.status_code == 400
                        and "reasoning" in body
                        and re.search(r"reason|effort|think", resp.text, re.I)
                    ):
                        logger.info(
                            "%s verlangt Denkmodus – zweiter Versuch ohne Abschaltung",
                            candidate,
                        )
                        retry_body = {k: v for k, v in body.items() if k != "reasoning"}
                        resp = await client.post(
                            f"{self.base}/chat/completions",
                            headers=self._headers(),
                            content=json.dumps(retry_body),
                        )
                    if resp.status_code >= 400:
                        last_error = RuntimeError(
                            f"OpenRouter {resp.status_code}: {resp.text[:300]}"
                        )
                        continue
                    payload = resp.json()
                except httpx.HTTPError as exc:  # pragma: no cover
                    last_error = exc
                    continue

                usage = payload.get("usage", {}) or {}
                cost = float(usage.get("total_cost", 0) or 0)
                db.add(
                    LlmUsage(
                        model=candidate,
                        task=task,
                        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                        cost_usd=cost,
                    )
                )
                await db.flush()
                payload["_model"] = candidate
                payload["_cost"] = cost
                return payload

        raise RuntimeError(f"OpenRouter nicht erreichbar: {last_error}")

    # ------------------------------------------------------------------ #
    async def describe_image(self, db: AsyncSession, image_bytes: bytes, mime: str) -> Dict[str, Any]:
        data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
        payload = await self._call(
            db,
            task="vision_describe",
            model=cfg("openrouter_model_vision"),
            temperature=0.2,
            max_tokens=400,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Beschreibe das Bild sachlich für eine Redaktionsdatenbank. "
                        'Antworte als JSON: {"description": "...", "alt_text": "...", '
                        '"tags": ["..."], "nsfw_level": "sfw|suggestive|explicit"}'
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Beschreibe dieses Bild."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        )
        content = content_of(payload)
        if not content:
            raise RuntimeError(empty_answer_reason(payload))
        try:
            return _extract_json(content)
        except json.JSONDecodeError:
            return {"description": content[:500], "alt_text": content[:300], "tags": []}

    async def generate_post_text(
        self,
        db: AsyncSession,
        *,
        persona: Optional[Persona],
        platform: str,
        channel_id,
        image_descriptions: Sequence[str],
        theme: str = "",
        instruction: str = "",
        variants: int = 3,
        max_chars: int = 280,
        when_local: Optional[datetime] = None,
        has_image: Optional[bool] = None,
    ) -> LlmResult:
        recent = await _recent_texts(db, channel_id)
        # Beispielposts passend zur Postart: Fragen an die Community sehen anders
        # aus als Bildunterschriften.
        shots = (
            await _few_shots(db, persona.id, platform, with_image=bool(image_descriptions))
            if persona
            else []
        )

        user_parts = [f"Erzeuge {variants} unterschiedliche Varianten für einen Post."]
        # Der Tagesrhythmus steht bewusst weit oben: Er ist die härteste
        # Randbedingung. Ein Post um 03:00 aus dem Gym ist sofort unglaubwürdig.
        if persona and persona.daily_rhythm and when_local:
            fragment = rhythm.prompt_fragment(persona.daily_rhythm, when_local)
            if fragment:
                user_parts.append(fragment)
        if has_image is None:
            has_image = bool(image_descriptions)

        if image_descriptions:
            user_parts.append(
                "Zu diesem Post gehört ein Bild. Das ist darauf zu sehen:\n"
                + "\n".join(f"- {d}" for d in image_descriptions if d)
                + "\n\nDer Text muss erkennbar zu diesem Bild gehören: gleicher Ort, gleiche "
                "Situation, gleiche Tageszeit. Erfinde keine andere Umgebung und keine andere "
                "Tätigkeit. Beschreibe das Bild aber nicht Wort für Wort – kommentiere den "
                "Moment, als hättest du ihn gerade selbst erlebt."
            )
        elif has_image:
            # Beschreibung fehlt (kein Schlüssel, Modellfehler). Lieber ein
            # zeitloser Text als eine erfundene Szene, die zum Bild nicht passt.
            user_parts.append(
                "Zu diesem Post gehört ein Bild, dessen Inhalt hier nicht vorliegt. "
                "Schreibe deshalb bewusst offen: nichts über Ort, Kleidung, Umgebung oder "
                "Tätigkeit behaupten, was das Bild widerlegen könnte."
            )
        else:
            user_parts.append("Es gibt kein Bild. Schreibe einen reinen Textpost.")
        if theme:
            user_parts.append(f"Thema des Tages: {theme}")
        if instruction:
            user_parts.append(f"Zusätzliche Anweisung: {instruction}")
        if persona and persona.hashtag_pool:
            user_parts.append(
                "Mögliche Hashtags (maximal 3 auswählen, passend): "
                + ", ".join(persona.hashtag_pool)
            )
        if shots:
            user_parts.append(
                "So klingen Posts dieser Person. Übernimm Aufbau, Länge, Satzmelodie und "
                "die Art des Schlusses – aber niemals den Inhalt:\n"
                + "\n".join(f"- {s}" for s in shots)
            )
        if recent:
            user_parts.append(
                "Diese Texte stehen für diesen Kanal bereits geschrieben oder veröffentlicht. "
                "Wiederhole weder Formulierungen noch Satzanfänge:\n"
                + "\n".join(f"- {t[:160]}" for t in recent)
            )
            openers = _openers(recent)
            if openers:
                user_parts.append(
                    "Diese Satzanfänge sind verbraucht. Beginne mit keinem davon und auch "
                    "mit keiner Abwandlung:\n" + "\n".join(f"- {o} …" for o in openers[:15])
                )
        # Ohne Vorgeschichte greift jedes Modell zu seiner Lieblingswendung.
        # Ein zufälliger Einstiegswinkel bricht das auf.
        user_parts.append(
            "Wähle für jede Variante einen anderen Einstieg. Möglicher Blickwinkel für "
            f"diesen Post: {random.choice(OPENING_ANGLES)}"
        )
        # Das Beispiel enthielt früher wörtlich "..." und "tag" als Werte. Manche
        # Modelle geben genau das zurück – und ein Post mit dem Text "..." und
        # dem Hashtag "tag" ist von einem echten Ergebnis nicht zu unterscheiden.
        # Deshalb benannte Platzhalter plus ausdrückliches Verbot.
        user_parts.append(
            f"Jede Variante darf höchstens {max_chars} Zeichen lang sein (inkl. Hashtags).\n"
            "Antworte mit NICHTS außer diesem JSON:\n"
            '{"variants":[{"text":"HIER_DER_FERTIGE_POST","hashtags":["HIER_EIN_SCHLAGWORT"]}]}\n'
            "Die Großbuchstaben-Wörter sind Platzhalter. Ersetze sie durch echten "
            "Inhalt und gib sie niemals unverändert zurück. Kein Vorwort, keine "
            "Überlegungen, keine Erklärung – nur das JSON."
        )

        # Aufgabe und Modell aus DERSELBEN Bedingung ableiten. Vorher stand in
        # der Modellzeile fest `openrouter_model_caption` – ein reiner Textpost
        # wurde also mit dem Bildunterschriften-Modell erzeugt, und die
        # Einstellung „Reine Textposts" blieb wirkungslos. Zwei Ausdrücke, die
        # dasselbe entscheiden sollen, laufen früher oder später auseinander.
        with_image = bool(image_descriptions)
        task = "caption" if with_image else "text_post"
        chosen_model = cfg(
            "openrouter_model_caption" if with_image else "openrouter_model_text"
        )
        if persona and persona.model_override:
            chosen_model = persona.model_override

        payload = await self._call(
            db,
            task=task,
            model=chosen_model,
            temperature=persona.temperature if persona else 0.9,
            frequency_penalty=0.5,
            presence_penalty=0.4,
            # Ein Zweizeiler braucht kein Nachdenken – und mit Denkmodus bleibt
            # vom Budget nichts für den Text übrig.
            no_reasoning=True,
            max_tokens=1600,
            messages=[
                {"role": "system", "content": build_system_prompt(persona, platform)},
                {"role": "user", "content": "\n\n".join(user_parts)},
            ],
        )

        content = content_of(payload)
        if not content:
            # Klar scheitern statt einen leeren Post anzulegen: Der Planer fängt
            # das ab, schreibt die Begründung an den Post und lässt ihn zur
            # Prüfung liegen.
            raise RuntimeError(empty_answer_reason(payload))
        try:
            parsed = _extract_json(content)
            raw_variants = parsed.get("variants") or []
        except json.JSONDecodeError:
            # Kein JSON. Früher wurde hier die GESAMTE Antwort zum Postext –
            # deshalb stand einmal die halbe Überlegung des Modells im Kalender.
            # Als Post durchgehen lassen wir sie nur, wenn sie auch wie einer
            # aussieht.
            fallback = content.strip()
            if looks_like_reasoning(fallback, max_chars):
                raise RuntimeError(
                    "Das Modell hat statt eines Posts seine Überlegungen "
                    f"geliefert ({len(fallback)} Zeichen, erlaubt sind {max_chars}). "
                    "Ein Modell ohne Denkmodus liefert hier verlässlichere "
                    "Ergebnisse — einzustellen unter Einstellungen → Modelle."
                )
            raw_variants = [{"text": fallback, "hashtags": []}]

        forbidden = [t.lower() for t in (persona.forbidden_topics if persona else [])]
        clean: List[Dict[str, Any]] = []
        for item in raw_variants:
            # Auch im JSON-Feld kann ein Denk-Block stecken.
            text = strip_reasoning(str(item.get("text", "")))
            # Abgeschriebene Platzhalter und Überlegungen fliegen raus, statt
            # als Post im Kalender zu landen.
            if not text or is_placeholder(text) or looks_like_reasoning(text, max_chars):
                continue
            tags = [
                str(t).lstrip("#")
                for t in (item.get("hashtags") or [])
                if not is_placeholder(str(t))
            ][:3]
            full_length = len(text) + (len(" ".join(f"#{t}" for t in tags)) + 2 if tags else 0)
            issues = []
            if full_length > max_chars:
                issues.append("zu lang")
            if any(word in text.lower() for word in forbidden if word):
                issues.append("verbotener Begriff")
            if any(similarity(text, old) > 0.85 for old in recent):
                issues.append("zu ähnlich zu einem früheren Post")
            opener = " ".join(text.split()[:3]).strip(" ,.:;–-").lower()
            if opener and opener in {o.lower() for o in _openers(recent)}:
                issues.append("gleicher Satzanfang wie ein anderer Post")
            clean.append({"text": text, "hashtags": tags, "issues": issues})

        clean.sort(key=lambda v: len(v["issues"]))
        return LlmResult(
            variants=clean or [{"text": "", "hashtags": [], "issues": ["leere Antwort"]}],
            model=payload.get("_model", ""),
            cost_usd=float(payload.get("_cost", 0)),
            raw={"finish_reason": payload["choices"][0].get("finish_reason")},
        )

    async def generate_alt_text(
        self, db: AsyncSession, description: str
    ) -> str:
        payload = await self._call(
            db,
            task="alt_text",
            model=cfg("openrouter_model_text"),
            temperature=0.3,
            max_tokens=200,
            messages=[
                {
                    "role": "system",
                    "content": 'Erzeuge einen sachlichen Alt-Text (max. 200 Zeichen). JSON: {"alt_text":"..."}',
                },
                {"role": "user", "content": description},
            ],
        )
        content = content_of(payload)
        try:
            return str(_extract_json(content).get("alt_text", ""))[:1000]
        except json.JSONDecodeError:
            return content.strip()[:1000]


llm = LlmClient()
