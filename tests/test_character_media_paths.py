"""File resolvers are also the provider upload boundary."""
from types import SimpleNamespace
from uuid import uuid4
import pytest
from autoposter.workspaces import character_context, CharacterBoundaryError
from autoposter.services import media


def test_provider_media_read_refuses_foreign_owner_before_open(tmp_path, monkeypatch):
    monkeypatch.setattr(media.settings, 'media_root', tmp_path)
    a, b = uuid4(), uuid4()
    with character_context(a):
        root = media.media_root()
        (root / 'original.jpg').write_bytes(b'alpha')
        foreign = SimpleNamespace(id=uuid4(), character_id=b, storage_path='original.jpg')
        with pytest.raises(CharacterBoundaryError):
            media.read_bytes(foreign)


@pytest.mark.parametrize('kind', ['original', 'thumbnail', 'character-root'])
def test_media_symlinks_cannot_escape_owner(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(media.settings, 'media_root', tmp_path / 'media')
    owner = uuid4()
    outside = tmp_path / 'private.jpg'
    outside.write_bytes(b'not public')
    asset = SimpleNamespace(id=uuid4(), character_id=owner, storage_path='escape.jpg')
    with character_context(owner):
        root = media.media_root()
        if kind == 'original':
            (root / 'escape.jpg').symlink_to(outside)
            with pytest.raises(CharacterBoundaryError):
                media.read_bytes(asset)
        elif kind == 'thumbnail':
            directory = media.asset_dir(asset.id)
            directory.mkdir(parents=True)
            (directory / 'thumb_256.jpg').symlink_to(outside)
            with pytest.raises(CharacterBoundaryError):
                media.thumb_path(asset)
        else:
            root.rmdir()
            root.symlink_to(tmp_path, target_is_directory=True)
            with pytest.raises(CharacterBoundaryError):
                media.media_root()


def test_unowned_media_root_fails_closed_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(media.settings, 'media_root', tmp_path)
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    with pytest.raises(CharacterBoundaryError):
        media.media_root()
