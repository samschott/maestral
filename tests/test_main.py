# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

"""
import os
import os.path as osp
import time
from maestral.main import Maestral
from maestral.errors import NotFoundError, FolderConflictError, PathError
from maestral.utils.appdirs import get_log_path
from maestral.utils.path import delete

import unittest
from unittest import TestCase


class TestAPI(TestCase):

    TEST_LOCK_PATH = '/test.lock'
    TEST_FOLDER_PATH = '/sync_tests'

    @classmethod
    def setUpClass(cls):

        cls.resources = osp.dirname(__file__) + '/resources'

        cls.m = Maestral('test-config')
        cls.m._auth._account_id = os.environ.get('DROPBOX_ID', '')
        cls.m._auth._access_token = os.environ.get('DROPBOX_TOKEN', '')
        cls.m.create_dropbox_directory('~/Dropbox_Test')

        # all our tests will be carried out within this folder
        cls.test_folder_dbx = cls.TEST_FOLDER_PATH
        cls.test_folder_local = cls.m.dropbox_path + cls.TEST_FOLDER_PATH

        # acquire test lock
        while True:
            try:
                cls.m.client.make_dir(cls.TEST_LOCK_PATH)
            except FolderConflictError:
                time.sleep(20)
            else:
                break

        # start syncing
        cls.m.start_sync()

        # create our temporary test folder
        os.mkdir(cls.test_folder_local)

    @classmethod
    def tearDownClass(cls):

        cls.m.stop_sync()
        try:
            cls.m.client.remove(cls.test_folder_dbx)
        except NotFoundError:
            pass

        try:
            cls.m.client.remove('/.mignore')
        except NotFoundError:
            pass

        # release test lock

        try:
            cls.m.client.remove(cls.TEST_LOCK_PATH)
        except NotFoundError:
            pass

        delete(cls.m.dropbox_path)
        delete(cls.m.sync.rev_file_path)
        delete(cls.m.account_profile_pic_path)
        cls.m._conf.cleanup()
        cls.m._state.cleanup()

        log_dir = get_log_path('maestral')

        log_files = []

        for file_name in os.listdir(log_dir):
            if file_name.startswith(cls.m.config_name):
                log_files.append(os.path.join(log_dir, file_name))

        for file in log_files:
            delete(file)

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
            self.m.client.remove('/.mignore')
        except NotFoundError:
            pass

        self.m.client.make_dir(self.test_folder_dbx)

    # test functions

    def test_selective_sync(self):
        """Test `Maestral.exclude_item` and  Maestral.include_item`."""

        test_path_local = self.test_folder_local + '/selective_sync_test_folder'
        test_path_dbx = self.test_folder_dbx + '/selective_sync_test_folder'

        # create a local folder 'folder'
        os.mkdir(test_path_local)
        os.mkdir(test_path_local + '/subfolder')
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
            self.m.include_item(test_path_dbx + '/subfolder')

        # test that 'folder' is removed from excluded_list on deletion
        self.m.client.remove(test_path_dbx)
        self.wait_for_idle()

        self.assertNotIn(test_path_dbx, self.m.excluded_items,
                         'deleted item is still in "excluded_items" list')

        # test excluding a non-existent folder
        with self.assertRaises(NotFoundError):
            self.m.exclude_item(test_path_dbx)

    def test_upload_sync_issues(self):

        # paths with backslash are not allowed on Dropbox
        test_path_local = self.test_folder_local + '/folder\\'

        n_errors_initial = len(self.m.sync_errors)

        os.mkdir(test_path_local)
        self.wait_for_idle()

        self.assertEqual(len(self.m.sync_errors), n_errors_initial + 1)
        self.assertTrue(any(e['local_path'] == test_path_local for e in self.m.sync_errors))

        delete(test_path_local)
        self.wait_for_idle()

        self.assertEqual(len(self.m.sync_errors), n_errors_initial)
        self.assertFalse(any(e['local_path'] == test_path_local for e in self.m.sync_errors))

    def test_download_sync_issues(self):
        # TODO: find a file with a reproducible download error
        pass


if __name__ == '__main__':
    unittest.main()
