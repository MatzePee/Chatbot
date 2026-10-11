"""FastAPI-Anwendung. Läuft auf Port 8001 (8000 ist auf dem Zielsystem belegt)."""
from __future__ import annotations

import logging
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text

from autoposter import __version__
from autoposter.adapters import AdapterError
from autoposter import scheduler
from autoposter.api import api_router
from autoposter.config import settings
from autoposter.db import SessionLocal, engine
from autoposter.models import AppSetting

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("autoposter")

BACKEND_DIR = Path(__file__).resolve().parent.parent

#: Die Oberfläche liegt in backend/ui und braucht keinen Build-Schritt –
#: Quelltext ist die Auslieferung. Die frühere React-Variante wurde entfernt;
#: UI_MODE wird nur noch entgegengenommen, damit alte .env-Dateien nicht
#: scheitern.
FRONTEND_DIR = Path(__file__).resolve().parent / "ui"

if settings.ui_mode == "react":
    logger.warning("UI_MODE=react wird nicht mehr unterstützt – es gilt backend/ui.")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings.ensure_dirs()

    # Im portablen Modus (SQLite) das Schema selbst anlegen, damit kein
    # separater Migrationslauf nötig ist.
    if settings.is_sqlite:
        from autoposter.db import Base
        import autoposter.models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # create_all legt nur fehlende TABELLEN an. Wächst das Modell um Spalten,
    # bleibt eine bestehende Datenbank sonst stumm veraltet und jede Abfrage
    # scheitert mit "no such column". Deshalb hier zusätzlich abgleichen.
    try:
        from autoposter import schema_sync

        report = await schema_sync.sync(engine)
        if report["added_columns"] or report["created_tables"]:
            logger.info("Schema angeglichen: %s", schema_sync.summarize(report))
            for entry in report["added_columns"]:
                logger.info("  + %s", entry)
        for entry in report["failed"]:
            logger.error(
                "Schema: %s konnte nicht ergänzt werden: %s", entry["column"], entry["error"]
            )
    except Exception as exc:
        logger.error("Schema-Abgleich fehlgeschlagen: %s", exc)

    async with SessionLocal() as db:
        try:
            row = (
                await db.execute(select(AppSetting).where(AppSetting.key == "runtime"))
            ).scalar_one_or_none()
            if row and isinstance(row.value, dict):
                settings.dry_run = bool(row.value.get("dry_run", settings.dry_run))
                settings.global_pause = bool(row.value.get("global_pause", settings.global_pause))
        except Exception as exc:  # Tabelle existiert evtl. noch nicht
            logger.warning("Laufzeit-Einstellungen nicht geladen: %s", exc)
    # Laufzeit-Einstellungen (Fanvue, X, OpenRouter) in den Speicher holen.
    async with SessionLocal() as db:
        try:
            from autoposter.services import appconfig

            await appconfig.refresh_snapshot(db)
        except Exception as exc:
            logger.warning("Integrations-Einstellungen nicht geladen: %s", exc)

    if not (Path(settings.data_dir) / '.creatorpilot-initialized').exists():
        settings.dry_run = settings.global_pause = True
    import os
    from app import deployment
    if os.environ.get('MP_PREVIEW') == '1' or deployment.pending():
        settings.scheduler_mode = 'off'
        settings.dry_run = settings.global_pause = True
    await scheduler.start_if_enabled()

    logger.info(
        "AutoPost %s initialisiert (Port-Konfiguration %s, dry_run=%s, db=%s, scheduler=%s)",
        __version__,
        settings.api_port,
        settings.dry_run,
        "sqlite" if settings.is_sqlite else "postgresql",
        settings.scheduler_mode,
    )
    yield

    await scheduler.stop_if_running()
    await engine.dispose()


app = FastAPI(
    title="AutoPost",
    version=__version__,
    description="Multi-Channel Content-Automation für X und Fanvue",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AdapterError)
async def adapter_error_handler(request: Request, exc: AdapterError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status or 502,
        content={"detail": str(exc), "retryable": exc.retryable, "needs_reauth": exc.needs_reauth},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Unerwartete Fehler mit Klartext beantworten statt mit 'Internal Server Error'.

    Die Anwendung läuft im internen Netz; eine aussagekräftige Meldung in der
    Oberfläche ist hier deutlich mehr wert als das Verbergen von Details. Der
    vollständige Traceback landet zusätzlich im Log.
    """
    logger.exception("Unbehandelter Fehler bei %s %s", request.method, request.url.path)
    detail = f"{type(exc).__name__}: {exc}"
    return JSONResponse(
        status_code=500,
        content={
            "detail": detail[:800],
            "path": request.url.path,
            "traceback": traceback.format_exc().splitlines()[-12:] if settings.debug else None,
        },
    )


app.include_router(api_router)


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict:
    return {
        "status": "ok",
        "version": __version__,
        "dry_run": settings.dry_run,
        "database": "sqlite" if settings.is_sqlite else "postgresql",
        "scheduler": settings.scheduler_mode,
    }


@app.get("/readyz", tags=["ops"])
async def readyz() -> JSONResponse:
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return JSONResponse({"status": "ready"})
    except Exception as exc:
        return JSONResponse({"status": "not-ready", "detail": str(exc)}, status_code=503)


try:  # optional
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
except Exception:  # pragma: no cover
    pass


# --------------------------------------------------------------------------- #
# Frontend aus demselben Port ausliefern
# --------------------------------------------------------------------------- #
if (FRONTEND_DIR / "index.html").exists():
    logger.info("Oberfläche: %s", FRONTEND_DIR.name)

    if (FRONTEND_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")
    if (FRONTEND_DIR / "js").is_dir():
        app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> Response:
        # API-Pfade dürfen hier nicht landen. Täte die Oberfläche das doch,
        # bekäme sie HTML statt JSON und meldete irgendeinen Folgefehler.
        # Ein klares 404 sagt stattdessen, was wirklich los ist.
        if full_path.startswith("api/"):
            return JSONResponse(
                {
                    "detail": (
                        "Unbekannte API-Route /{path}. Läuft der Server noch mit einem "
                        "älteren Stand? Dann hilft ein Neustart über ./run.sh start."
                    ).format(path=full_path),
                    "path": "/" + full_path,
                },
                status_code=404,
            )
        # Kein Ausbruch aus dem Oberflächen-Verzeichnis über '../'.
        candidate = (FRONTEND_DIR / full_path).resolve()
        if full_path and candidate.is_file() and FRONTEND_DIR.resolve() in candidate.parents:
            return FileResponse(candidate)
        # Die Oberfläche wird häufig geändert – der Browser soll sie nicht cachen.
        return FileResponse(
            FRONTEND_DIR / "index.html",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
else:  # pragma: no cover
    logger.warning("Keine Oberfläche gefunden – nur die API ist erreichbar.")
