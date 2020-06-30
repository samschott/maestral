# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

"""
import os
import os.path as osp
import time
import shutil
from pathlib import Path
from threading import Event
import timeit
from dropbox.files import WriteMode
from maestral.sync import (
    FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent,
    DirCreatedEvent, DirDeletedEvent, DirMovedEvent,
)
from maestral.sync import delete, move
from maestral.sync import is_child, is_fs_case_sensitive
from maestral.sync import get_local_hash, DirectorySnapshot
from maestral.sync import SyncEngine, Observer, FSEventHandler
from maestral.errors import NotFoundError, FolderConflictError
from maestral.main import Maestral
from maestral.main import get_log_path

import unittest
from unittest import TestCase


class DummySyncEngine(SyncEngine):

    def __init__(self, dropbox_path=''):
        self._dropbox_path = dropbox_path

    def _should_split_excluded(self, event):
        return False

    def is_excluded(self, dbx_path):
        return False


def path(i):
    """Returns path names '/test 1', '/test 2', ... """
    return f'/test {i}'


class TestCleanLocalEvents(TestCase):

    def setUp(self):
        self.sync = DummySyncEngine()

    def test_single_file_events(self):

        file_events = [
            FileModifiedEvent(path(1)),
            FileCreatedEvent(path(2)),
            FileDeletedEvent(path(3)),
            FileMovedEvent(path(4), path(5)),
        ]

        res = [
            FileModifiedEvent(path(1)),
            FileCreatedEvent(path(2)),
            FileDeletedEvent(path(3)),
            FileMovedEvent(path(4), path(5)),
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_single_path_cases(self):

        file_events = [
            # created + deleted -> None
            FileCreatedEvent(path(1)),
            FileDeletedEvent(path(1)),
            # deleted + created -> modified
            FileDeletedEvent(path(2)),
            FileCreatedEvent(path(2)),
        ]

        res = [
            # created + deleted -> None
            # deleted + created -> modified
            FileModifiedEvent(path(2))
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_move_events(self):

        file_events = [
            # created + moved -> created
            FileCreatedEvent(path(1)),
            FileMovedEvent(path(1), path(2)),
            # moved + deleted -> deleted
            FileMovedEvent(path(1), path(4)),
            FileDeletedEvent(path(4)),
            # moved + moved back -> modified
            FileMovedEvent(path(5), path(6)),
            FileMovedEvent(path(6), path(5)),
            # moved + moved -> deleted + created
            # (this is currently not handled as a single moved)
            FileMovedEvent(path(7), path(8)),
            FileMovedEvent(path(8), path(9)),
        ]

        res = [
            # created + moved -> created
            FileCreatedEvent(path(2)),
            # moved + deleted -> deleted
            FileDeletedEvent(path(1)),
            # moved + moved back -> modified
            FileModifiedEvent(path(5)),
            # moved + moved -> deleted + created
            # (this is currently not handled as a single moved)
            FileDeletedEvent(path(7)),
            FileCreatedEvent(path(9)),
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_gedit_save(self):

        file_events = [
            FileCreatedEvent('.gedit-save-UR4EC0'),   # save new version to tmp file
            FileModifiedEvent('.gedit-save-UR4EC0'),  # modify tmp file
            FileMovedEvent(path(1), path(1) + '~'),   # move old version to backup
            FileMovedEvent('.gedit-save-UR4EC0', path(1)),  # replace old version with tmp
        ]

        res = [
            FileModifiedEvent(path(1)),       # modified file
            FileCreatedEvent(path(1) + '~'),  # backup
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_macos_safe_save(self):

        file_events = [
            FileMovedEvent(path(1), path(1) + '.sb-b78ef837-dLht38'),  # move to backup
            FileCreatedEvent(path(1)),                                 # create new version
            FileDeletedEvent(path(1) + '.sb-b78ef837-dLht38'),         # delete backup
        ]

        res = [
            FileModifiedEvent(path(1)),  # modified file
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_msoffice_created(self):

        file_events = [
            FileCreatedEvent(path(1)),
            FileDeletedEvent(path(1)),
            FileCreatedEvent(path(1)),
            FileCreatedEvent('~$' + path(1)),
        ]

        res = [
            FileCreatedEvent(path(1)),         # created file
            FileCreatedEvent('~$' + path(1)),  # backup
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_type_changes(self):

        file_events = [
            # keep as is
            FileDeletedEvent(path(1)),
            DirCreatedEvent(path(1)),
            # keep as is
            DirDeletedEvent(path(2)),
            FileCreatedEvent(path(2)),
        ]

        res = [
            # keep as is
            FileDeletedEvent(path(1)),
            DirCreatedEvent(path(1)),
            # keep as is
            DirDeletedEvent(path(2)),
            FileCreatedEvent(path(2)),
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_type_changes_difficult(self):

        file_events = [
            # convert to FileDeleted -> DirCreated
            FileModifiedEvent(path(1)),
            FileDeletedEvent(path(1)),
            FileCreatedEvent(path(1)),
            FileDeletedEvent(path(1)),
            DirCreatedEvent(path(1)),
            # convert to FileDeleted(path1) -> DirCreated(path2)
            FileModifiedEvent(path(2)),
            FileDeletedEvent(path(2)),
            FileCreatedEvent(path(2)),
            FileDeletedEvent(path(2)),
            DirCreatedEvent(path(2)),
            DirMovedEvent(path(2), path(3)),
        ]

        res = [
            FileDeletedEvent(path(1)),
            DirCreatedEvent(path(1)),

            FileDeletedEvent(path(2)),
            DirCreatedEvent(path(3)),
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_nested_events(self):

        file_events = [
            # convert to a single DirDeleted
            DirDeletedEvent(path(1)),
            FileDeletedEvent(path(1) + '/file1.txt'),
            FileDeletedEvent(path(1) + '/file2.txt'),
            DirDeletedEvent(path(1) + '/sub'),
            FileDeletedEvent(path(1) + '/sub/file3.txt'),
            # convert to a single DirMoved
            DirMovedEvent(path(2), path(3)),
            FileMovedEvent(path(2) + '/file1.txt', path(3) + '/file1.txt'),
            FileMovedEvent(path(2) + '/file2.txt', path(3) + '/file2.txt'),
            DirMovedEvent(path(2) + '/sub', path(3) + '/sub'),
            FileMovedEvent(path(2) + '/sub/file3.txt', path(3) + '/sub/file3.txt'),
        ]

        res = [
            DirDeletedEvent(path(1)),
            DirMovedEvent(path(2), path(3)),
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_performance(self):

        # 15,000 nested deleted events (10,000 folders, 5,000 files)
        file_events = [DirDeletedEvent(n * path(1)) for n in range(1, 10001)]
        file_events += [FileDeletedEvent(n * path(1) + '.txt') for n in range(1, 5001)]

        # 15,000 nested moved events (10,000 folders, 5,000 files)
        file_events += [DirMovedEvent(n * path(2), n * path(3)) for n in range(1, 10001)]
        file_events += [FileMovedEvent(n * path(2) + '.txt', n * path(3) + '.txt')
                        for n in range(1, 5001)]

        # 4,995 unrelated created events
        file_events += [FileCreatedEvent(path(n)) for n in range(5, 5001)]

        res = [
            DirDeletedEvent(path(1)),
            DirMovedEvent(path(2), path(3)),
            FileDeletedEvent(path(1) + '.txt'),
            FileMovedEvent(path(2) + '.txt', path(3) + '.txt'),
        ]
        res += [FileCreatedEvent(path(n)) for n in range(5, 5001)]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

        n_loops = 4
        duration = timeit.timeit(lambda: self.sync._clean_local_events(file_events),
                                 number=n_loops)

        self.assertLess(duration, 5 * n_loops)


class TestIgnoreLocalEvents(TestCase):

    def setUp(self):
        self.sync = DummySyncEngine()
        self.dummy_dir = Path(os.getcwd()).parent / 'dropbox_dir'

        delete(self.dummy_dir)
        self.dummy_dir.mkdir()

        syncing = Event()
        startup = Event()
        syncing.set()

        self.sync = DummySyncEngine(self.dummy_dir)
        self.fs_event_handler = FSEventHandler(syncing, startup)

        self.observer = Observer()
        self.observer.schedule(self.fs_event_handler, str(self.dummy_dir), recursive=True)
        self.observer.start()

    def test_receiving_events(self):

        new_dir = self.dummy_dir / 'parent'
        new_dir.mkdir()

        changes, local_cursor = self.sync.wait_for_local_changes()

        expected_event_list = [DirCreatedEvent(str(new_dir))]

        self.assertEqual(changes, expected_event_list)

    def test_ignore_tree_creation(self):

        new_dir = self.dummy_dir / 'parent'

        with self.fs_event_handler.ignore(DirCreatedEvent(str(new_dir))):
            new_dir.mkdir()
            for i in range(10):
                file = new_dir / f'test_{i}'
                file.touch()

        changes, local_cursor = self.sync.wait_for_local_changes()
        self.assertEqual(len(changes), 0)

    def test_ignore_tree_move(self):

        new_dir = self.dummy_dir / 'parent'

        new_dir.mkdir()
        for i in range(10):
            file = new_dir / f'test_{i}'
            file.touch()

        self.sync.wait_for_local_changes()

        new_dir_1 = self.dummy_dir / 'parent2'

        with self.fs_event_handler.ignore(DirMovedEvent(str(new_dir), str(new_dir_1))):
            move(new_dir, new_dir_1)

        changes, local_cursor = self.sync.wait_for_local_changes()
        self.assertEqual(len(changes), 0)

    def test_catching_non_ignored_events(self):

        new_dir = self.dummy_dir / 'parent'

        with self.fs_event_handler.ignore(DirCreatedEvent(str(new_dir)), recursive=False):
            new_dir.mkdir()
            for i in range(10):
                # may trigger FileCreatedEvent and FileModifiedVent
                file = new_dir / f'test_{i}'
                file.touch()

        changes, local_cursor = self.sync.wait_for_local_changes()
        self.assertTrue(all(not c.is_directory for c in changes))

    def tearDown(self):

        # cleanup
        delete(self.dummy_dir)

        self.observer.stop()
        self.observer.join()


class TestSync(TestCase):
    """
    We do not test individual methods of `maestral.sync` but rather ensure an effective
    result: successful syncing and conflict resolution in standard and challenging cases.
    """

    TEST_LOCK_PATH = '/test.lock'
    TEST_FOLDER_PATH = '/sync_tests'

    @classmethod
    def setUpClass(cls):

        cls.resources = osp.dirname(__file__) + '/resources'

        cls.m = Maestral('test-config')
        cls.m._auth._account_id = os.environ.get('DROPBOX_ID', '')
        cls.m._auth._access_token = os.environ.get('DROPBOX_TOKEN', '')
        cls.m._auth._loaded = True
        cls.m._auth.token_access_type = 'legacy'
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
        """Recreates a fresh test folder on remote Dropbox."""
        try:
            self.m.client.remove(self.test_folder_dbx)
        except NotFoundError:
            pass

        try:
            self.m.client.remove('/.mignore')
        except NotFoundError:
            pass

        self.m.client.make_dir(self.test_folder_dbx)

    def clean_local(self):
        """Recreates a fresh test folder locally."""
        delete(self.m.dropbox_path + '/.mignore')
        delete(self.test_folder_local)
        os.mkdir(self.test_folder_local)

    def assert_synced(self, local_folder, remote_folder):
        """Asserts that the `local_folder` and `remote_folder` are synced."""
        remote_items = self.m.list_folder(remote_folder, recursive=True)
        local_snapshot = DirectorySnapshot(local_folder)
        rev_index = self.m.sync.get_rev_index()

        for r in remote_items:
            dbx_path = r['path_display']
            local_path = self.m.to_local_path(dbx_path)

            remote_hash = r['content_hash'] if r['type'] == 'FileMetadata' else 'folder'
            remote_rev = r['rev'] if r['type'] == 'FileMetadata' else 'folder'

            self.assertEqual(get_local_hash(local_path), remote_hash,
                             f'different file content for "{dbx_path}"')
            self.assertEqual(self.m.sync.get_local_rev(dbx_path), remote_rev,
                             f'different revs for "{dbx_path}"')

        for path in rev_index:
            if is_child(path, remote_folder):
                matching_items = list(r for r in remote_items if r['path_lower'] == path)
                self.assertEqual(len(matching_items), 1,
                                 f'indexed item "{path}" does not exist on dbx')

        for path in local_snapshot.paths:
            if not self.m.sync.is_excluded(path) and is_child(path, local_folder):
                dbx_path = self.m.sync.to_dbx_path(path).lower()
                matching_items = list(r for r in remote_items if r['path_lower'] == dbx_path)
                self.assertEqual(len(matching_items), 1,
                                 f'local item "{path}" does not exist on dbx')

    @staticmethod
    def _count_conflicts(entries, name):
        basename, ext = osp.splitext(name)

        candidates = list(e for e in entries if e['name'].startswith(basename))
        ccs = list(e for e in candidates
                   if '(1)' in e['name']  # created by Dropbox for add conflict
                   or 'conflicted copy' in e['name']  # created by Dropbox for update conflict
                   or 'conflicting copy' in e['name'])  # created by us
        return len(ccs)

    @staticmethod
    def _count_originals(entries, name):
        originals = list(e for e in entries if e['name'] == name)
        return len(originals)

    def assert_exists(self, dbx_folder, name):
        """Asserts that an item with `name` exists in `dbx_folder`."""
        entries = self.m.list_folder(dbx_folder)
        self.assertEqual(
            self._count_originals(entries, name), 1,
            f'"{name}" missing on Dropbox'
        )

    def assert_conflict(self, dbx_folder, name):
        """Asserts that a conflicting copy has been created for
         an item with `name` inside `dbx_folder`."""
        entries = self.m.list_folder(dbx_folder)
        self.assertEqual(
            self._count_conflicts(entries, name), 1,
            f'conflicting copy for "{name}" missing on Dropbox'
        )

    def assert_count(self, dbx_folder, n):
        """Asserts that `dbx_folder` has `n` entries (excluding itself)."""
        entries = self.m.list_folder(dbx_folder, recursive=True)
        n_remote = len(entries) - 1
        self.assertEqual(n_remote, n, f'Expected {n} items but found {n_remote}: {entries}')

    # test functions

    def setUp(self):
        self.m.resume_sync()
        self.clean_remote()
        self.wait_for_idle()

    def tearDown(self):
        self.assertFalse(self.m.fatal_errors)

    def test_setup(self):
        self.assertFalse(self.m.pending_link)
        self.assertFalse(self.m.pending_dropbox_folder)
        self.assert_synced(self.m.dropbox_path, '/')

    def test_file_lifecycle(self):

        # test creating a local file

        shutil.copy(self.resources + '/file.txt', self.test_folder_local)

        self.wait_for_idle()
        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, 'file.txt')
        self.assert_count(self.test_folder_dbx, 1)

        # test changing the file locally

        with open(self.test_folder_local + '/file.txt', 'w') as f:
            f.write('content changed')

        self.wait_for_idle()
        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, 'file.txt')
        self.assert_count(self.test_folder_dbx, 1)

        # test changing the file on remote

        self.m.client.upload(self.resources + '/file1.txt',
                             self.test_folder_dbx + '/file.txt',
                             mode=WriteMode.overwrite)

        self.wait_for_idle()
        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, 'file.txt')
        self.assert_count(self.test_folder_dbx, 1)

        # test deleting the file remotely

        self.m.client.remove(self.test_folder_dbx + '/file.txt')

        self.wait_for_idle()
        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, 0)

    def test_file_conflict(self):

        # create a local file
        shutil.copy(self.resources + '/file.txt', self.test_folder_local)
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # modify file.txt locally
        with open(self.test_folder_local + '/file.txt', 'a') as f:
            f.write(' modified conflict')

        # modify file.txt on remote
        self.m.client.upload(self.resources + '/file2.txt',
                             self.test_folder_dbx + '/file.txt',
                             mode=WriteMode.overwrite)

        # resume syncing and check for conflicting copy
        self.m.resume_sync()

        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, 'file.txt')
        self.assert_conflict(self.test_folder_dbx, 'file.txt')
        self.assert_count(self.test_folder_dbx, 2)

    def test_parallel_deletion_when_paused(self):

        # create a local file
        shutil.copy(self.resources + '/file.txt', self.test_folder_local)

        self.wait_for_idle()
        self.assert_synced(self.test_folder_local, self.test_folder_dbx)

        self.m.pause_sync()
        self.wait_for_idle()

        # delete local files
        for entry in os.scandir(self.test_folder_local):
            delete(entry.path)

        # delete remote files
        self.clean_remote()

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, 0)

    def test_local_and_remote_creation_with_equal_content(self):

        self.m.pause_sync()
        self.wait_for_idle()

        # create local file
        shutil.copy(self.resources + '/file.txt', self.test_folder_local)
        # create remote file with equal content
        self.m.client.upload(self.resources + '/file.txt',
                             self.test_folder_dbx + '/file.txt')

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, 'file.txt')
        self.assert_count(self.test_folder_dbx, 1)

    def test_local_and_remote_creation_with_different_content(self):

        self.m.pause_sync()
        self.wait_for_idle()

        # create local file
        shutil.copy(self.resources + '/file.txt', self.test_folder_local)
        # create remote file with different content
        self.m.client.upload(self.resources + '/file1.txt',
                             self.test_folder_dbx + '/file.txt')

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, 'file.txt')
        self.assert_conflict(self.test_folder_dbx, 'file.txt')
        self.assert_count(self.test_folder_dbx, 2)

    def test_local_deletion_during_upload(self):

        fake_created_event = FileCreatedEvent(self.test_folder_local + '/file.txt')
        self.m.monitor.fs_event_handler.local_file_event_queue.put(fake_created_event)

        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, 0)

    def test_rapid_local_changes(self):

        for t in (0.1, 0.1, 0.5, 0.5, 1.0, 1.0, 2.0, 2.0):
            time.sleep(t)
            with open(self.test_folder_local + '/file.txt', 'a') as f:
                f.write(f' {t} ')

        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, 'file.txt')
        self.assert_count(self.test_folder_dbx, 1)

    def test_rapid_remote_changes(self):

        shutil.copy(self.resources + '/file.txt', self.test_folder_local)
        self.wait_for_idle()

        md = self.m.client.get_metadata(self.test_folder_dbx + '/file.txt')

        for t in (0.1, 0.1, 0.5, 0.5, 1.0, 1.0, 2.0, 2.0):
            time.sleep(t)
            with open(self.resources + '/file.txt', 'a') as f:
                f.write(f' {t} ')
            md = self.m.client.upload(self.resources + '/file.txt',
                                      self.test_folder_dbx + '/file.txt',
                                      mode=WriteMode.update(md.rev))

        with open(self.resources + '/file.txt', 'w') as f:
            f.write('content')  # reset file content

        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, 'file.txt')
        self.assert_count(self.test_folder_dbx, 1)

    def test_folder_tree_local(self):

        # test creating tree

        shutil.copytree(self.resources + '/test_folder',
                        self.test_folder_local + '/test_folder')

        snap = DirectorySnapshot(self.resources + '/test_folder')
        num_items = len(list(p for p in snap.paths if not self.m.sync.is_excluded(p)))

        self.wait_for_idle(10)

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, num_items)

        # test deleting tree

        delete(self.test_folder_local + '/test_folder')

        self.wait_for_idle()
        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, 0)

    def test_folder_tree_remote(self):

        # test creating remote tree

        for i in range(1, 11):
            path = self.test_folder_dbx + i * '/nested_folder'
            self.m.client.make_dir(path)

        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, 10)

        # test deleting remote tree

        self.m.client.remove(self.test_folder_dbx + '/nested_folder')

        self.wait_for_idle(10)

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, 0)

    def test_remote_file_replaced_by_folder(self):

        shutil.copy(self.resources + '/file.txt', self.test_folder_local + '/file.txt')
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # replace remote file with folder
        self.m.client.remove(self.test_folder_dbx + '/file.txt')
        self.m.client.make_dir(self.test_folder_dbx + '/file.txt')

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_count(self.test_folder_dbx, 1)
        self.assertTrue(os.path.isdir(self.test_folder_local + '/file.txt'))

    def test_remote_file_replaced_by_folder_and_unsynced_local_changes(self):

        shutil.copy(self.resources + '/file.txt', self.test_folder_local + '/file.txt')
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # replace remote file with folder
        self.m.client.remove(self.test_folder_dbx + '/file.txt')
        self.m.client.make_dir(self.test_folder_dbx + '/file.txt')

        # create local changes
        with open(self.test_folder_local + '/file.txt', 'a') as f:
            f.write(' modified')

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, 'file.txt')
        self.assert_conflict(self.test_folder_dbx, 'file.txt')
        self.assert_count(self.test_folder_dbx, 2)

    def test_remote_folder_replaced_by_file(self):

        os.mkdir(self.test_folder_local + '/folder')
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # replace remote folder with file
        self.m.client.remove(self.test_folder_dbx + '/folder')
        self.m.client.upload(self.resources + '/file.txt', self.test_folder_dbx + '/folder')

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assertTrue(os.path.isfile(self.test_folder_local + '/folder'))
        self.assert_count(self.test_folder_dbx, 1)

    def test_remote_folder_replaced_by_file_and_unsynced_local_changes(self):

        os.mkdir(self.test_folder_local + '/folder')
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # replace remote folder with file
        self.m.client.remove(self.test_folder_dbx + '/folder')
        self.m.client.upload(self.resources + '/file.txt', self.test_folder_dbx + '/folder')

        # create local changes
        os.mkdir(self.test_folder_local + '/folder/subfolder')

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, 'folder')
        self.assert_conflict(self.test_folder_dbx, 'folder')
        self.assert_count(self.test_folder_dbx, 3)

    def test_local_folder_replaced_by_file(self):

        os.mkdir(self.test_folder_local + '/folder')
        self.wait_for_idle()

        self.m.pause_sync()

        # replace local folder with file
        delete(self.test_folder_local + '/folder')
        shutil.copy(self.resources + '/file.txt', self.test_folder_local + '/folder')

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assertTrue(osp.isfile(self.test_folder_local + '/folder'))
        self.assert_count(self.test_folder_dbx, 1)

    def test_local_folder_replaced_by_file_and_unsynced_remote_changes(self):

        # remote folder is currently not checked for unsynced changes but replaced

        os.mkdir(self.test_folder_local + '/folder')
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # replace local folder with file
        delete(self.test_folder_local + '/folder')
        shutil.copy(self.resources + '/file.txt', self.test_folder_local + '/folder')

        # create remote changes
        self.m.client.upload(self.resources + '/file1.txt', self.test_folder_dbx + '/folder/file.txt')

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, 'folder')
        self.assert_count(self.test_folder_dbx, 1)

    def test_local_file_replaced_by_folder(self):

        shutil.copy(self.resources + '/file.txt', self.test_folder_local + '/file.txt')
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # replace local file with folder
        os.unlink(self.test_folder_local + '/file.txt')
        os.mkdir(self.test_folder_local + '/file.txt')

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assertTrue(osp.isdir(self.test_folder_local + '/file.txt'))
        self.assert_count(self.test_folder_dbx, 1)

    def test_local_file_replaced_by_folder_and_unsynced_remote_changes(self):

        # Check if server-modified time > last_sync of file and only delete file if
        # older. Otherwise, let Dropbox handle creating a CC.

        shutil.copy(self.resources + '/file.txt', self.test_folder_local + '/file.txt')
        self.wait_for_idle()

        self.m.pause_sync()
        self.wait_for_idle()

        # replace local file with folder
        os.unlink(self.test_folder_local + '/file.txt')
        os.mkdir(self.test_folder_local + '/file.txt')

        # create remote changes
        self.m.client.upload(self.resources + '/file1.txt',
                             self.test_folder_dbx + '/file.txt',
                             mode=WriteMode.overwrite)

        self.m.resume_sync()
        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, 'file.txt')
        self.assert_conflict(self.test_folder_dbx, 'file.txt')
        self.assert_count(self.test_folder_dbx, 2)

    def test_selective_sync_conflict(self):

        os.mkdir(self.test_folder_local + '/folder')
        self.wait_for_idle()

        # exclude 'folder' from sync
        self.m.exclude_item(self.test_folder_dbx + '/folder')
        self.wait_for_idle()

        self.assertFalse(osp.exists(self.test_folder_local + '/folder'))

        # recreate 'folder' locally
        os.mkdir(self.test_folder_local + '/folder')
        self.wait_for_idle()

        os.mkdir(self.test_folder_local + '/folder')
        self.wait_for_idle()

        self.assertFalse(osp.exists(self.test_folder_local + '/folder'))
        self.assertTrue(osp.isdir(self.test_folder_local + '/folder (selective sync conflict)'))
        self.assertTrue(osp.isdir(self.test_folder_local + '/folder (selective sync conflict 1)'))
        self.assertTrue(self.m.client.get_metadata(self.test_folder_dbx + '/folder'))
        self.assertIsNotNone(self.m.client.get_metadata(self.test_folder_dbx + '/folder (selective sync conflict)'))
        self.assertIsNotNone(self.m.client.get_metadata(self.test_folder_dbx + '/folder (selective sync conflict 1)'))

    @unittest.skipUnless(is_fs_case_sensitive('/home'), 'file system is not case sensitive')
    def test_case_conflict(self):

        os.mkdir(self.test_folder_local + '/folder')
        self.wait_for_idle()

        os.mkdir(self.test_folder_local + '/Folder')
        self.wait_for_idle()

        self.assertTrue(osp.isdir(self.test_folder_local + '/folder'))
        self.assertTrue(osp.isdir(self.test_folder_local + '/Folder (case conflict)'))
        self.assertIsNotNone(self.m.client.get_metadata(self.test_folder_dbx + '/folder'))
        self.assertIsNotNone(self.m.client.get_metadata(self.test_folder_dbx + '/Folder (case conflict)'))

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)

    def test_mignore(self):

        # 1) test that tracked items are unaffected

        os.mkdir(self.test_folder_local + '/bar')
        self.wait_for_idle()

        with open(self.m.sync.mignore_path, 'w') as f:
            f.write('foo/\n')   # ignore folder "foo"
            f.write('bar\n')    # ignore file or folder "bar"
            f.write('build\n')  # ignore file or folder "build"

        self.wait_for_idle()

        self.assert_synced(self.test_folder_local, self.test_folder_dbx)
        self.assert_exists(self.test_folder_dbx, 'bar')

        # 2) test that new items are excluded

        os.mkdir(self.test_folder_local + '/foo')
        self.wait_for_idle()

        self.assertIsNone(self.m.client.get_metadata(self.test_folder_dbx + '/foo'))

        # 3) test that renaming an item excludes it

        move(self.test_folder_local + '/bar', self.test_folder_local + '/build')
        self.wait_for_idle()

        self.assertIsNone(self.m.client.get_metadata(self.test_folder_dbx + '/build'))

        # 4) test that renaming an item includes it

        move(self.test_folder_local + '/build', self.test_folder_local + '/folder')
        self.wait_for_idle()

        self.assert_exists(self.test_folder_dbx, 'folder')

        self.clean_local()
        self.wait_for_idle()


if __name__ == '__main__':
    unittest.main()
