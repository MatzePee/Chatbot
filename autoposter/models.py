"""Vollständiges Domänenmodell von AutoPoster."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from autoposter.db import Base

# --------------------------------------------------------------------------- #
# Portable Spaltentypen (Postgres in Produktion, SQLite in Tests)
# --------------------------------------------------------------------------- #
#: Dialekt-unabhängiger UUID-Typ.
#: WICHTIG: hier NICHT PGUUID().with_variant(String(36)) verwenden – der Variantentyp
#: tauscht zwar die Spalte, kann aber keine Python-UUID-Objekte binden und scheitert
#: auf SQLite mit "type 'UUID' is not supported". SQLAlchemys eigener Uuid-Typ
#: erledigt die Umwandlung in beide Richtungen: nativ auf PostgreSQL, CHAR(32) sonst.
GUID = Uuid(as_uuid=True)
JSONB_ = JSONB().with_variant(JSON(), "sqlite")
#: Listen werden bewusst als JSONB (nicht als Postgres-ARRAY) gespeichert. So ist die
#: Textdarstellung auf Postgres und SQLite identisch ('["a", "b"]'), wodurch dieselbe
#: Filterlogik auf beiden Dialekten funktioniert — und JSONB lässt sich per GIN
#: (jsonb_path_ops) genauso schnell indizieren wie ein Array.
StrArray = JSONB().with_variant(JSON(), "sqlite")
UuidArray = JSONB().with_variant(JSON(), "sqlite")


class UTCDateTime(TypeDecorator):
    """Zeitstempel, die IMMER mit UTC-Zeitzone zurückkommen.

    SQLite kennt keine Zeitzonen: DateTime(timezone=True) speichert dort nur
    einen Text und liefert beim Lesen einen NAIVEN datetime zurück. Vergleicht
    man den mit datetime.now(timezone.utc), wirft Python
    "can't compare offset-naive and offset-aware datetimes".

    Dieser Typ macht Schluss damit: beim Schreiben wird nach UTC normalisiert,
    beim Lesen die UTC-Zeitzone wieder angeheftet. Auf PostgreSQL ändert sich
    nichts – dort ist der Wert ohnehin schon behaftet.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            # Naive Eingaben gelten als UTC – so wie überall sonst im Code.
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, server_default=func.now()
    )


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Role(str, enum.Enum):
    admin = "admin"
    editor = "editor"
    viewer = "viewer"


class Platform(str, enum.Enum):
    x = "x"
    fanvue = "fanvue"


class NsfwLevel(str, enum.Enum):
    sfw = "sfw"
    suggestive = "suggestive"
    explicit = "explicit"

    @property
    def rank(self) -> int:
        return {"sfw": 0, "suggestive": 1, "explicit": 2}[self.value]


class MediaStatus(str, enum.Enum):
    processing = "processing"
    ready = "ready"
    failed = "failed"
    archived = "archived"


class Lifecycle(str, enum.Enum):
    """Lebenszyklus eines Bildes — zentral für die Übersicht bei großen Beständen."""

    new = "new"                        # hochgeladen, keinem Kanal zugeordnet
    assigned = "assigned"              # zugeordnet, aber nicht eingeplant
    scheduled = "scheduled"            # in mind. einem geplanten Post
    partially_used = "partially_used"  # auf einigen zugeordneten Kanälen veröffentlicht
    fully_used = "fully_used"          # auf allen zugeordneten Kanälen veröffentlicht
    archived = "archived"


class PostType(str, enum.Enum):
    image_single = "image_single"
    image_set = "image_set"
    text_only = "text_only"


class Audience(str, enum.Enum):
    """Fanvue unterscheidet zwei Sichtbarkeiten.

    free       – für alle Follower sichtbar  (API: followers-and-subscribers)
    subscribers – nur für Abonnenten          (API: subscribers)
    """

    free = "followers-and-subscribers"
    subscribers = "subscribers"

    @property
    def label(self) -> str:
        return "Free-Post" if self.value == "followers-and-subscribers" else "Sub-Post"


AUDIENCE_LABELS = {
    "followers-and-subscribers": "Free-Post (alle Follower)",
    "subscribers": "Sub-Post (nur Abonnenten)",
}


class PostStatus(str, enum.Enum):
    draft = "draft"
    needs_review = "needs_review"
    approved = "approved"
    scheduled = "scheduled"
    publishing = "publishing"
    published = "published"
    failed = "failed"
    cancelled = "cancelled"
    skipped = "skipped"


class ChannelHealth(str, enum.Enum):
    ok = "ok"
    needs_reauth = "needs_reauth"
    paused = "paused"
    error = "error"


class AssignmentSource(str, enum.Enum):
    manual = "manual"
    rule = "rule"
    inherited = "inherited"


# --------------------------------------------------------------------------- #
# Benutzer & Persona
# --------------------------------------------------------------------------- #
class User(Base, TimestampMixin):
    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(20), default=Role.editor.value)
    totp_secret: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class Persona(Base, TimestampMixin):
    __tablename__ = "persona"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    bio: Mapped[str] = mapped_column(Text, default="")
    tone_guidelines: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    #: Wochenplan in Textform – siehe app/services/rhythm.py. Sorgt dafür, dass
    #: ein Post um 03:00 nicht behauptet, die Persona sei im Gym.
    daily_rhythm: Mapped[str] = mapped_column(Text, default="")
    forbidden_topics: Mapped[List[str]] = mapped_column(StrArray, default=list)
    emoji_policy: Mapped[str] = mapped_column(String(20), default="sparse")
    language: Mapped[str] = mapped_column(String(10), default="en")
    hashtag_pool: Mapped[List[str]] = mapped_column(StrArray, default=list)
    cta_pool: Mapped[List[str]] = mapped_column(StrArray, default=list)
    model_override: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.9)
    avatar_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)

    channels: Mapped[List["Channel"]] = relationship(
        back_populates="persona", lazy="selectin"
    )
    examples: Mapped[List["PersonaExample"]] = relationship(
        back_populates="persona", cascade="all, delete-orphan", lazy="selectin"
    )


class PersonaExample(Base, TimestampMixin):
    """Bewertete Generierungen — die besten fließen als Few-Shot zurück in den Prompt."""

    __tablename__ = "persona_example"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    persona_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("persona.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(String(20), default=Platform.x.value)
    image_description: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text)
    rating: Mapped[int] = mapped_column(Integer, default=0)  # +1 / -1

    persona: Mapped[Persona] = relationship(back_populates="examples", lazy="selectin")


# --------------------------------------------------------------------------- #
# Kanäle
# --------------------------------------------------------------------------- #
class PostingPolicy(Base, TimestampMixin):
    __tablename__ = "posting_policy"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    posts_per_day: Mapped[float] = mapped_column(Float, default=2.0)
    min_gap_minutes: Mapped[int] = mapped_column(Integer, default=180)
    jitter_minutes: Mapped[int] = mapped_column(Integer, default=12)
    max_images_per_post: Mapped[int] = mapped_column(Integer, default=4)
    text_only_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    #: Allgemeines Zeitfenster. Gilt, wenn kein typspezifisches Fenster gesetzt ist.
    #: [{"dow": [0,1,2,3,4,5,6], "from": "09:00", "to": "22:00"}]
    allowed_time_windows: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSONB_,
        default=lambda: [{"dow": [0, 1, 2, 3, 4, 5, 6], "from": "09:00", "to": "22:00"}],
    )
    #: Eigene Fenster für Posts MIT Bild. Leer = allowed_time_windows verwenden.
    image_time_windows: Mapped[List[Dict[str, Any]]] = mapped_column(JSONB_, default=list)
    #: Eigene Fenster für reine TEXTposts. Leer = allowed_time_windows verwenden.
    text_time_windows: Mapped[List[Dict[str, Any]]] = mapped_column(JSONB_, default=list)
    #: Fester Versatz dieses Kanals in Minuten, damit mehrere Kanäle nicht zur
    #: selben Uhrzeit posten.
    stagger_minutes: Mapped[int] = mapped_column(Integer, default=0)
    #: Anteil der Fanvue-Posts, die als Free-Post (für alle Follower sichtbar)
    #: geplant werden. 0 = alles nur für Abonnenten, 1 = alles frei.
    free_post_ratio: Mapped[float] = mapped_column(Float, default=0.3)
    #: 0 = niemals wiederverwenden. Gilt ausschließlich innerhalb DIESES Kanals.
    reuse_cooldown_days: Mapped[int] = mapped_column(Integer, default=0)
    #: Ähnlichkeitsschwelle (Hamming-Distanz phash) gegen die Historie desselben Kanals.
    phash_min_distance: Mapped[int] = mapped_column(Integer, default=6)
    phash_lookback_posts: Mapped[int] = mapped_column(Integer, default=20)
    daily_api_quota: Mapped[int] = mapped_column(Integer, default=50)
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=False)
    #: "together" = ein Set wird zu einem Post, "single" = jedes Bild einzeln in
    #: Set-Reihenfolge. Leer bedeutet "nach Plattform": Fanvue lebt von
    #: Bildstrecken, auf X wirken einzelne Bilder besser. Dadurch verhalten sich
    #: auch bestehende Kanäle ohne Migration sofort sinnvoll.
    set_handling: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    auto_retry_on_fail: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_recycling: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Wenn True, konkurriert dieser Kanal mit seiner Gruppe um dieselben Bilder.
    exclusive_pool_group: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)


class ChannelCredential(Base, TimestampMixin):
    __tablename__ = "channel_credential"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    platform: Mapped[str] = mapped_column(String(20))
    access_token_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    refresh_token_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    scopes: Mapped[List[str]] = mapped_column(StrArray, default=list)
    external_user_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    raw_meta: Mapped[Dict[str, Any]] = mapped_column(JSONB_, default=dict)


class Channel(Base, TimestampMixin):
    __tablename__ = "channel"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    handle: Mapped[str] = mapped_column(String(120), default="")
    color: Mapped[str] = mapped_column(String(9), default="#6366f1")
    persona_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("persona.id", ondelete="SET NULL"), nullable=True
    )
    credential_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("channel_credential.id", ondelete="SET NULL"), nullable=True
    )

    # --- Eigene OAuth-App je Kanal ------------------------------------------
    # Bei X gehört zu jeder API-App genau EIN Account. Wer mehrere X-Profile
    # betreibt, braucht deshalb pro Kanal eigene Zugangsdaten. Bleiben die
    # Felder leer, gelten die globalen aus den Einstellungen.
    oauth_client_id: Mapped[str] = mapped_column(String(200), default="")
    oauth_client_secret_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: Notizfeld, um App und Account im Entwicklerportal zuordnen zu können.
    oauth_app_name: Mapped[str] = mapped_column(String(200), default="")
    #: Überschreibt PUBLIC_BASE_URL nur für die Redirect-URI dieses Kanals.
    #: Nötig, weil die Basis-URL zwei Aufgaben hat, die auseinanderfallen können:
    #: Sie beschreibt, wie der Server im Netz erreichbar ist – die Redirect-URI
    #: dagegen muss zu dem passen, was im Portal der Plattform steht, und das
    #: ist beim Verbinden oft der Rechner vor dem Bildschirm (localhost).
    oauth_redirect_base: Mapped[str] = mapped_column(String(255), default="")
    #: Ergebnis des letzten Verbindungstests.
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    verified_account: Mapped[str] = mapped_column(String(200), default="")

    # ---- Wasserzeichen -----------------------------------------------------
    #: PNG mit Transparenz, relativ zum Medienverzeichnis. Wird erst beim
    #: Veröffentlichen aufs Bild gerechnet, nie in die Originaldatei geschrieben.
    watermark_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    watermark_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Einer von neun Ankerpunkten, Standard links unten.
    watermark_anchor: Mapped[str] = mapped_column(String(20), default="bottom-left")
    #: Breite in Prozent der Bildbreite – dadurch passt es auf jedes Format.
    watermark_scale_pct: Mapped[float] = mapped_column(Float, default=18.0)
    #: Randabstände in Prozent der kürzeren Bildseite.
    watermark_margin_x_pct: Mapped[float] = mapped_column(Float, default=3.0)
    watermark_margin_y_pct: Mapped[float] = mapped_column(Float, default=3.0)
    watermark_opacity: Mapped[float] = mapped_column(Float, default=1.0)

    policy_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("posting_policy.id", ondelete="SET NULL"), nullable=True
    )
    timezone: Mapped[str] = mapped_column(String(60), default="Europe/Berlin")
    nsfw_level: Mapped[str] = mapped_column(String(20), default=NsfwLevel.suggestive.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    health: Mapped[str] = mapped_column(String(20), default=ChannelHealth.ok.value)
    health_note: Mapped[str] = mapped_column(Text, default="")
    #: Fanvue: Planung an die Plattform übergeben (publishAt) statt lokal halten.
    platform_side_scheduling: Mapped[bool] = mapped_column(Boolean, default=False)
    default_audience: Mapped[str] = mapped_column(String(40), default="subscribers")
    last_published_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    quota_used_today: Mapped[int] = mapped_column(Integer, default=0)
    quota_reset_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )

#: lazy="selectin": Im async-Betrieb darf NICHT nachgeladen werden – ein
#: Zugriff auf eine lazy geladene Beziehung wirft MissingGreenlet.
    persona: Mapped[Optional[Persona]] = relationship(
        back_populates="channels", lazy="selectin"
    )
    credential: Mapped[Optional[ChannelCredential]] = relationship(lazy="selectin")
    policy: Mapped[Optional[PostingPolicy]] = relationship(lazy="selectin")


# --------------------------------------------------------------------------- #
# Medien
# --------------------------------------------------------------------------- #
class MediaAsset(Base, TimestampMixin):
    __tablename__ = "media_asset"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    phash: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    mime: Mapped[str] = mapped_column(String(60), default="image/jpeg")
    exif_stripped: Mapped[bool] = mapped_column(Boolean, default=False)
    nsfw_level: Mapped[str] = mapped_column(String(20), default=NsfwLevel.sfw.value, index=True)
    tags: Mapped[List[str]] = mapped_column(StrArray, default=list)
    caption_hint: Mapped[str] = mapped_column(Text, default="")
    ai_description: Mapped[str] = mapped_column(Text, default="")
    source_note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=MediaStatus.processing.value, index=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)

    # --- denormalisierte Felder für die Übersicht bei großen Beständen -------
    lifecycle: Mapped[str] = mapped_column(String(20), default=Lifecycle.new.value, index=True)
    assigned_channel_ids: Mapped[List[str]] = mapped_column(UuidArray, default=list)
    used_channel_ids: Mapped[List[str]] = mapped_column(UuidArray, default=list)
    scheduled_channel_ids: Mapped[List[str]] = mapped_column(UuidArray, default=list)
    usage_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    first_used_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    archived_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        Index("ix_media_lifecycle_created", "lifecycle", "created_at"),
        Index("ix_media_status_lifecycle", "status", "lifecycle"),
    )


class MediaSet(Base, TimestampMixin):
    __tablename__ = "media_set"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    nsfw_level: Mapped[str] = mapped_column(String(20), default=NsfwLevel.sfw.value)
    tags: Mapped[List[str]] = mapped_column(StrArray, default=list)
    is_ordered: Mapped[bool] = mapped_column(Boolean, default=True)
    cover_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)
    preview_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)

    items: Mapped[List["MediaSetItem"]] = relationship(
        back_populates="media_set", cascade="all, delete-orphan",
        order_by="MediaSetItem.position", lazy="selectin",
    )


class MediaSetItem(Base):
    __tablename__ = "media_set_item"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    media_set_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("media_set.id", ondelete="CASCADE"), index=True
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("media_asset.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)

    media_set: Mapped[MediaSet] = relationship(back_populates="items", lazy="selectin")
    asset: Mapped[MediaAsset] = relationship(lazy="selectin")

    __table_args__ = (UniqueConstraint("media_set_id", "media_asset_id", name="uq_set_asset"),)


class MediaAssignment(Base, TimestampMixin):
    """Vom Nutzer per Drag & Drop gesetzte Zuordnung Bild/Set -> Kanal.

    Ein Asset darf beliebig vielen Kanälen zugeordnet sein — plattformübergreifende
    Mehrfachverwendung ist ausdrücklich der Normalfall.
    """

    __tablename__ = "media_assignment"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    media_asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("media_asset.id", ondelete="CASCADE"), nullable=True, index=True
    )
    media_set_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("media_set.id", ondelete="CASCADE"), nullable=True, index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("channel.id", ondelete="CASCADE"), index=True
    )
    assigned_by: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default=AssignmentSource.manual.value)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (
        UniqueConstraint("media_asset_id", "channel_id", name="uq_asset_channel"),
        UniqueConstraint("media_set_id", "channel_id", name="uq_set_channel"),
        Index("ix_assignment_channel_priority", "channel_id", "priority"),
    )


class MediaChannelUsage(Base):
    """Verwendungs-Historie. Duplikat-Prüfung läuft AUSSCHLIESSLICH gegen diese Tabelle
    gefiltert nach channel_id — nie bestandsweit."""

    __tablename__ = "media_channel_usage"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("media_asset.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("channel.id", ondelete="CASCADE"), index=True
    )
    post_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)
    used_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    __table_args__ = (Index("ix_usage_asset_channel", "media_asset_id", "channel_id"),)


class AssignmentRule(Base, TimestampMixin):
    __tablename__ = "assignment_rule"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("channel.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    #: {"tags_any": [...], "tags_all": [...], "nsfw_max": "suggestive", "set_id": "..."}
    match: Mapped[Dict[str, Any]] = mapped_column(JSONB_, default=dict)
    mode: Mapped[str] = mapped_column(String(20), default="suggest")  # suggest | auto_assign
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SavedView(Base, TimestampMixin):
    """Smart Collection: gespeicherte Filterkombination für die Bibliothek."""

    __tablename__ = "saved_view"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200))
    icon: Mapped[str] = mapped_column(String(40), default="bookmark")
    query: Mapped[Dict[str, Any]] = mapped_column(JSONB_, default=dict)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)


# --------------------------------------------------------------------------- #
# Posts
# --------------------------------------------------------------------------- #
class ContentPlan(Base, TimestampMixin):
    __tablename__ = "content_plan"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("channel.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), default="Standardplan")
    horizon_days: Mapped[int] = mapped_column(Integer, default=14)
    #: {"0": "Gym", "4": "Q&A"}  (0 = Montag)
    themes: Mapped[Dict[str, Any]] = mapped_column(JSONB_, default=dict)
    text_only_templates: Mapped[List[str]] = mapped_column(StrArray, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Post(Base, TimestampMixin):
    __tablename__ = "post"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("channel.id", ondelete="CASCADE"), index=True
    )
    persona_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)
    type: Mapped[str] = mapped_column(String(20), default=PostType.image_single.value)
    status: Mapped[str] = mapped_column(String(20), default=PostStatus.draft.value, index=True)

    scheduled_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )

    body_text: Mapped[str] = mapped_column(Text, default="")
    body_text_variants: Mapped[List[Dict[str, Any]]] = mapped_column(JSONB_, default=list)
    hashtags: Mapped[List[str]] = mapped_column(StrArray, default=list)
    #: {"<asset_id>": "Alt-Text"}
    alt_texts: Mapped[Dict[str, str]] = mapped_column(JSONB_, default=dict)

    media_asset_ids: Mapped[List[str]] = mapped_column(UuidArray, default=list)
    media_set_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)

    # Fanvue
    audience: Mapped[str] = mapped_column(String(40), default="subscribers")
    price_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    media_preview_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    pin_after_publish: Mapped[bool] = mapped_column(Boolean, default=False)

    # X
    reply_settings: Mapped[str] = mapped_column(String(40), default="everyone")
    thread_parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("post.id", ondelete="CASCADE"), nullable=True
    )
    thread_position: Mapped[int] = mapped_column(Integer, default=0)
    quote_of_url: Mapped[str] = mapped_column(String(500), default="")

    external_post_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    external_url: Mapped[str] = mapped_column(String(500), default="")
    generation_meta: Mapped[Dict[str, Any]] = mapped_column(JSONB_, default=dict)
    metrics: Mapped[Dict[str, Any]] = mapped_column(JSONB_, default=dict)

    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True, default=lambda: uuid.uuid4().hex)
    error_message: Mapped[str] = mapped_column(Text, default="")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)

    channel: Mapped[Channel] = relationship(lazy="selectin")

    __table_args__ = (Index("ix_post_due", "status", "scheduled_at"),)


class XImageComment(Base):
    __tablename__ = "x_image_comment"
    post_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("post.id", ondelete="CASCADE"), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    external_id: Mapped[str] = mapped_column(String(100), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    attempted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class PostRevision(Base):
    __tablename__ = "post_revision"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    post_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("post.id", ondelete="CASCADE"), index=True
    )
    editor_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)
    diff: Mapped[Dict[str, Any]] = mapped_column(JSONB_, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PublishAttempt(Base):
    __tablename__ = "publish_attempt"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    post_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("post.id", ondelete="CASCADE"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str] = mapped_column(String(120), default="")
    response_excerpt: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(String(40), default="pending")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)


class ExternalMediaRef(Base):
    """Gecachte Plattform-Media-ID, damit ein Bild pro Kanal nur einmal hochgeladen wird."""

    __tablename__ = "external_media_ref"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("media_asset.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("channel.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(200))
    expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("media_asset_id", "channel_id", name="uq_extmedia"),)


class BlackoutPeriod(Base, TimestampMixin):
    __tablename__ = "blackout_period"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    channel_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("channel.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), default="")
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)


# --------------------------------------------------------------------------- #
# Betrieb
# --------------------------------------------------------------------------- #
class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity: Mapped[str] = mapped_column(String(80), default="")
    entity_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB_, default=dict)
    ip: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class AppSetting(Base, TimestampMixin):
    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[Dict[str, Any]] = mapped_column(JSONB_, default=dict)


class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    level: Mapped[str] = mapped_column(String(20), default="info")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    entity: Mapped[str] = mapped_column(String(80), default="")
    entity_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class LlmUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    model: Mapped[str] = mapped_column(String(120))
    task: Mapped[str] = mapped_column(String(40))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class OAuthState(Base):
    """PKCE-State, kurzlebig."""

    __tablename__ = "oauth_state"

    state: Mapped[str] = mapped_column(String(80), primary_key=True)
    platform: Mapped[str] = mapped_column(String(20))
    code_verifier: Mapped[str] = mapped_column(String(200))
    channel_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
