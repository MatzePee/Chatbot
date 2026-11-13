"""Zentrale Konfiguration. Alles über Umgebungsvariablen / .env."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, List, Literal, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: Listenfelder aus der .env.
#: pydantic-settings versucht bei List-/Dict-Feldern zuerst json.loads und wirft
#: einen SettingsError, BEVOR ein eigener Validator laufen kann. NoDecode schaltet
#: das ab, sodass unten sowohl "a,b,c" als auch '["a","b"]' akzeptiert werden.
CsvList = Annotated[List[str], NoDecode]
CsvIntList = Annotated[List[int], NoDecode]

#: backend/app/config.py  ->  backend/app  ->  backend  ->  Projektwurzel
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_path(value: object) -> Path:
    """Relative Pfade beziehen sich immer auf die Projektwurzel, nie auf das
    aktuelle Arbeitsverzeichnis. Sonst landen Datenbank und Bilder je nach
    Aufrufort in unterschiedlichen Ordnern."""
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Absoluter Pfad: run.sh startet aus backend/, die .env liegt aber eine
        # Ebene höher. Der zweite Eintrag erlaubt zusätzlich eine lokale .env.
        env_file=PROJECT_ROOT / "data" / "autoposter.env",
        env_prefix="MP_AUTOPOSTER_",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Allgemein -----------------------------------------------------------
    app_name: str = "AutoPost"
    environment: Literal["dev", "prod"] = "dev"
    debug: bool = False
    tz: str = "Europe/Berlin"

    #: Öffentlich erreichbare Basis-URL. Wird für OAuth-Redirects gebraucht.
    public_base_url: str = "http://localhost:8000"

    #: Der Dienst lauscht auf 8001, da 8000 auf dem Zielsystem belegt ist.
    api_host: str = "0.0.0.0"
    api_port: int = 8001

    cors_origins: CsvList = Field(default_factory=lambda: ["http://localhost:5173"])

    #: Es gibt nur noch die build-freie Oberfläche in backend/ui. Das Feld
    #: bleibt bestehen, damit ältere .env-Dateien mit UI_MODE=react oder =auto
    #: nicht beim Start scheitern – gültig ist in jedem Fall backend/ui.
    ui_mode: Literal["auto", "builtin", "react"] = "builtin"

    # --- Sicherheit ----------------------------------------------------------
    #: Anmeldung. Im internen Netz nicht nötig – dann arbeitet die Anwendung
    #: unter einem Systembenutzer mit Administratorrechten.
    #: WICHTIG: Nur auf false lassen, solange der Port nicht öffentlich erreichbar
    #: ist. Wer die Oberfläche ins Internet stellt, setzt AUTH_ENABLED=true.
    auth_enabled: bool = False
    system_user_email: str = "system@local"

    secret_key: str = "change-me-secret-key"
    #: Fernet-Key (44 Zeichen base64). `make genkeys` erzeugt einen.
    encryption_key: str = ""
    access_token_ttl_minutes: int = 60
    refresh_token_ttl_days: int = 30
    cookie_secure: bool = False
    cookie_name: str = "autoposter_session"

    # --- Datenbank / Queue ---------------------------------------------------
    #: Standard ist SQLite – dadurch läuft die App ohne externe Dienste und der
    #: gesamte Ordner ist zwischen Mac und Ubuntu kopierbar.
    #: Für größere Installationen stattdessen z. B.:
    #:   postgresql+asyncpg://autoposter:pass@localhost:5432/autoposter
    database_url: str = "sqlite+aiosqlite:///./data/autoposter/autoposter.db"

    #: Nur nötig, wenn SCHEDULER_MODE=celery verwendet wird.
    redis_url: str = "redis://localhost:6379/0"

    #: inprocess = Hintergrundjobs laufen im API-Prozess (kein Redis nötig)
    #: celery    = klassisch mit Worker und Beat (für größere Installationen)
    #: off       = keine automatischen Jobs, nur manuelle Auslösung im UI
    scheduler_mode: Literal["inprocess", "celery", "off"] = "inprocess"

    # --- Medien --------------------------------------------------------------
    #: Relativ zum Arbeitsverzeichnis, damit der Ordner portabel bleibt.
    data_dir: Path = Path("./data/autoposter")
    media_root: Path = Path("./data/autoposter/media")
    max_upload_mb: int = 60
    allowed_mimes: CsvList = Field(
        default_factory=lambda: ["image/jpeg", "image/png", "image/webp"]
    )
    thumbnail_sizes: CsvIntList = Field(default_factory=lambda: [256, 640, 1280])

    # --- OpenRouter ----------------------------------------------------------
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model_caption: str = "anthropic/claude-sonnet-4"
    openrouter_model_text: str = "anthropic/claude-sonnet-4"
    openrouter_model_vision: str = "anthropic/claude-sonnet-4"
    openrouter_model_fallback: str = "openai/gpt-4o-mini"
    openrouter_monthly_budget_usd: float = 25.0
    llm_timeout_seconds: int = 90

    # --- X / Twitter ---------------------------------------------------------
    x_client_id: str = ""
    x_client_secret: str = ""
    x_api_tier: Literal["free", "basic", "pro"] = "basic"
    x_api_base: str = "https://api.x.com"
    x_oauth_authorize_url: str = "https://x.com/i/oauth2/authorize"
    x_oauth_token_url: str = "https://api.x.com/2/oauth2/token"

    # --- Fanvue --------------------------------------------------------------
    fanvue_client_id: str = ""
    fanvue_client_secret: str = ""
    fanvue_api_base: str = "https://api.fanvue.com"
    fanvue_api_version: str = "2025-06-26"
    fanvue_oauth_authorize_url: str = "https://auth.fanvue.com/oauth2/auth"
    fanvue_oauth_token_url: str = "https://auth.fanvue.com/oauth2/token"

    # --- Betrieb -------------------------------------------------------------
    #: Globaler Trockenlauf: es wird alles gerechnet, aber nichts veröffentlicht.
    dry_run: bool = True
    #: Kill-Switch. Wenn True, wird nichts publiziert und nichts geplant.
    global_pause: bool = False
    inventory_warn_days: int = 14

    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None
    notify_email_to: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    # --- Seed ----------------------------------------------------------------
    admin_email: str = "admin@example.com"
    admin_password: str = "changeme123"

    @staticmethod
    def _parse_list(value: object) -> object:
        """Akzeptiert beide Schreibweisen in der .env:
        FOO=a,b,c        (bequem)
        FOO=["a","b"]    (JSON, falls Werte Kommas enthalten)"""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        return [item.strip() for item in text.split(",") if item.strip()]

    @field_validator("cors_origins", "allowed_mimes", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        return cls._parse_list(v)

    @field_validator("thumbnail_sizes", mode="before")
    @classmethod
    def _split_int_csv(cls, v: object) -> object:
        parsed = cls._parse_list(v)
        if isinstance(parsed, list):
            return [int(str(item).strip()) for item in parsed if str(item).strip()]
        return parsed

    @field_validator("media_root", "data_dir", mode="after")
    @classmethod
    def _absolute_paths(cls, v: Path) -> Path:
        return resolve_path(v)

    @model_validator(mode="after")
    def _absolute_sqlite_path(self) -> "Settings":
        """Auch die SQLite-Datei an die Projektwurzel binden."""
        prefix = "sqlite+aiosqlite:///"
        if self.database_url.startswith(prefix):
            raw = self.database_url[len(prefix):]
            if raw and raw != ":memory:" and not raw.startswith("/"):
                object.__setattr__(
                    self, "database_url", prefix + str(resolve_path(raw))
                )
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sync_database_url(self) -> str:
        """Sync-DSN für Alembic."""
        return self.database_url.replace("+asyncpg", "+psycopg").replace("+aiosqlite", "")

    def ensure_dirs(self) -> None:
        """Legt Daten- und Medienverzeichnis an. Für den portablen Betrieb wichtig,
        weil SQLite die Datei sonst nicht anlegen kann."""
        for label, directory in (("DATA_DIR", self.data_dir), ("MEDIA_ROOT", self.media_root)):
            try:
                Path(directory).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise RuntimeError(
                    f"{label} ist nicht nutzbar: {directory} ({exc.strerror}).\n"
                    f"Häufigste Ursache: In der .env steht noch ein absoluter Pfad aus dem "
                    f"Docker-Betrieb, etwa MEDIA_ROOT=/data/media. Für den nativen Betrieb "
                    f"bitte auf einen relativen Pfad ändern, z. B. MEDIA_ROOT=./data/media."
                ) from exc


@lru_cache
def get_settings() -> Settings:
    from .bootstrap import ensure_config
    ensure_config(PROJECT_ROOT)
    return Settings()


settings = get_settings()
