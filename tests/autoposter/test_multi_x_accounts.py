"""Mehrere X-Accounts mit je eigener API-App.

Kernregel: Zu jeder X-App gehört genau EIN Account. Zwei Kanäle dürfen sich
deshalb keine Client-ID teilen, und jeder Kanal muss seine eigenen Zugangsdaten
verwenden — nie versehentlich die eines anderen Kanals.
"""
from __future__ import annotations

import pytest

from autoposter.adapters.base import OAuthApp
from autoposter.crypto import decrypt
from autoposter.models import Channel, ChannelHealth, PostingPolicy
from autoposter.services import oauthapp
from autoposter.services.appconfig import _snapshot


async def make_x_channel(db, name: str, client_id: str = "", secret: str = "") -> Channel:
    policy = PostingPolicy()
    db.add(policy)
    await db.flush()
    channel = Channel(
        platform="x",
        display_name=name,
        handle="@" + name.lower().replace(" ", ""),
        policy_id=policy.id,
        health=ChannelHealth.ok.value,
    )
    if client_id:
        oauthapp.store_app(channel, client_id, secret, f"App {name}")
    db.add(channel)
    await db.flush()
    await db.refresh(channel)
    return channel


@pytest.mark.asyncio
async def test_jeder_kanal_nutzt_seine_eigene_app(db):
    a = await make_x_channel(db, "Profil A", "CLIENT-A", "SECRET-A")
    b = await make_x_channel(db, "Profil B", "CLIENT-B", "SECRET-B")

    app_a = oauthapp.for_channel(a)
    app_b = oauthapp.for_channel(b)

    assert app_a.client_id == "CLIENT-A"
    assert app_b.client_id == "CLIENT-B"
    assert app_a.client_secret == "SECRET-A"
    assert app_b.client_secret == "SECRET-B"
    assert app_a.client_id != app_b.client_id


@pytest.mark.asyncio
async def test_secret_liegt_verschluesselt_in_der_datenbank(db):
    channel = await make_x_channel(db, "Krypto", "CID", "TOPSECRET")
    assert channel.oauth_client_secret_enc
    assert "TOPSECRET" not in channel.oauth_client_secret_enc
    assert decrypt(channel.oauth_client_secret_enc) == "TOPSECRET"


@pytest.mark.asyncio
async def test_doppelte_client_id_wird_erkannt(db):
    await make_x_channel(db, "Erster", "SAME-ID", "s1")
    zweiter = await make_x_channel(db, "Zweiter", "SAME-ID", "s2")

    clash, names = await oauthapp.conflicting_channels(db, zweiter)
    assert clash is True
    assert "Erster" in names


@pytest.mark.asyncio
async def test_unterschiedliche_client_ids_kollidieren_nicht(db):
    await make_x_channel(db, "Eins", "ID-1", "s1")
    zwei = await make_x_channel(db, "Zwei", "ID-2", "s2")

    clash, names = await oauthapp.conflicting_channels(db, zwei)
    assert clash is False
    assert names == []


@pytest.mark.asyncio
async def test_ohne_eigene_app_gilt_die_globale(db):
    _snapshot.update({
        "x_client_id": "GLOBAL-ID",
        "x_client_secret": "GLOBAL-SECRET",
        "public_base_url": "https://example.test",
    })
    try:
        channel = await make_x_channel(db, "Ohne App")
        app = oauthapp.for_channel(channel)
        assert oauthapp.uses_own_app(channel) is False
        assert app.client_id == "GLOBAL-ID"
        assert app.redirect_uri("x") == "https://example.test/api/v1/oauth/x/callback"
    finally:
        _snapshot.clear()


@pytest.mark.asyncio
async def test_client_id_entfernen_verwirft_das_secret(db):
    channel = await make_x_channel(db, "Zurueck", "CID", "SEC")
    assert channel.oauth_client_secret_enc

    oauthapp.store_app(channel, "", None, "")
    assert channel.oauth_client_id == ""
    assert channel.oauth_client_secret_enc is None
    assert oauthapp.uses_own_app(channel) is False


@pytest.mark.asyncio
async def test_leeres_secret_laesst_gespeichertes_stehen(db):
    """Das Formular zeigt Geheimnisse maskiert an – Speichern darf sie nicht löschen."""
    channel = await make_x_channel(db, "Maske", "CID", "ORIGINAL")
    oauthapp.store_app(channel, "CID", "********", "Neuer Name")
    assert decrypt(channel.oauth_client_secret_enc) == "ORIGINAL"
    assert channel.oauth_app_name == "Neuer Name"

    oauthapp.store_app(channel, "CID", "", "Noch neuer")
    assert decrypt(channel.oauth_client_secret_enc) == "ORIGINAL"


def test_redirect_uri_haengt_am_server_nicht_an_der_app():
    a = OAuthApp(client_id="A", redirect_base="https://host/")
    b = OAuthApp(client_id="B", redirect_base="https://host")
    assert a.redirect_uri("x") == b.redirect_uri("x") == "https://host/api/v1/oauth/x/callback"
    assert a.redirect_uri("fanvue").endswith("/api/v1/oauth/fanvue/callback")


def test_app_ohne_client_id_gilt_als_unkonfiguriert():
    assert OAuthApp(client_id="").is_configured is False
    assert OAuthApp(client_id="abc").is_configured is True
