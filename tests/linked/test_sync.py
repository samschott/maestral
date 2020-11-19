# -*- coding: utf-8 -*-

import os
import os.path as osp
import time
import shutil
import timeit

import pytest

from dropbox.files import WriteMode
from maestral.sync import FileCreatedEvent
from maestral.sync import delete, move
from maestral.sync import is_fs_case_sensitive
from maestral.sync import DirectorySnapshot, SyncEvent

from .fixtures import m, assert_synced, wait_for_idle, resources


if not os.environ.get("DROPBOX_TOKEN"):
    pytest.skip("Requires auth token", allow_module_level=True)


# test functions


def test_setup(m):
    assert_synced(m)


def test_file_lifecycle(m):

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

    # create a local file
    shutil.copy(resources + "/file.txt", m.test_folder_local)
    wait_for_idle(m)

    m.pause_sync()
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
    m.resume_sync()

    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_conflict(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 2)

    # check for fatal errors
    assert not m.fatal_errors


def test_parallel_deletion_when_paused(m):

    # create a local file
    shutil.copy(resources + "/file.txt", m.test_folder_local)

    wait_for_idle(m)
    assert_synced(m)

    m.pause_sync()
    wait_for_idle(m)

    # delete local file
    delete(m.test_folder_local + "/file.txt")

    # delete remote file
    m.client.remove("/sync_tests/file.txt")

    m.resume_sync()
    wait_for_idle(m)

    assert_synced(m)
    assert_child_count(m, "/sync_tests", 0)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_and_remote_creation_with_equal_content(m):

    m.pause_sync()
    wait_for_idle(m)

    # create local file
    shutil.copy(resources + "/file.txt", m.test_folder_local)
    # create remote file with equal content
    m.client.upload(resources + "/file.txt", "/sync_tests/file.txt")

    m.resume_sync()
    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 1)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_and_remote_creation_with_different_content(m):

    m.pause_sync()
    wait_for_idle(m)

    # create local file
    shutil.copy(resources + "/file.txt", m.test_folder_local)
    # create remote file with different content
    m.client.upload(resources + "/file1.txt", "/sync_tests/file.txt")

    m.resume_sync()
    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_conflict(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 2)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_deletion_during_upload(m):

    # we mimic a deletion during upload by queueing a fake FileCreatedEvent
    fake_created_event = FileCreatedEvent(m.test_folder_local + "/file.txt")
    m.monitor.fs_event_handler.local_file_event_queue.put(fake_created_event)

    wait_for_idle(m)

    assert_synced(m)
    assert_child_count(m, "/sync_tests", 0)

    # check for fatal errors
    assert not m.fatal_errors


def test_rapid_local_changes(m):

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

    with open(resources + "/file.txt", "w") as f:
        f.write("content")  # reset file content

    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 1)

    # check for fatal errors
    assert not m.fatal_errors


def test_folder_tree_created_local(m):

    # test creating tree

    shutil.copytree(resources + "/test_folder", m.test_folder_local + "/test_folder")

    snap = DirectorySnapshot(resources + "/test_folder")
    num_items = len(list(p for p in snap.paths if not m.sync.is_excluded(p)))

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


def test_folder_tree_created_remote(m):

    # test creating remote tree

    for i in range(1, 11):
        path = "/sync_tests" + i * "/nested_folder"
        m.client.make_dir(path)

    wait_for_idle(m)

    assert_synced(m)
    assert_child_count(m, "/sync_tests", 10)

    # test deleting remote tree

    m.client.remove("/sync_tests/nested_folder")
    wait_for_idle(m, 10)

    assert_synced(m)
    assert_child_count(m, "/sync_tests", 0)

    # check for fatal errors
    assert not m.fatal_errors


def test_remote_file_replaced_by_folder(m):

    shutil.copy(resources + "/file.txt", m.test_folder_local + "/file.txt")
    wait_for_idle(m)

    m.pause_sync()
    wait_for_idle(m)

    # replace remote file with folder
    m.client.remove("/sync_tests/file.txt")
    m.client.make_dir("/sync_tests/file.txt")

    m.resume_sync()
    wait_for_idle(m)

    assert_synced(m)
    assert_child_count(m, "/sync_tests", 1)
    assert os.path.isdir(m.test_folder_local + "/file.txt")

    # check for fatal errors
    assert not m.fatal_errors


def test_remote_file_replaced_by_folder_and_unsynced_local_changes(m):

    shutil.copy(resources + "/file.txt", m.test_folder_local + "/file.txt")
    wait_for_idle(m)

    m.pause_sync()
    wait_for_idle(m)

    # replace remote file with folder
    m.client.remove("/sync_tests/file.txt")
    m.client.make_dir("/sync_tests/file.txt")

    # create local changes
    with open(m.test_folder_local + "/file.txt", "a") as f:
        f.write(" modified")

    m.resume_sync()
    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_conflict(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 2)

    # check for fatal errors
    assert not m.fatal_errors


def test_remote_folder_replaced_by_file(m):

    os.mkdir(m.test_folder_local + "/folder")
    wait_for_idle(m)

    m.pause_sync()
    wait_for_idle(m)

    # replace remote folder with file
    m.client.remove("/sync_tests/folder")
    m.client.upload(resources + "/file.txt", "/sync_tests/folder")

    m.resume_sync()
    wait_for_idle(m)

    assert_synced(m)
    assert os.path.isfile(m.test_folder_local + "/folder")
    assert_child_count(m, "/sync_tests", 1)

    # check for fatal errors
    assert not m.fatal_errors


def test_remote_folder_replaced_by_file_and_unsynced_local_changes(m):

    os.mkdir(m.test_folder_local + "/folder")
    wait_for_idle(m)

    m.pause_sync()
    wait_for_idle(m)

    # replace remote folder with file
    m.client.remove("/sync_tests/folder")
    m.client.upload(resources + "/file.txt", "/sync_tests/folder")

    # create local changes
    os.mkdir(m.test_folder_local + "/folder/subfolder")

    m.resume_sync()
    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "folder")
    assert_conflict(m, "/sync_tests", "folder")
    assert_child_count(m, "/sync_tests", 3)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_folder_replaced_by_file(m):

    os.mkdir(m.test_folder_local + "/folder")
    wait_for_idle(m)

    m.pause_sync()

    # replace local folder with file
    delete(m.test_folder_local + "/folder")
    shutil.copy(resources + "/file.txt", m.test_folder_local + "/folder")

    m.resume_sync()
    wait_for_idle(m)

    assert_synced(m)
    assert osp.isfile(m.test_folder_local + "/folder")
    assert_child_count(m, "/sync_tests", 1)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_folder_replaced_by_file_and_unsynced_remote_changes(m):

    # remote folder is currently not checked for unsynced changes but replaced

    os.mkdir(m.test_folder_local + "/folder")
    wait_for_idle(m)

    m.pause_sync()
    wait_for_idle(m)

    # replace local folder with file
    delete(m.test_folder_local + "/folder")
    shutil.copy(resources + "/file.txt", m.test_folder_local + "/folder")

    # create remote changes
    m.client.upload(resources + "/file1.txt", "/sync_tests/folder/file.txt")

    m.resume_sync()
    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "folder")
    assert_child_count(m, "/sync_tests", 1)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_file_replaced_by_folder(m):

    shutil.copy(resources + "/file.txt", m.test_folder_local + "/file.txt")
    wait_for_idle(m)

    m.pause_sync()
    wait_for_idle(m)

    # replace local file with folder
    os.unlink(m.test_folder_local + "/file.txt")
    os.mkdir(m.test_folder_local + "/file.txt")

    m.resume_sync()
    wait_for_idle(m)

    assert_synced(m)
    assert osp.isdir(m.test_folder_local + "/file.txt")
    assert_child_count(m, "/sync_tests", 1)

    # check for fatal errors
    assert not m.fatal_errors


def test_local_file_replaced_by_folder_and_unsynced_remote_changes(m):

    # Check if server-modified time > last_sync of file and only delete file if
    # older. Otherwise, let Dropbox handle creating a conflicting copy.

    shutil.copy(resources + "/file.txt", m.test_folder_local + "/file.txt")
    wait_for_idle(m)

    m.pause_sync()
    wait_for_idle(m)

    # replace local file with folder
    os.unlink(m.test_folder_local + "/file.txt")
    os.mkdir(m.test_folder_local + "/file.txt")

    # create remote changes
    m.client.upload(
        resources + "/file1.txt",
        "/sync_tests/file.txt",
        mode=WriteMode.overwrite,
    )

    m.resume_sync()
    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "file.txt")
    assert_conflict(m, "/sync_tests", "file.txt")
    assert_child_count(m, "/sync_tests", 2)

    # check for fatal errors
    assert not m.fatal_errors


def test_selective_sync_conflict(m):

    os.mkdir(m.test_folder_local + "/folder")
    wait_for_idle(m)

    # exclude 'folder' from sync
    m.exclude_item("/sync_tests/folder")
    wait_for_idle(m)

    assert not (osp.exists(m.test_folder_local + "/folder"))

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

    # 1) test that tracked items are unaffected

    os.mkdir(m.test_folder_local + "/bar")
    wait_for_idle(m)

    with open(m.sync.mignore_path, "w") as f:
        f.write("foo/\n")  # ignore folder "foo"
        f.write("bar\n")  # ignore file or folder "bar"
        f.write("build\n")  # ignore file or folder "build"

    wait_for_idle(m)

    assert_synced(m)
    assert_exists(m, "/sync_tests", "bar")

    # 2) test that new items are excluded

    os.mkdir(m.test_folder_local + "/foo")
    wait_for_idle(m)

    assert not (m.client.get_metadata("/sync_tests/foo"))

    # 3) test that renaming an item excludes it

    move(m.test_folder_local + "/bar", m.test_folder_local + "/build")
    wait_for_idle(m)

    assert not (m.client.get_metadata("/sync_tests/build"))

    # 4) test that renaming an item includes it

    move(m.test_folder_local + "/build", m.test_folder_local + "/folder")
    wait_for_idle(m)

    assert_exists(m, "/sync_tests", "folder")

    clean_local(m)
    wait_for_idle(m)

    # check for fatal errors
    assert not m.fatal_errors


def test_upload_sync_issues(m):

    # paths with backslash are not allowed on Dropbox
    # we create such a local folder and assert that it triggers a sync issue

    test_path_local = m.test_folder_local + "/folder\\"
    test_path_dbx = "/sync_tests/folder\\"

    n_errors_initial = len(m.sync_errors)

    os.mkdir(test_path_local)
    wait_for_idle(m)

    assert len(m.sync_errors) == n_errors_initial + 1
    assert m.sync_errors[-1]["local_path"] == test_path_local
    assert m.sync_errors[-1]["dbx_path"] == test_path_dbx
    assert m.sync_errors[-1]["type"] == "PathError"

    # remove folder with invalid name and assert that sync issue is cleared

    delete(test_path_local)
    wait_for_idle(m)

    assert len(m.sync_errors) == n_errors_initial
    assert all(e["local_path"] != test_path_local for e in m.sync_errors)
    assert all(e["dbx_path"] != test_path_dbx for e in m.sync_errors)

    # check for fatal errors
    assert not m.fatal_errors


def test_download_sync_issues(m):
    test_path_local = m.test_folder_local + "/dmca.gif"
    test_path_dbx = "/sync_tests/dmca.gif"

    wait_for_idle(m)

    n_errors_initial = len(m.sync_errors)

    m.client.upload(resources + "/dmca.gif", test_path_dbx)

    wait_for_idle(m)

    # 1) Check that the sync issue is logged

    assert len(m.sync_errors) == n_errors_initial + 1
    assert m.sync_errors[-1]["local_path"] == test_path_local
    assert m.sync_errors[-1]["dbx_path"] == test_path_dbx
    assert m.sync_errors[-1]["type"] == "RestrictedContentError"
    assert test_path_dbx in m.sync.download_errors

    # 2) Check that the sync is retried after pause / resume

    m.pause_sync()
    m.resume_sync()

    wait_for_idle(m)

    assert len(m.sync_errors) == n_errors_initial + 1
    assert m.sync_errors[-1]["local_path"] == test_path_local
    assert m.sync_errors[-1]["dbx_path"] == test_path_dbx
    assert m.sync_errors[-1]["type"] == "RestrictedContentError"
    assert test_path_dbx in m.sync.download_errors

    # 3) Check that the error is cleared when the file is deleted

    m.client.remove(test_path_dbx)
    wait_for_idle(m)

    assert len(m.sync_errors) == n_errors_initial
    assert all(e["local_path"] != test_path_local for e in m.sync_errors)
    assert all(e["dbx_path"] != test_path_dbx for e in m.sync_errors)
    assert test_path_dbx not in m.sync.download_errors

    # check for fatal errors
    assert not m.fatal_errors


def test_excluded_folder_cleared_on_deletion(m):
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


def test_indexing_performance(m):

    # generate tree with 5 entries
    shutil.copytree(resources + "/test_folder", m.test_folder_local + "/test_folder")
    wait_for_idle(m)
    m.pause_sync()

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

    assert duration < 3  # expected ~ 1.8 sec


# ==== helper functions ================================================================


def clean_local(m):
    """Recreates a fresh test folder locally."""
    delete(m.dropbox_path + "/.mignore")
    delete(m.test_folder_local)
    os.mkdir(m.test_folder_local)


def count_conflicts(entries, name):
    basename, ext = osp.splitext(name)

    candidates = list(e for e in entries if e["name"].startswith(basename))
    ccs = list(
        e
        for e in candidates
        if "(1)" in e["name"]  # created by Dropbox for add conflict
        or "conflicted copy" in e["name"]  # created by Dropbox for update conflict
        or "conflicting copy" in e["name"]
    )  # created by us
    return len(ccs)


def count_originals(entries, name):
    originals = list(e for e in entries if e["name"] == name)
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
