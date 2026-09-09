"""Adapter-Tests gegen gemockte HTTP-Antworten."""
from __future__ import annotations

import httpx
import pytest
import respx

from autoposter.adapters.base import AdapterError, OAuthApp, raise_for_response
from autoposter.adapters.fanvue import FanvueAdapter
from autoposter.adapters.x import XAdapter
from autoposter.config import settings


def response(status: int, headers=None, text: str = "{}") -> httpx.Response:
    return httpx.Response(status, headers=headers or {}, text=text)


def test_401_fordert_neuautorisierung():
    with pytest.raises(AdapterError) as exc:
        raise_for_response(response(401), "Test")
    assert exc.value.needs_reauth is True
    assert exc.value.retryable is False


def test_403_ist_scope_fehler_ohne_retry():
    with pytest.raises(AdapterError) as exc:
        raise_for_response(response(403, text='{"error":"Insufficient scopes"}'), "Test")
    assert exc.value.status == 403
    assert exc.value.retryable is False


def test_410_meldet_abgelaufene_api_version():
    with pytest.raises(AdapterError) as exc:
        raise_for_response(response(410), "Test")
    assert "API-Version" in str(exc.value)


def test_429_liest_retry_after():
    with pytest.raises(AdapterError) as exc:
        raise_for_response(response(429, {"Retry-After": "45"}), "Test")
    assert exc.value.retryable is True
    assert exc.value.retry_after == 45


def test_500_ist_wiederholbar():
    with pytest.raises(AdapterError) as exc:
        raise_for_response(response(503), "Test")
    assert exc.value.retryable is True


def test_x_limits_und_authorize_url():
    adapter = XAdapter()
    limits = adapter.limits()
    assert limits.max_chars == 280
    assert limits.max_images_per_post == 4

    app = OAuthApp(client_id="APP-A", client_secret="s", redirect_base="https://host")
    url = adapter.authorize_url(app, "state123", "challenge456")
    assert "code_challenge_method=S256" in url
    assert "state=state123" in url
    assert "tweet.write" in url
    assert "client_id=APP-A" in url


def test_jede_x_app_erzeugt_eine_eigene_autorisierungs_url():
    """Zu jeder X-App gehört genau ein Account – zwei Kanäle müssen deshalb
    mit unterschiedlichen Client-IDs zur Autorisierung geschickt werden."""
    adapter = XAdapter()
    a = OAuthApp(client_id="APP-A", client_secret="sa", redirect_base="https://host")
    b = OAuthApp(client_id="APP-B", client_secret="sb", redirect_base="https://host")

    url_a = adapter.authorize_url(a, "s1", "c1")
    url_b = adapter.authorize_url(b, "s2", "c2")

    assert "client_id=APP-A" in url_a
    assert "client_id=APP-B" in url_b
    assert url_a != url_b
    # Die Redirect-URI bleibt gleich – sie hängt am Server, nicht an der App.
    assert a.redirect_uri("x") == b.redirect_uri("x") == "https://host/api/v1/oauth/x/callback"


def test_basic_auth_nutzt_die_app_des_kanals():
    import base64

    adapter = XAdapter()
    header = adapter._basic_auth(OAuthApp(client_id="ID1", client_secret="SEC1"))
    decoded = base64.b64decode(header["Authorization"].split(" ", 1)[1]).decode()
    assert decoded == "ID1:SEC1"


def test_fanvue_limits_und_pflichtheader():
    adapter = FanvueAdapter()
    limits = adapter.limits()
    assert limits.max_chars == 5000
    assert limits.supports_platform_scheduling is True
    from autoposter.adapters.base import Credentials

    headers = adapter._headers(Credentials(access_token="abc"))
    assert headers["X-Fanvue-API-Version"] == settings.fanvue_api_version
    assert headers["Authorization"] == "Bearer abc"


@pytest.mark.asyncio
@respx.mock
async def test_fanvue_bulk_meldet_teilfehler_pro_item():
    """HTTP 200 heißt nicht, dass alle Items erfolgreich waren."""
    adapter = FanvueAdapter()
    respx.post(f"{settings.fanvue_api_base}/agencies/posts").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "creatorUserUuid": "a", "status": "success", "data": {"uuid": "p1"}},
                    {
                        "index": 1,
                        "creatorUserUuid": "b",
                        "status": "error",
                        "code": 404,
                        "message": "Creator not found",
                    },
                ]
            },
        )
    )
    from autoposter.adapters.base import Credentials

    results = await adapter.bulk_publish(Credentials(access_token="t"), [{}, {}])
    assert len(results) == 2
    assert results[0]["status"] == "success"
    assert results[1]["status"] == "error"
    assert results[1]["index"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_x_tweet_erstellen():
    adapter = XAdapter()
    respx.post(f"{settings.x_api_base}/2/tweets").mock(
        return_value=httpx.Response(201, json={"data": {"id": "1750000000000000000"}})
    )
    from autoposter.adapters.base import Credentials
    from autoposter.models import Channel, Post

    channel = Channel(platform="x", display_name="T", handle="@tester")
    post = Post(channel_id=None, body_text="Hallo Welt", hashtags=["test"])
    result = await adapter.publish(Credentials(access_token="t"), channel, post, [])
    assert result.external_post_id == "1750000000000000000"
    assert "tester/status/" in result.external_url


def test_x_text_haengt_hashtags_nur_bei_platz_an():
    from autoposter.models import Post

    post = Post(body_text="A" * 275, hashtags=["sehrlangerhashtag"])
    assert len(XAdapter._compose_text(post)) <= 280
