# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

"""
import os
import os.path as osp
import time
from maestral.errors import NotFoundError, PathError
from maestral.utils.path import delete

import unittest
from unittest import TestCase

from fixtures import setup_test_config, cleanup_test_config, DropboxTestLock


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

    def clean_remote(self):
        """Recreates a fresh test folder."""
        try:
            self.m.client.remove(self.test_folder_dbx)
        except NotFoundError:
            pass

        try:
            self.m.client.remove("/.mignore")
        except NotFoundError:
            pass

        self.m.client.make_dir(self.test_folder_dbx)

    # test functions

    def test_selective_sync(self):
        """Test `Maestral.exclude_item` and  Maestral.include_item`."""

        test_path_local = self.test_folder_local + "/selective_sync_test_folder"
        test_path_dbx = self.test_folder_dbx + "/selective_sync_test_folder"

        # create a local folder 'folder'
        os.mkdir(test_path_local)
        os.mkdir(test_path_local + "/subfolder")
        self.wait_for_idle()

        # exclude 'folder' from sync
        self.m.exclude_item(test_path_dbx)
        self.wait_for_idle()

        self.assertFalse(osp.exists(test_path_local))
        self.assertIn(test_path_dbx, self.m.excluded_items)

        # include 'folder' in sync
        self.m.include_item(test_path_dbx)
        self.wait_for_idle()

        self.assertTrue(osp.exists(test_path_local))
        self.assertNotIn(test_path_dbx, self.m.excluded_items)

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

    def test_upload_sync_issues(self):

        # paths with backslash are not allowed on Dropbox
        test_path_local = self.test_folder_local + "/folder\\"
        test_path_dbx = self.test_folder_dbx + "/folder\\"

        n_errors_initial = len(self.m.sync_errors)

        os.mkdir(test_path_local)
        self.wait_for_idle()

        self.assertEqual(len(self.m.sync_errors), n_errors_initial + 1)
        self.assertEqual(self.m.sync_errors[-1]["local_path"], test_path_local)
        self.assertEqual(self.m.sync_errors[-1]["dbx_path"], test_path_dbx)
        self.assertEqual(self.m.sync_errors[-1]["type"], "PathError")

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
