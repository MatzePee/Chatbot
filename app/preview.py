"""Shadow mode: live reads and AI drafts, never platform writes or token refresh."""
import json
import os
from pathlib import Path
import re
import shlex
import smtplib
import subprocess
import threading
import time

import httpx
from starlette.responses import JSONResponse
from . import deployment

MESSAGE = 'Mitlesemodus: Nachrichten, Posts, Lesebestätigungen und OAuth-Änderungen sind gesperrt.'
_installed = False
_token_lock = threading.Lock()
_token_cache = {}
_stop = threading.Event()
_worker = None
_status = {'last_poll': None, 'last_error': None, 'cycles': 0}


def enabled():
    return os.environ.get('MP_PREVIEW') == '1'


class PreviewNetworkBlocked(RuntimeError):
    pass


def request_allowed(request):
    url = request.url
    if url.scheme != 'https' or url.port not in (None, 443):
        return False
    host = url.host.lower()
    if request.method in ('GET', 'HEAD'):
        if host == 'api.fanvue.com':
            return not any(k.lower() == 'markasread' and v.lower() != 'false' for k, v in url.params.multi_items())
        return host in ('api.x.com', 'api.twitter.com', 'openrouter.ai')
    return (host == 'openrouter.ai' and request.method == 'POST'
            and url.path.rstrip('/') == '/api/v1/chat/completions')


def install_network_guard():
    global _installed
    if _installed or not (enabled() or deployment.pending()):
        return
    _installed = True
    sync_send = httpx.HTTPTransport.handle_request
    async_send = httpx.AsyncHTTPTransport.handle_async_request
    smtp_connect = smtplib.SMTP.connect

    def guarded_sync(self, request):
        if deployment.pending():
            raise PreviewNetworkBlocked('Update-Prüfung: externe Anfragen sind vorübergehend gesperrt.')
        if enabled() and not request_allowed(request):
            raise PreviewNetworkBlocked(MESSAGE)
        return sync_send(self, request)

    async def guarded_async(self, request):
        if deployment.pending():
            raise PreviewNetworkBlocked('Update-Prüfung: externe Anfragen sind vorübergehend gesperrt.')
        if enabled() and not request_allowed(request):
            raise PreviewNetworkBlocked(MESSAGE)
        return await async_send(self, request)

    def guarded_smtp(self, *args, **kwargs):
        if enabled() or deployment.pending():
            raise PreviewNetworkBlocked(MESSAGE)
        return smtp_connect(self, *args, **kwargs)

    httpx.HTTPTransport.handle_request = guarded_sync
    httpx.AsyncHTTPTransport.handle_async_request = guarded_async
    smtplib.SMTP.connect = guarded_smtp


def access_token(local_tokens):
    """Read the server's latest access token; never fetch/use its refresh token."""
    config_file = Path(__file__).resolve().parents[1] / 'data/preview.json'
    config = json.loads(config_file.read_text()) if config_file.exists() else {}
    host = config.get('ssh_host', '')
    if host:
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]*', host):
            raise PreviewNetworkBlocked('Ungültige SSH-Adresse in data/preview.json.')
        with _token_lock:
            cached = _token_cache
            if cached.get('expires_at', 0) > time.time() + 10 and cached.get('loaded_at', 0) > time.time() - 20:
                return cached['access_token']
            database = config.get('database', '/srv/fanvue/Fanvue_Chatbot/data/bot.db')
            # SQLite URI mode=ro ensures the server database cannot be written.
            script = ('import sqlite3,json; from pathlib import Path; '
                      f'p=Path({database!r}); '
                      'c=sqlite3.connect(p.as_uri()+"?mode=ro",uri=True); '
                      'c.execute("PRAGMA query_only=ON"); '
                      'r=c.execute("SELECT access_token, expires_at FROM tokens WHERE id=1").fetchone(); '
                      'print(json.dumps(dict(zip(("access_token","expires_at"),r)) if r else {}))')
            identity = config.get('ssh_identity')
            identity_args = ['-i', str(config_file.parent.parent / identity), '-o', 'IdentitiesOnly=yes'] if identity else []
            result = subprocess.run(['ssh', *identity_args, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                                     '-o', 'ConnectTimeout=5', '--', host,
                                     'python3 -c ' + shlex.quote(script)],
                                    capture_output=True, text=True, timeout=12)
            if result.returncode != 0:
                raise PreviewNetworkBlocked('Der Server-Zugang für den Mitlesemodus ist nicht erreichbar. SSH-Schlüssel und bekannte Hostkennung prüfen.')
            values = json.loads(result.stdout)
            if not values.get('access_token') or (values.get('expires_at') or 0) <= time.time():
                raise PreviewNetworkBlocked('Der Access-Token auf dem Server ist abgelaufen. Nur der bisherige Bot darf ihn erneuern.')
            _token_cache.clear()
            _token_cache.update(values, loaded_at=time.time())
            return values['access_token']
    if local_tokens and local_tokens['access_token'] and (local_tokens['expires_at'] or 0) > time.time():
        return local_tokens['access_token']
    raise PreviewNetworkBlocked('Für Live-Daten fehlt ein aktueller Access-Token. Bitte den lesenden Server-Zugang einrichten. Der Refresh-Token wird zum Schutz des laufenden Bots nicht benutzt.')


def status():
    return {**_status, 'alive': bool(_worker and _worker.is_alive())}


def start_shadow_worker():
    global _worker
    if _worker and _worker.is_alive():
        return
    _stop.clear()

    def loop():
        from . import db, poller
        while not _stop.is_set():
            try:
                # Never run the production loop: it includes sends, reactivation
                # and Telegram. This path only reads chats and builds local drafts.
                poller.poll_cycle()
                _status['last_error'] = None
            except Exception as exc:
                _status['last_error'] = str(exc)
            _status['last_poll'] = time.time()
            _status['cycles'] += 1
            try:
                interval = max(60, float(db.get_setting('poll_interval_seconds', 60)))
            except (TypeError, ValueError):
                interval = 60
            _stop.wait(interval)

    _worker = threading.Thread(target=loop, name='creatorpilot-shadow', daemon=True)
    _worker.start()


def stop_shadow_worker():
    _stop.set()
    if _worker:
        _worker.join(timeout=2)


class PreviewMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and deployment.pending():
            if scope['method'] not in ('GET', 'HEAD', 'OPTIONS') or '/oauth/' in scope['path']:
                await JSONResponse({'detail': 'Update wird geprüft. Änderungen sind vorübergehend gesperrt.'}, status_code=503)(scope, receive, send)
                return
        if scope['type'] == 'http' and enabled():
            path = scope['path']
            safe_post = (path in ('/test', '/upload/publish', '/upload/settings', '/api/v1/media/search', '/autoposter/api/v1/media/search', '/api/v1/settings/image-comment', '/autoposter/api/v1/settings/image-comment')
                         or re.fullmatch(r'/queue/\d+/(regenerate|save|reject)', path)
                         or re.fullmatch(r'/(?:autoposter/)?api/v1/channels/[0-9a-f-]+/test', path))
            writes = scope['method'] not in ('GET', 'HEAD', 'OPTIONS') and not (scope['method'] == 'POST' and safe_post)
            oauth = '/oauth/' in path
            update = path == '/api/update-check' and b'force=' in scope.get('query_string', b'')
            if writes or oauth or update:
                await JSONResponse({'detail': MESSAGE}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)
