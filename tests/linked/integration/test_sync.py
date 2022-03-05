import sys
import os
import os.path as osp
import time
import shutil
import timeit

import pytest
from watchdog.utils.dirsnapshot import DirectorySnapshot
from watchdog.events import FileCreatedEvent
from dropbox.files import WriteMode
from maestral.models import SyncEvent
from maestral.utils import sanitize_string
from maestral.utils.appdirs import get_home_dir
from maestral.utils.path import (
    delete,
    move,
    is_fs_case_sensitive,
    normalize,
    fs_max_lengths_for_path,
)
from maestral.exceptions import PathError

from tests.linked.conftest import assert_synced, wait_for_idle, resources


if not ("DROPBOX_ACCESS_TOKEN" in os.environ or "DROPBOX_REFRESH_TOKEN" in os.environ):
    pytest.skip("Requires auth token", allow_module_level=True)


HOME = get_home_dir()


# ==== test basic sync =================================================================


def test_setup(m):
    assert_synced(m)
    assert_no_errors(m)


def test_file_lifecycle(m):
    """Tests creating, modifying and deleting a file."""

    # Test local file creation.

    shutil.copy(resources + "/file.txt", m.dropbox_path)

    wait_for_idle(m)
    assert_exists(m, "/file.txt")
    assert_child_count(m, "/", 1)

    assert_synced(m)
    assert_no_errors(m)

    # Test local file changes.

    with open(m.dropbox_path + "/file.txt", "w") as f:
        f.write("content changed")

    wait_for_idle(m)

    assert_exists(m, "/file.txt")
    assert_child_count(m, "/", 1)

    assert_synced(m)
    assert_no_errors(m)

    # Test remote file changes.

    m.client.upload(resources + "/file1.txt", "/file.txt", mode=WriteMode.overwrite)

    wait_for_idle(m)

    assert_exists(m, "/file.txt")
    assert_child_count(m, "/", 1)

    assert_synced(m)
    assert_no_errors(m)

    # Test remote file deletion.

    m.client.remove("/file.txt")

    wait_for_idle(m)

    assert_child_count(m, "/", 0)

    assert_synced(m)
    assert_no_errors(m)


def test_folder_tree_local(m):
    """Tests the upload sync of a nested local folder structure."""

    # Test local tree creation.

    shutil.copytree(resources + "/test_folder", m.dropbox_path + "/test_folder")

    snap = DirectorySnapshot(resources + "/test_folder")
    num_items = len([p for p in snap.paths if not m.sync.is_excluded(p)])

    wait_for_idle(m, 10)

    assert_child_count(m, "/", num_items)

    assert_synced(m)
    assert_no_errors(m)

    # Test local tree deletion.

    delete(m.dropbox_path + "/test_folder")

    wait_for_idle(m)

    assert_child_count(m, "/", 0)

    assert_synced(m)
    assert_no_errors(m)


def test_folder_tree_remote(m):
    """Tests the download sync of a nested remote folder structure."""

    # Test remote tree creation.

    for i in range(1, 11):
        path = i * "/nested_folder"
        m.client.make_dir(path)

    wait_for_idle(m)

    assert_child_count(m, "/", 10)

    assert_synced(m)
    assert_no_errors(m)

    # Test remote tree deletion.

    m.client.remove("/nested_folder")
    wait_for_idle(m, 15)

    assert_child_count(m, "/", 0)

    assert_synced(m)
    assert_no_errors(m)


def test_local_indexing(m):
    """Tests the upload sync of a nested local folder structure during startup sync."""

    m.stop_sync()
    wait_for_idle(m)

    # Create a local tree.

    shutil.copytree(resources + "/test_folder", m.dropbox_path + "/test_folder")

    snap = DirectorySnapshot(resources + "/test_folder")
    num_items = len([p for p in snap.paths if not m.sync.is_excluded(p)])

    # Start sync and check that all items are indexed and uploaded.

    m.start_sync()
    wait_for_idle(m, 10)

    assert_child_count(m, "/", num_items)

    assert_synced(m)
    assert_no_errors(m)


def test_case_change_local(m):
    """
    Tests the upload sync of local rename which only changes the casing of the name.
    """

    # Start with nested folders.
    os.mkdir(m.dropbox_path + "/folder")
    os.mkdir(m.dropbox_path + "/folder/Subfolder")
    wait_for_idle(m)

    # Rename local parent folder to upper case.
    shutil.move(m.dropbox_path + "/folder", m.dropbox_path + "/FOLDER")
    wait_for_idle(m)

    # Check that case change was propagated to the server.

    assert osp.isdir(m.dropbox_path + "/FOLDER")
    assert osp.isdir(m.dropbox_path + "/FOLDER/Subfolder")
    assert (
        m.client.get_metadata("/folder").name == "FOLDER"
    ), "casing was not propagated to Dropbox"

    assert_synced(m)
    assert_no_errors(m)


def test_case_change_remote(m):
    """
    Tests the download sync of a remote rename which only changes the casing of the file
    name.
    """

    # Start with nested folders.
    os.mkdir(m.dropbox_path + "/folder")
    os.mkdir(m.dropbox_path + "/folder/Subfolder")
    wait_for_idle(m)

    assert_synced(m)

    # Rename the remote folder.
    m.client.move("/folder", "/FOLDER", autorename=True)

    wait_for_idle(m)

    # Check that case change was propagated to the local folder.

    assert osp.isdir(m.dropbox_path + "/FOLDER")
    assert osp.isdir(m.dropbox_path + "/FOLDER/Subfolder")
    assert (
        m.client.get_metadata("/folder").name == "FOLDER"
    ), "casing was not propagated to local folder"

    assert_synced(m)
    assert_no_errors(m)


def test_mignore(m):
    """Tests the exclusion of local items by a mignore file."""

    # 1) Test that changes have no effect when the sync is running.

    os.mkdir(m.dropbox_path + "/bar")
    os.mkdir(m.dropbox_path + "/folder")
    wait_for_idle(m)

    with open(m.sync.mignore_path, "w") as f:
        f.write("foo/\n")  # ignore folder "foo"
        f.write("bar\n")  # ignore file or folder "bar"
        f.write("build\n")  # ignore file or folder "build"

    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/bar")

    # 2) Test that items are removed after a restart.

    m.stop_sync()
    wait_for_idle(m)
    m.start_sync()

    os.mkdir(m.dropbox_path + "/foo")
    wait_for_idle(m)

    assert not m.client.get_metadata("/foo")
    assert not m.client.get_metadata("/bar")

    # 3) Test that renaming an item excludes it.

    move(m.dropbox_path + "/folder", m.dropbox_path + "/build")
    wait_for_idle(m)

    assert not m.client.get_metadata("/build")

    # 4) Test that renaming an item includes it.

    move(m.dropbox_path + "/build", m.dropbox_path + "/folder")
    wait_for_idle(m)

    assert_exists(m, "/folder")
    assert_synced(m)
    assert_no_errors(m)


def test_move_to_existing_file(m):
    """Tests moving a local file onto another and replacing it."""

    # Create two local files.

    path0 = m.dropbox_path + "/file0.txt"
    path1 = m.dropbox_path + "/file1.txt"

    with open(path0, "a") as f:
        f.write("c0")

    with open(path1, "a") as f:
        f.write("c1")

    wait_for_idle(m)

    # Move file0 to file1.

    shutil.move(path0, path1)

    wait_for_idle(m)

    # Check that move was propagated to the server.

    assert_exists(m, "/file1.txt")
    assert_child_count(m, "/", 1)

    assert_synced(m)
    assert_no_errors(m)


def test_excluded_folder_cleared_on_deletion(m):
    """
    Tests that an entry in our selective sync excluded list gets removed when the
    corresponding item is deleted.
    """

    dbx_path = "/selective_sync_test_folder"
    local_path = m.to_local_path("/selective_sync_test_folder")

    # Create local folder.
    os.mkdir(local_path)
    wait_for_idle(m)

    # Exclude the folder from sync.
    m.exclude_item(dbx_path)
    wait_for_idle(m)

    assert normalize(dbx_path) in m.excluded_items
    assert m.excluded_status(dbx_path) == "excluded"
    assert not osp.exists(local_path)

    # Check that an excluded folder is removed from excluded_list on deletion.
    m.client.remove(dbx_path)
    wait_for_idle(m)

    assert (
        normalize(dbx_path) not in m.excluded_items
    ), 'deleted item is still in "excluded_items" list'

    assert_synced(m)
    assert_no_errors(m)


def test_unix_permissions(m):
    """
    Tests that a newly downloaded file is created with default permissions for our
    process and that any locally set permissions are preserved on remote file
    modifications.
    """

    # Create a remote file and wait for it to download.
    dbx_path = "/file"
    local_path = m.to_local_path(dbx_path)

    m.client.upload(resources + "/file.txt", dbx_path)
    wait_for_idle(m)

    # Check if its permissions correspond to the default user permissions by comparig
    # them to a reference file in the home directory.

    reference_file = osp.join(HOME, "reference")

    try:
        open(reference_file, "ab").close()
        assert os.stat(local_path).st_mode == os.stat(reference_file).st_mode
    finally:
        delete(reference_file)

    # Make the local file executable.
    os.chmod(local_path, 0o744)
    new_mode = os.stat(local_path).st_mode  # might not be 744...
    wait_for_idle(m)

    # Perform some remote modifications.
    m.client.upload(resources + "/file1.txt", dbx_path, mode=WriteMode.overwrite)
    wait_for_idle(m)

    # Check that the local permissions have not changed.
    assert os.stat(local_path).st_mode == new_mode

    assert_synced(m)
    assert_no_errors(m)


@pytest.mark.parametrize(
    "name",
    [
        "tést_file",  # U+00E9
        "täst_file",  # U+00E4
    ],
)
def test_unicode_allowed(m, name):
    """Tests syncing files with exotic unicode characters."""

    local_path = osp.join(m.dropbox_path, name)

    os.makedirs(local_path)

    wait_for_idle(m)

    assert_exists(m, "/" + name)
    assert_child_count(m, "/", 1)

    assert_synced(m)
    assert_no_errors(m)


# ==== test conflict resolution ========================================================


def test_file_conflict_modified(m):
    """Tests conflicting local vs remote file changes."""

    # Create a test file and stop syncing.
    shutil.copy(resources + "/file.txt", m.dropbox_path)
    wait_for_idle(m)

    m.stop_sync()
    wait_for_idle(m)

    # Modify file.txt locally
    with open(m.dropbox_path + "/file.txt", "a") as f:
        f.write(" modified conflict")

    # Modify file.txt on remote.
    m.client.upload(
        resources + "/file2.txt",
        "/file.txt",
        mode=WriteMode.overwrite,
    )

    # Resume syncing and check for conflicting copy.
    m.start_sync()

    wait_for_idle(m)

    assert_exists(m, "/file.txt")
    assert_conflict(m, "/", "file.txt")
    assert_child_count(m, "/", 2)

    assert_synced(m)
    assert_no_errors(m)


def test_file_conflict_created(m):
    """Tests conflicting local vs remote file creations."""

    m.stop_sync()

    # Create local and remote files at the same location with different contents.

    shutil.copy(resources + "/file.txt", m.dropbox_path)

    m.client.upload(
        resources + "/file2.txt",
        "/file.txt",
        mode=WriteMode.overwrite,
    )

    # Resume syncing and check for conflicting copy
    m.start_sync()
    wait_for_idle(m)

    assert_exists(m, "/file.txt")
    assert_conflict(m, "/", "file.txt")
    assert_child_count(m, "/", 2)

    assert_synced(m)
    assert_no_errors(m)


def test_remote_file_replaced_by_folder(m):
    """Tests the download sync when a file is replaced by a folder."""

    # Create a test file.

    shutil.copy(resources + "/file.txt", m.dropbox_path + "/file.txt")
    wait_for_idle(m)

    with m.sync.sync_lock:

        # Replace the remote file with folder.
        m.client.remove("/file.txt")
        m.client.make_dir("/file.txt")

    wait_for_idle(m, 10)

    # Check that the remote change was applied locally.

    assert_child_count(m, "/", 1)
    assert os.path.isdir(m.dropbox_path + "/file.txt")

    assert_synced(m)
    assert_no_errors(m)


def test_remote_file_replaced_by_folder_and_unsynced_local_changes(m):
    """
    Tests the download sync when a file is replaced by a folder and the local file has
    unsynced changes.
    """

    shutil.copy(resources + "/file.txt", m.dropbox_path + "/file.txt")
    wait_for_idle(m)

    with m.sync.sync_lock:

        # replace remote file with folder
        m.client.remove("/file.txt")
        m.client.make_dir("/file.txt")

        # create local changes
        with open(m.dropbox_path + "/file.txt", "a") as f:
            f.write(" modified")

    wait_for_idle(m)

    assert_exists(m, "/file.txt")
    assert_conflict(m, "/", "file.txt")
    assert_child_count(m, "/", 2)

    assert_synced(m)
    assert_no_errors(m)


def test_remote_folder_replaced_by_file(m):
    """Tests the download sync when a folder is replaced by a file."""

    m.client.make_dir("/folder")
    wait_for_idle(m)

    # replace remote folder with file

    with m.sync.sync_lock:
        m.client.remove("/folder")
        m.client.upload(resources + "/file.txt", "/folder")

    wait_for_idle(m)

    assert os.path.isfile(m.dropbox_path + "/folder")
    assert_child_count(m, "/", 1)

    assert_synced(m)
    assert_no_errors(m)


def test_remote_folder_replaced_by_file_and_unsynced_local_changes(m):
    """
    Tests the download sync when a folder is replaced by a file and the local folder has
    unsynced changes.
    """

    # Create a remote folder.
    os.mkdir(m.dropbox_path + "/folder")
    wait_for_idle(m)

    with m.sync.sync_lock:

        # Replace the remote folder with a file.
        # Remote state:
        # - '/Sync Tests/folder'

        m.client.remove("/folder")
        m.client.upload(resources + "/file.txt", "/folder")

        # Make some local changes to the folder.
        # Local state:
        # - '/Sync Tests/folder/'
        # - '/Sync Tests/folder/subfolder'
        os.mkdir(m.dropbox_path + "/folder/subfolder")

    wait_for_idle(m)

    # Check for expected result:
    # - '/Sync Tests/folder'
    # - '/Sync Tests/folder (conflicting copy)/'
    # - '/Sync Tests/folder (conflicting copy)/subfolder'

    assert_exists(m, "/folder")
    assert_conflict(m, "/", "folder")
    assert_child_count(m, "/", 3)

    assert_synced(m)
    assert_no_errors(m)


def test_local_folder_replaced_by_file(m):
    """Tests the upload sync when a local folder is replaced by a file."""

    os.mkdir(m.dropbox_path + "/folder")
    wait_for_idle(m)

    with m.sync.sync_lock:

        # replace local folder with file
        delete(m.dropbox_path + "/folder")
        shutil.copy(resources + "/file.txt", m.dropbox_path + "/folder")

    wait_for_idle(m)

    assert osp.isfile(m.dropbox_path + "/folder")
    assert_child_count(m, "/", 1)

    assert_synced(m)
    assert_no_errors(m)


def test_local_folder_replaced_by_file_and_unsynced_remote_changes(m):
    """
    Tests the upload sync when a local folder is replaced by a file and the remote
    folder has unsynced changes.
    """

    # remote folder is currently not checked for unsynced changes but replaced

    os.mkdir(m.dropbox_path + "/folder")
    wait_for_idle(m)

    with m.sync.sync_lock:

        # replace local folder with file
        delete(m.dropbox_path + "/folder")
        shutil.copy(resources + "/file.txt", m.dropbox_path + "/folder")

        # create remote changes
        m.client.upload(resources + "/file1.txt", "/folder/file.txt")

    wait_for_idle(m)

    assert_exists(m, "/folder")
    assert_child_count(m, "/", 1)

    assert_synced(m)
    assert_no_errors(m)


def test_local_file_replaced_by_folder(m):
    """Tests the upload sync when a local file is replaced by a folder."""

    shutil.copy(resources + "/file.txt", m.dropbox_path + "/file.txt")
    wait_for_idle(m)

    with m.sync.sync_lock:

        # replace local file with folder
        os.unlink(m.dropbox_path + "/file.txt")
        os.mkdir(m.dropbox_path + "/file.txt")

    wait_for_idle(m)

    assert osp.isdir(m.dropbox_path + "/file.txt")
    assert_child_count(m, "/", 1)

    assert_synced(m)
    assert_no_errors(m)


def test_local_file_replaced_by_folder_and_unsynced_remote_changes(m):
    """
    Tests the upload sync when a local file is replaced by a folder and the remote
    file has unsynced changes.
    """

    # Check if server-modified time > last_sync of file and only delete file if
    # older. Otherwise, let Dropbox handle creating a conflicting copy.

    shutil.copy(resources + "/file.txt", m.dropbox_path + "/file.txt")
    wait_for_idle(m)

    with m.sync.sync_lock:

        # replace local file with folder
        os.unlink(m.dropbox_path + "/file.txt")
        os.mkdir(m.dropbox_path + "/file.txt")

        # create remote changes
        m.client.upload(
            resources + "/file1.txt",
            "/file.txt",
            mode=WriteMode.overwrite,
        )

    wait_for_idle(m)

    assert_exists(m, "/file.txt")
    assert_conflict(m, "/", "file.txt")
    assert_child_count(m, "/", 2)

    assert_synced(m)
    assert_no_errors(m)


def test_selective_sync_conflict(m):
    """
    Tests the creation of a selective sync conflict when a local item is created with a
    path that is excluded by selective sync.
    """

    os.mkdir(m.dropbox_path + "/folder")
    wait_for_idle(m)

    # exclude 'folder' from sync
    m.exclude_item("/folder")
    wait_for_idle(m)

    assert not osp.exists(m.dropbox_path + "/folder")

    # recreate 'folder' locally
    os.mkdir(m.dropbox_path + "/folder")
    wait_for_idle(m)

    os.mkdir(m.dropbox_path + "/folder")
    wait_for_idle(m)

    assert not osp.exists(m.dropbox_path + "/folder")
    assert osp.isdir(m.dropbox_path + "/folder (selective sync conflict)")
    assert osp.isdir(m.dropbox_path + "/folder (selective sync conflict 1)")
    assert m.client.get_metadata("/folder")
    assert m.client.get_metadata("/folder (selective sync conflict)")
    assert m.client.get_metadata("/folder (selective sync conflict 1)")

    assert_synced(m)
    assert_no_errors(m)


@pytest.mark.skipif(
    not is_fs_case_sensitive(HOME), reason="file system is not case sensitive"
)
def test_case_conflict(m):
    """
    Tests the creation of a case conflict when a local item is created with a path that
    only differs in casing from an existing path.
    """

    os.mkdir(m.dropbox_path + "/folder")
    wait_for_idle(m)

    os.mkdir(m.dropbox_path + "/Folder")
    wait_for_idle(m)

    assert osp.isdir(m.dropbox_path + "/folder")
    assert osp.isdir(m.dropbox_path + "/Folder (case conflict)")
    assert m.client.get_metadata("/folder")
    assert m.client.get_metadata("/Folder (case conflict)")

    assert_synced(m)
    assert_no_errors(m)


def test_unicode_conflict(m):
    """
    Tests the creation of a unicode conflict when a local item is created with a path
    that only differs in utf-8 form from an existing path.
    """

    os.mkdir(m.dropbox_path.encode() + b"/fo\xcc\x81lder")  # decomposed "ó"
    wait_for_idle(m)

    try:
        os.mkdir(m.dropbox_path.encode() + b"/f\xc3\xb3lder")  # composed "ó"
    except FileExistsError:
        # file system / OS does not allow for unicode conflicts
        return
    wait_for_idle(m)

    assert osp.isdir(m.dropbox_path + "/folder")
    assert osp.isdir(m.dropbox_path + "/Folder (case conflict)")
    assert m.client.get_metadata("/folder")
    assert m.client.get_metadata("/Folder (case conflict)")

    assert_synced(m)
    assert_no_errors(m)


# ==== test race conditions ============================================================


def test_parallel_deletion_when_paused(m):
    """Tests parallel remote and local deletions of an item."""

    # create a local file
    shutil.copy(resources + "/file.txt", m.dropbox_path)

    wait_for_idle(m)
    assert_synced(m)

    m.stop_sync()
    wait_for_idle(m)

    # delete local file
    delete(m.dropbox_path + "/file.txt")

    # delete remote file
    m.client.remove("/file.txt")

    m.start_sync()
    wait_for_idle(m)

    assert_child_count(m, "/", 0)

    assert_synced(m)
    assert_no_errors(m)


def test_local_and_remote_creation_with_equal_content(m):
    """Tests parallel and equal remote and local changes of an item."""

    m.stop_sync()
    wait_for_idle(m)

    # create local file
    shutil.copy(resources + "/file.txt", m.dropbox_path)
    # create remote file with equal content
    m.client.upload(resources + "/file.txt", "/file.txt")

    m.start_sync()
    wait_for_idle(m)

    assert_exists(m, "/file.txt")
    assert_child_count(m, "/", 1)

    assert_synced(m)
    assert_no_errors(m)


def test_local_and_remote_creation_with_different_content(m):
    """Tests parallel and different remote and local changes of an item."""

    m.stop_sync()
    wait_for_idle(m)

    # create local file
    shutil.copy(resources + "/file.txt", m.dropbox_path)
    # create remote file with different content
    m.client.upload(resources + "/file1.txt", "/file.txt")

    m.start_sync()
    wait_for_idle(m)

    assert_exists(m, "/file.txt")
    assert_conflict(m, "/", "file.txt")
    assert_child_count(m, "/", 2)

    assert_synced(m)
    assert_no_errors(m)


def test_local_deletion_during_upload(m):
    """Tests the case where a local item is deleted during the upload."""

    # we mimic a deletion during upload by queueing a fake FileCreatedEvent
    fake_created_event = FileCreatedEvent(m.dropbox_path + "/file.txt")
    m.manager.sync.fs_events.queue_event(fake_created_event)

    wait_for_idle(m)

    assert_child_count(m, "/", 0)

    assert_synced(m)
    assert_no_errors(m)


def test_rapid_local_changes(m):
    """Tests local changes to the content of a file with varying intervals."""

    for t in (0.1, 0.1, 0.5, 0.5, 1.0, 1.0, 2.0, 2.0):
        time.sleep(t)
        with open(m.dropbox_path + "/file.txt", "a") as f:
            f.write(f" {t} ")

    wait_for_idle(m)

    assert_exists(m, "/file.txt")
    assert_child_count(m, "/", 1)

    assert_synced(m)
    assert_no_errors(m)


def test_rapid_remote_changes(m):
    """Tests remote changes to the content of a file with varying intervals."""

    shutil.copy(resources + "/file.txt", m.dropbox_path)
    wait_for_idle(m)

    md = m.client.get_metadata("/file.txt")

    for t in (0.1, 0.1, 0.5, 0.5, 1.0, 1.0, 2.0, 2.0):
        time.sleep(t)
        with open(resources + "/file.txt", "a") as f:
            f.write(f" {t} ")
        md = m.client.upload(
            resources + "/file.txt",
            "/file.txt",
            mode=WriteMode.update(md.rev),
        )

    # reset file content
    with open(resources + "/file.txt", "w") as f:
        f.write("content")

    wait_for_idle(m, 5)

    assert_exists(m, "/file.txt")
    assert_child_count(m, "/", 1)

    assert_synced(m)
    assert_no_errors(m)


# ==== test error handling =============================================================


def test_local_path_error(m):
    """Tests error handling for forbidden file names."""

    # paths with backslash are not allowed on Dropbox
    # we create such a local folder and assert that it triggers a sync issue

    test_path_local = m.dropbox_path + "/folder\\"
    test_path_dbx = "/folder\\"

    os.mkdir(test_path_local)
    wait_for_idle(m)

    sync_errors = m.sync_errors

    assert len(sync_errors) == 1
    assert sync_errors[0]["local_path"] == test_path_local
    assert sync_errors[0]["dbx_path"] == test_path_dbx
    assert sync_errors[0]["type"] == "PathError"
    assert sync_errors[0]["direction"] == "up"

    # remove folder with invalid name and assert that sync issue is cleared

    delete(test_path_local)
    wait_for_idle(m)

    assert len(m.sync_errors) == 0

    assert_synced(m)
    assert_no_errors(m)


def test_local_indexing_error(m):
    """Tests handling of PermissionError during local indexing."""

    shutil.copytree(resources + "/test_folder", m.dropbox_path + "/test_folder")
    wait_for_idle(m)

    m.stop_sync()
    wait_for_idle(m)

    # change permissions of local folder
    subfolder = m.dropbox_path + "/test_folder/sub_folder_2"
    os.chmod(subfolder, 0o000)

    m.start_sync()
    wait_for_idle(m)

    # check for fatal errors
    assert len(m.fatal_errors) == 1
    assert m.fatal_errors[0]["local_path"] == subfolder


def test_local_permission_error(m):
    """Tests error handling on local PermissionError."""

    test_path_local = m.dropbox_path + "/file"
    test_path_dbx = "/file"

    m.stop_sync()

    open(test_path_local, "w").close()
    os.chmod(test_path_local, 0o000)

    m.start_sync()
    wait_for_idle(m)

    sync_errors = m.sync_errors

    assert len(sync_errors) == 1
    assert sync_errors[0]["local_path"] == test_path_local
    assert sync_errors[0]["dbx_path"] == test_path_dbx
    assert sync_errors[0]["type"] == "InsufficientPermissionsError"
    assert sync_errors[0]["direction"] == "up"

    # reset file permission

    os.chmod(test_path_local, 0o666)
    os.utime(test_path_local, times=None)  # touch
    wait_for_idle(m)

    # check that error is cleared and file is uploaded

    assert len(m.sync_errors) == 0
    assert_exists(m, "/file")

    assert_synced(m)
    assert_no_errors(m)


def test_long_path_error(m):
    """Tests error handling on trying to download an item with a too long path."""

    max_path_length, _ = fs_max_lengths_for_path()

    # Create a remote folder with a path name longer than locally allowed.
    test_path = "/nested" * (max_path_length // 6)

    try:
        m.client.upload(f"{resources}/file.txt", test_path)
    except PathError:
        pytest.skip(f"Cannot create path with {len(test_path)} chars on Dropbox")

    wait_for_idle(m)

    sync_errors = m.sync_errors

    assert len(sync_errors) > 0
    assert sync_errors[-1]["dbx_path"] == test_path
    assert sync_errors[-1]["type"] == "PathError"
    assert sync_errors[-1]["direction"] == "down"


@pytest.mark.parametrize(
    "name",
    [
        "file_🦑",  # U+1F991
    ],
)
def test_unicode_forbidden(m, name):
    """Tests syncing files with exotic unicode characters."""

    local_path = osp.join(m.dropbox_path, name)

    os.mkdir(local_path)
    wait_for_idle(m)

    sync_errors = m.sync_errors

    assert len(sync_errors) == 1
    assert sync_errors[0]["local_path"] == local_path


@pytest.mark.skipif(
    sys.platform != "linux", reason="macOS enforces utf-8 path encoding"
)
def test_unknown_path_encoding(m, capsys):
    """
    Tests the handling of a local path with bytes that cannot be decoded with the
    file system encoding reported by the platform.
    """

    # create a path with Python surrogate escapes and convert it to bytes
    test_path_dbx = "/my_folder_\udce4"
    test_path_local = m.sync.to_local_path(test_path_dbx)
    test_path_local_bytes = os.fsencode(test_path_local)

    # create the local directory while we are syncing
    os.mkdir(test_path_local_bytes)
    wait_for_idle(m)

    # 1) Check that the sync issue is logged

    # This requires that our sync logic from the emitted watchdog event all the
    # way to `SyncEngine._on_local_created` can handle strings with surrogate escapes.

    sync_errors = m.sync_errors

    assert len(m.fatal_errors) == 0
    assert len(sync_errors) == 1
    assert sync_errors[0]["local_path"] == sanitize_string(test_path_local)
    assert sync_errors[0]["dbx_path"] == sanitize_string(test_path_dbx)
    assert sync_errors[0]["type"] == "PathError"
    assert sync_errors[0]["direction"] == "up"

    # 2) Check that the sync is retried after pause / resume

    # This requires that our logic to save failed paths in our state file and retry the
    # sync on startup can handle strings with surrogate escapes.

    m.stop_sync()
    m.start_sync()

    wait_for_idle(m)

    sync_errors = m.sync_errors

    assert len(m.fatal_errors) == 0
    assert len(sync_errors) == 1
    assert sync_errors[0]["local_path"] == sanitize_string(test_path_local)
    assert sync_errors[0]["dbx_path"] == sanitize_string(test_path_dbx)
    assert sync_errors[0]["type"] == "PathError"
    assert sync_errors[0]["direction"] == "up"

    # 3) Check that the error is cleared when the file is deleted

    # This requires that `SyncEngine.upload_local_changes_while_inactive` can handle
    # strings with surrogate escapes all they way to `SyncEngine._on_local_deleted`.

    delete(test_path_local_bytes)  # type: ignore
    wait_for_idle(m)

    assert normalize(test_path_dbx) not in m.sync.upload_errors

    assert_synced(m)
    assert_no_errors(m)


def test_symlink_error(m):

    local_path = m.dropbox_path + "/link"

    os.symlink("to_nowhere", local_path)
    wait_for_idle(m)

    sync_errors = m.sync_errors

    assert len(m.fatal_errors) == 0
    assert len(sync_errors) == 1
    assert sync_errors[0]["local_path"] == local_path
    assert sync_errors[0]["type"] == "SymlinkError"
    assert sync_errors[0]["direction"] == "up"


def test_symlink_indexing_error(m):

    m.stop_sync()

    local_path = m.dropbox_path + "/link"

    os.symlink("to_nowhere", local_path)

    m.start_sync()
    wait_for_idle(m)

    sync_errors = m.sync_errors

    assert len(m.fatal_errors) == 0
    assert len(m.sync_errors) == 1
    assert sync_errors[0]["local_path"] == local_path
    assert sync_errors[0]["type"] == "SymlinkError"
    assert sync_errors[0]["direction"] == "up"


def test_dropbox_dir_delete_during_sync(m):

    delete(m.dropbox_path)

    wait_for_idle(m)

    assert len(m.fatal_errors) == 1
    assert m.fatal_errors[-1]["type"] == "NoDropboxDirError"


@pytest.mark.skipif(is_fs_case_sensitive(HOME), reason="file system is case sensitive")
def test_dropbox_dir_rename_during_sync(m):

    dirname, basename = osp.split(m.dropbox_path)

    # Move the directory to a new location with a different casing.
    shutil.move(m.dropbox_path, osp.join(dirname, basename.upper()))

    wait_for_idle(m)

    assert len(m.fatal_errors) == 1
    assert m.fatal_errors[0]["type"] == "NoDropboxDirError"


def test_dropbox_dir_delete_during_pause(m):

    m.stop_sync()

    delete(m.dropbox_path)

    m.start_sync()

    wait_for_idle(m)

    assert len(m.fatal_errors) == 1
    assert m.fatal_errors[0]["type"] == "NoDropboxDirError"


# ==== performance tests ===============================================================


def test_sync_event_conversion_performance(m):
    """Tests the performance of converting remote file changes to SyncEvents."""

    # generate tree with 5 entries
    shutil.copytree(resources + "/test_folder", m.dropbox_path + "/test_folder")
    wait_for_idle(m)
    m.stop_sync()

    res = m.client.list_folder("/", recursive=True)

    def setup():
        m.sync.reset_sync_state()
        m.sync._case_conversion_cache.clear()

    def generate_sync_events():
        cleaned_res = m.sync._clean_remote_changes(res)
        cleaned_res.entries.sort(key=lambda x: x.path_lower.count("/"))
        for md in cleaned_res.entries:
            SyncEvent.from_dbx_metadata(md, m.sync)

    n_loops = 1000  # equivalent to to 5,000 items

    duration = timeit.timeit(stmt=generate_sync_events, setup=setup, number=n_loops)

    assert duration < 4  # expected ~ 1.8 sec


# ==== test recovery from inconsistent state ===========================================


def test_invalid_pending_download(m):
    """
    Tests error handling when an invalid path is saved in the pending downloads list.
    This can happen for instance when Dropbox servers have a hickup or when our state
    file gets corrupted.
    """

    # add a non-existent path to the pending downloads list
    bogus_path = "/bogus path"
    m.sync.pending_downloads.add(bogus_path)

    # trigger a resync
    m.stop_sync()
    m.start_sync()
    wait_for_idle(m)

    # assert that there are no sync errors / fatal errors and that the invalid path
    # was cleared
    assert bogus_path not in m.sync.pending_downloads

    assert_synced(m)
    assert_no_errors(m)


def test_out_of_order_indexing(m):
    """Tests applying remote events when children are synced before their parents."""

    m.stop_sync()

    # Create a nested remote folder structure.

    m.client.make_dir("/parent")
    m.client.upload(resources + "/file.txt", "/parent/child_2")
    m.client.make_dir("/parent/child_1")

    # Fetch remote index manually and scramble order.

    all_changes = []

    for changes, cursor in m.sync.list_remote_changes_iterator(m.sync.remote_cursor):
        all_changes += changes

    # Reverse order of changes with children coming first.

    for sync_event in reversed(all_changes):
        m.sync._create_local_entry(sync_event)

    # Check that all local items have been created.

    assert os.path.isdir(f"{m.dropbox_path}/parent")
    assert os.path.isdir(f"{m.dropbox_path}/parent/child_1")
    assert os.path.isfile(f"{m.dropbox_path}/parent/child_2")

    assert_synced(m)
    assert_no_errors(m)


# ==== helper functions ================================================================


def count_conflicts(entries, name):
    basename, ext = osp.splitext(name)

    candidates = [e for e in entries if e["name"].startswith(basename)]
    ccs = [
        e
        for e in candidates
        if "(1)" in e["name"]  # created by Dropbox for add conflict
        or "conflicted copy" in e["name"]  # created by Dropbox for update conflict
        or "conflicting copy" in e["name"]  # created by us
    ]
    return len(ccs)


def assert_exists(m, dbx_path):
    """Asserts that an item at `dbx_path` exists on the server."""
    md = m.client.get_metadata(dbx_path)
    assert md is not None
    assert md.name == osp.basename(dbx_path)


def assert_conflict(m, dbx_folder, name):
    """Asserts that a conflicting copy has been created for
    an item with `name` inside `dbx_folder`."""
    entries = m.list_folder(dbx_folder)
    assert (
        count_conflicts(entries, name) == 1
    ), f'conflicting copy for "{name}" missing on Dropbox'


def assert_child_count(m, dbx_folder, n):
    """Asserts that `dbx_folder` has `n` entries (excluding itself)."""
    entries = m.list_folder(dbx_folder, recursive=True)
    n_remote = len(entries)

    if dbx_folder != "/":
        n_remote -= 1

    assert n_remote == n, f"Expected {n} items but found {n_remote}: {entries}"


def assert_no_errors(m):
    assert len(m.fatal_errors) == 0, m.fatal_errors
    assert len(m.sync_errors) == 0, m.sync_errors
