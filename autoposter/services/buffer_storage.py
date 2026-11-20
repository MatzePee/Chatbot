"""Dedicated public media copies on an explicit-TLS FTP account.

Only generated files inside mp-creatorstudio/<channel UUID>/<post UUID> are
managed here. Originals and files elsewhere in the hosting account are untouched.
"""
from __future__ import annotations

import asyncio
import ftplib
import hashlib
import io
import ipaddress
import posixpath
import socket
import ssl
import uuid
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageOps

from autoposter.adapters.base import AdapterError, MediaRef
from autoposter.crypto import decrypt

PREFIX = "mp-creatorstudio"


def normalize(config):
    result = {key: str(config.get(key) or "").strip() for key in ("ftp_host", "ftp_user", "ftp_directory", "public_base_url")}
    try:
        result["ftp_port"] = int(config.get("ftp_port") or 21)
        if not 1 <= result["ftp_port"] <= 65535:
            raise ValueError()
        host = result["ftp_host"]
        if not host or any(c in host for c in "/\\\r\n\x00 :@"):
            raise ValueError()
        if not result["ftp_user"] or any(c in result["ftp_user"] for c in "\r\n\x00"):
            raise ValueError()
        directory = result["ftp_directory"] or "/"
        if "\\" in directory or any(c in directory for c in "\r\n\x00") or ".." in directory.split("/"):
            raise ValueError()
        result["ftp_directory"] = "/" + directory.strip("/") if directory.strip("/") else "/"
        url = urlsplit(result["public_base_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.port not in (None, 443):
            raise ValueError()
        if any(c in url.path for c in "%\\\r\n\x00") or ".." in url.path.split("/"):
            raise ValueError()
        result["public_base_url"] = result["public_base_url"].rstrip("/")
    except (ValueError, TypeError):
        raise AdapterError("Webspace: FTP-Host, Benutzer, Port, Verzeichnis und öffentliche HTTPS-Adresse prüfen.") from None
    return result


def fingerprint(config):
    return hashlib.sha256(repr(sorted(normalize(config).items())).encode()).hexdigest()


def public_host(host):
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError()
    except (OSError, ValueError):
        raise AdapterError("Webspace-Host muss öffentlich erreichbar sein. DNS und Hostnamen prüfen.") from None


def connect(cred):
    from app import preview, deployment
    if preview.enabled() or deployment.pending():
        raise AdapterError("Webspace-Zugriff in Vorschau oder ausstehender Bereitstellung gesperrt.")
    config = normalize(cred.raw_meta)
    public_host(config["ftp_host"])
    ftp = ftplib.FTP_TLS(context=ssl.create_default_context(), timeout=60)
    try:
        ftp.connect(config["ftp_host"], config["ftp_port"])
        ftp.login(config["ftp_user"], decrypt(cred.raw_meta.get("ftp_password_enc")) or "")
        ftp.prot_p()  # Both login and data transfer encrypted; never downgrade to FTP.
        ftp.cwd(config["ftp_directory"])
        return ftp
    except Exception:
        ftp.close()
        raise AdapterError("FTPS-Verbindung fehlgeschlagen. Host, Port, Zugangsdaten, Verzeichnis und TLS prüfen.") from None


def managed_path(path):
    parts = path.split("/")
    try:
        if len(parts) != 4 or parts[0] != PREFIX:
            raise ValueError()
        uuid.UUID(parts[1]); uuid.UUID(parts[2])
        stem, extension = parts[3].rsplit(".", 1)
        uuid.UUID(stem)
        if extension not in ("jpg", "mp4", "txt"):
            raise ValueError()
    except (ValueError, AttributeError):
        raise AdapterError("Unsicherer Webspace-Dateipfad blockiert.") from None
    return path


def upload(cred, path, data):
    managed_path(path)
    ftp = connect(cred)
    try:
        for part in posixpath.dirname(path).split("/"):
            try:
                ftp.cwd(part)
            except ftplib.error_perm:
                ftp.mkd(part)
                ftp.cwd(part)
        filename = posixpath.basename(path)
        ftp.storbinary("STOR " + filename + ".part", io.BytesIO(data))
        ftp.rename(filename + ".part", filename)
    except Exception:
        raise AdapterError("FTPS-Upload fehlgeschlagen. Schreibrechte und freien Speicher prüfen.") from None
    finally:
        ftp.close()


def remove(cred, path):
    managed_path(path)
    ftp = connect(cred)
    try:
        ftp.cwd(posixpath.dirname(path))
        names = {name for name, facts in ftp.mlsd() if facts.get("type") == "file"}
        for suffix in ("", ".part"):
            if posixpath.basename(path + suffix) in names:
                ftp.delete(posixpath.basename(path + suffix))
    finally:
        ftp.close()


async def verify_public(cred, path, data):
    config = normalize(cred.raw_meta)
    public_host(urlsplit(config["public_base_url"]).hostname)
    url = config["public_base_url"] + "/" + managed_path(path)
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
            async with client.stream("GET", url, headers={"Range": "bytes=0-511"}) as response:
                if response.status_code not in (200, 206):
                    raise ValueError()
                sample = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=512):
                    sample.extend(chunk)
                    if len(sample) >= min(512, len(data)):
                        break
                if bytes(sample[:512]) != data[:512]:
                    raise ValueError()
    except (httpx.HTTPError, ValueError):
        raise AdapterError("Öffentliche Mediendatei nicht korrekt über HTTPS erreichbar. FTP-Verzeichnis und Subdomain müssen auf dasselbe Verzeichnis zeigen.") from None
    return url


def jpeg(data):
    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        image.thumbnail((1440, 1440), Image.Resampling.LANCZOS)
        width, height = image.size
        from autoposter.services.image_edits import instagram_ok
        if not instagram_ok(width, height):
            raise AdapterError("Instagram: Bildformat passt nicht. Bitte das Bild im Bildeditor zuschneiden.")
        # At most a rounding pixel near a ratio boundary; no added borders.
        if width / height < .75:
            image = image.crop((0, 0, width, int(width / .75)))
        elif width / height > 1.91:
            image = image.crop((0, 0, int(height * 1.91), height))
        canvas = Image.new("RGB", image.size, "white")
        canvas.paste(image, (0, 0), image)
        output = io.BytesIO()
        canvas.save(output, "JPEG", quality=92, optimize=True)
        result = output.getvalue()
        if len(result) > 8_000_000:
            raise AdapterError("Instagram-Bildkopie ist größer als 8 MB.")
        return result


async def probe(cred, channel_id):
    from app import preview
    if preview.enabled():
        raise AdapterError("Webspace-Test in der Vorschau deaktiviert.")
    path = f"{PREFIX}/{uuid.UUID(str(channel_id))}/{uuid.uuid4()}/{uuid.uuid4()}.txt"
    data = ("MP CreatorStudio Verbindungstest " + str(uuid.uuid4())).encode()
    uploaded = False
    try:
        await asyncio.to_thread(upload, cred, path, data)
        uploaded = True
        await verify_public(cred, path, data)
    finally:
        if uploaded:
            await asyncio.to_thread(remove, cred, path)


async def prepare(db, post, channel, cred, assets):
    from autoposter.services import media, watermark
    from autoposter.services.runtime import assert_external_write_allowed
    from autoposter.services.media_rules import combination_issue
    issue = combination_issue("instagram", assets)
    if issue or len(assets) != len(post.media_asset_ids or []):
        raise AdapterError(issue or "Instagram: Medien fehlen oder sind doppelt ausgewählt.")
    manifest = []
    for asset in assets:
        extension = "mp4" if asset.mime == "video/mp4" else "jpg"
        path = f"{PREFIX}/{channel.id}/{post.id}/{uuid.uuid4()}.{extension}"
        manifest.append({"path": path, "asset_id": str(asset.id), "mime": "video/mp4" if extension == "mp4" else "image/jpeg", "storage": fingerprint(cred.raw_meta)})
    previous = (post.generation_meta or {}).get("buffer_delivery", {})
    previous_files = [{**entry, "storage": entry.get("storage") or previous.get("storage")} for entry in previous.get("files", [])]
    delivery = {**(post.generation_meta or {}).get("buffer_delivery", {}),
                "files": previous_files + manifest, "storage": fingerprint(cred.raw_meta), "state": "uploading"}
    post.generation_meta = {**(post.generation_meta or {}), "buffer_delivery": delivery}
    await db.commit()  # Journal planned paths before transferring any file.
    refs = []
    for asset, entry in zip(assets, manifest):
        data = media.read_bytes(asset, platform="instagram")
        if asset.mime != "video/mp4":
            data = await asyncio.to_thread(watermark.apply_bytes, data, channel, mime=asset.mime)
            data = await asyncio.to_thread(jpeg, data)
        elif len(data) > 50_000_000:
            raise AdapterError("Instagram: Video größer als 50 MB.")
        await assert_external_write_allowed()
        await asyncio.to_thread(upload, cred, entry["path"], data)
        url = await verify_public(cred, entry["path"], data)
        refs.append(MediaRef(str(asset.id), url, mime=entry["mime"]))
    return refs
