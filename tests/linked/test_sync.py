# -*- coding: utf-8 -*-

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
from maestral.database import SyncEvent
from maestral.utils import sanitize_string
from maestral.utils.appdirs import get_home_dir
from maestral.utils.path import delete, move, is_fs_case_sensitive

from .conftest import assert_synced, wait_for_idle, resources


if not ("DROPBOX_ACCESS_TOKEN" in os.environ or "DROPBOX_REFRESH_TOKEN" in os.environ):
    pytest.skip("Requires auth token", allow_module_level=True)


# test functions


def test_setup(m):
    assert_synced(m)


def test_file_lifecycle(m):
    """Tests creating, modifying and deleting a file."""

    # test creating a local file

    shutil.copy(resources + "/file.txt", m.test_folder_local)

    wait_for_idle(m)
    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 1)

    # test changing the file locally

    with open(m.test_folder_local + "/file.txt", "w") as f:
        f.write("content changed")

    wait_for_idle(m)
    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 1)

    # test changing the file on remote

    m.client.upload(
        resources + "/file1.txt",
        "/sync_tests/file.txt",
        mode=WriteMode.overwrite,
    )

    wait_for_idle(m)
    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 1)

    # test deleting the file remotely

    m.client.remove("/sync_tests/file.txt")

    wait_for_idle(m)
    assert_synced(m)
    assert_child_count(m, "/sync_tests", 0)

    # check for fatal errors
    assert not m.fatal_errors


def test_file_conflict(m):
    """Tests conflicting local vs remote file changes."""

    # create a local file
    shutil.copy(resources + "/file.txt", m.test_folder_local)
    wait_for_idle(m)

    m.stop_sync()
    wait_for_idle(m)

    # modify file.txt locally
    with open(m.test_folder_local + "/file.txt", "a") as f:
        f.write(" modified conflict")

    # modify file.txt on remote
    m.client.upload(
        resources + "/file2.txt",
        "/sync_tests/file.txt",
        mode=WriteMode.overwrite,
    )

    # resume syncing and check for conflicting copy
    m.start_sync()

    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_conflict(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 2)

    # check for fatal errors
    assert not m.fatal_errors


def test_parallel_deletion_when_paused(m):
    """Tests parallel remote and local deletions of an item."""

    # create a local file
    shutil.copy(resources + "/file.txt", m.test_folder_local)

    wait_for_idle(m)
    assert_synced(m)

    m.stop_sync()
    wait_for_idle(m)

    # delete local file
    delete(m.test_folder_local + "/file.txt")

    # delete remote file
    m.client.remove("/sync_tests/file.txt")

    m.start_sync()
    wait_for_idle(m)

    assert_synced(m)
    assert_child_count(m, "/sync_tests", 0)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_and_remote_creation_with_equal_content(m):
    """Tests parallel and equal remote and local changes of an item."""

    m.stop_sync()
    wait_for_idle(m)

    # create local file
    shutil.copy(resources + "/file.txt", m.test_folder_local)
    # create remote file with equal content
    m.client.upload(resources + "/file.txt", "/sync_tests/file.txt")

    m.start_sync()
    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 1)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_and_remote_creation_with_different_content(m):
    """Tests parallel and different remote and local changes of an item."""

    m.stop_sync()
    wait_for_idle(m)

    # create local file
    shutil.copy(resources + "/file.txt", m.test_folder_local)
    # create remote file with different content
    m.client.upload(resources + "/file1.txt", "/sync_tests/file.txt")

    m.start_sync()
    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_conflict(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 2)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_deletion_during_upload(m):
    """Tests the case where a local item is deleted during the upload."""

    # we mimic a deletion during upload by queueing a fake FileCreatedEvent
    fake_created_event = FileCreatedEvent(m.test_folder_local + "/file.txt")
    m.manager.sync.fs_events.queue_event(fake_created_event)

    wait_for_idle(m)

    assert_synced(m)
    assert_child_count(m, "/sync_tests", 0)

    # check for fatal errors
    assert not m.fatal_errors


def test_rapid_local_changes(m):
    """Tests local changes to the content of a file with varying intervals."""

    for t in (0.1, 0.1, 0.5, 0.5, 1.0, 1.0, 2.0, 2.0):
        time.sleep(t)
        with open(m.test_folder_local + "/file.txt", "a") as f:
            f.write(f" {t} ")

    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 1)

    # check for fatal errors
    assert not m.fatal_errors


def test_rapid_remote_changes(m):
    """Tests remote changes to the content of a file with varying intervals."""

    shutil.copy(resources + "/file.txt", m.test_folder_local)
    wait_for_idle(m)

    md = m.client.get_metadata("/sync_tests/file.txt")

    for t in (0.1, 0.1, 0.5, 0.5, 1.0, 1.0, 2.0, 2.0):
        time.sleep(t)
        with open(resources + "/file.txt", "a") as f:
            f.write(f" {t} ")
        md = m.client.upload(
            resources + "/file.txt",
            "/sync_tests/file.txt",
            mode=WriteMode.update(md.rev),
        )

    # reset file content
    with open(resources + "/file.txt", "w") as f:
        f.write("content")

    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 1)

    # check for fatal errors
    assert not m.fatal_errors


def test_folder_tree_local(m):
    """Tests the upload sync of a nested local folder structure."""

    # test creating tree

    shutil.copytree(resources + "/test_folder", m.test_folder_local + "/test_folder")

    snap = DirectorySnapshot(resources + "/test_folder")
    num_items = len([p for p in snap.paths if not m.sync.is_excluded(p)])

    wait_for_idle(m, 10)

    assert_synced(m)
    assert_child_count(m, "/sync_tests", num_items)

    # test deleting tree

    delete(m.test_folder_local + "/test_folder")

    wait_for_idle(m)
    assert_synced(m)
    assert_child_count(m, "/sync_tests", 0)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_indexing(m):
    """Tests the upload sync of a nested local folder structure."""

    m.stop_sync()
    wait_for_idle(m, 1)

    # create local tree

    shutil.copytree(resources + "/test_folder", m.test_folder_local + "/test_folder")

    snap = DirectorySnapshot(resources + "/test_folder")
    num_items = len([p for p in snap.paths if not m.sync.is_excluded(p)])

    m.start_sync()
    wait_for_idle(m, 10)

    assert_synced(m)
    assert_child_count(m, "/sync_tests", num_items)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_indexing_error(m):
    """Tests handling of PermissionError during local indexing."""

    shutil.copytree(resources + "/test_folder", m.test_folder_local + "/test_folder")
    wait_for_idle(m)

    m.stop_sync()
    wait_for_idle(m, 1)

    # change permissions of local folder
    subfolder = m.test_folder_local + "/test_folder/sub_folder_2"
    os.chmod(subfolder, 0o000)

    m.start_sync()
    wait_for_idle(m)

    # check for fatal errors
    assert len(m.fatal_errors) == 1
    assert m.fatal_errors[0]["local_path"] == subfolder


def test_folder_tree_remote(m):
    """Tests the download sync of a nested remote folder structure."""

    # test creating remote tree

    for i in range(1, 11):
        path = "/sync_tests" + i * "/nested_folder"
        m.client.make_dir(path)

    wait_for_idle(m)

    assert_synced(m)
    assert_child_count(m, "/sync_tests", 10)

    # test deleting remote tree

    m.client.remove("/sync_tests/nested_folder")
    wait_for_idle(m, 15)

    assert_synced(m)
    assert_child_count(m, "/sync_tests", 0)

    # check for fatal errors
    assert not m.fatal_errors


def test_remote_file_replaced_by_folder(m):
    """Tests the download sync when a file is replaced by a folder."""

    shutil.copy(resources + "/file.txt", m.test_folder_local + "/file.txt")
    wait_for_idle(m)

    with m.sync.sync_lock:

        # replace remote file with folder
        m.client.remove("/sync_tests/file.txt")
        m.client.make_dir("/sync_tests/file.txt")

    wait_for_idle(m)

    assert_synced(m)
    assert_child_count(m, "/sync_tests", 1)
    assert os.path.isdir(m.test_folder_local + "/file.txt")

    # check for fatal errors
    assert not m.fatal_errors


def test_remote_file_replaced_by_folder_and_unsynced_local_changes(m):
    """
    Tests the download sync when a file is replaced by a folder and the local file has
    unsynced changes.
    """

    shutil.copy(resources + "/file.txt", m.test_folder_local + "/file.txt")
    wait_for_idle(m)

    with m.sync.sync_lock:

        # replace remote file with folder
        m.client.remove("/sync_tests/file.txt")
        m.client.make_dir("/sync_tests/file.txt")

        # create local changes
        with open(m.test_folder_local + "/file.txt", "a") as f:
            f.write(" modified")

    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_conflict(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 2)

    # check for fatal errors
    assert not m.fatal_errors


def test_remote_folder_replaced_by_file(m):
    """Tests the download sync when a folder is replaced by a file."""

    m.client.make_dir("/sync_tests/folder")
    wait_for_idle(m)

    # replace remote folder with file

    with m.sync.sync_lock:
        m.client.remove("/sync_tests/folder")
        m.client.upload(resources + "/file.txt", "/sync_tests/folder")

    wait_for_idle(m)

    assert_synced(m)
    assert os.path.isfile(m.test_folder_local + "/folder")
    assert_child_count(m, "/sync_tests", 1)

    # check for fatal errors
    assert not m.fatal_errors


def test_remote_folder_replaced_by_file_and_unsynced_local_changes(m):
    """
    Tests the download sync when a folder is replaced by a file and the local folder has
    unsynced changes.
    """

    os.mkdir(m.test_folder_local + "/folder")
    wait_for_idle(m)

    with m.sync.sync_lock:

        # replace remote folder with file
        m.client.remove("/sync_tests/folder")
        m.client.upload(resources + "/file.txt", "/sync_tests/folder")

        # create local changes
        os.mkdir(m.test_folder_local + "/folder/subfolder")

    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "folder")
    assert_conflict(m, "/sync_tests", "folder")
    assert_child_count(m, "/sync_tests", 3)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_folder_replaced_by_file(m):
    """Tests the upload sync when a local folder is replaced by a file."""

    os.mkdir(m.test_folder_local + "/folder")
    wait_for_idle(m)

    with m.sync.sync_lock:

        # replace local folder with file
        delete(m.test_folder_local + "/folder")
        shutil.copy(resources + "/file.txt", m.test_folder_local + "/folder")

    wait_for_idle(m)

    assert_synced(m)
    assert osp.isfile(m.test_folder_local + "/folder")
    assert_child_count(m, "/sync_tests", 1)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_folder_replaced_by_file_and_unsynced_remote_changes(m):
    """
    Tests the upload sync when a local folder is replaced by a file and the remote
    folder has unsynced changes.
    """

    # remote folder is currently not checked for unsynced changes but replaced

    os.mkdir(m.test_folder_local + "/folder")
    wait_for_idle(m)

    with m.sync.sync_lock:

        # replace local folder with file
        delete(m.test_folder_local + "/folder")
        shutil.copy(resources + "/file.txt", m.test_folder_local + "/folder")

        # create remote changes
        m.client.upload(resources + "/file1.txt", "/sync_tests/folder/file.txt")

    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "folder")
    assert_child_count(m, "/sync_tests", 1)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_file_replaced_by_folder(m):
    """Tests the upload sync when a local file is replaced by a folder."""

    shutil.copy(resources + "/file.txt", m.test_folder_local + "/file.txt")
    wait_for_idle(m)

    with m.sync.sync_lock:

        # replace local file with folder
        os.unlink(m.test_folder_local + "/file.txt")
        os.mkdir(m.test_folder_local + "/file.txt")

    wait_for_idle(m)

    assert_synced(m)
    assert osp.isdir(m.test_folder_local + "/file.txt")
    assert_child_count(m, "/sync_tests", 1)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_file_replaced_by_folder_and_unsynced_remote_changes(m):
    """
    Tests the upload sync when a local file is replaced by a folder and the remote
    file has unsynced changes.
    """

    # Check if server-modified time > last_sync of file and only delete file if
    # older. Otherwise, let Dropbox handle creating a conflicting copy.

    shutil.copy(resources + "/file.txt", m.test_folder_local + "/file.txt")
    wait_for_idle(m)

    with m.sync.sync_lock:

        # replace local file with folder
        os.unlink(m.test_folder_local + "/file.txt")
        os.mkdir(m.test_folder_local + "/file.txt")

        # create remote changes
        m.client.upload(
            resources + "/file1.txt",
            "/sync_tests/file.txt",
            mode=WriteMode.overwrite,
        )

    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_conflict(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 2)

    # check for fatal errors
    assert not m.fatal_errors


def test_selective_sync_conflict(m):
    """
    Tests the creation of a selective sync conflict when a local item is created with a
    path that is excluded by selective sync.
    """

    os.mkdir(m.test_folder_local + "/folder")
    wait_for_idle(m)

    # exclude 'folder' from sync
    m.exclude_item("/sync_tests/folder")
    wait_for_idle(m)

    assert not osp.exists(m.test_folder_local + "/folder")

    # recreate 'folder' locally
    os.mkdir(m.test_folder_local + "/folder")
    wait_for_idle(m)

    os.mkdir(m.test_folder_local + "/folder")
    wait_for_idle(m)

    assert not osp.exists(m.test_folder_local + "/folder")
    assert osp.isdir(m.test_folder_local + "/folder (selective sync conflict)")
    assert osp.isdir(m.test_folder_local + "/folder (selective sync conflict 1)")
    assert m.client.get_metadata("/sync_tests/folder")
    assert m.client.get_metadata("/sync_tests/folder (selective sync conflict)")
    assert m.client.get_metadata("/sync_tests/folder (selective sync conflict 1)")

    # check for fatal errors
    assert not m.fatal_errors


@pytest.mark.skipif(
    not is_fs_case_sensitive("/home"), reason="file system is not case sensitive"
)
def test_case_conflict(m):
    """
    Tests the creation of a case conflict when a local item is created with a path that
    only differs in casing from an existing path.
    """

    os.mkdir(m.test_folder_local + "/folder")
    wait_for_idle(m)

    os.mkdir(m.test_folder_local + "/Folder")
    wait_for_idle(m)

    assert osp.isdir(m.test_folder_local + "/folder")
    assert osp.isdir(m.test_folder_local + "/Folder (case conflict)")
    assert m.client.get_metadata("/sync_tests/folder")
    assert m.client.get_metadata("/sync_tests/Folder (case conflict)")

    assert_synced(m)

    # check for fatal errors
    assert not m.fatal_errors


def test_case_change_local(m):
    """
    Tests the upload sync of local rename which only changes the casing of the local
    file name.
    """

    # start with nested folders
    os.mkdir(m.test_folder_local + "/folder")
    os.mkdir(m.test_folder_local + "/folder/Subfolder")
    wait_for_idle(m)

    assert_synced(m)

    # rename to parent folder to upper case
    shutil.move(m.test_folder_local + "/folder", m.test_folder_local + "/FOLDER")
    wait_for_idle(m)

    assert osp.isdir(m.test_folder_local + "/FOLDER")
    assert osp.isdir(m.test_folder_local + "/FOLDER/Subfolder")
    assert (
        m.client.get_metadata("/sync_tests/folder").name == "FOLDER"
    ), "casing was not propagated to Dropbox"

    assert_synced(m)

    # check for fatal errors
    assert not m.fatal_errors


def test_case_change_remote(m):
    """
    Tests the download sync of remote rename which only changes the casing of the remote
    file name.
    """

    # start with nested folders
    os.mkdir(m.test_folder_local + "/folder")
    os.mkdir(m.test_folder_local + "/folder/Subfolder")
    wait_for_idle(m)

    assert_synced(m)

    # rename remote folder
    m.client.move("/sync_tests/folder", "/sync_tests/FOLDER", autorename=True)

    wait_for_idle(m)

    assert osp.isdir(m.test_folder_local + "/FOLDER")
    assert osp.isdir(m.test_folder_local + "/FOLDER/Subfolder")
    assert (
        m.client.get_metadata("/sync_tests/folder").name == "FOLDER"
    ), "casing was not propagated to local folder"
    assert_synced(m)

    # check for fatal errors
    assert not m.fatal_errors


def test_mignore(m):
    """Tests the exclusion of local items by an mignore file."""

    # 1) test changes have no effect when sync is running

    os.mkdir(m.test_folder_local + "/bar")
    os.mkdir(m.test_folder_local + "/folder")
    wait_for_idle(m)

    with open(m.sync.mignore_path, "w") as f:
        f.write("foo/\n")  # ignore folder "foo"
        f.write("bar\n")  # ignore file or folder "bar"
        f.write("build\n")  # ignore file or folder "build"

    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "bar")

    # 2) test that items are removed after restart

    m.stop_sync()
    wait_for_idle(m)
    m.start_sync()

    os.mkdir(m.test_folder_local + "/foo")
    wait_for_idle(m)

    assert not m.client.get_metadata("/sync_tests/foo")
    assert not m.client.get_metadata("/sync_tests/bar")

    # 3) test that renaming an item excludes it

    move(m.test_folder_local + "/folder", m.test_folder_local + "/build")
    wait_for_idle(m)

    assert not m.client.get_metadata("/sync_tests/build")

    # 4) test that renaming an item includes it

    move(m.test_folder_local + "/build", m.test_folder_local + "/folder")
    wait_for_idle(m)

    assert_exists(m, "/sync_tests", "folder")

    clean_local(m)
    wait_for_idle(m)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_path_error(m):
    """Tests error handling for forbidden file names."""

    # paths with backslash are not allowed on Dropbox
    # we create such a local folder and assert that it triggers a sync issue

    test_path_local = m.test_folder_local + "/folder\\"
    test_path_dbx = "/sync_tests/folder\\"

    os.mkdir(test_path_local)
    wait_for_idle(m)

    assert len(m.sync_errors) == 1
    assert m.sync_errors[-1]["local_path"] == test_path_local
    assert m.sync_errors[-1]["dbx_path"] == test_path_dbx
    assert m.sync_errors[-1]["type"] == "PathError"
    assert test_path_dbx in m.sync.upload_errors

    # remove folder with invalid name and assert that sync issue is cleared

    delete(test_path_local)
    wait_for_idle(m)

    assert len(m.sync_errors) == 0
    assert test_path_dbx not in m.sync.upload_errors

    # check for fatal errors
    assert not m.fatal_errors


def test_local_permission_error(m):
    """Tests error handling on local PermissionError."""

    test_path_local = m.test_folder_local + "/file"
    test_path_dbx = "/sync_tests/file"

    m.stop_sync()

    open(test_path_local, "w").close()
    os.chmod(test_path_local, 0o000)

    m.start_sync()
    wait_for_idle(m)

    assert len(m.sync_errors) == 1
    assert m.sync_errors[-1]["local_path"] == test_path_local
    assert m.sync_errors[-1]["dbx_path"] == test_path_dbx
    assert m.sync_errors[-1]["type"] == "InsufficientPermissionsError"
    assert test_path_dbx in m.sync.upload_errors

    # reset file permission

    os.chmod(test_path_local, 0o666)
    wait_for_idle(m)

    assert len(m.sync_errors) == 0
    assert test_path_dbx not in m.sync.upload_errors

    # check for fatal errors
    assert not m.fatal_errors


def test_download_sync_issues(m):
    """
    Tests error handling for issues during download sync. This is done by attempting to
    download sync a file with a DMCA take down notice (not allowed through the public
    API).
    """

    test_path_local = m.test_folder_local + "/dmca.gif"
    test_path_dbx = "/sync_tests/dmca.gif"

    m.client.upload(resources + "/dmca.gif", test_path_dbx)

    wait_for_idle(m)

    # 1) Check that the sync issue is logged

    assert len(m.sync_errors) == 1
    assert m.sync_errors[-1]["local_path"] == test_path_local
    assert m.sync_errors[-1]["dbx_path"] == test_path_dbx
    assert m.sync_errors[-1]["type"] == "RestrictedContentError"
    assert test_path_dbx in m.sync.download_errors

    # 2) Check that the sync is retried after pause / resume

    m.stop_sync()
    m.start_sync()

    wait_for_idle(m)

    assert len(m.sync_errors) == 1
    assert m.sync_errors[-1]["local_path"] == test_path_local
    assert m.sync_errors[-1]["dbx_path"] == test_path_dbx
    assert m.sync_errors[-1]["type"] == "RestrictedContentError"
    assert test_path_dbx in m.sync.download_errors

    # 3) Check that the error is cleared when the file is deleted

    m.client.remove(test_path_dbx)
    wait_for_idle(m)

    assert len(m.sync_errors) == 0
    assert test_path_dbx not in m.sync.download_errors

    # check for fatal errors
    assert not m.fatal_errors


def test_excluded_folder_cleared_on_deletion(m):
    """
    Tests that an entry in our selective sync excluded list gets removed when the
    corresponding item is deleted.
    """

    dbx_path = "/sync_tests/selective_sync_test_folder"
    local_path = m.to_local_path("/sync_tests/selective_sync_test_folder")

    # create folder structure
    os.mkdir(local_path)
    wait_for_idle(m)

    # exclude "/sync_tests/selective_sync_test_folder" from sync
    m.exclude_item(dbx_path)
    wait_for_idle(m)

    assert dbx_path in m.excluded_items
    assert m.excluded_status(dbx_path) == "excluded"
    assert not osp.exists(local_path)

    # test that an excluded folder is removed from excluded_list on deletion
    m.client.remove(dbx_path)
    wait_for_idle(m)

    assert (
        dbx_path not in m.excluded_items
    ), 'deleted item is still in "excluded_items" list'

    # check for fatal errors
    assert not m.fatal_errors


def test_unix_permissions(m):
    """
    Tests that a newly downloaded file is created with default permissions for our
    process and that any locally set permissions are preserved on remote file
    modifications.
    """

    dbx_path = "/sync_tests/file"
    local_path = m.to_local_path(dbx_path)

    m.client.upload(resources + "/file.txt", dbx_path)
    wait_for_idle(m)

    # create a local file and compare its permissions to the new download
    reference_file = osp.join(get_home_dir(), "reference")

    try:
        open(reference_file, "ab").close()
        assert os.stat(local_path).st_mode == os.stat(reference_file).st_mode
    finally:
        delete(reference_file)

    # make the local file executable
    os.chmod(local_path, 0o744)
    new_mode = os.stat(local_path).st_mode  # might not be 744...
    wait_for_idle(m)

    # perform some remote modifications
    m.client.upload(resources + "/file1.txt", dbx_path, mode=WriteMode.overwrite)
    wait_for_idle(m)

    # check that the local permissions have not changed
    assert os.stat(local_path).st_mode == new_mode


@pytest.mark.parametrize(
    "name",
    [
        "tést_file",  # U+00E9
        "täst_file",  # U+00E4
    ],
)
def test_unicode_allowed(m, name):
    """Tests syncing files with exotic unicode characters."""

    local_path = osp.join(m.test_folder_local, name)

    os.mkdir(local_path)

    wait_for_idle(m)
    assert_synced(m)
    assert_exists(m, "/sync_tests", name)
    assert_child_count(m, "/sync_tests", 1)


@pytest.mark.parametrize(
    "name",
    [
        "file_🦑",  # U+1F991
    ],
)
def test_unicode_forbidden(m, name):
    """Tests syncing files with exotic unicode characters."""

    local_path = osp.join(m.test_folder_local, name)

    os.mkdir(local_path)
    wait_for_idle(m)

    assert len(m.sync_errors) == 1
    assert m.sync_errors[-1]["local_path"] == local_path


@pytest.mark.skipif(
    sys.platform != "linux", reason="macOS enforces utf-8 path encoding"
)
def test_unknown_path_encoding(m, capsys):
    """
    Tests the handling of a local path with bytes that cannot be decoded with the
    file system encoding reported by the platform.
    """

    # create a path with Python surrogate escapes and convert it to bytes
    test_path_dbx = "/sync_tests/my_folder_\udce4"
    test_path_local = m.sync.to_local_path(test_path_dbx)
    test_path_local_bytes = os.fsencode(test_path_local)

    # create the local directory while we are syncing
    os.mkdir(test_path_local_bytes)
    wait_for_idle(m)

    # 1) Check that the sync issue is logged

    # This requires that our sync logic from the emitted watchdog event all the
    # way to `SyncEngine._on_local_created` can handle strings with surrogate escapes.

    assert len(m.fatal_errors) == 0
    assert len(m.sync_errors) == 1
    assert m.sync_errors[-1]["local_path"] == sanitize_string(test_path_local)
    assert m.sync_errors[-1]["dbx_path"] == sanitize_string(test_path_dbx)
    assert m.sync_errors[-1]["type"] == "PathError"
    assert test_path_dbx in m.sync.upload_errors

    # 2) Check that the sync is retried after pause / resume

    # This requires that our logic to save failed paths in our state file and retry the
    # sync on startup can handle strings with surrogate escapes.

    m.stop_sync()
    m.start_sync()

    wait_for_idle(m)

    assert len(m.fatal_errors) == 0
    assert len(m.sync_errors) == 1
    assert m.sync_errors[-1]["local_path"] == sanitize_string(test_path_local)
    assert m.sync_errors[-1]["dbx_path"] == sanitize_string(test_path_dbx)
    assert m.sync_errors[-1]["type"] == "PathError"
    assert test_path_dbx in m.sync.upload_errors

    # 3) Check that the error is cleared when the file is deleted

    # This requires that `SyncEngine.upload_local_changes_while_inactive` can handle
    # strings with surrogate escapes all they way to `SyncEngine._on_local_deleted`.

    delete(test_path_local_bytes)  # type: ignore
    wait_for_idle(m)

    assert len(m.fatal_errors) == 0
    assert len(m.sync_errors) == 0
    assert test_path_dbx not in m.sync.upload_errors


def test_sync_event_conversion_performance(m):
    """
    Tests the performance of converting remote file changes to SyncEvents.
    """

    # generate tree with 5 entries
    shutil.copytree(resources + "/test_folder", m.test_folder_local + "/test_folder")
    wait_for_idle(m)
    m.stop_sync()

    res = m.client.list_folder("/sync_tests", recursive=True)

    def setup():
        m.sync.clear_index()
        m.sync.clear_hash_cache()
        m.sync._case_conversion_cache.clear()

    def generate_sync_events():
        cleaned_res = m.sync._clean_remote_changes(res)
        cleaned_res.entries.sort(key=lambda x: x.path_lower.count("/"))
        for md in cleaned_res.entries:
            SyncEvent.from_dbx_metadata(md, m.sync)

    n_loops = 1000  # equivalent to to 5,000 items

    duration = timeit.timeit(stmt=generate_sync_events, setup=setup, number=n_loops)

    assert duration < 4  # expected ~ 1.8 sec


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
    assert len(m.sync_errors) == 0
    assert len(m.fatal_errors) == 0


# ==== helper functions ================================================================


def clean_local(m):
    """Recreates a fresh test folder locally."""
    delete(m.dropbox_path + "/.mignore")
    delete(m.test_folder_local)
    os.mkdir(m.test_folder_local)


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


def count_originals(entries, name):
    originals = [e for e in entries if e["name"] == name]
    return len(originals)


def assert_exists(m, dbx_folder, name):
    """Asserts that an item with `name` exists in `dbx_folder`."""
    entries = m.list_folder(dbx_folder)
    assert count_originals(entries, name) == 1, f'"{name}" missing on Dropbox'


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
    n_remote = len(entries) - 1
    assert n_remote == n, f"Expected {n} items but found {n_remote}: {entries}"
