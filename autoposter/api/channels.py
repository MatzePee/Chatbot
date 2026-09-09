"""Kanäle, Personas und OAuth-Verbindung."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from slugify import slugify
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.adapters import AdapterError, get_adapter
from autoposter.adapters.base import OAuthApp
from autoposter.db import get_db
from autoposter.deps import current_user, require_admin, require_editor
from autoposter.models import (
    Channel,
    ChannelCredential,
    ChannelHealth,
    ContentPlan,
    ExternalMediaRef,
    MediaAsset,
    MediaAssignment,
    MediaChannelUsage,
    MediaStatus,
    OAuthState,
    Persona,
    PersonaExample,
    Post,
    PostStatus,
    PostingPolicy,
    User,
)
from autoposter.schemas import (
    ChannelCreate,
    ChannelOut,
    ChannelUpdate,
    PersonaCreate,
    PersonaExampleBulk,
    PersonaExampleOut,
    PersonaOut,
    PersonaUpdate,
    PolicyOut,
    RhythmCheckRequest,
    RhythmCheckResult,
)
from autoposter.services import credentials as cred_service
from autoposter.services import library, lifecycle
from autoposter.services import media as media_service
from autoposter.services import watermark
from autoposter.services import rhythm
from autoposter.services import oauthapp

router = APIRouter(tags=["channels"])


# --------------------------------------------------------------------------- #
# Personas
# --------------------------------------------------------------------------- #
@router.get("/personas", response_model=List[PersonaOut])
async def list_personas(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> List[PersonaOut]:
    rows = (await db.execute(select(Persona).order_by(Persona.name))).scalars().all()
    return [PersonaOut.model_validate(r) for r in rows]


@router.post("/personas", response_model=PersonaOut, status_code=201)
async def create_persona(
    payload: PersonaCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> PersonaOut:
    persona = Persona(**payload.model_dump(), slug=slugify(payload.name))
    db.add(persona)
    await db.flush()
    return PersonaOut.model_validate(persona)


@router.patch("/personas/{persona_id}", response_model=PersonaOut)
async def update_persona(
    persona_id: uuid.UUID,
    payload: PersonaUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> PersonaOut:
    persona = (
        await db.execute(select(Persona).where(Persona.id == persona_id))
    ).scalar_one_or_none()
    if not persona:
        raise HTTPException(404, "Persona nicht gefunden")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(persona, key, value)
    if payload.name:
        persona.slug = slugify(payload.name)
    db.add(persona)
    return PersonaOut.model_validate(persona)


@router.get("/personas/{persona_id}/examples", response_model=List[PersonaExampleOut])
async def list_examples(
    persona_id: uuid.UUID,
    platform: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> List[PersonaExampleOut]:
    stmt = select(PersonaExample).where(PersonaExample.persona_id == persona_id)
    if platform:
        stmt = stmt.where(PersonaExample.platform == platform)
    rows = (
        await db.execute(stmt.order_by(PersonaExample.created_at.desc()))
    ).scalars().all()
    return [PersonaExampleOut.model_validate(r) for r in rows]


@router.post("/personas/{persona_id}/examples", response_model=List[PersonaExampleOut], status_code=201)
async def add_examples(
    persona_id: uuid.UUID,
    payload: PersonaExampleBulk,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> List[PersonaExampleOut]:
    """Beispiele anlegen – eine Zeile je Beispiel.

    Zwanzig Beispiele einzeln einzutippen wäre Arbeit ohne Gegenwert, deshalb
    nimmt der Endpunkt einen ganzen Block entgegen. Führende Nummerierungen
    ("1. ", "- ") werden entfernt, Dubletten übersprungen.
    """
    persona = (
        await db.execute(select(Persona).where(Persona.id == persona_id))
    ).scalar_one_or_none()
    if not persona:
        raise HTTPException(404, "Persona nicht gefunden")

    existing = {
        row.text.strip()
        for row in (
            await db.execute(
                select(PersonaExample).where(
                    PersonaExample.persona_id == persona_id,
                    PersonaExample.platform == payload.platform,
                )
            )
        ).scalars().all()
    }

    created: List[PersonaExample] = []
    for raw in payload.text.splitlines():
        line = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", raw).strip()
        if not line or line.startswith("#") or line in existing:
            continue
        existing.add(line)
        example = PersonaExample(
            persona_id=persona_id,
            platform=payload.platform,
            image_description=payload.image_description,
            text=line[:2000],
            rating=payload.rating,
        )
        db.add(example)
        created.append(example)

    await db.flush()
    return [PersonaExampleOut.model_validate(e) for e in created]


@router.delete("/personas/examples/{example_id}", status_code=204, response_model=None)
async def delete_example(
    example_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> None:
    example = (
        await db.execute(select(PersonaExample).where(PersonaExample.id == example_id))
    ).scalar_one_or_none()
    if example:
        await db.delete(example)


@router.post("/personas/rhythm/check", response_model=RhythmCheckResult)
async def check_rhythm(
    payload: RhythmCheckRequest, _: User = Depends(current_user)
) -> RhythmCheckResult:
    """Tagesrhythmus prüfen, ohne ihn zu speichern.

    Liefert die verstandenen Regeln, fehlerhafte Zeilen mit Zeilennummer, alle
    nicht abgedeckten Zeiträume und ein Wochenraster für die Vorschau.
    """
    parsed = rhythm.parse(payload.text)
    return RhythmCheckResult(
        rules=[
            {
                "line": rule.line_no,
                "label": rule.label(),
                "days": sorted(rule.days),
                "from": rule.start.strftime("%H:%M"),
                "to": rule.end.strftime("%H:%M"),
                "minutes": rule.length_minutes,
                "crosses_midnight": rule.crosses_midnight,
                "activity": rule.activity,
            }
            for rule in parsed.rules
        ],
        errors=parsed.errors,
        gaps=rhythm.gaps(parsed.rules),
        grid=rhythm.grid(parsed.rules),
        ok=parsed.ok,
    )


@router.get("/personas/rhythm/template")
async def rhythm_template(_: User = Depends(current_user)) -> Dict[str, str]:
    """Ein sofort benutzbarer Beispielplan für den leeren Editor."""
    return {"text": rhythm.EXAMPLE_PLAN}


# --------------------------------------------------------------------------- #
# Kanäle
# --------------------------------------------------------------------------- #
async def _to_out(db: AsyncSession, channel: Channel) -> ChannelOut:
    out = ChannelOut.model_validate(channel)
    out.policy = PolicyOut.model_validate(channel.policy) if channel.policy else None
    out.token_expires_at = await cred_service.token_expiry(db, channel)
    out.is_connected = cred_service.is_connected(channel)
    out.connection_source = "chatbot" if channel.platform == "fanvue" else "channel"
    if channel.platform == 'fanvue':
        from autoposter.services.chatbot_fanvue import health
        out.health = health(channel)
        out.health_note = 'Verwendet die Fanvue-Verbindung des AutoChat.' if out.is_connected else 'Bitte AutoChat-Verbindung und Kanal-Handle prüfen.' 
    app = oauthapp.for_channel(channel)
    out.uses_own_app = oauthapp.uses_own_app(channel)
    out.oauth_client_id = channel.oauth_client_id
    out.oauth_app_name = channel.oauth_app_name
    out.has_client_secret = bool(channel.oauth_client_secret_enc)
    out.app_configured = app.is_configured and bool(app.client_secret or channel.platform == "x")
    out.redirect_uri = app.redirect_uri(channel.platform)
    if channel.platform == 'fanvue':
        out.oauth_client_id = ''
        out.oauth_app_name = 'AutoChat'
        out.has_client_secret = bool(app.client_secret)
    out.last_verified_at = channel.last_verified_at
    out.verified_account = channel.verified_account
    out.has_watermark = watermark.path_for(channel.id).exists()
    # Zeitstempel im Link: sonst zeigt der Browser nach dem Austausch noch das
    # alte Zeichen aus seinem Zwischenspeicher.
    out.watermark_url = (
        f"/api/v1/channels/{channel.id}/watermark?v={int(channel.updated_at.timestamp())}"
        if out.has_watermark
        else ""
    )
    return out


@router.get("/channels", response_model=List[ChannelOut])
async def list_channels(
    include_inactive: bool = True,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> List[ChannelOut]:
    stmt = select(Channel).order_by(Channel.platform, Channel.display_name)
    if not include_inactive:
        stmt = stmt.where(Channel.is_active.is_(True))
    rows = (await db.execute(stmt)).scalars().all()
    return [await _to_out(db, c) for c in rows]


@router.post("/channels", response_model=ChannelOut, status_code=201)
async def create_channel(
    payload: ChannelCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> ChannelOut:
    if payload.platform not in ("x", "fanvue"):
        raise HTTPException(400, "Unbekannte Plattform")
    try:
        redirect_base = oauthapp.normalize_redirect_base(payload.oauth_redirect_base, payload.platform)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    policy = PostingPolicy(**payload.policy.model_dump())
    if payload.platform == "x":
        adapter = get_adapter("x")
        policy.daily_api_quota = min(policy.daily_api_quota, adapter.default_daily_quota())
    db.add(policy)
    await db.flush()

    channel = Channel(
        platform=payload.platform,
        display_name=payload.display_name,
        handle=payload.handle,
        color=payload.color,
        persona_id=payload.persona_id,
        timezone=payload.timezone,
        nsfw_level=payload.nsfw_level,
        platform_side_scheduling=payload.platform_side_scheduling,
        default_audience=payload.default_audience,
        oauth_redirect_base=redirect_base,
        policy_id=policy.id,
        health=ChannelHealth.needs_reauth.value,
        health_note="Noch nicht verbunden",
    )
    if channel.platform == 'fanvue':
        from autoposter.services import chatbot_fanvue
        tokens = chatbot_fanvue.chatbot_db.get_tokens()
        if not channel.handle and tokens:
            channel.handle = tokens['account_handle'] or ''
        channel.health = chatbot_fanvue.health(channel)
        channel.health_note = 'Verbindung über AutoChat' if channel.health == 'ok' else 'Bitte AutoChat-Verbindung und Kanal-Handle prüfen.'
    if payload.oauth_client_id:
        oauthapp.store_app(
            channel, payload.oauth_client_id, payload.oauth_client_secret, payload.oauth_app_name
        )
    db.add(channel)
    await db.flush()
    db.add(ContentPlan(channel_id=channel.id))
    await db.flush()
    await db.refresh(channel)
    return await _to_out(db, channel)


@router.patch("/channels/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: uuid.UUID,
    payload: ChannelUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> ChannelOut:
    channel = (
        await db.execute(select(Channel).where(Channel.id == channel_id))
    ).scalar_one_or_none()
    if not channel:
        raise HTTPException(404, "Kanal nicht gefunden")

    data = payload.model_dump(exclude_unset=True)
    if "oauth_redirect_base" in data:
        try:
            data["oauth_redirect_base"] = oauthapp.normalize_redirect_base(data["oauth_redirect_base"], channel.platform)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    policy_data = data.pop("policy", None)
    client_id = data.pop("oauth_client_id", None)
    client_secret = data.pop("oauth_client_secret", None)
    app_name = data.pop("oauth_app_name", None)
    for key, value in data.items():
        setattr(channel, key, value)

    if client_id is not None or client_secret is not None or app_name is not None:
        oauthapp.store_app(
            channel,
            client_id if client_id is not None else channel.oauth_client_id,
            client_secret,
            app_name if app_name is not None else channel.oauth_app_name,
        )
        # Eine X-App bedient genau einen Account – doppelte Nutzung verhindern.
        clash, names = await oauthapp.conflicting_channels(db, channel)
        if clash and channel.platform == "x":
            raise HTTPException(
                409,
                "Diese Client-ID wird bereits von folgendem Kanal genutzt: "
                + ", ".join(names)
                + ". Zu jeder X-App gehört genau ein Account – bitte eine eigene App anlegen.",
            )

    if policy_data:
        policy = channel.policy
        if not policy:
            policy = PostingPolicy()
            db.add(policy)
            await db.flush()
            channel.policy_id = policy.id
        for key, value in policy_data.items():
            setattr(policy, key, value)
        db.add(policy)

    db.add(channel)
    await db.flush()
    await db.refresh(channel)
    return await _to_out(db, channel)


@router.get("/channels/{channel_id}/deletion-preview")
async def deletion_preview(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> Dict[str, Any]:
    """Was hinge am Kanal? Zahlen für die Rückfrage vor dem Löschen.

    Ohne diese Vorschau wäre die Rückfrage eine leere Warnung: Niemand weiß im
    Moment des Klicks, ob an dem Kanal drei Testposts oder ein halbes Jahr
    Verlauf hängen. Gelöscht wird hier nichts.
    """
    channel = (
        await db.execute(select(Channel).where(Channel.id == channel_id))
    ).scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Kanal nicht gefunden")

    async def count(model: Any, *where: Any) -> int:
        stmt = select(func.count()).select_from(model).where(*where)
        return int((await db.execute(stmt)).scalar_one() or 0)

    published = await count(
        Post, Post.channel_id == channel_id, Post.status == PostStatus.published.value
    )
    total_posts = await count(Post, Post.channel_id == channel_id)

    return {
        "channel_id": str(channel_id),
        "display_name": channel.display_name,
        "platform": channel.platform,
        "is_connected": cred_service.is_connected(channel),
        "posts_total": total_posts,
        "posts_published": published,
        "posts_open": total_posts - published,
        "assignments": await count(
            MediaAssignment, MediaAssignment.channel_id == channel_id
        ),
        "usages": await count(
            MediaChannelUsage, MediaChannelUsage.channel_id == channel_id
        ),
        "has_watermark": watermark.path_for(channel_id).exists(),
    }


@router.delete("/channels/{channel_id}", status_code=204, response_model=None)
async def delete_channel(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> None:
    """Kanal samt allem, was an ihm hängt, entfernen.

    Posts, Zuordnungen und Verlauf verschwinden über ON DELETE CASCADE in der
    Datenbank. Drei Dinge holt die Kaskade aber nicht ein, und die stehen
    deshalb hier:

    * Die Bild-Zähler (`assigned_channel_ids` und Geschwister) liegen als Liste
      im Datensatz des Bildes, nicht als Fremdschlüssel. Ohne Nachrechnen zeigt
      die Bibliothek noch tagelang Zuordnungen zu einem Kanal, den es nicht
      mehr gibt.
    * Die Wasserzeichendatei liegt im Dateisystem.
    * Der Zugangsdatensatz hängt andersherum am Kanal (`SET NULL`) und bliebe
      als verwaister, verschlüsselter Token liegen.
    """
    channel = (
        await db.execute(select(Channel).where(Channel.id == channel_id))
    ).scalar_one_or_none()
    if not channel:
        return  # Schon weg – für den Aufrufer dasselbe Ergebnis.

    credential_id = channel.credential_id

    # Betroffene Bilder VOR dem Löschen einsammeln – danach ist die Verbindung
    # zum Kanal fort und niemand wüsste mehr, was nachzurechnen ist.
    touched: set[uuid.UUID] = set()
    for column, model in (
        (MediaAssignment.media_asset_id, MediaAssignment),
        (MediaChannelUsage.media_asset_id, MediaChannelUsage),
    ):
        rows = (
            await db.execute(select(column).where(model.channel_id == channel_id))
        ).scalars().all()
        touched.update(r for r in rows if r)

    post_media = (
        await db.execute(select(Post.media_asset_ids).where(Post.channel_id == channel_id))
    ).scalars().all()
    for entry in post_media:
        for raw in entry or []:
            try:
                touched.add(uuid.UUID(str(raw)))
            except (ValueError, AttributeError, TypeError):
                continue

    # OAuthState hat als einzige Tabelle mit channel_id KEINEN Fremdschlüssel –
    # die Kaskade greift dort nicht. Nachgemessen an der echten Datenbank:
    # pragma foreign_key_list(oauth_state) ist leer.
    await db.execute(delete(OAuthState).where(OAuthState.channel_id == channel_id))

    await db.delete(channel)
    await db.flush()

    if credential_id:
        # Nur wenn ihn kein anderer Kanal mitbenutzt.
        still_used = (
            await db.execute(
                select(func.count())
                .select_from(Channel)
                .where(Channel.credential_id == credential_id)
            )
        ).scalar_one()
        if not still_used:
            await db.execute(
                delete(ChannelCredential).where(ChannelCredential.id == credential_id)
            )

    await lifecycle.refresh_assets(db, touched)
    library.invalidate_counts()
    watermark.remove(channel_id)


@router.post("/channels/{channel_id}/pause", response_model=ChannelOut)
async def pause_channel(
    channel_id: uuid.UUID,
    paused: bool = True,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> ChannelOut:
    channel = (
        await db.execute(select(Channel).where(Channel.id == channel_id))
    ).scalar_one_or_none()
    if not channel:
        raise HTTPException(404, "Kanal nicht gefunden")
    channel.health = ChannelHealth.paused.value if paused else ChannelHealth.ok.value
    channel.is_active = not paused
    db.add(channel)
    await db.flush()
    return await _to_out(db, channel)


# --------------------------------------------------------------------------- #
# OAuth
# --------------------------------------------------------------------------- #
def _pkce() -> tuple:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


@router.get("/oauth/{platform}/start")
async def oauth_start(
    platform: str,
    channel_id: Optional[uuid.UUID] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict:
    if platform == "fanvue":
        raise HTTPException(409, "Fanvue wird zentral im AutoChat verwaltet. Keine zweite Anmeldung erforderlich.")
    adapter = get_adapter(platform)

    channel = None
    if channel_id:
        channel = (
            await db.execute(select(Channel).where(Channel.id == channel_id))
        ).scalar_one_or_none()
        if not channel:
            raise HTTPException(404, "Kanal nicht gefunden")
        app = oauthapp.for_channel(channel)
    else:
        app = oauthapp.global_app(platform)

    if not app.is_configured:
        raise HTTPException(
            400,
            "Keine API-Zugangsdaten hinterlegt. Bei X gehört zu jedem Account eine "
            "eigene App – Client-ID und Secret beim Kanal unter „API-Zugang\" eintragen.",
        )

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    db.add(
        OAuthState(
            state=state, platform=platform, code_verifier=verifier, channel_id=channel_id
        )
    )
    await db.flush()
    return {
        "authorize_url": adapter.authorize_url(app, state, challenge),
        "state": state,
        "redirect_uri": app.redirect_uri(platform),
        "app": app.name,
    }


@router.get("/oauth/{platform}/callback", response_class=HTMLResponse)
async def oauth_callback(
    platform: str,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    # Der nackte Fehlercode („invalid_request") sagt nichts. Die Begründung
    # steht immer daneben und wurde bisher weggeworfen – dadurch war jeder
    # Fehlschlag eine Sackgasse.
    error_description: Optional[str] = None,
    error_hint: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    if platform == "fanvue":
        raise HTTPException(409, "Bitte die Fanvue-Verbindung im AutoChat verwalten.")
    def page(title: str, message: str, ok: bool) -> HTMLResponse:
        color = "#16a34a" if ok else "#dc2626"
        return HTMLResponse(
            f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
            <title>{title}</title><style>body{{font-family:system-ui;padding:48px;
            background:#0b1020;color:#e5e7eb}}h1{{color:{color}}}a{{color:#818cf8}}</style></head>
            <body><h1>{title}</h1><p>{message}</p>
            <p><a href="/channels">Zurück zu AutoPoster</a></p>
            <script>setTimeout(()=>{{window.close()}},2500)</script></body></html>""",
            status_code=200 if ok else 400,
        )

    if error or not code or not state:
        parts = [p for p in (error, error_description, error_hint) if p]
        # Escapen ist Pflicht: Der Text kommt aus der Query und landet im HTML.
        detail = " — ".join(html.escape(p) for p in parts) or "Kein Code erhalten"
        if "redirect" in " ".join(parts).lower():
            expected = oauthapp.global_app(platform).redirect_uri(platform)
            detail += (
                "<br><br>Bei der App muss zeichengenau diese Redirect-URI "
                f"hinterlegt sein:<br><code>{html.escape(expected)}</code>"
            )
        return page("Verbindung abgebrochen", detail, False)

    row = (
        await db.execute(select(OAuthState).where(OAuthState.state == state))
    ).scalar_one_or_none()
    if not row or row.platform != platform:
        return page("Ungültiger Status", "Der OAuth-State ist unbekannt oder abgelaufen.", False)

    channel: Optional[Channel] = None
    if row.channel_id:
        channel = (
            await db.execute(select(Channel).where(Channel.id == row.channel_id))
        ).scalar_one_or_none()
    app = oauthapp.for_channel(channel) if channel else oauthapp.global_app(platform)

    adapter = get_adapter(platform)
    try:
        cred = await adapter.exchange_code(app, code, row.code_verifier)
    except AdapterError as exc:
        return page("Token-Tausch fehlgeschlagen", str(exc), False)

    credential = ChannelCredential(platform=platform)
    cred_service.store_credentials(credential, cred)
    db.add(credential)
    await db.flush()

    if channel:
        channel.credential_id = credential.id
        channel.health = ChannelHealth.ok.value
        channel.health_note = ""
        db.add(channel)

    await db.delete(row)
    await db.flush()

    name = channel.display_name if channel else platform
    return page("Verbunden", f"Kanal '{name}' ist jetzt mit {platform} verbunden.", True)


@router.post("/channels/{channel_id}/test")
async def test_channel(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> Dict[str, Any]:
    """Verbindung prüfen: App hinterlegt, Token gültig, welcher Account hängt dran.

    Bei X ist der zurückgemeldete Account die eigentliche Kontrolle – so sieht
    man sofort, ob eine App versehentlich auf das falsche Profil zeigt.
    """
    channel = (
        await db.execute(select(Channel).where(Channel.id == channel_id))
    ).scalar_one_or_none()
    if not channel:
        raise HTTPException(404, "Kanal nicht gefunden")

    app = oauthapp.for_channel(channel)
    redirect = app.redirect_uri(channel.platform)
    result: Dict[str, Any] = {
        "channel_id": str(channel.id),
        "channel_name": channel.display_name,
        "platform": channel.platform,
        "uses_own_app": oauthapp.uses_own_app(channel),
        "app_name": app.name,
        "redirect_uri": redirect,
    }

    # localhost funktioniert bei X grundsätzlich – die offizielle CLI `xurl`
    # benutzt selbst http://localhost:8080/callback. Es gibt aber Berichte über
    # Konten, bei denen die Autorisierungsseite damit ohne Begründung mit
    # „Something went wrong" abbricht. Deshalb ein Hinweis, keine Fehlermeldung:
    # Der erste Verdacht bei diesem Symptom sind die Einstellungen der App.
    if channel.platform == "x" and re.search(
        r"//(localhost|127\.0\.0\.1)\b", redirect
    ):
        result["warning"] = (
            "Die Redirect-URI zeigt auf localhost. Das ist bei X erlaubt, muss aber "
            "zeichengenau so in den „User authentication settings\" der App stehen. "
            "Bricht die Autorisierung ohne Begründung ab, hilft ersatzweise ein "
            "eigener Name in /etc/hosts auf 127.0.0.1 (z. B. autoposter.local)."
        )

    if not app.is_configured:
        result.update(ok=False, step="app", message=(
            "Keine Client-ID hinterlegt. Bei X braucht jeder Account eine eigene App."
        ))
        return result
    # Beide Plattformen erwarten einen vertraulichen Client. Fehlt das Secret,
    # schickt AutoPoster beim Token-Tausch GAR KEINE Anmeldung mit – Fanvue
    # antwortet dann mit „invalid_client: no client authentication included",
    # was wie ein falsches Secret aussieht, obwohl schlicht keines da ist.
    if not app.client_secret:
        result.update(ok=False, step="app", message=(
            "Client-Secret fehlt. Ohne Secret kann sich AutoPoster am "
            "Token-Endpunkt nicht anmelden; der Tausch scheitert mit "
            "„invalid_client\". Eine Client-ID allein genügt nicht — wer die "
            "globale App aus den Einstellungen nutzen will, muss auch die "
            "Client-ID hier leer lassen."
        ))
        return result
    if not cred_service.is_connected(channel):
        result.update(ok=False, step="oauth", message=(
            "Noch nicht verbunden. Auf „Verbinden\" klicken und die Autorisierung abschließen."
        ))
        return result

    # Doppelte App-Nutzung ist bei X ein echter Fehler.
    clash, names = await oauthapp.conflicting_channels(db, channel)
    if clash and channel.platform == "x":
        result.update(ok=False, step="app", message=(
            "Dieselbe Client-ID wird auch von " + ", ".join(names) +
            " genutzt. Zu jeder X-App gehört genau ein Account."
        ))
        return result

    try:
        cred = await cred_service.get_valid_credentials(db, channel)
        info = await get_adapter(channel.platform).verify(app, cred)
    except AdapterError as exc:
        result.update(ok=False, step="token", message=str(exc), needs_reauth=exc.needs_reauth)
        return result
    except Exception as exc:  # pragma: no cover - defensiv
        result.update(ok=False, step="token", message=f"Unerwarteter Fehler: {exc}")
        return result

    if info.ok:
        channel.last_verified_at = datetime.now(timezone.utc)
        channel.verified_account = info.username or info.account_id
        if channel.health == ChannelHealth.needs_reauth.value:
            channel.health = ChannelHealth.ok.value
            channel.health_note = ""
        db.add(channel)
        await db.flush()

        # Warnen, wenn der verbundene Account nicht zum eingetragenen Handle passt.
        expected = channel.handle.lstrip("@").lower()
        actual = (info.username or "").lower()
        if expected and actual and expected != actual:
            result["warning"] = (
                f"Verbunden ist @{info.username}, im Kanal steht aber '{channel.handle}'. "
                "Zeigt die App auf das richtige Profil?"
            )

    result.update(
        ok=info.ok,
        step="ok" if info.ok else "token",
        message=info.message,
        account=info.username,
        account_id=info.account_id,
        scopes=list(cred.scopes or []),
        token_expires_at=cred.expires_at.isoformat() if cred.expires_at else None,
    )
    return result


@router.post("/channels/test-all")
async def test_all_channels(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> Dict[str, Any]:
    """Alle Kanäle nacheinander prüfen – für die Einstellungsseite."""
    channels = (
        await db.execute(select(Channel).order_by(Channel.platform, Channel.display_name))
    ).scalars().all()
    results = []
    for channel in channels:
        try:
            results.append(await test_channel(channel.id, db, _))
        except HTTPException as exc:
            results.append(
                {
                    "channel_id": str(channel.id),
                    "channel_name": channel.display_name,
                    "platform": channel.platform,
                    "ok": False,
                    "message": exc.detail,
                }
            )
    return {
        "results": results,
        "ok_count": sum(1 for r in results if r.get("ok")),
        "total": len(results),
    }


@router.post("/channels/{channel_id}/disconnect", status_code=204, response_model=None)
async def disconnect(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> None:
    channel = (
        await db.execute(select(Channel).where(Channel.id == channel_id))
    ).scalar_one_or_none()
    if not channel:
        raise HTTPException(404, "Kanal nicht gefunden")
    if channel.platform == "fanvue":
        raise HTTPException(409, "Die gemeinsame Fanvue-Verbindung kann nur im AutoChat getrennt werden.")
    channel.credential_id = None
    channel.health = ChannelHealth.needs_reauth.value
    channel.health_note = "Verbindung getrennt"
    db.add(channel)


# --------------------------------------------------------------------------- #
# Wasserzeichen
# --------------------------------------------------------------------------- #
async def _channel_or_404(db: AsyncSession, channel_id: uuid.UUID) -> Channel:
    channel = (
        await db.execute(select(Channel).where(Channel.id == channel_id))
    ).scalar_one_or_none()
    if not channel:
        raise HTTPException(404, "Kanal nicht gefunden")
    return channel


async def _drop_media_cache(db: AsyncSession, channel_id: uuid.UUID) -> None:
    """Hochgeladene Bilder dieses Kanals vergessen.

    Die Plattformen bekommen Bilder einmal und merken sich die ID. Ändert sich
    das Wasserzeichen, müssen sie neu hochgeladen werden – sonst trüge der
    nächste Post noch das alte Zeichen.
    """
    await db.execute(
        delete(ExternalMediaRef).where(ExternalMediaRef.channel_id == channel_id)
    )


@router.get("/channels/{channel_id}/watermark", include_in_schema=False)
async def get_watermark(
    channel_id: uuid.UUID, _: User = Depends(current_user)
) -> FileResponse:
    path = watermark.path_for(channel_id)
    if not path.exists():
        raise HTTPException(404, "Für diesen Kanal ist kein Wasserzeichen hinterlegt")
    return FileResponse(path, media_type="image/png")


@router.post("/channels/{channel_id}/watermark", response_model=ChannelOut)
async def upload_watermark(
    channel_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> ChannelOut:
    """PNG mit freigestelltem Hintergrund hinterlegen."""
    channel = await _channel_or_404(db, channel_id)
    data = await file.read()
    try:
        path, width, height = watermark.store(channel.id, data)
    except watermark.WatermarkError as exc:
        raise HTTPException(400, str(exc))

    channel.watermark_path = path
    channel.watermark_enabled = True
    db.add(channel)
    await _drop_media_cache(db, channel.id)
    await db.flush()
    await db.refresh(channel)
    return await _to_out(db, channel)


@router.delete("/channels/{channel_id}/watermark", response_model=ChannelOut)
async def delete_watermark(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> ChannelOut:
    channel = await _channel_or_404(db, channel_id)
    watermark.remove(channel.id)
    channel.watermark_path = None
    channel.watermark_enabled = False
    db.add(channel)
    await _drop_media_cache(db, channel.id)
    await db.flush()
    await db.refresh(channel)
    return await _to_out(db, channel)


@router.get("/channels/{channel_id}/watermark/preview", include_in_schema=False)
async def watermark_preview(
    channel_id: uuid.UUID,
    asset_id: Optional[uuid.UUID] = None,
    anchor: Optional[str] = None,
    scale_pct: Optional[float] = None,
    margin_x_pct: Optional[float] = None,
    margin_y_pct: Optional[float] = None,
    opacity: Optional[float] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> Response:
    """Vorschau auf einem echten Bild des Kanals.

    Die Parameter überschreiben die gespeicherten Werte – so sieht man die
    Wirkung eines Reglers, bevor man speichert.
    """
    channel = await _channel_or_404(db, channel_id)
    if not watermark.path_for(channel.id).exists():
        raise HTTPException(404, "Kein Wasserzeichen hinterlegt")

    stmt = select(MediaAsset).where(MediaAsset.status == MediaStatus.ready.value)
    if asset_id:
        stmt = stmt.where(MediaAsset.id == asset_id)
    else:
        # Ein Bild dieses Kanals, damit die Vorschau realistisch ist.
        stmt = stmt.join(
            MediaAssignment, MediaAssignment.media_asset_id == MediaAsset.id
        ).where(MediaAssignment.channel_id == channel.id)
    asset = (await db.execute(stmt.limit(1))).scalars().first()
    if not asset:
        asset = (
            await db.execute(
                select(MediaAsset).where(MediaAsset.status == MediaStatus.ready.value).limit(1)
            )
        ).scalars().first()
    if not asset:
        raise HTTPException(404, "Es gibt noch kein Bild für die Vorschau")

    placement = watermark.Placement.from_channel(channel)
    if anchor:
        placement.anchor = anchor
    if scale_pct is not None:
        placement.scale_pct = scale_pct
    if margin_x_pct is not None:
        placement.margin_x_pct = margin_x_pct
    if margin_y_pct is not None:
        placement.margin_y_pct = margin_y_pct
    if opacity is not None:
        placement.opacity = opacity

    data = await asyncio.to_thread(media_service.read_bytes, asset)
    rendered = await asyncio.to_thread(watermark.preview_bytes, data, channel, placement)
    return Response(content=rendered, media_type="image/png",
                    headers={"Cache-Control": "no-store"})
