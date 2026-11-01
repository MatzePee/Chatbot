"""Independent outbound Bot API adapter. No polling/webhooks, paid sends or blind retries."""
from __future__ import annotations
import io
import json
from dataclasses import dataclass
import httpx
from PIL import Image
from .base import AccountInfo, AdapterError, ChannelLimits, MediaRef, PublishResult, http_client

import logging
import re

class _TokenRedactor(logging.Filter):
    def filter(self, record):
        record.msg = re.sub(r'bot[0-9]+:[A-Za-z0-9_-]+', 'bot[REDACTED]', record.getMessage())
        record.args = ()
        return True

# HTTPX logs the full URL at INFO; never allow Bot API tokens into application logs.
for _name in ('httpx', 'httpcore.connection', 'httpcore.http11', 'httpcore.http2', 'httpcore.proxy'):
    logging.getLogger(_name).addFilter(_TokenRedactor())

class TelegramUncertain(AdapterError):
    """A send may have reached Telegram. Retrying could duplicate a public post."""
    def __init__(self, message, *, confirmed=None):
        super().__init__(message)
        self.confirmed = confirmed or []

@dataclass
class TelegramMediaRef(MediaRef):
    data: bytes = b''
    mime: str = ''
    filename: str = ''

def compose_text(post):
    text = post.body_text or ""
    tags = " ".join("#" + tag.lstrip("#") for tag in (post.hashtags or []))
    return text + ("\n\n" + tags if text and tags else tags)

def text_units(text):
    # Conservative UTF-16 bound also covers astral emoji without splitting text.
    return len(text.encode('utf-16-le')) // 2

def validate_media(mime, data):
    if mime not in ('image/jpeg','image/png','video/mp4'):
        raise AdapterError('Telegram: nur JPEG/PNG-Fotos und MP4-Videos unterstützt.')
    if not data or len(data) > (50_000_000 if mime == 'video/mp4' else 10_000_000):
        raise AdapterError('Telegram: leere/zu große Datei (Foto max. 10 MB, Video max. 50 MB).')
    if mime != 'video/mp4':
        try:
            with Image.open(io.BytesIO(data)) as image:
                w,h = image.size
                if image.format not in ('JPEG','PNG') or w+h > 10000 or min(w,h) <= 0 or max(w,h)/min(w,h) > 20:
                    raise ValueError()
                image.verify()
        except Exception:
            raise AdapterError('Telegram: ungültiges Foto; Breite+Höhe max. 10000, Seitenverhältnis max. 20:1.') from None

class TelegramAdapter:
    platform = 'telegram'

    def limits(self):
        return ChannelLimits(4096,10,['image/jpeg','image/png','video/mp4'],True,False,False,False,50_000_000)

    async def _call(self, cred, method, payload, files=None):
        sending = method.startswith('send')
        try:
            async with http_client() as client:
                response = await client.post(
                    f'https://api.telegram.org/bot{cred.access_token}/{method}',
                    **({'data':payload,'files':files} if files else {'json':payload}))
            body = response.json()
            if not isinstance(body,dict):
                raise ValueError()
        except Exception:  # Provider/transport exceptions may embed the secret URL.
            error = TelegramUncertain if sending else AdapterError
            raise error('Telegram: Versand unklar; keine automatische Wiederholung. Zielkanal vor weiteren Aktionen manuell prüfen.' if sending else 'Telegram: keine eindeutige API-Antwort.') from None
        if response.status_code != 200 or body.get('ok') is not True:
            code = body.get('error_code', response.status_code)
            if not isinstance(code, int):
                code = response.status_code
            delay = body.get('parameters', {}).get('retry_after')
            if sending and (response.status_code >= 500 or body.get('ok') is not False):
                raise TelegramUncertain('Telegram: Versand unklar (Serverantwort); Zielkanal manuell prüfen.')
            raise AdapterError(f'Telegram: Anfrage abgelehnt ({code}). Token, Zielkanal und Bot-Rechte prüfen.' + (f' Wartezeit: {delay}s.' if isinstance(delay,int) else ''), status=code, retry_after=delay, needs_reauth=code == 401)
        if 'result' not in body:
            error = TelegramUncertain if sending else AdapterError
            raise error('Telegram: unvollständige API-Antwort; Zielkanal manuell prüfen.')
        return body['result']

    async def verify(self, app, cred):
        destination = cred.raw_meta.get('destination','')
        if not cred.access_token or not destination:
            raise AdapterError('Telegram: Bot-Token und Zielkanal fehlen.')
        me = await self._call(cred, 'getMe', {})
        chat = await self._call(cred, 'getChat', {'chat_id':destination})
        try:
            chat_id = str(chat['id'])
            if not chat_id.startswith('-100') or (destination.startswith('@') and chat.get('username', '').lower() != destination[1:].lower()) or (not destination.startswith('@') and destination != chat_id):
                raise AdapterError('Telegram: Zielkanal stimmt nicht überein.')
            member = await self._call(cred, 'getChatMember', {'chat_id':chat_id, 'user_id':me['id']})
            if not me.get('is_bot') or chat.get('type') != 'channel' or member.get('user', {}).get('id') != me['id'] or member.get('status') != 'administrator' or member.get('can_post_messages') is not True:
                raise AdapterError('Telegram: Bot benötigt Administratorrecht „Nachrichten posten“ im Zielkanal.')
        except (KeyError,TypeError,AttributeError):
            raise AdapterError('Telegram: Identität/Kanalrechte nicht bestätigt.') from None
        if cred.external_user_id and str(me['id']) != cred.external_user_id:
            raise AdapterError('Telegram: Bot-Identität stimmt nicht mit der gespeicherten Bindung überein.')
        if cred.raw_meta.get('chat_id') and cred.raw_meta['chat_id'] != chat_id:
            raise AdapterError('Telegram: Kanal-ID hat sich geändert; Zugang neu speichern.')
        return AccountInfo(True, account_id=str(me['id']), username=me.get('username',''), message='Bot und Kanal-Schreibrecht geprüft (ohne Testnachricht).', raw={'chat_id':chat_id, 'username':chat.get('username','')})

    async def upload_media(self, cred, asset, data):
        # Stage in memory only. Telegram uploads happen atomically with sendPhoto/Video/MediaGroup.
        validate_media(asset.mime,data)
        return TelegramMediaRef(str(asset.id),'',data=data,mime=asset.mime,filename='media.mp4' if asset.mime=='video/mp4' else 'media.png' if asset.mime=='image/png' else 'media.jpg')

    async def publish(self, cred, channel, post, media):
        if post.price_cents or post.pin_after_publish or post.thread_parent_id:
            raise AdapterError("Telegram: Preise, automatisches Anpinnen und X-Threads werden nicht unterstützt.")
        chat_id = cred.raw_meta.get('chat_id','')
        if not chat_id.startswith('-100') or not cred.external_user_id or cred.raw_meta.get('destination') != channel.handle:
            raise AdapterError('Telegram: Zielkanal zuerst speichern und Rechte testen.')
        text = compose_text(post)
        limit = 1024 if media else 4096
        if text_units(text) > limit or (not media and not text.strip()):
            raise AdapterError(f'Telegram: Textlimit {limit} UTF-16-Zeichen; Text bitte selbst kürzen, keine automatische Aufteilung.')
        if len(media)>10:
            raise AdapterError('Telegram: maximal 10 Medien pro Album; keine automatische Aufteilung.')
        if sum(len(getattr(item,'data',b'')) for item in media)>100_000_000:
            raise AdapterError('Telegram: lokales Sicherheitslimit 100 MB pro Album.')
        files={}
        payload={'chat_id':chat_id,'allow_paid_broadcast':False}
        method='sendMessage'
        if not media:
            payload['text']=text
        else:
            for item in media:
                if not isinstance(item,TelegramMediaRef):
                    raise AdapterError('Telegram: Medien müssen lokal neu geladen werden.')
                validate_media(item.mime,item.data)
            payload['allow_paid_broadcast']='false'
            if len(media)==1:
                item=media[0]; kind='video' if item.mime=='video/mp4' else 'photo'
                method='sendVideo' if kind=='video' else 'sendPhoto'
                payload['caption']=text
                files[kind]=(item.filename,item.data,item.mime)
            else:
                method='sendMediaGroup'; album=[]
                for i,item in enumerate(media):
                    key=f'media{i}'
                    entry={'type':'video' if item.mime=='video/mp4' else 'photo','media':f'attach://{key}'}
                    if i==0:entry['caption']=text
                    album.append(entry);files[key]=(item.filename,item.data,item.mime)
                payload['media']=json.dumps(album,ensure_ascii=False)
        result=await self._call(cred,method,payload,files or None)
        messages=result if isinstance(result,list) else [result]
        ids=[]
        try:
            for message in messages:
                if str(message['chat']['id'])!=chat_id or not isinstance(message['message_id'],int) or message['message_id']<=0:
                    raise ValueError()
                ids.append(message['message_id'])
            if len(messages)!=max(1,len(media)) or len(set(ids))!=len(ids):raise ValueError()
        except (KeyError,TypeError,ValueError):
            raise TelegramUncertain('Telegram: Versand unklar/teilweise bestätigt; kein erneuter Versand. Zielkanal manuell prüfen.',confirmed=ids) from None
        username=cred.raw_meta.get('username','')
        url=f'https://t.me/{username}/{ids[0]}' if username else f'https://t.me/c/{chat_id[4:]}/{ids[0]}'
        return PublishResult(','.join(map(str,ids)),url,raw={'chat_id':chat_id,'message_ids':ids})

    async def fetch_metrics(self,cred,external_post_id):
        return None  # Bot API offers no generic message GET or follower analytics.
