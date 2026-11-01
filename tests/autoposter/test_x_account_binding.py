from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import respx

from autoposter.adapters.base import Credentials, AdapterError
from autoposter.adapters.x import XAdapter
from autoposter.models import AppSetting, ChannelCredential
from autoposter.services import followers, credentials
from tests.autoposter.test_duplicates import make_channel


@pytest.mark.asyncio
async def test_wrong_account_metrics_are_not_shown_under_another_channel(db):
    a = await make_channel(db, 'Correct', 'x')
    b = await make_channel(db, 'Wrong', 'x')
    a.handle = '@correct'; a.verified_account = 'Correct'
    b.handle = '@different'; b.verified_account = 'Correct'
    cred = ChannelCredential(platform='x', external_user_id='same-id')
    db.add(cred); await db.flush()
    a.credential_id = b.credential_id = cred.id
    db.add(AppSetting(key='x_followers:same-id', value={'days':{'2026-11-01':112},'status':'OK'}))
    await db.flush()
    overview = await followers.overview(db, datetime(2026,11,1,tzinfo=timezone.utc))
    rows = {row['channel_id']:row for row in overview}
    assert rows[str(a.id)]['current'] == 112
    assert rows[str(b.id)]['current'] is None
    assert rows[str(b.id)]['account_id'] is None
    assert 'Falsches X-Konto' in rows[str(b.id)]['status']
    assert all(point['count'] is None for point in rows[str(b.id)]['history'])
    assert (await db.get(AppSetting,'x_followers:same-id')).value['days']['2026-11-01'] == 112
    with pytest.raises(AdapterError, match='Falsches X-Konto'):
        await credentials.get_valid_credentials(db,b)


@pytest.mark.asyncio
async def test_follower_fetch_checks_username_even_when_stored_id_is_wrong():
    adapter = XAdapter()
    with respx.mock() as mock:
        mock.get(adapter.base+'/2/users/me').respond(200,json={'data':{
            'id':'wrong-shared-id','username':'lastAccount','public_metrics':{'followers_count':112}}})
        cred = Credentials('fake', external_user_id='wrong-shared-id', raw_meta={'expected_x_username':'@expected'})
        with pytest.raises(ValueError, match='username mismatch'):
            await adapter.fetch_followers(cred)


def test_x_handles_are_case_insensitive_but_must_match():
    assert followers.x_account_matches(SimpleNamespace(handle='@Correct'),'correct')
    assert not followers.x_account_matches(SimpleNamespace(handle='@Other'),'correct')
