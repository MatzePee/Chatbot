"""Additive integration; legacy routes, database and OAuth stay authoritative."""
import os
import subprocess
from pathlib import Path


def install_content_studio(app):
    from . import deployment
    from autoposter.main import app as studio, lifespan
    from autoposter.api import api_router
    from autoposter.config import settings

    @app.middleware('http')
    async def fresh_frontend(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith('/autoposter/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    app.include_router(api_router)
    app.exception_handlers.update(studio.exception_handlers)
    app.mount('/autoposter', studio, name='autoposter')

    @app.get('/api/creatorpilot/health')
    def health():
        from . import poller
        return {'application': 'MP CreatorStudio', 'status': 'ok', 'preview': os.environ.get('MP_PREVIEW') == '1',
                'preview_policy': 'read-and-draft', 'deployment_pending': deployment.pending(),
                'revision': revision, 'autochat_worker': bool(poller.status().get('alive'))}

    try:
        revision = subprocess.check_output(['git', '-C', str(deployment.ROOT), 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, text=True, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        revision = ''

    @app.get('/api/deployment-status')
    def deployment_status():
        return deployment.status()

    @app.on_event('startup')
    async def start_studio():
        if os.environ.get('MP_PREVIEW') == '1' or deployment.pending():
            settings.scheduler_mode = 'off'
            settings.dry_run = True
            settings.global_pause = True
        # First install is deliberately paused, including previously stored runtime.
        marker = Path(settings.data_dir) / '.creatorpilot-initialized'
        if not marker.exists():
            settings.global_pause = True
            settings.dry_run = True
        app.state.studio_lifespan = lifespan(studio)
        await app.state.studio_lifespan.__aenter__()
        if not marker.exists():
            from autoposter.db import SessionLocal
            from autoposter.models import AppSetting
            from sqlalchemy import select
            async with SessionLocal() as db:
                row = (await db.execute(select(AppSetting).where(AppSetting.key == 'runtime'))).scalar_one_or_none()
                value = dict(row.value or {}) if row else {}
                value.update(dry_run=True, global_pause=True)
                if row: row.value = value
                else: db.add(AppSetting(key='runtime', value=value))
                await db.commit()
            settings.dry_run = settings.global_pause = True
            marker.touch()
        if deployment.pending():
            return
        if os.environ.get('MP_PREVIEW') == '1':
            from .preview import start_shadow_worker
            start_shadow_worker()
        else:
            from . import poller
            poller.start()

    @app.on_event('shutdown')
    async def stop_studio():
        from .preview import stop_shadow_worker
        stop_shadow_worker()
        await app.state.studio_lifespan.__aexit__(None, None, None)
        from . import poller
        if os.environ.get('MP_PREVIEW') != '1':
            poller.stop()
