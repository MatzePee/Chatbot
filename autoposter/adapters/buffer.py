"""Instagram publishing through Buffer's GraphQL API (no browser automation)."""
from __future__ import annotations

import httpx

from autoposter.adapters.base import (
    AccountInfo, AdapterError, ChannelLimits, PublishResult,
)

ENDPOINT = "https://api.buffer.com"
CHANNEL_FIELDS = "id name displayName service isDisconnected isLocked isQueuePaused"
POST_FIELDS = "id channelId status externalLink sentAt schedulingType"
READS = {
    "organizations": "query { account { organizations { id } } }",
    "channels": "query($input: ChannelsInput!) { channels(input: $input) { " + CHANNEL_FIELDS + " } }",
    "channel": "query($input: ChannelInput!) { channel(input: $input) { " + CHANNEL_FIELDS + " } }",
    "post": "query($input: PostInput!) { post(input: $input) { " + POST_FIELDS + " } }",
}
CREATE = ("mutation($input: CreatePostInput!) { createPost(input: $input) { __typename "
          "... on PostActionSuccess { post { " + POST_FIELDS + " } } "
          "... on MutationError { message } } }")


class BufferUncertain(AdapterError):
    """Request may have been accepted. Never retry createPost automatically."""


async def _request(token, query, variables=None, *, write=False):
    if not token:
        raise AdapterError("Buffer-API-Key fehlt.", needs_reauth=True)
    if write:
        from autoposter.services.runtime import assert_external_write_allowed
        await assert_external_write_allowed()
    # Read operations are a closed allowlist, independent of the HTTP POST verb.
    elif query not in READS.values():
        raise AdapterError("Unbekannte Buffer-Leseoperation.")
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
            response = await client.post(ENDPOINT, headers={"Authorization": f"Bearer {token}"},
                                         json={"query": query, "variables": variables or {}})
    except httpx.HTTPError:
        error = BufferUncertain if write else AdapterError
        raise error("Buffer nicht erreichbar. Versandstatus in Buffer prüfen; nicht erneut senden." if write
                    else "Buffer nicht erreichbar.") from None
    if response.status_code in (401, 403):
        raise AdapterError("Buffer: API-Key oder Berechtigung abgelehnt.", needs_reauth=True)
    if response.status_code >= 400:
        error = BufferUncertain if write and response.status_code >= 500 else AdapterError
        raise error(f"Buffer: HTTP {response.status_code}." + (" Versand in Buffer prüfen." if write else ""))
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError()
    except ValueError:
        raise (BufferUncertain if write else AdapterError)("Buffer-Antwort nicht lesbar.") from None
    if payload.get("errors") or not isinstance(payload.get("data"), dict):
        # Never log a raw provider response: it may echo media URLs or input.
        raise (BufferUncertain if write else AdapterError)("Buffer hat die Anfrage nicht bestätigt. Verbindung und Status in Buffer prüfen.")
    return payload["data"]


async def read(token, operation, **input):
    return await _request(token, READS[operation], {"input": input} if input else None)


async def instagram_channels(token):
    organizations = (await read(token, "organizations"))["account"]["organizations"]
    result = []
    for organization in organizations[:20]:
        rows = (await read(token, "channels", organizationId=organization["id"]))["channels"]
        result.extend(row for row in rows if row.get("service") == "instagram")
    return result


def account_matches(channel, row):
    expected = (channel.handle or "").strip().lstrip("@").lower()
    actual = (row.get("name") or "").strip().lstrip("@").lower()
    return bool(expected and expected == actual and row.get("service") == "instagram"
                and not row.get("isDisconnected") and not row.get("isLocked") and not row.get("isQueuePaused"))


class BufferAdapter:
    platform = "instagram"

    def limits(self):
        return ChannelLimits(2200, 10, ["image/jpeg", "image/png", "image/webp", "video/mp4"],
                             False, False, False, False, 20_000_000, 50_000_000)

    async def verify(self, app, cred):
        row = (await read(cred.access_token, "channel", id=cred.external_user_id))["channel"]
        ok = row.get("service") == "instagram" and not row.get("isDisconnected") and not row.get("isLocked")
        return AccountInfo(ok, row["id"], row.get("name", ""), "Buffer-Verbindung geprüft.", row)

    async def refresh(self, app, cred):
        raise AdapterError("Buffer-API-Key bitte in den Kanal-Einstellungen ersetzen.", needs_reauth=True)

    async def upload_media(self, cred, asset, data):
        raise AdapterError("Instagram-Medien müssen mit einem gespeicherten Post auf den Webspace geladen werden.")

    async def publish(self, cred, channel, post, media):
        from autoposter.services.media_rules import combination_issue
        issue = combination_issue(self.platform, media)
        if issue:
            raise AdapterError(issue)
        if len(media) != len(post.media_asset_ids or []):
            raise AdapterError("Instagram: Medien fehlen oder sind doppelt ausgewählt.")
        row = (await read(cred.access_token, "channel", id=cred.external_user_id))["channel"]
        if not account_matches(channel, row):
            raise AdapterError("Buffer: Instagram-Konto stimmt nicht überein oder ist getrennt/gesperrt.", needs_reauth=True)
        tags = " ".join("#" + str(tag).strip().lstrip("#") for tag in post.hashtags or [] if str(tag).strip())
        text = "\n\n".join(part for part in [(post.body_text or "").strip(), tags] if part)
        if len(text) > 2200:
            raise AdapterError("Instagram: maximal 2200 Zeichen einschließlich Hashtags.")
        result = await _request(cred.access_token, CREATE, {"input": {
            "channelId": cred.external_user_id, "mode": "shareNow", "schedulingType": "automatic",
            "text": text, "assets": [{"video" if ref.mime == "video/mp4" else "image": {"url": ref.external_id}} for ref in media],
            "metadata": {"instagram": {"type": "reel" if media[0].mime == "video/mp4" else "post", "shouldShareToFeed": True}},
        }}, write=True)
        action = result.get("createPost") or {}
        if action.get("__typename") != "PostActionSuccess":
            raise AdapterError("Buffer hat den Beitrag abgelehnt. Kontoverbindung, Medienformat und Buffer-Limits prüfen.")
        remote = action.get("post") or {}
        if not remote.get("id") or remote.get("channelId") != cred.external_user_id:
            raise BufferUncertain("Buffer-Zusage unvollständig. Beitrag in Buffer prüfen; nicht erneut senden.")
        return PublishResult(remote["id"], remote.get("externalLink") or "", raw=remote)

    async def delete(self, cred, external_post_id):
        raise AdapterError("An Buffer übergebene Beiträge bitte direkt in Buffer verwalten.")

    async def fetch_metrics(self, cred, external_post_id):
        return None
