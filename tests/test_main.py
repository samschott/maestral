# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

"""
import os
import os.path as osp
import time
import unittest
from unittest import TestCase

from maestral.errors import NotFoundError, PathError
from maestral.main import FileStatus, IDLE
from maestral.main import logger as maestral_logger

from .fixtures import setup_test_config, cleanup_test_config, DropboxTestLock


@unittest.skipUnless(os.environ.get("DROPBOX_TOKEN"), "Requires auth token")
class TestAPI(TestCase):

    TEST_FOLDER_PATH = "/sync_tests"

    resources = osp.dirname(__file__) + "/resources"

    def setUp(self):

        self.m = setup_test_config()
        self.lock = DropboxTestLock(self.m)
        if not self.lock.acquire(timeout=60 * 60):
            raise TimeoutError("Could not acquire test lock")

        # all our tests will be carried out within this folder
        self.test_folder_dbx = TestAPI.TEST_FOLDER_PATH
        self.test_folder_local = self.m.dropbox_path + self.TEST_FOLDER_PATH

        # start syncing
        self.m.start_sync()

        # create our temporary test folder
        os.mkdir(self.test_folder_local)

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

    # API unit tests

    def test_status_properties(self):
        self.assertEqual(IDLE, self.m.status)
        self.assertTrue(self.m.running)
        self.assertTrue(self.m.connected)
        self.assertTrue(self.m.syncing)
        self.assertFalse(self.m.paused)
        self.assertFalse(self.m.sync_errors)
        self.assertFalse(self.m.fatal_errors)

        maestral_logger.info("test message")
        self.assertEqual(self.m.status, "test message")

    def test_file_status(self):

        # test synced folder
        file_status = self.m.get_file_status(self.test_folder_local)
        self.assertEqual(FileStatus.Synced.value, file_status)

        # test unwatched outside of dropbox
        file_status = self.m.get_file_status("/url/local")
        self.assertEqual(FileStatus.Unwatched.value, file_status)

        # test unwatched non-existent
        file_status = self.m.get_file_status("/this is not a folder")
        self.assertEqual(FileStatus.Unwatched.value, file_status)

        # test unwatched when paused
        self.m.pause_sync()
        self.wait_for_idle()

        file_status = self.m.get_file_status(self.test_folder_local)
        self.assertEqual(FileStatus.Unwatched.value, file_status)

        self.m.resume_sync()
        self.wait_for_idle()

        # test error status
        invalid_local_folder = self.test_folder_local + "/test_folder\\"
        os.mkdir(invalid_local_folder)
        self.wait_for_idle()

        file_status = self.m.get_file_status(invalid_local_folder)
        self.assertEqual(FileStatus.Error.value, file_status)

    def test_selective_sync_api(self):
        """Test `Maestral.exclude_item` and  Maestral.include_item`."""

        test_path_local = self.test_folder_local + "/selective_sync_test_folder"
        test_path_local_sub = test_path_local + "/subfolder"
        test_path_dbx = self.test_folder_dbx + "/selective_sync_test_folder"
        test_path_dbx_sub = test_path_dbx + "/subfolder"

        # create a local folder 'folder'
        os.mkdir(test_path_local)
        os.mkdir(test_path_local_sub)
        self.wait_for_idle()

        # exclude 'folder' from sync
        self.m.exclude_item(test_path_dbx)
        self.wait_for_idle()

        self.assertFalse(osp.exists(test_path_local))
        self.assertIn(test_path_dbx, self.m.excluded_items)
        self.assertEqual(self.m.excluded_status(test_path_dbx), "excluded")
        self.assertEqual(self.m.excluded_status(test_path_dbx_sub), "excluded")
        self.assertEqual(
            self.m.excluded_status(self.test_folder_dbx), "partially excluded"
        )

        # include 'folder' in sync
        self.m.include_item(test_path_dbx)
        self.wait_for_idle()

        self.assertTrue(osp.exists(test_path_local))
        self.assertNotIn(test_path_dbx, self.m.excluded_items)
        self.assertEqual(self.m.excluded_status(self.test_folder_dbx), "included")
        self.assertEqual(self.m.excluded_status(test_path_dbx_sub), "included")

        # exclude 'folder' again for further tests
        self.m.exclude_item(test_path_dbx)
        self.wait_for_idle()

        # test including a folder inside 'folder'
        with self.assertRaises(PathError):
            self.m.include_item(test_path_dbx + "/subfolder")

        # test that 'folder' is removed from excluded_list on deletion
        self.m.client.remove(test_path_dbx)
        self.wait_for_idle()

        self.assertNotIn(
            test_path_dbx,
            self.m.excluded_items,
            'deleted item is still in "excluded_items" list',
        )

        # test excluding a non-existent folder
        with self.assertRaises(NotFoundError):
            self.m.exclude_item(test_path_dbx)

        # check for fatal errors
        self.assertFalse(self.m.fatal_errors)

    def test_move_dropbox_folder(self):
        new_dir_short = "~/New Dropbox"
        new_dir = osp.realpath(osp.expanduser(new_dir_short))

        self.m.move_dropbox_directory(new_dir_short)
        self.assertTrue(osp.isdir(new_dir))
        self.assertEqual(new_dir, self.m.dropbox_path)

        # assert that sync was resumed after moving folder
        self.assertTrue(self.m.syncing)


if __name__ == "__main__":
    unittest.main()
