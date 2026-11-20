"""Opt-in per-tab character context for both application areas.

No cookie/localStorage selection: every content URL must name its owner. The
feature remains disabled on existing installations until an offline migration.
"""
import os
from uuid import UUID
from urllib.parse import parse_qs
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from autoposter.workspaces import character_context, control_context


def install(app):
    from fastapi.responses import HTMLResponse
    app.add_middleware(CharacterMiddleware)

    @app.get('/characters',response_class=HTMLResponse,include_in_schema=False)
    async def chooser():
        from pathlib import Path
        return (Path(__file__).parent / 'templates' / 'characters.html').read_text(encoding='utf-8')



class CharacterMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or os.environ.get('MP_CHARACTERS_ENABLED') != '1':
            return await self.app(scope, receive, send)
        path = scope.get('path','')
        if path == '/autoposter/app.css' or path.startswith(('/static/', '/autoposter/js/', '/js/', '/assets/')):
            return await self.app(scope, receive, send)
        from autoposter import db
        from autoposter.config import settings
        from autoposter.models import Character, User
        from autoposter.security import decode_token
        from sqlalchemy import select
        request = Request(scope)
        params = parse_qs(scope.get('query_string',b'').decode('utf-8'),keep_blank_values=True)
        callback_owner = None
        import re
        match = re.fullmatch(r'(?:/autoposter)?/api/v1/oauth/(x)/callback', path)
        callback_platform = 'fanvue-chat' if path == '/oauth/callback' else (match.group(1) if match else None)
        if callback_platform:
            from autoposter.models import OAuthState
            from datetime import datetime, timedelta, timezone
            states = params.get('state', [])
            if len(states) != 1 or not states[0]:
                return await JSONResponse({'detail':'Invalid OAuth state'},400)(scope,receive,send)
            async with db.SessionLocal(info={'control_plane':True}) as session:
                row = await session.get(OAuthState, states[0])
                if row is None or row.platform != callback_platform or row.created_at < datetime.now(timezone.utc)-timedelta(minutes=10):
                    return await JSONResponse({'detail':'Invalid or expired OAuth state'},400)(scope,receive,send)
                callback_owner = row.character_id
        # AutoChat historically has no authentication dependency. Apply the same
        # installation authentication to its HTML, files, forms and OAuth routes.
        if settings.auth_enabled and callback_owner is None and not path.startswith('/api/v1/auth/'):
            value = request.cookies.get(settings.cookie_name)
            auth = request.headers.get('authorization','')
            if not value and auth.lower().startswith('bearer '):
                value = auth.split(' ',1)[1]
            payload = decode_token(value) if value else None
            user = None
            if payload and payload.get('typ') == 'access':
                try:
                    async with db.SessionLocal(info={'control_plane':True}) as session:
                        user = await session.get(User, UUID(str(payload['sub'])))
                except (ValueError, KeyError):
                    pass
            if user is None or not user.is_active:
                return await JSONResponse({'detail':'Nicht angemeldet'},401)(scope,receive,send)
            if request.method not in ('GET','HEAD','OPTIONS') and user.role not in ('editor','admin'):
                return await JSONResponse({'detail':'Editor erforderlich'},403)(scope,receive,send)
        global_routes = ('/api/v1/characters','/api/v1/auth','/api/v1/system',
                         '/characters','/system','/upload','/health','/healthz',
                         '/api/deployment-status','/api/creatorpilot/health','/api/sysinfo')
        if any(path == p or path.startswith(p+'/') for p in global_routes):
            with control_context():
                return await self.app(scope,receive,send)
        values = [str(callback_owner)] if callback_owner is not None else params.get('character_id',[])
        if not values:
            if path == '/' and request.method in ('GET', 'HEAD'):
                return await RedirectResponse('/characters', status_code=307)(scope,receive,send)
            return await JSONResponse({'detail':'Character URL context required; open /characters'},409)(scope,receive,send)
        try:
            if len(values) != 1:
                raise ValueError()
            owner = UUID(values[0])
        except ValueError:
            return await JSONResponse({'detail':'Invalid character_id'},400)(scope,receive,send)
        async with db.SessionLocal(info={'control_plane':True}) as session:
            character = await session.get(Character,owner)
            if character is None or character.is_archived:
                return await JSONResponse({'detail':'Character not found'},404)(scope,receive,send)
        scope.setdefault('state', {})['character_name'] = character.name
        async def owned_send(message):
            if message['type'] == 'http.response.start':
                from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
                headers=[]
                for key,value in message.get('headers',[]):
                    if key.lower() == b'location':
                        target=urlsplit(value.decode('latin-1'))
                        if not target.netloc or target.netloc == request.url.netloc:
                            if not any(target.path == p or target.path.startswith(p+'/') for p in global_routes):
                                query=[(k,v) for k,v in parse_qsl(target.query,keep_blank_values=True) if k!='character_id']
                                query.append(('character_id',str(owner)))
                                value=urlunsplit(target._replace(query=urlencode(query))).encode('latin-1')
                    headers.append((key,value))
                message={**message,'headers':headers}
            await send(message)
        with character_context(owner):
            from autoposter.services.runtime import load as load_runtime
            from autoposter.services.appconfig import refresh_snapshot
            async with db.SessionLocal() as session:
                await load_runtime(session)
                await refresh_snapshot(session)
            return await self.app(scope,receive,owned_send)
