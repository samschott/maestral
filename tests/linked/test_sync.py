# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

"""
import os
import os.path as osp
import time
import shutil
import unittest
from unittest import TestCase

from dropbox.files import WriteMode
from maestral.sync import FileCreatedEvent
from maestral.sync import delete, move
from maestral.sync import is_child, is_fs_case_sensitive
from maestral.sync import DirectorySnapshot
from maestral.errors import NotFoundError
from maestral.utils.path import to_existing_cased_path

from .fixtures import setup_test_config, cleanup_test_config, DropboxTestLock


@unittest.skipUnless(os.environ.get("DROPBOX_TOKEN"), "Requires auth token")
class TestSync(TestCase):
    """
    We don't test individual methods of `maestral.sync` but ensure an effective result:
    successful syncing and conflict resolution in standard and challenging cases.
    """

    config_name = "sync-test-config"

    TEST_FOLDER_PATH = "/sync_tests"
    resources = osp.dirname(__file__) + "/resources"

    def setUp(self):

        self.m = setup_test_config(self.config_name)
        self.lock = DropboxTestLock(self.m)
        if not self.lock.acquire(timeout=60 * 60):
            raise TimeoutError("Could not acquire test lock")

        # all our tests will be carried out within this folder
        self.test_folder_dbx = TestSync.TEST_FOLDER_PATH
        self.test_folder_local = self.m.dropbox_path + self.TEST_FOLDER_PATH

        # create / clean our temporary test folder
        try:
            self.m.client.remove(self.test_folder_dbx)
        except NotFoundError:
            pass
        self.m.client.make_dir(self.test_folder_dbx)

        # start syncing
        self.m.start_sync()

        # wait until initial sync has completed
        self.wait_for_idle()

    def tearDown(self):

        cleanup_test_config(self.m, self.test_folder_dbx)
        self.lock.release()

    # helper functions

    def wait_for_idle(self, minimum=4):
        """Blocks until Maestral is idle for at least `minimum` sec."""

        t0 = time.time()
        while time.time() - t0 < minimum:
            if self.m.sync.busy():
                self.m.monitor._wait_for_idle()
                t0 = time.time()
            else:
                time.sleep(0.1)

    def clean_local(self):
        """Recreates a fresh test folder locally."""
        delete(self.m.dropbox_path + "/.mignore")
        delete(self.test_folder_local)
        os.mkdir(self.test_folder_local)

    def assert_synced(self, local_folder, remote_folder):
        """Asserts that the `local_folder` and `remote_folder` are synced."""

        remote_folder = remote_folder.lower()

        remote_items = self.m.list_folder(remote_folder, recursive=True)
        local_snapshot = DirectorySnapshot(local_folder)

        # assert that all items from server are present locally
        # with the same content hash
        for r in remote_items:
            dbx_path = r["path_display"]
            local_path = to_existing_cased_path(dbx_path, root=self.m.dropbox_path)

            remote_hash = r["content_hash"] if r["type"] == "FileMetadata" else "folder"
            self.assertEqual(
                self.m.sync.get_local_hash(local_path),
                remote_hash,
                f'different file content for "{dbx_path}"',
            )

        # assert that all local items are present on server
        for path in local_snapshot.paths:
            if not self.m.sync.is_excluded(path) and is_child(path, local_folder):
                dbx_path = self.m.sync.to_dbx_path(path).lower()
                matching_items = list(
                    r for r in remote_items if r["path_lower"] == dbx_path
                )
                self.assertEqual(
                    len(matching_items), 1, f'local item "{path}" does not exist on dbx'
                )

        # check that our index is correct
        for entry in self.m.sync.get_index():
            if is_child(entry.dbx_path_lower, remote_folder):
                # check that there is a match on the server
                matching_items = list(
                    r for r in remote_items if r["path_lower"] == entry.dbx_path_lower
                )
                self.assertEqual(
                    len(matching_items),
                    1,
                    f'indexed item "{entry.dbx_path_lower}" does not exist on dbx',
                )

                r = matching_items[0]
                remote_rev = r["rev"] if r["type"] == "FileMetadata" else "folder"

                # check if revs are equal on server and locally
                self.assertEqual(
                    entry.rev,
                    remote_rev,
                    f'different revs for "{entry.dbx_path_lower}"',
                )

                # check if casing on drive is the same as in index
                local_path_expected_casing = self.m.dropbox_path + entry.dbx_path_cased
                local_path_actual_casing = to_existing_cased_path(
                    local_path_expected_casing
                )

                self.assertEqual(
                    local_path_expected_casing,
                    local_path_actual_casing,
                    "casing on drive does not match index",
                )

    @staticmethod
    def _count_conflicts(entries, name):
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

    @staticmethod
    def _count_originals(entries, name):
        originals = list(e for e in entries if e["name"] == name)
        return len(originals)

    def assert_exists(self, dbx_folder, name):
        """Asserts that an item with `name` exists in `dbx_folder`."""
        entries = self.m.list_folder(dbx_folder)
        self.assertEqual(
            self._count_originals(entries, name), 1, f'"{name}" missing on Dropbox'
        )

    def assert_conflict(self, dbx_folder, name):
        """Asserts that a conflicting copy has been created for
        an item with `name` inside `dbx_folder`."""
        entries = self.m.list_folder(dbx_folder)
        self.assertEqual(
            self._count_conflicts(entries, name),
            1,
            f'conflicting copy for "{name}" missing on Dropbox',
        )

    def assert_count(self, dbx_folder, n):
        """Asserts that `dbx_folder` has `n` entries (excluding itself)."""
        entries = self.m.list_folder(dbx_folder, recursive=True)
        n_remote = len(entries) - 1
        self.assertEqual(
            n_remote, n, f"Expected {n} items but found {n_remote}: {entries}"
        )

    # test functions

    def test_setup(self):
        self.assertFalse(self.m.pending_link)
        self.assertFalse(self.m.pending_dropbox_folder)
        self.assert_synced(self.m.dropbox_path, "/")
        self.assertEqual(self.m.status, "Up to date")

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_file_lifecycle(self):

        # test creating a local file

        shutil.copy(self.resources + "/file.txt", self.test_folder_local)

        self.wait_for_idle()
        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, "file.txt")
        self.assert_count(self.test_folder_dbx, 1)

        # test changing the file locally

        with open(self.test_folder_local + "/file.txt", "w") as f:
            f.write("content changed")

        self.wait_for_idle()
        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, "file.txt")
        self.assert_count(self.test_folder_dbx, 1)

        # test changing the file on remote

        self.m.client.upload(
            self.resources + "/file1.txt",
            self.test_folder_dbx + "/file.txt",
            mode=WriteMode.overwrite,
        )

        self.wait_for_idle()
        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, "file.txt")
        self.assert_count(self.test_folder_dbx, 1)

        # test deleting the file remotely

        self.m.client.remove(self.test_folder_dbx + "/file.txt")

        self.wait_for_idle()
        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, 0)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_file_conflict(self):

        # create a local file
        shutil.copy(self.resources + "/file.txt", self.test_folder_local)
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # modify file.txt locally
        with open(self.test_folder_local + "/file.txt", "a") as f:
            f.write(" modified conflict")

        # modify file.txt on remote
        self.m.client.upload(
            self.resources + "/file2.txt",
            self.test_folder_dbx + "/file.txt",
            mode=WriteMode.overwrite,
        )

        # resume syncing and check for conflicting copy
        self.m.resume_sync()

        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, "file.txt")
        self.assert_conflict(self.test_folder_dbx, "file.txt")
        self.assert_count(self.test_folder_dbx, 2)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_parallel_deletion_when_paused(self):

        # create a local file
        shutil.copy(self.resources + "/file.txt", self.test_folder_local)

        self.wait_for_idle()
        self.assert_synced(self.test_folder_local, self.test_folder_dbx)

        self.m.pause_sync()
        self.wait_for_idle()

        # delete local file
        delete(self.test_folder_local + "/file.txt")

        # delete remote file
        self.m.client.remove(self.test_folder_dbx + "/file.txt")

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, 0)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_local_and_remote_creation_with_equal_content(self):

        self.m.pause_sync()
        self.wait_for_idle()

        # create local file
        shutil.copy(self.resources + "/file.txt", self.test_folder_local)
        # create remote file with equal content
        self.m.client.upload(
            self.resources + "/file.txt", self.test_folder_dbx + "/file.txt"
        )

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, "file.txt")
        self.assert_count(self.test_folder_dbx, 1)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_local_and_remote_creation_with_different_content(self):

        self.m.pause_sync()
        self.wait_for_idle()

        # create local file
        shutil.copy(self.resources + "/file.txt", self.test_folder_local)
        # create remote file with different content
        self.m.client.upload(
            self.resources + "/file1.txt", self.test_folder_dbx + "/file.txt"
        )

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, "file.txt")
        self.assert_conflict(self.test_folder_dbx, "file.txt")
        self.assert_count(self.test_folder_dbx, 2)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_local_deletion_during_upload(self):

        fake_created_event = FileCreatedEvent(self.test_folder_local + "/file.txt")
        self.m.monitor.fs_event_handler.local_file_event_queue.put(fake_created_event)

        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, 0)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_rapid_local_changes(self):

        for t in (0.1, 0.1, 0.5, 0.5, 1.0, 1.0, 2.0, 2.0):
            time.sleep(t)
            with open(self.test_folder_local + "/file.txt", "a") as f:
                f.write(f" {t} ")

        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, "file.txt")
        self.assert_count(self.test_folder_dbx, 1)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_rapid_remote_changes(self):

        shutil.copy(self.resources + "/file.txt", self.test_folder_local)
        self.wait_for_idle()

        md = self.m.client.get_metadata(self.test_folder_dbx + "/file.txt")

        for t in (0.1, 0.1, 0.5, 0.5, 1.0, 1.0, 2.0, 2.0):
            time.sleep(t)
            with open(self.resources + "/file.txt", "a") as f:
                f.write(f" {t} ")
            md = self.m.client.upload(
                self.resources + "/file.txt",
                self.test_folder_dbx + "/file.txt",
                mode=WriteMode.update(md.rev),
            )

        with open(self.resources + "/file.txt", "w") as f:
            f.write("content")  # reset file content

        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, "file.txt")
        self.assert_count(self.test_folder_dbx, 1)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_folder_tree_local(self):

        # test creating tree

        shutil.copytree(
            self.resources + "/test_folder", self.test_folder_local + "/test_folder"
        )

        snap = DirectorySnapshot(self.resources + "/test_folder")
        num_items = len(list(p for p in snap.paths if not self.m.sync.is_excluded(p)))

        self.wait_for_idle(10)

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, num_items)

        # test deleting tree

        delete(self.test_folder_local + "/test_folder")

        self.wait_for_idle()
        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, 0)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_folder_tree_remote(self):

        # test creating remote tree

        for i in range(1, 11):
            path = self.test_folder_dbx + i * "/nested_folder"
            self.m.client.make_dir(path)

        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, 10)

        # test deleting remote tree

        self.m.client.remove(self.test_folder_dbx + "/nested_folder")
        self.wait_for_idle(10)

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, 0)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_remote_file_replaced_by_folder(self):

        shutil.copy(self.resources + "/file.txt", self.test_folder_local + "/file.txt")
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # replace remote file with folder
        self.m.client.remove(self.test_folder_dbx + "/file.txt")
        self.m.client.make_dir(self.test_folder_dbx + "/file.txt")

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, 1)
        self.assertTrue(os.path.isdir(self.test_folder_local + "/file.txt"))

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_remote_file_replaced_by_folder_and_unsynced_local_changes(self):

        shutil.copy(self.resources + "/file.txt", self.test_folder_local + "/file.txt")
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # replace remote file with folder
        self.m.client.remove(self.test_folder_dbx + "/file.txt")
        self.m.client.make_dir(self.test_folder_dbx + "/file.txt")

        # create local changes
        with open(self.test_folder_local + "/file.txt", "a") as f:
            f.write(" modified")

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, "file.txt")
        self.assert_conflict(self.test_folder_dbx, "file.txt")
        self.assert_count(self.test_folder_dbx, 2)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_remote_folder_replaced_by_file(self):

        os.mkdir(self.test_folder_local + "/folder")
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # replace remote folder with file
        self.m.client.remove(self.test_folder_dbx + "/folder")
        self.m.client.upload(
            self.resources + "/file.txt", self.test_folder_dbx + "/folder"
        )

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assertTrue(os.path.isfile(self.test_folder_local + "/folder"))
        self.assert_count(self.test_folder_dbx, 1)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_remote_folder_replaced_by_file_and_unsynced_local_changes(self):

        os.mkdir(self.test_folder_local + "/folder")
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # replace remote folder with file
        self.m.client.remove(self.test_folder_dbx + "/folder")
        self.m.client.upload(
            self.resources + "/file.txt", self.test_folder_dbx + "/folder"
        )

        # create local changes
        os.mkdir(self.test_folder_local + "/folder/subfolder")

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, "folder")
        self.assert_conflict(self.test_folder_dbx, "folder")
        self.assert_count(self.test_folder_dbx, 3)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_local_folder_replaced_by_file(self):

        os.mkdir(self.test_folder_local + "/folder")
        self.wait_for_idle()

        self.m.pause_sync()

        # replace local folder with file
        delete(self.test_folder_local + "/folder")
        shutil.copy(self.resources + "/file.txt", self.test_folder_local + "/folder")

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assertTrue(osp.isfile(self.test_folder_local + "/folder"))
        self.assert_count(self.test_folder_dbx, 1)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_local_folder_replaced_by_file_and_unsynced_remote_changes(self):

        # remote folder is currently not checked for unsynced changes but replaced

        os.mkdir(self.test_folder_local + "/folder")
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # replace local folder with file
        delete(self.test_folder_local + "/folder")
        shutil.copy(self.resources + "/file.txt", self.test_folder_local + "/folder")

        # create remote changes
        self.m.client.upload(
            self.resources + "/file1.txt", self.test_folder_dbx + "/folder/file.txt"
        )

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, "folder")
        self.assert_count(self.test_folder_dbx, 1)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_local_file_replaced_by_folder(self):

        shutil.copy(self.resources + "/file.txt", self.test_folder_local + "/file.txt")
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # replace local file with folder
        os.unlink(self.test_folder_local + "/file.txt")
        os.mkdir(self.test_folder_local + "/file.txt")

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assertTrue(osp.isdir(self.test_folder_local + "/file.txt"))
        self.assert_count(self.test_folder_dbx, 1)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_local_file_replaced_by_folder_and_unsynced_remote_changes(self):

        # Check if server-modified time > last_sync of file and only delete file if
        # older. Otherwise, let Dropbox handle creating a CC.

        shutil.copy(self.resources + "/file.txt", self.test_folder_local + "/file.txt")
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # replace local file with folder
        os.unlink(self.test_folder_local + "/file.txt")
        os.mkdir(self.test_folder_local + "/file.txt")

        # create remote changes
        self.m.client.upload(
            self.resources + "/file1.txt",
            self.test_folder_dbx + "/file.txt",
            mode=WriteMode.overwrite,
        )

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, "file.txt")
        self.assert_conflict(self.test_folder_dbx, "file.txt")
        self.assert_count(self.test_folder_dbx, 2)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_selective_sync_conflict(self):

        os.mkdir(self.test_folder_local + "/folder")
        self.wait_for_idle()

        # exclude 'folder' from sync
        self.m.exclude_item(self.test_folder_dbx + "/folder")
        self.wait_for_idle()

        self.assertFalse(osp.exists(self.test_folder_local + "/folder"))

        # recreate 'folder' locally
        os.mkdir(self.test_folder_local + "/folder")
        self.wait_for_idle()

        os.mkdir(self.test_folder_local + "/folder")
        self.wait_for_idle()

        self.assertFalse(osp.exists(self.test_folder_local + "/folder"))
        self.assertTrue(
            osp.isdir(self.test_folder_local + "/folder (selective sync conflict)")
        )
        self.assertTrue(
            osp.isdir(self.test_folder_local + "/folder (selective sync conflict 1)")
        )
        self.assertTrue(self.m.client.get_metadata(self.test_folder_dbx + "/folder"))
        self.assertIsNotNone(
            self.m.client.get_metadata(
                self.test_folder_dbx + "/folder (selective sync conflict)"
            )
        )
        self.assertIsNotNone(
            self.m.client.get_metadata(
                self.test_folder_dbx + "/folder (selective sync conflict 1)"
            )
        )

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    @unittest.skipUnless(
        is_fs_case_sensitive("/home"), "file system is not case sensitive"
    )
    def test_case_conflict(self):

        os.mkdir(self.test_folder_local + "/folder")
        self.wait_for_idle()

        os.mkdir(self.test_folder_local + "/Folder")
        self.wait_for_idle()

        self.assertTrue(osp.isdir(self.test_folder_local + "/folder"))
        self.assertTrue(osp.isdir(self.test_folder_local + "/Folder (case conflict)"))
        self.assertIsNotNone(
            self.m.client.get_metadata(self.test_folder_dbx + "/folder")
        )
        self.assertIsNotNone(
            self.m.client.get_metadata(self.test_folder_dbx + "/Folder (case conflict)")
        )

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_case_change_local(self):

        # start with nested folders
        os.mkdir(self.test_folder_local + "/folder")
        os.mkdir(self.test_folder_local + "/folder/Subfolder")
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)

        # rename to parent folder to upper case
        shutil.move(
            self.test_folder_local + "/folder", self.test_folder_local + "/FOLDER"
        )
        self.wait_for_idle()

        self.assertTrue(osp.isdir(self.test_folder_local + "/FOLDER"))
        self.assertTrue(osp.isdir(self.test_folder_local + "/FOLDER/Subfolder"))
        self.assertEqual(
            self.m.client.get_metadata(self.test_folder_dbx + "/folder").name,
            "FOLDER",
            "casing was not propagated to Dropbox",
        )

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_case_change_remote(self):

        # start with nested folders
        os.mkdir(self.test_folder_local + "/folder")
        os.mkdir(self.test_folder_local + "/folder/Subfolder")
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)

        # rename remote folder
        self.m.client.move(
            self.test_folder_dbx + "/folder",
            self.test_folder_dbx + "/FOLDER",
            autorename=True,
        )

        self.wait_for_idle()

        self.assertTrue(osp.isdir(self.test_folder_local + "/FOLDER"))
        self.assertTrue(osp.isdir(self.test_folder_local + "/FOLDER/Subfolder"))
        self.assertEqual(
            self.m.client.get_metadata(self.test_folder_dbx + "/folder").name,
            "FOLDER",
            "casing was not propagated to local folder",
        )

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_mignore(self):

        # 1) test that tracked items are unaffected

        os.mkdir(self.test_folder_local + "/bar")
        self.wait_for_idle()

        with open(self.m.sync.mignore_path, "w") as f:
            f.write("foo/\n")  # ignore folder "foo"
            f.write("bar\n")  # ignore file or folder "bar"
            f.write("build\n")  # ignore file or folder "build"

        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, "bar")

        # 2) test that new items are excluded

        os.mkdir(self.test_folder_local + "/foo")
        self.wait_for_idle()

        self.assertIsNone(self.m.client.get_metadata(self.test_folder_dbx + "/foo"))

        # 3) test that renaming an item excludes it

        move(self.test_folder_local + "/bar", self.test_folder_local + "/build")
        self.wait_for_idle()

        self.assertIsNone(self.m.client.get_metadata(self.test_folder_dbx + "/build"))

        # 4) test that renaming an item includes it

        move(self.test_folder_local + "/build", self.test_folder_local + "/folder")
        self.wait_for_idle()

        self.assert_exists(self.test_folder_dbx, "folder")

        self.clean_local()
        self.wait_for_idle()

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_upload_sync_issues(self):

        # paths with backslash are not allowed on Dropbox
        # we create such a local folder and assert that it triggers a sync issue

        test_path_local = self.test_folder_local + "/folder\\"
        test_path_dbx = self.test_folder_dbx + "/folder\\"

        n_errors_initial = len(self.m.sync_errors)

        os.mkdir(test_path_local)
        self.wait_for_idle()

        self.assertEqual(len(self.m.sync_errors), n_errors_initial + 1)
        self.assertEqual(self.m.sync_errors[-1]["local_path"], test_path_local)
        self.assertEqual(self.m.sync_errors[-1]["dbx_path"], test_path_dbx)
        self.assertEqual(self.m.sync_errors[-1]["type"], "PathError")

        # remove folder with invalid name and assert that sync issue is cleared

        delete(test_path_local)
        self.wait_for_idle()

        self.assertEqual(len(self.m.sync_errors), n_errors_initial)
        self.assertTrue(
            all(e["local_path"] != test_path_local for e in self.m.sync_errors)
        )
        self.assertTrue(all(e["dbx_path"] != test_path_dbx for e in self.m.sync_errors))

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_download_sync_issues(self):
        test_path_local = self.test_folder_local + "/dmca.gif"
        test_path_dbx = self.test_folder_dbx + "/dmca.gif"

        self.wait_for_idle()

        n_errors_initial = len(self.m.sync_errors)

        self.m.client.upload(self.resources + "/dmca.gif", test_path_dbx)

        self.wait_for_idle()

        # 1) Check that the sync issue is logged

        self.assertEqual(len(self.m.sync_errors), n_errors_initial + 1)
        self.assertEqual(self.m.sync_errors[-1]["local_path"], test_path_local)
        self.assertEqual(self.m.sync_errors[-1]["dbx_path"], test_path_dbx)
        self.assertEqual(self.m.sync_errors[-1]["type"], "RestrictedContentError")
        self.assertIn(test_path_dbx, self.m.sync.download_errors)

        # 2) Check that the sync is retried after pause / resume

        self.m.pause_sync()
        self.m.resume_sync()

        self.wait_for_idle()

        self.assertEqual(len(self.m.sync_errors), n_errors_initial + 1)
        self.assertEqual(self.m.sync_errors[-1]["local_path"], test_path_local)
        self.assertEqual(self.m.sync_errors[-1]["dbx_path"], test_path_dbx)
        self.assertEqual(self.m.sync_errors[-1]["type"], "RestrictedContentError")
        self.assertIn(test_path_dbx, self.m.sync.download_errors)

        # 3) Check that the error is cleared when the file is deleted

        self.m.client.remove(test_path_dbx)
        self.wait_for_idle()

        self.assertEqual(len(self.m.sync_errors), n_errors_initial)
        self.assertTrue(
            all(e["local_path"] != test_path_local for e in self.m.sync_errors)
        )
        self.assertTrue(all(e["dbx_path"] != test_path_dbx for e in self.m.sync_errors))
        self.assertNotIn(test_path_dbx, self.m.sync.download_errors)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)


if __name__ == "__main__":
    unittest.main()
