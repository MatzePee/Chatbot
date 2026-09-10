"""Pydantic-Schemas für die REST-API."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    totp: Optional[str] = None


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    is_active: bool


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10)
    display_name: str = ""
    role: str = "editor"


# --------------------------------------------------------------------------- #
# Persona
# --------------------------------------------------------------------------- #
class PersonaBase(BaseModel):
    name: str
    bio: str = ""
    tone_guidelines: str = ""
    system_prompt: str = ""
    #: Wochenplan in Textform, siehe app/services/rhythm.py
    daily_rhythm: str = ""
    forbidden_topics: List[str] = []
    emoji_policy: str = "sparse"
    language: str = "en"
    hashtag_pool: List[str] = []
    cta_pool: List[str] = []
    model_override: Optional[str] = None
    temperature: float = 0.9


class PersonaCreate(PersonaBase):
    pass


class PersonaUpdate(BaseModel):
    name: Optional[str] = None
    bio: Optional[str] = None
    tone_guidelines: Optional[str] = None
    system_prompt: Optional[str] = None
    daily_rhythm: Optional[str] = None
    forbidden_topics: Optional[List[str]] = None
    emoji_policy: Optional[str] = None
    language: Optional[str] = None
    hashtag_pool: Optional[List[str]] = None
    cta_pool: Optional[List[str]] = None
    model_override: Optional[str] = None
    temperature: Optional[float] = None


class PersonaOut(ORMModel, PersonaBase):
    id: uuid.UUID
    slug: str


class PersonaExampleIn(BaseModel):
    """Ein Beispielpost als Stilvorlage."""

    text: str
    platform: str = "x"
    #: Gefüllt = Beispiel für Bildposts, leer = für reine Textposts
    image_description: str = ""
    rating: int = 1


class PersonaExampleOut(ORMModel, PersonaExampleIn):
    id: uuid.UUID
    persona_id: uuid.UUID
    created_at: datetime


class PersonaExampleBulk(BaseModel):
    """Mehrere Beispiele auf einmal – eine Zeile je Beispiel."""

    text: str
    platform: str = "x"
    image_description: str = ""
    rating: int = 1


class GitSettingsIn(BaseModel):
    """Zugang zu GitHub. Der Token wird verschlüsselt abgelegt."""

    git_remote_url: str = ""
    git_branch: str = "main"
    git_user_name: str = ""
    git_user_email: str = ""
    git_commit_default: str = ""
    #: Leer = unverändert lassen. Zum Löschen clear_token setzen.
    github_token: str = ""
    clear_token: bool = False


class PublishRequest(BaseModel):
    message: str = "Aktualisierung"
    #: Leer = nur committen und hochladen, ohne neue Version zu veröffentlichen.
    tag: str = ""
    push: bool = True


class RhythmCheckRequest(BaseModel):
    text: str = ""


class RhythmCheckResult(BaseModel):
    """Antwort der Prüfung: verstandene Regeln, Fehler, Lücken, Vorschau."""

    rules: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    grid: List[List[Optional[str]]] = []
    ok: bool = True


# --------------------------------------------------------------------------- #
# Kanäle
# --------------------------------------------------------------------------- #
class PolicyBase(BaseModel):
    posts_per_day: float = 2.0
    min_gap_minutes: int = 180
    jitter_minutes: int = 12
    max_images_per_post: int = 4
    text_only_ratio: float = 0.0
    allowed_time_windows: List[Dict[str, Any]] = [
        {"dow": [0, 1, 2, 3, 4, 5, 6], "from": "09:00", "to": "22:00"}
    ]
    #: Leer = allowed_time_windows verwenden
    image_time_windows: List[Dict[str, Any]] = []
    text_time_windows: List[Dict[str, Any]] = []
    #: Fester Versatz gegen zeitgleiche Posts anderer Kanäle
    stagger_minutes: int = 0
    #: Anteil Free-Posts bei Fanvue (0 = nur Abonnenten, 1 = alles frei)
    free_post_ratio: float = 0.3
    reuse_cooldown_days: int = 0
    phash_min_distance: int = 6
    phash_lookback_posts: int = 20
    daily_api_quota: int = 50
    auto_approve: bool = False
    #: together = Set als ein Post, single = Bilder einzeln in Set-Reihenfolge,
    #: leer = nach Plattform (Fanvue gemeinsam, X einzeln)
    set_handling: Optional[str] = None
    auto_retry_on_fail: bool = True
    allow_recycling: bool = False
    exclusive_pool_group: Optional[str] = None


class PolicyOut(ORMModel, PolicyBase):
    id: uuid.UUID


class ChannelCreate(BaseModel):
    platform: str
    display_name: str
    handle: str = ""
    color: str = "#6366f1"
    persona_id: Optional[uuid.UUID] = None
    timezone: str = "Europe/Berlin"
    nsfw_level: str = "suggestive"
    platform_side_scheduling: bool = False
    default_audience: str = "subscribers"
    #: Eigene OAuth-App. Bei X gehört zu jeder App genau EIN Account, deshalb
    #: braucht dort jeder Kanal eigene Zugangsdaten.
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    oauth_app_name: str = ""
    oauth_redirect_base: str = ""
    policy: PolicyBase = PolicyBase()


class ChannelUpdate(BaseModel):
    display_name: Optional[str] = None
    handle: Optional[str] = None
    color: Optional[str] = None
    persona_id: Optional[uuid.UUID] = None
    timezone: Optional[str] = None
    nsfw_level: Optional[str] = None
    is_active: Optional[bool] = None
    platform_side_scheduling: Optional[bool] = None
    default_audience: Optional[str] = None
    oauth_client_id: Optional[str] = None
    #: Leer oder "********" lässt das gespeicherte Secret unverändert.
    oauth_client_secret: Optional[str] = None
    oauth_app_name: Optional[str] = None
    oauth_redirect_base: Optional[str] = None
    #: Wasserzeichen – die Datei selbst kommt über einen eigenen Endpunkt.
    watermark_enabled: Optional[bool] = None
    watermark_anchor: Optional[str] = None
    watermark_scale_pct: Optional[float] = None
    watermark_margin_x_pct: Optional[float] = None
    watermark_margin_y_pct: Optional[float] = None
    watermark_opacity: Optional[float] = None
    policy: Optional[PolicyBase] = None


class ChannelOut(ORMModel):
    id: uuid.UUID
    platform: str
    display_name: str
    handle: str
    color: str
    persona_id: Optional[uuid.UUID]
    timezone: str
    nsfw_level: str
    is_active: bool
    health: str
    health_note: str
    platform_side_scheduling: bool
    default_audience: str
    last_published_at: Optional[datetime]
    quota_used_today: int
    policy: Optional[PolicyOut] = None
    token_expires_at: Optional[datetime] = None
    is_connected: bool = False
    connection_source: str = "channel"
    #: OAuth-App dieses Kanals
    uses_own_app: bool = False
    oauth_client_id: str = ""
    oauth_app_name: str = ""
    oauth_redirect_base: str = ""
    has_client_secret: bool = False
    app_configured: bool = False
    redirect_uri: str = ""
    fanvue_audience: str = ""
    fanvue_account_uuid: str = ""
    #: Letzter Verbindungstest
    last_verified_at: Optional[datetime] = None
    verified_account: str = ""
    #: Wasserzeichen
    has_watermark: bool = False
    watermark_enabled: bool = False
    watermark_anchor: str = "bottom-left"
    watermark_scale_pct: float = 18.0
    watermark_margin_x_pct: float = 3.0
    watermark_margin_y_pct: float = 3.0
    watermark_opacity: float = 1.0
    watermark_url: str = ""


# --------------------------------------------------------------------------- #
# Medien
# --------------------------------------------------------------------------- #
class MediaOut(ORMModel):
    id: uuid.UUID
    filename: str
    width: int
    height: int
    bytes: int
    mime: str
    nsfw_level: str
    tags: List[str]
    caption_hint: str
    ai_description: str
    source_note: str
    status: str
    error_message: str
    lifecycle: str
    assigned_channel_ids: List[str]
    used_channel_ids: List[str]
    scheduled_channel_ids: List[str]
    usage_count: int
    first_used_at: Optional[datetime]
    last_used_at: Optional[datetime]
    created_at: datetime
    thumb_url: str = ""
    url: str = ""
    #: Sets, zu denen dieses Bild gehört – für die Kennzeichnung in der Bibliothek.
    set_ids: List[str] = []
    set_names: List[str] = []


class MediaUpdate(BaseModel):
    tags: Optional[List[str]] = None
    caption_hint: Optional[str] = None
    nsfw_level: Optional[str] = None
    source_note: Optional[str] = None
    status: Optional[str] = None


class MediaBulkUpdate(BaseModel):
    asset_ids: List[uuid.UUID]
    add_tags: List[str] = []
    remove_tags: List[str] = []
    nsfw_level: Optional[str] = None
    archive: Optional[bool] = None


class MediaQuery(BaseModel):
    """Alle Filterachsen der Bibliothek."""

    q: Optional[str] = None
    lifecycle: List[str] = []
    tags_any: List[str] = []
    tags_all: List[str] = []
    tags_none: List[str] = []
    nsfw_level: List[str] = []
    status: List[str] = []
    set_id: Optional[uuid.UUID] = None
    assigned_to: List[uuid.UUID] = []
    used_on: List[uuid.UUID] = []
    not_used_on: List[uuid.UUID] = []
    unassigned: bool = False
    include_archived: bool = False
    uploaded_after: Optional[datetime] = None
    uploaded_before: Optional[datetime] = None
    used_after: Optional[datetime] = None
    used_before: Optional[datetime] = None
    unused_since_days: Optional[int] = None
    min_usage: Optional[int] = None
    max_usage: Optional[int] = None
    similar_to: Optional[uuid.UUID] = None
    similar_distance: int = 10
    sort: str = "created_desc"
    limit: int = 100
    cursor: Optional[str] = None


class MediaPage(BaseModel):
    items: List[MediaOut]
    next_cursor: Optional[str] = None
    total_estimate: Optional[int] = None


class LifecycleCounts(BaseModel):
    new: int = 0
    assigned: int = 0
    scheduled: int = 0
    partially_used: int = 0
    fully_used: int = 0
    archived: int = 0
    total: int = 0


class MediaUsageEntry(BaseModel):
    channel_id: uuid.UUID
    channel_name: str
    platform: str
    used_at: datetime
    post_id: Optional[uuid.UUID]
    post_text: str = ""
    external_url: str = ""
    metrics: Dict[str, Any] = {}


class MediaDetail(MediaOut):
    history: List[MediaUsageEntry] = []
    open_channels: List[uuid.UUID] = []
    sets: List[uuid.UUID] = []


# --------------------------------------------------------------------------- #
# Sets
# --------------------------------------------------------------------------- #
class MediaSetCreate(BaseModel):
    name: str
    description: str = ""
    nsfw_level: str = "sfw"
    tags: List[str] = []
    is_ordered: bool = True
    asset_ids: List[uuid.UUID] = []


class MediaSetUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    nsfw_level: Optional[str] = None
    tags: Optional[List[str]] = None
    cover_media_id: Optional[uuid.UUID] = None
    preview_media_id: Optional[uuid.UUID] = None
    asset_ids: Optional[List[uuid.UUID]] = None


class MediaSetOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str
    nsfw_level: str
    tags: List[str]
    is_ordered: bool
    cover_media_id: Optional[uuid.UUID]
    preview_media_id: Optional[uuid.UUID]
    asset_ids: List[uuid.UUID] = []
    assigned_channel_ids: List[str] = []


# --------------------------------------------------------------------------- #
# Zuordnung
# --------------------------------------------------------------------------- #
class AssignmentBatch(BaseModel):
    """Ein Drag-&-Drop-Vorgang."""

    asset_ids: List[uuid.UUID] = []
    set_ids: List[uuid.UUID] = []
    channel_ids: List[uuid.UUID]
    mode: str = "copy"  # copy | move
    #: Bei move: von diesen Kanälen entfernen. Leer = von allen anderen.
    remove_from_channel_ids: List[uuid.UUID] = []


class AssignmentRemove(BaseModel):
    asset_ids: List[uuid.UUID] = []
    set_ids: List[uuid.UUID] = []
    channel_ids: List[uuid.UUID]
    cascade_posts: bool = False


class AssignmentReorder(BaseModel):
    channel_id: uuid.UUID
    ordered_asset_ids: List[uuid.UUID]


class AssignmentResult(BaseModel):
    created: int = 0
    skipped: int = 0
    removed: int = 0
    rejected: List[Dict[str, str]] = []
    affected_channels: List[uuid.UUID] = []


class AssignmentRuleIn(BaseModel):
    channel_id: uuid.UUID
    name: str
    match: Dict[str, Any] = {}
    mode: str = "suggest"
    is_active: bool = True


class AssignmentRuleOut(ORMModel, AssignmentRuleIn):
    id: uuid.UUID


# --------------------------------------------------------------------------- #
# Posts / Kalender
# --------------------------------------------------------------------------- #
class PostCreate(BaseModel):
    channel_id: uuid.UUID
    type: str = "image_single"
    scheduled_at: Optional[datetime] = None
    body_text: str = ""
    hashtags: List[str] = []
    media_asset_ids: List[uuid.UUID] = []
    media_set_id: Optional[uuid.UUID] = None
    alt_texts: Dict[str, str] = {}
    audience: Optional[str] = None
    price_cents: Optional[int] = None
    media_preview_id: Optional[uuid.UUID] = None
    expires_at: Optional[datetime] = None
    pin_after_publish: bool = False
    status: str = "draft"


class PostUpdate(BaseModel):
    scheduled_at: Optional[datetime] = None
    body_text: Optional[str] = None
    hashtags: Optional[List[str]] = None
    media_asset_ids: Optional[List[uuid.UUID]] = None
    alt_texts: Optional[Dict[str, str]] = None
    audience: Optional[str] = None
    price_cents: Optional[int] = None
    media_preview_id: Optional[uuid.UUID] = None
    expires_at: Optional[datetime] = None
    pin_after_publish: Optional[bool] = None
    status: Optional[str] = None
    type: Optional[str] = None


class PostOut(ORMModel):
    id: uuid.UUID
    channel_id: uuid.UUID
    persona_id: Optional[uuid.UUID]
    type: str
    status: str
    scheduled_at: Optional[datetime]
    published_at: Optional[datetime]
    body_text: str
    body_text_variants: List[Dict[str, Any]]
    hashtags: List[str]
    alt_texts: Dict[str, str]
    media_asset_ids: List[str]
    media_set_id: Optional[uuid.UUID]
    audience: str
    price_cents: Optional[int]
    media_preview_id: Optional[uuid.UUID]
    expires_at: Optional[datetime]
    pin_after_publish: bool
    thread_parent_id: Optional[uuid.UUID]
    thread_position: int
    external_post_id: Optional[str]
    external_url: str
    error_message: str
    attempt_count: int
    metrics: Dict[str, Any]
    generation_meta: Dict[str, Any]
    created_at: datetime


class PostBulkMove(BaseModel):
    post_ids: List[uuid.UUID]
    shift_minutes: int = 0
    new_status: Optional[str] = None


class PreflightIssue(BaseModel):
    level: str  # error | warning | ok
    code: str
    message: str


class PreflightResult(BaseModel):
    ok: bool
    issues: List[PreflightIssue]


class GenerateRequest(BaseModel):
    post_id: Optional[uuid.UUID] = None
    channel_id: Optional[uuid.UUID] = None
    media_asset_ids: List[uuid.UUID] = []
    instruction: str = ""
    variants: int = 3


class GenerateResult(BaseModel):
    variants: List[Dict[str, Any]]
    model: str
    cost_usd: float = 0.0


class PlanFillRequest(BaseModel):
    channel_ids: List[uuid.UUID] = []
    days: int = 14
    dry_run: bool = True


class PlannedSlot(BaseModel):
    channel_id: uuid.UUID
    scheduled_at: datetime
    type: str
    media_asset_ids: List[uuid.UUID] = []
    reason: str = ""
    #: Fanvue: "followers-and-subscribers" (Free) oder "subscribers" (Sub)
    audience: str = "subscribers"


class PlanFillResult(BaseModel):
    slots: List[PlannedSlot] = []
    created_post_ids: List[uuid.UUID] = []
    gaps: List[Dict[str, Any]] = []


# --------------------------------------------------------------------------- #
# Zeitraum-Planung („Automatisch planen")
# --------------------------------------------------------------------------- #
class PlanRangeRequest(BaseModel):
    channel_ids: List[uuid.UUID] = []
    date_from: date
    date_to: date
    #: Wie viele Posts je Tag und Typ entstehen sollen (X: Bild und Text).
    image_posts_per_day: int = 1
    text_posts_per_day: int = 0
    #: Fanvue kennt keine reinen Textposts, dafür zwei Sichtbarkeiten. Sind
    #: beide Felder 0, gilt die alte Aufteilung nach free_post_ratio – so
    #: bleiben der X-Dialog und das automatische Nachplanen unverändert.
    sub_posts_per_day: int = 0
    free_posts_per_day: int = 0
    #: Zeitfenster für diesen Lauf. Überschreibt die Kanal-Regeln.
    time_from: str = "09:00"
    time_to: str = "22:00"
    #: 0 = Montag … 6 = Sonntag
    weekdays: List[int] = [0, 1, 2, 3, 4, 5, 6]
    min_gap_minutes: Optional[int] = None
    jitter_minutes: Optional[int] = None
    #: Nur Bilder mit mindestens einem dieser Tags verwenden.
    tags_any: List[str] = []
    #: True = bestehende Posts bleiben, es wird nur bis zur Zielmenge ergänzt.
    fill_gaps_only: bool = True
    generate_text: bool = True
    dry_run: bool = True


class PlanCapacity(BaseModel):
    """Reicht der zugeordnete Bildvorrat für den gewünschten Zeitraum?"""

    channel_id: uuid.UUID
    channel_name: str
    needed_images: int
    available_images: int
    enough: bool
    missing: int = 0


class PlanRangeResult(BaseModel):
    slots: List[PlannedSlot] = []
    created_post_ids: List[uuid.UUID] = []
    gaps: List[Dict[str, Any]] = []
    capacity: List[PlanCapacity] = []
    existing_in_range: int = 0
    #: Angelegt, aber ohne Text – die Generierung ist fehlgeschlagen.
    text_failed: List[uuid.UUID] = []


class PostBulkDelete(BaseModel):
    post_ids: List[uuid.UUID]


class PostBulkGenerate(BaseModel):
    post_ids: List[uuid.UUID]
    instruction: str = ""
    variants: int = 3


class PostDuplicate(BaseModel):
    scheduled_at: Optional[datetime] = None
    channel_id: Optional[uuid.UUID] = None
    #: Anzahl Kopien, jeweils um shift_days versetzt.
    copies: int = 1
    shift_days: int = 1


# --------------------------------------------------------------------------- #
# Inventar / Dashboard
# --------------------------------------------------------------------------- #
class ChannelInventory(BaseModel):
    channel_id: uuid.UUID
    channel_name: str
    platform: str
    color: str
    assigned_total: int
    available: int
    used: int
    scheduled: int
    consumption_per_day: float
    days_left: Optional[float]
    empty_on: Optional[datetime]
    traffic_light: str
    shares_pool_with: List[uuid.UUID] = []


class InventoryOverview(BaseModel):
    channels: List[ChannelInventory]
    unassigned_assets: int
    total_assets: int
    generated_at: datetime


class DashboardOut(BaseModel):
    inventory: InventoryOverview
    lifecycle_counts: LifecycleCounts
    upcoming_posts: List[PostOut]
    recent_failures: List[PostOut]
    channel_health: List[ChannelOut]
    notifications: List[Dict[str, Any]]
    dry_run: bool
    global_pause: bool


class MaintenanceReport(BaseModel):
    unassigned: int
    untagged: int
    archive_candidates: int
    near_duplicates: List[Dict[str, Any]]
    failed_assets: int
    orphan_set_items: int


class SavedViewIn(BaseModel):
    name: str
    icon: str = "bookmark"
    query: Dict[str, Any] = {}
    position: int = 0


class SavedViewOut(ORMModel, SavedViewIn):
    id: uuid.UUID
    is_system: bool
