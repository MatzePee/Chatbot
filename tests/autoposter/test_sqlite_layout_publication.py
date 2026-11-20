from pathlib import Path
import pytest


def test_publication_never_replaces_even_an_empty_directory(tmp_path):
    from tools import sqlite_layout
    stage = tmp_path / 'stage';stage.mkdir()
    destination = tmp_path / 'destination';destination.mkdir()
    inode = destination.stat().st_ino
    with pytest.raises(FileExistsError):
        sqlite_layout._publish_copy(stage, destination)
    assert destination.stat().st_ino == inode
    assert stage.is_dir()
