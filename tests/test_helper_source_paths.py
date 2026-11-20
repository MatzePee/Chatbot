"""Source discovery must not discard symlink or AutoChat environment identity."""
import pytest
from app import character_upgrade as upgrade
from autoposter.config import settings

@pytest.mark.parametrize('fault',['post-link','media-link','custom-chat'])
def test_activation_refuses_ambiguous_sources_before_copy(tmp_path,monkeypatch,fault):
    data=tmp_path/'data';data.mkdir();(data/'bot.db').write_bytes(b'original chat')
    post=data/'post.db';post.write_bytes(b'original post')
    media=data/'media';media.mkdir()
    if fault=='post-link':
        linked=data/'linked.db';linked.symlink_to(post);post=linked
    elif fault=='media-link':
        linked=data/'linked-media';linked.symlink_to(media);media=linked
    else:
        monkeypatch.setenv('MP_AUTOCHAT_DATA_DIR',str(tmp_path/'another-chat'))
    monkeypatch.setattr(settings,'database_url','sqlite+aiosqlite:///'+str(post))
    monkeypatch.setattr(settings,'media_root',media)
    monkeypatch.setattr(settings,'scheduler_mode','off')
    upgrade.stage(tmp_path,name='Cherry',confirmed=True)
    import tools.sqlite_layout
    monkeypatch.setattr(tools.sqlite_layout,'_assert_stopped',lambda *a:pytest.fail('ambiguous paths reached copy boundary'))
    with pytest.raises(ValueError,match='[Ss]ymlink|[Cc]ustom|[Pp]ath|AutoChat'):
        upgrade.apply_stopped(tmp_path)
    assert not (data/'character-releases').exists()
    assert (data/'bot.db').read_bytes()==b'original chat'
