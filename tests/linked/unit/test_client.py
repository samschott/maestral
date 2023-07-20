from __future__ import annotations

import os

import pytest
import requests

import maestral.client
from maestral.client import DropboxClient
from maestral.core import AccountType, FolderMetadata
from maestral.exceptions import (
    NotFoundError,
    PathError,
    DataCorruptionError,
    SharedLinkError,
)
from maestral.utils.path import normalize, content_hash
from maestral.utils.hashing import DropboxContentHasher

from .conftest import resources


if not ("DROPBOX_ACCESS_TOKEN" in os.environ or "DROPBOX_REFRESH_TOKEN" in os.environ):
    pytest.skip("Requires auth token", allow_module_level=True)


# Client API unit tests: we currently test those method calls which are not covered
# by integration tests, either because they are not used by the sync module or because
# niche cases require additional testing.
# TODO: Expand to cover all methods, independent of integration tests.


def failing_content_hasher(
    start_fail: int, end_fail: int
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


def test_upload(client: DropboxClient) -> None:
    """Test upload in a single chunk"""

    file = resources + "/file.txt"
    file_size = os.path.getsize(file)
    client.UPLOAD_REQUEST_CHUNK_SIZE = file_size * 2

    md = client.upload(file, "/file.txt")
    assert md.content_hash == content_hash(file)[0]


def test_upload_session(client: DropboxClient) -> None:
    """Test an upload session in multiple chunks"""

    large_file = resources + "/large-file.pdf"
    file_size = os.path.getsize(large_file)
    client.UPLOAD_REQUEST_CHUNK_SIZE = file_size // 10

    md = client.upload(large_file, "/large-file.pdf")

    assert md.content_hash == content_hash(large_file)[0]


def test_upload_hash_mismatch(client: DropboxClient, monkeypatch) -> None:
    """Test that DataCorruptionError is raised after 10 failed attempts."""

    file = resources + "/file.txt"

    hasher = failing_content_hasher(0, 11)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", hasher)

    with pytest.raises(DataCorruptionError):
        client.upload(file, "/file.txt")

    assert not client.get_metadata("/file.txt")


def test_upload_hash_mismatch_retry(client: DropboxClient, monkeypatch) -> None:
    """Test that upload succeeds after 3 failed attempts when starting session."""

    file = resources + "/file.txt"

    hasher = failing_content_hasher(0, 3)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", hasher)

    md = client.upload(file, "/file.txt")

    local_hash = content_hash(file)[0]

    assert md.content_hash == local_hash
    assert client.get_metadata("/file.txt").content_hash == local_hash


def test_upload_session_start_hash_mismatch(client: DropboxClient, monkeypatch) -> None:
    """Test that DataCorruptionError is raised after 10 failed attempts when starting
    to upload session."""

    large_file = resources + "/large-file.pdf"
    file_size = os.path.getsize(large_file)
    client.UPLOAD_REQUEST_CHUNK_SIZE = file_size // 10

    hasher = failing_content_hasher(0, 11)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", hasher)

    with pytest.raises(DataCorruptionError):
        client.upload(large_file, "/large-file.pdf")

    assert not client.get_metadata("/large-file.pdf")


def test_upload_session_start_retry(client: DropboxClient, monkeypatch) -> None:
    """Test that upload succeeds after 3 failed attempts when starting session."""

    large_file = resources + "/large-file.pdf"
    file_size = os.path.getsize(large_file)
    client.UPLOAD_REQUEST_CHUNK_SIZE = file_size // 10

    hasher = failing_content_hasher(0, 3)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", hasher)

    md = client.upload(large_file, "/large-file.pdf")

    assert md.content_hash == content_hash(large_file)[0]


def test_upload_session_append_hash_mismatch(
    client: DropboxClient, monkeypatch
) -> None:
    """Test that DataCorruptionError is raised after 10 failed attempts when appending
    to upload session."""

    large_file = resources + "/large-file.pdf"
    file_size = os.path.getsize(large_file)
    client.UPLOAD_REQUEST_CHUNK_SIZE = file_size // 10

    hasher = failing_content_hasher(1, 12)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", hasher)

    with pytest.raises(DataCorruptionError):
        client.upload(large_file, "/large-file.pdf")

    assert not client.get_metadata("/large-file.pdf")


def test_upload_session_append_hash_mismatch_retry(
    client: DropboxClient, monkeypatch
) -> None:
    """Test that upload succeeds after 3 failed attempts when appending to
    session."""

    large_file = resources + "/large-file.pdf"
    file_size = os.path.getsize(large_file)
    client.UPLOAD_REQUEST_CHUNK_SIZE = file_size // 10

    hasher = failing_content_hasher(1, 4)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", hasher)

    md = client.upload(large_file, "/large-file.pdf")
    assert md.content_hash == content_hash(large_file)[0]


def test_upload_session_finish_hash_mismatch(
    client: DropboxClient, monkeypatch
) -> None:
    """Test that DataCorruptionError is raised after 10 failed attempts when finishing
    upload session."""

    large_file = resources + "/large-file.pdf"
    file_size = os.path.getsize(large_file)
    client.UPLOAD_REQUEST_CHUNK_SIZE = file_size // 10

    hasher = failing_content_hasher(9, 20)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", hasher)

    with pytest.raises(DataCorruptionError):
        client.upload(large_file, "/large-file.pdf")

    assert not client.get_metadata("/large-file.pdf")


def test_upload_session_finish_hash_mismatch_retry(
    client: DropboxClient, monkeypatch
) -> None:
    """Test that download fails after 3 retried DataCorruptionErrors when finishing
    download session."""

    large_file = resources + "/large-file.pdf"
    file_size = os.path.getsize(large_file)
    client.UPLOAD_REQUEST_CHUNK_SIZE = file_size // 10

    hasher = failing_content_hasher(9, 12)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", hasher)

    md = client.upload(large_file, "/large-file.pdf")

    assert md.content_hash == content_hash(large_file)[0]


def test_download_hash_mismatch(client: DropboxClient, monkeypatch, tmp_path) -> None:
    """Test that download fails after 10 retried DataCorruptionErrors."""

    file = resources + "/file.txt"
    client.upload(file, "/file.txt")

    hasher = failing_content_hasher(0, 11)
    monkeypatch.setattr(maestral.client, "DropboxContentHasher", hasher)

    with pytest.raises(DataCorruptionError):
        client.download("/file.txt", str(tmp_path / "file.txt"))


@pytest.mark.parametrize("batch_size", [10, 30])
@pytest.mark.parametrize("force_async", [True, False])
def test_batch_methods(client: DropboxClient, batch_size, force_async) -> None:
    # batch methods are not currently used by sync module

    folders = [f"/folder {i}" for i in range(20)]

    # create some test directories
    res_create = client.make_dir_batch(
        folders + ["/invalid\\"], force_async=force_async
    )

    for i in range(20):
        md_create = res_create[i]
        assert isinstance(md_create, FolderMetadata)
        assert md_create.path_lower == normalize(folders[i])

    assert isinstance(res_create[20], PathError)

    # remove them again
    res_remove = client.remove_batch(
        [(folder, None) for folder in folders] + [("/not_a_folder", None)],
        batch_size=batch_size,
    )

    for i in range(20):
        md_remove = res_remove[i]
        assert isinstance(md_remove, FolderMetadata)
        assert md_remove.path_lower == normalize(folders[i])

    assert isinstance(res_remove[20], NotFoundError)


@pytest.mark.parametrize("force_async", [True, False])
def test_share_dir_new(client: DropboxClient, force_async: bool) -> None:
    """Test creating a shared directory."""
    md_old = client.get_metadata("/folder")
    md_shared = client.share_dir("/folder", force_async=force_async)

    assert md_old is None
    assert isinstance(md_shared, FolderMetadata)


def test_share_dir_existing(client: DropboxClient) -> None:
    """Test sharing an existing directory."""
    md = client.make_dir("/folder")
    md_shared = client.share_dir("/folder")

    assert not md.shared
    assert isinstance(md_shared, FolderMetadata)


def test_sharedlink_lifecycle(client: DropboxClient) -> None:
    # create a folder to share
    dbx_path = "/shared_folder"
    client.make_dir(dbx_path)

    # test creating a shared link
    link_data = client.create_shared_link(dbx_path)

    resp = requests.get(link_data.url)
    assert resp.status_code == 200

    links = client.list_shared_links(dbx_path)
    assert link_data.url in [link.url for link in links]

    # test revoking a shared link
    client.revoke_shared_link(link_data.url)
    links = client.list_shared_links(dbx_path)
    assert link_data.url not in [link.url for link in links]


def test_sharedlink_errors(client: DropboxClient) -> None:
    dbx_path = "/shared_folder"
    client.make_dir(dbx_path)

    # test creating a shared link with password fails on basic account
    account_info = client.get_account_info()

    if account_info.account_type is AccountType.Basic:
        with pytest.raises(SharedLinkError):
            client.create_shared_link(dbx_path, password="secret")

    # test creating a shared link with the same settings as an existing link
    client.create_shared_link(dbx_path)

    with pytest.raises(SharedLinkError):
        client.create_shared_link(dbx_path)

    # test creating a shared link with an invalid path
    with pytest.raises(NotFoundError):
        client.create_shared_link("/this_is_not_a_file.txt")

    # test listing shared links for an invalid path
    with pytest.raises(NotFoundError):
        client.list_shared_links("/this_is_not_a_file.txt")

    # test revoking a non existent link
    with pytest.raises(NotFoundError):
        client.revoke_shared_link(
            "https://www.dropbox.com/sh/48r2qxq748jfk5x/AAAS-niuW"
        )

    # test revoking a malformed link
    with pytest.raises(SharedLinkError):
        client.revoke_shared_link("https://www.testlink.de")
