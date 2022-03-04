from __future__ import annotations

import os

import pytest
from dropbox.files import FolderMetadata
from dropbox.sharing import SharedFolderMetadata

import maestral.client
from maestral.exceptions import NotFoundError, PathError, DataCorruptionError
from maestral.utils.path import normalize
from maestral.utils.hashing import DropboxContentHasher

from .conftest import resources


if not ("DROPBOX_ACCESS_TOKEN" in os.environ or "DROPBOX_REFRESH_TOKEN" in os.environ):
    pytest.skip("Requires auth token", allow_module_level=True)


# Client API unit tests: we explicitly test those method calls which are not covered
# by current integration tests, either because they are not used by the sync module or
# because niche cases require additional testing.


def failing_content_hasher(
    start_fail: int = 0, end_fail: int = 4
) -> type[DropboxContentHasher]:
    class FailingHasher(DropboxContentHasher):

        START_FAIL = start_fail
        END_FAIL = end_fail
        DONE = -1

        _fake_hash = "c4eec85eb66b69b8f59ff76a97d5d97aac1b5eca8c6675b4e988a5deea786e53"

        def hexdigest(self) -> str:
            FailingHasher.DONE += 1

            if FailingHasher.START_FAIL <= FailingHasher.DONE < FailingHasher.END_FAIL:
                return FailingHasher._fake_hash
            else:
                return super().hexdigest()

    return FailingHasher


def test_upload_session(m):
    # not tested during integration tests

    large_file = resources + "/large-file.pdf"
    file_size = os.path.getsize(large_file)
    chunk_size = file_size // 10

    md = m.client.upload(large_file, "/large-file.pdf", chunk_size=chunk_size)

    assert md.content_hash == m.sync.get_local_hash(large_file)


def test_upload_hash_mismatch(m, monkeypatch):
    """Test that DataCorruptionError is raised after four failed attempts."""

    file = resources + "/file2.txt"

    Hasher = failing_content_hasher()
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", Hasher)

    with pytest.raises(DataCorruptionError):
        m.client.upload(file, "/file2.txt")

    assert not m.client.get_metadata("/file2.txt")


def test_upload_session_start_hash_mismatch(m, monkeypatch):
    """Test that DataCorruptionError is raised after four failed attempts when starting
    to upload session."""

    large_file = resources + "/large-file.pdf"
    file_size = os.path.getsize(large_file)
    chunk_size = file_size // 10

    Hasher = failing_content_hasher(0, 4)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", Hasher)

    with pytest.raises(DataCorruptionError):
        m.client.upload(large_file, "/large-file.pdf", chunk_size=chunk_size)

    assert not m.client.get_metadata("/large-file.pdf")


def test_upload_session_start_retry(m, monkeypatch):
    """Test that upload succeeds after three failed attempts when starting session."""

    large_file = resources + "/large-file.pdf"
    file_size = os.path.getsize(large_file)
    chunk_size = file_size // 10

    Hasher = failing_content_hasher(0, 3)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", Hasher)

    m.client.upload(large_file, "/large-file.pdf", chunk_size=chunk_size)

    remote_hash = m.client.get_metadata("/large-file.pdf").content_hash

    assert remote_hash == m.sync.get_local_hash(large_file)


def test_upload_session_append_hash_mismatch(m, monkeypatch):
    """Test that DataCorruptionError is raised after four failed attempts when appending
    to upload session."""

    large_file = resources + "/large-file.pdf"
    file_size = os.path.getsize(large_file)
    chunk_size = file_size // 10

    Hasher = failing_content_hasher(1, 5)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", Hasher)

    with pytest.raises(DataCorruptionError):
        m.client.upload(large_file, "/large-file.pdf", chunk_size=chunk_size)

    assert not m.client.get_metadata("/large-file.pdf")


def test_upload_session_append_hash_mismatch_retry(m, monkeypatch):
    """Test that upload succeeds after three failed attempts when appending to
    session."""

    large_file = resources + "/large-file.pdf"
    file_size = os.path.getsize(large_file)
    chunk_size = file_size // 10

    Hasher = failing_content_hasher(1, 4)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", Hasher)

    m.client.upload(large_file, "/large-file.pdf", chunk_size=chunk_size)

    remote_hash = m.client.get_metadata("/large-file.pdf").content_hash

    assert remote_hash == m.sync.get_local_hash(large_file)


def test_upload_session_finish_hash_mismatch(m, monkeypatch):
    """Test that DataCorruptionError is raised after four failed attempts when finishing
    upload session."""

    large_file = resources + "/large-file.pdf"
    file_size = os.path.getsize(large_file)
    chunk_size = file_size // 10

    Hasher = failing_content_hasher(9, 13)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", Hasher)

    with pytest.raises(DataCorruptionError):
        m.client.upload(large_file, "/large-file.pdf", chunk_size=chunk_size)

    assert not m.client.get_metadata("/large-file.pdf")


def test_upload_session_finish_hash_mismatch_retry(m, monkeypatch):
    """Test that upload succeeds after three failed attempts when finishing session."""

    large_file = resources + "/large-file.pdf"
    file_size = os.path.getsize(large_file)
    chunk_size = file_size // 10

    Hasher = failing_content_hasher(9, 12)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", Hasher)

    m.client.upload(large_file, "/large-file.pdf", chunk_size=chunk_size)

    remote_hash = m.client.get_metadata("/large-file.pdf").content_hash

    assert remote_hash == m.sync.get_local_hash(large_file)


def test_download_hash_mismatch(m, monkeypatch, tmp_path):
    # not tested during integration tests

    file = resources + "/file2.txt"
    m.client.upload(file, "/file2.txt")

    Hasher = failing_content_hasher(0, 4)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", Hasher)

    with pytest.raises(DataCorruptionError):
        m.client.download("/file2.txt", str(tmp_path / "file2.txt"))


@pytest.mark.parametrize("batch_size", [10, 30])
@pytest.mark.parametrize("force_async", [True, False])
def test_batch_methods(m, batch_size, force_async):
    # batch methods are not currently used by sync module

    folders = [f"/folder {i}" for i in range(20)]

    # create some test directories
    res = m.client.make_dir_batch(folders + ["/invalid\\"], force_async=force_async)

    for i in range(20):
        assert isinstance(res[i], FolderMetadata)
        assert res[i].path_lower == normalize(folders[i])

    assert isinstance(res[20], PathError)

    # remove them again
    res = m.client.remove_batch(
        [(folder, None) for folder in folders] + [("/not_a_folder", None)],
        batch_size=batch_size,
    )

    for i in range(20):
        assert isinstance(res[i], FolderMetadata)
        assert res[i].path_lower == normalize(folders[i])

    assert isinstance(res[20], NotFoundError)


@pytest.mark.parametrize("force_async", [True, False])
def test_share_dir_new(m, force_async):
    """Test creating a shared directory."""
    md_old = m.client.get_metadata("/folder")
    md_shared = m.client.share_dir("/folder", force_async=force_async)

    assert md_old is None
    assert isinstance(md_shared, SharedFolderMetadata)


def test_share_dir_existing(m):
    """Test sharing an existing directory."""
    md = m.client.make_dir("/folder")
    md_shared = m.client.share_dir("/folder")

    assert md.sharing_info is None
    assert isinstance(md_shared, SharedFolderMetadata)
