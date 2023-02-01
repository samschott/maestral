from __future__ import annotations

import sys
import os
import os.path as osp
import subprocess

import pytest
from dropbox import files

from maestral.main import Maestral
from maestral.core import FileMetadata
from maestral.exceptions import (
    NotFoundError,
    UnsupportedFileTypeForDiff,
    SyncError,
    InotifyError,
)
from maestral.constants import FileStatus, IDLE
from maestral.utils.path import delete
from maestral.utils.integration import get_inotify_limits

from .conftest import wait_for_idle


if not ("DROPBOX_ACCESS_TOKEN" in os.environ or "DROPBOX_REFRESH_TOKEN" in os.environ):
    pytest.skip("Requires auth token", allow_module_level=True)


def _create_file_with_content(
    m: Maestral, dbx_path: str, content: str | bytes
) -> FileMetadata:
    if isinstance(content, str):
        content = content.encode()

    return m.client.dbx.files_upload(content, dbx_path, mode=files.WriteMode.overwrite)


def test_status_properties(m: Maestral) -> None:
    assert not m.pending_link
    assert not m.pending_dropbox_folder

    assert m.status == IDLE
    assert m.running
    assert m.connected
    assert not m.paused
    assert not m.sync_errors
    assert not m.fatal_errors

    m._root_logger.info("test message")
    assert m.status == "test message"


def test_file_status(m: Maestral) -> None:
    # test synced folder
    file_status = m.get_file_status(m.dropbox_path)
    assert file_status == FileStatus.Synced.value

    # test unwatched outside of dropbox
    file_status = m.get_file_status("/url/local")
    assert file_status == FileStatus.Unwatched.value

    # test unwatched non-existent
    file_status = m.get_file_status("/this is not a folder")
    assert file_status == FileStatus.Unwatched.value

    # test unwatched when paused
    m.stop_sync()
    wait_for_idle(m)

    file_status = m.get_file_status(m.dropbox_path)
    assert file_status == FileStatus.Unwatched.value

    m.start_sync()
    wait_for_idle(m)

    # test error status
    invalid_local_folder = m.dropbox_path + "/test_folder\\"
    os.mkdir(invalid_local_folder)
    wait_for_idle(m)

    file_status = m.get_file_status(invalid_local_folder)
    file_status_parent = m.get_file_status(m.dropbox_path)

    assert file_status == FileStatus.Error.value
    assert file_status_parent == FileStatus.Error.value


def test_move_dropbox_folder(m: Maestral) -> None:
    new_dir_short = "~/New Dropbox"
    new_dir = osp.realpath(osp.expanduser(new_dir_short))

    m.move_dropbox_directory(new_dir_short)
    assert osp.isdir(new_dir)
    assert m.dropbox_path == new_dir

    wait_for_idle(m)

    # assert that sync was resumed after moving folder
    assert m.running


def test_move_dropbox_folder_to_itself(m: Maestral) -> None:
    m.move_dropbox_directory(m.dropbox_path)

    # assert that sync is still running
    assert m.running


def test_move_dropbox_folder_to_existing(m: Maestral) -> None:
    new_dir_short = "~/New Dropbox"
    new_dir = osp.realpath(osp.expanduser(new_dir_short))
    os.mkdir(new_dir)

    try:
        with pytest.raises(FileExistsError):
            m.move_dropbox_directory(new_dir)

        # assert that sync is still running
        assert m.running

    finally:
        # cleanup
        delete(new_dir)


# API integration tests


def test_selective_sync(m: Maestral) -> None:
    """
    Tests :meth:`Maestral.exclude_item`, :meth:`MaestralMaestral.include_item`,
    :meth:`Maestral.excluded_status` and :meth:`Maestral.excluded_items`.
    """

    dbx_dirs = [
        "/selective_sync_test_folder",
        "/independent_folder",
        "/selective_sync_test_folder/subfolder_0",
        "/selective_sync_test_folder/subfolder_1",
    ]

    local_dirs = [m.to_local_path(dbx_path) for dbx_path in dbx_dirs]

    # create folder structure
    for path in local_dirs:
        os.mkdir(path)

    wait_for_idle(m)

    # exclude "/selective_sync_test_folder" from sync
    m.exclude_item("/selective_sync_test_folder")
    wait_for_idle(m)

    # check that local items have been deleted
    assert not osp.exists(m.to_local_path("/selective_sync_test_folder"))

    # check that `Maestral.excluded_items` only contains top-level folder
    assert "/selective_sync_test_folder" in m.excluded_items
    assert "/selective_sync_test_folder/subfolder_0" not in m.excluded_items
    assert "/selective_sync_test_folder/subfolder_1" not in m.excluded_items

    # check that `Maestral.excluded_status` returns the correct values
    assert m.excluded_status("") == "partially excluded"
    assert m.excluded_status("/independent_folder") == "included"

    for dbx_path in dbx_dirs:
        if dbx_path != "/independent_folder":
            assert m.excluded_status(dbx_path) == "excluded"

    # include folder in sync, check that it worked
    m.include_item("/selective_sync_test_folder")
    wait_for_idle(m)

    assert osp.exists(m.to_local_path("/selective_sync_test_folder"))
    assert "/selective_sync_test_folder" not in m.excluded_items

    for dbx_path in dbx_dirs:
        assert m.excluded_status(dbx_path) == "included"

    # test excluding a non-existent folder
    with pytest.raises(NotFoundError):
        m.exclude_item("/bogus_folder")

    # check for fatal errors
    assert not m.fatal_errors


def test_selective_sync_global(m: Maestral) -> None:
    """Test :meth:`Maestral.exclude_items` to change all items at once."""

    dbx_dirs = [
        "/selective_sync_test_folder",
        "/independent_folder",
        "/selective_sync_test_folder/subfolder_0",
        "/selective_sync_test_folder/subfolder_1",
    ]

    local_dirs = [m.to_local_path(dbx_path) for dbx_path in dbx_dirs]

    # create folder structure
    for path in local_dirs:
        os.mkdir(path)

    wait_for_idle(m)

    # exclude "/selective_sync_test_folder" and one child from sync
    m.excluded_items = [
        "/selective_sync_test_folder",
        "/selective_sync_test_folder/subfolder_0",
    ]
    wait_for_idle(m)

    # check that local items have been deleted
    assert not osp.exists(m.to_local_path("/selective_sync_test_folder"))

    # check that `Maestral.excluded_items` has been updated correctly
    assert m.excluded_items == ["/selective_sync_test_folder"]

    # exclude only child folder from sync, check that it worked
    m.excluded_items = ["/selective_sync_test_folder/subfolder_0"]
    wait_for_idle(m)

    assert osp.exists(m.to_local_path("/selective_sync_test_folder"))
    assert osp.exists(m.to_local_path("/selective_sync_test_folder/subfolder_1"))
    assert m.excluded_items == ["/selective_sync_test_folder/subfolder_0"]

    # check for fatal errors
    assert not m.fatal_errors


def test_selective_sync_nested(m: Maestral) -> None:
    """Tests special cases of nested selected sync changes."""

    dbx_dirs = [
        "/selective_sync_test_folder",
        "/independent_folder",
        "/selective_sync_test_folder/subfolder_0",
        "/selective_sync_test_folder/subfolder_1",
    ]

    local_dirs = [m.to_local_path(dbx_path) for dbx_path in dbx_dirs]

    # create folder structure
    for path in local_dirs:
        os.mkdir(path)

    wait_for_idle(m)

    # exclude "/selective_sync_test_folder" from sync
    m.exclude_item("/selective_sync_test_folder")
    wait_for_idle(m)

    # test including a folder inside "/selective_sync_test_folder",
    # "/selective_sync_test_folder" should become included itself but
    # its other children will still be excluded
    m.include_item("/selective_sync_test_folder/subfolder_0")

    assert "/selective_sync_test_folder" not in m.excluded_items
    assert "/selective_sync_test_folder/subfolder_1" in m.excluded_items

    # check for fatal errors
    assert not m.fatal_errors


def test_get_file_diff(m: Maestral) -> None:
    dbx_path = "/test.txt"

    md_old = _create_file_with_content(m, dbx_path, "old")
    md_new = _create_file_with_content(m, dbx_path, "new")
    diff = m.get_file_diff(md_old.rev, md_new.rev)

    assert diff[2] == "@@ -1 +1 @@\n"
    assert diff[3] == "-old"
    assert diff[4] == "+new"


def test_get_file_diff_local(m: Maestral) -> None:
    dbx_path = "/test.txt"
    local_path = m.to_local_path(dbx_path)

    m.stop_sync()
    wait_for_idle(m)

    md_old = _create_file_with_content(m, dbx_path, "old")

    with open(local_path, "w") as f:
        f.write("new")

    diff = m.get_file_diff(md_old.rev, None)

    assert diff[2] == "@@ -1 +1 @@\n"
    assert diff[3] == "-old"
    assert diff[4] == "+new"


def test_get_file_diff_not_found(m: Maestral) -> None:
    dbx_path = "/test.txt"

    md_new = _create_file_with_content(m, dbx_path, "new")

    with pytest.raises(NotFoundError):
        m.get_file_diff("015db1e6dec9da000000001f7709020", md_new.rev)


def test_get_file_diff_unsupported_ext(m: Maestral) -> None:
    """Tests file diffs for unsupported file types."""

    dbx_path = "/test.pdf"
    md_old = _create_file_with_content(m, dbx_path, "old")
    md_new = _create_file_with_content(m, dbx_path, "new")

    with pytest.raises(UnsupportedFileTypeForDiff):
        m.get_file_diff(md_old.rev, md_new.rev)


def test_get_file_diff_unsupported_content(m: Maestral) -> None:
    """Tests file diffs for unsupported file types."""

    dbx_path = "/test.txt"
    # Upload a compiled c file with .txt extension
    md_old = _create_file_with_content(m, dbx_path, b"\xcf\xfa\xed\xfe\x07")
    md_new = _create_file_with_content(m, dbx_path, "new")

    with pytest.raises(UnsupportedFileTypeForDiff):
        m.get_file_diff(md_old.rev, md_new.rev)


def test_get_file_diff_unsupported_content_local(m: Maestral) -> None:
    dbx_path = "/test.txt"
    local_path = m.to_local_path(dbx_path)

    m.stop_sync()
    wait_for_idle(m)

    md_old = _create_file_with_content(m, dbx_path, "old")

    with open(local_path, "wb") as f:
        f.write("möglich".encode("cp273"))

    with pytest.raises(UnsupportedFileTypeForDiff):
        m.get_file_diff(md_old.rev, None)


def test_restore(m: Maestral) -> None:
    """Tests restoring an old revision"""

    dbx_path = "/file.txt"
    local_path = m.to_local_path(dbx_path)

    # create a local file and sync it, remember its rev
    with open(local_path, "w") as f:
        f.write("old content")

    wait_for_idle(m)

    old_md = m.client.get_metadata(dbx_path)
    assert isinstance(old_md, FileMetadata)

    # modify the file and sync it
    with open(local_path, "w") as f:
        f.write("new content")

    wait_for_idle(m)

    new_md = m.client.get_metadata(dbx_path)
    assert isinstance(new_md, FileMetadata)
    assert new_md.content_hash == m.sync.get_local_hash(local_path)

    # restore the old rev

    try:
        m.restore(dbx_path, old_md.rev)
    except SyncError as exc:
        # catch all error for restore in progress, raise otherwise
        if "in progress" not in exc.title:
            raise

    wait_for_idle(m)

    with open(local_path) as f:
        restored_content = f.read()

    assert restored_content == "old content"


def test_restore_failed(m: Maestral) -> None:
    """Tests restoring a non-existing file"""

    with pytest.raises(NotFoundError):
        m.restore("/restored-file", "015982ea314dac40000000154e40990")


@pytest.mark.skipif(sys.platform != "linux", reason="inotify specific test")
@pytest.mark.skipif(os.getenv("CI", False) is False, reason="Only running on CI")
def test_inotify_error(m: Maestral) -> None:
    max_user_watches, max_user_instances, _ = get_inotify_limits()

    try:
        subprocess.check_call(["sudo", "sysctl", "-w", "fs.inotify.max_user_watches=1"])
    except subprocess.CalledProcessError:
        return

    try:
        m.stop_sync()
        wait_for_idle(m)

        # create some folders for us to watch
        os.mkdir(m.dropbox_path + "/folder 1")
        os.mkdir(m.dropbox_path + "/folder 2")
        os.mkdir(m.dropbox_path + "/folder 3")

        m.start_sync()

        assert len(m.fatal_errors) == 1
        assert isinstance(m.fatal_errors[0], InotifyError)

    finally:
        subprocess.check_call(
            ["sudo", "sysctl", "-w", f"fs.inotify.max_user_watches={max_user_watches}"]
        )
