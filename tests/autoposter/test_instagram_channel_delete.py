import pytest
from fastapi import HTTPException
from autoposter.models import Channel, Post
from autoposter.services import buffer_channels


@pytest.mark.parametrize('state',['preparing','pending','queued','uncertain','remote_error'])
def test_local_removal_requires_explicit_confirmation(state):
    channel=Channel(display_name='Old Instagram')
    post=Post(generation_meta={'buffer_delivery':{'state':state}})
    with pytest.raises(HTTPException) as blocked:
        buffer_channels.validate_channel_deletion(channel,[post])
    assert blocked.value.status_code==409
    with pytest.raises(HTTPException) as mismatch:
        buffer_channels.validate_channel_deletion(channel,[post],detach_external=True,confirm_name='Different channel')
    assert mismatch.value.status_code==422
    buffer_channels.validate_channel_deletion(channel,[post],detach_external=True,confirm_name='Old Instagram')


def test_uploaded_files_are_disclosed_even_for_confirmed_posts():
    post=Post(generation_meta={'buffer_delivery':{'state':'confirmed','files':[{'path':'example.jpg'}]}})
    assert buffer_channels.deletion_details([post])=={'external_deliveries':0,'hosted_files':1,'requires_external_detach':True}
    assert buffer_channels.deletion_details([])['requires_external_detach'] is False
