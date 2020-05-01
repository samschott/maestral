# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
import os
import os.path as osp
import time
import shutil
from pathlib import Path
from threading import Event
import timeit
import logging

from dropbox.files import WriteMode

from maestral.sync import (
    FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent,
    DirCreatedEvent, DirDeletedEvent, DirMovedEvent,
)
from maestral.sync import FolderMetadata
from maestral.sync import delete, move
from maestral.sync import is_child
from maestral.sync import get_local_hash, DirectorySnapshot
from maestral.sync import UpDownSync, Observer, FSEventHandler
from maestral.errors import NotFoundError
from maestral.main import Maestral
from maestral.main import get_log_path
from maestral.constants import IS_FS_CASE_SENSITIVE


logger = logging.getLogger(__file__)


class DummyUpDownSync(UpDownSync):

    def __init__(self, dropbox_path=''):
        self._dropbox_path = dropbox_path

    def _should_split_excluded(self, event):
        return False

    def is_excluded(self, dbx_path):
        return False


def test_clean_local_events():

    def path(i):
        return f'/test {i}'

    # 1) Simple cases
    file_events_test0 = [
        # created + deleted -> None
        FileCreatedEvent(path(1)),
        FileDeletedEvent(path(1)),
        # deleted + created -> modified
        FileDeletedEvent(path(2)),
        FileCreatedEvent(path(2)),
    ]

    res0 = [
        # created + deleted -> None
        # deleted + created -> modified
        FileModifiedEvent(path(2))
    ]

    # 2) Single file events, keep as is
    file_events_test1 = [
        FileModifiedEvent(path(1)),
        FileCreatedEvent(path(2)),
        FileDeletedEvent(path(3)),
        FileMovedEvent(path(4), path(5)),
    ]

    res1 = [
        FileModifiedEvent(path(1)),
        FileCreatedEvent(path(2)),
        FileDeletedEvent(path(3)),
        FileMovedEvent(path(4), path(5)),
    ]

    # 3) Difficult move cases
    file_events_test2 = [
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

    res2 = [
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

    # 4) Gedit save event
    file_events_test3 = [
        FileCreatedEvent('.gedit-save-UR4EC0'),   # save new version to tmp file
        FileModifiedEvent('.gedit-save-UR4EC0'),  # modify tmp file
        FileMovedEvent(path(1), path(1) + '~'),   # move old version to backup
        FileMovedEvent('.gedit-save-UR4EC0', path(1)),  # replace old version with tmp
    ]

    res3 = [
        FileModifiedEvent(path(1)),       # modified file
        FileCreatedEvent(path(1) + '~'),  # backup
    ]

    # 5) macOS safe-save event
    file_events_test4 = [
        FileMovedEvent(path(1), path(1) + '.sb-b78ef837-dLht38'),  # move to backup
        FileCreatedEvent(path(1)),                                 # create new version
        FileDeletedEvent(path(1) + '.sb-b78ef837-dLht38'),         # delete backup
    ]

    res4 = [
        FileModifiedEvent(path(1)),  # modified file
    ]

    # 6) Word on macOS created event
    file_events_test5 = [
        FileCreatedEvent(path(1)),
        FileDeletedEvent(path(1)),
        FileCreatedEvent(path(1)),
        FileCreatedEvent('~$' + path(1)),
    ]

    res5 = [
        FileCreatedEvent(path(1)),         # created file
        FileCreatedEvent('~$' + path(1)),  # backup
    ]

    # 7) Simple type changes
    file_events_test6 = [
        # keep as is
        FileDeletedEvent(path(1)),
        DirCreatedEvent(path(1)),
        # keep as is
        DirDeletedEvent(path(2)),
        FileCreatedEvent(path(2)),
    ]

    res6 = [
        # keep as is
        FileDeletedEvent(path(1)),
        DirCreatedEvent(path(1)),
        # keep as is
        DirDeletedEvent(path(2)),
        FileCreatedEvent(path(2)),
    ]

    # 8) Difficult type changes
    file_events_test7 = [
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

    res7 = [
        FileDeletedEvent(path(1)),
        DirCreatedEvent(path(1)),

        FileDeletedEvent(path(2)),
        DirCreatedEvent(path(3)),
    ]

    # 9) Event hierarchies
    file_events_test8 = [
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

    res8 = [
        DirDeletedEvent(path(1)),
        DirMovedEvent(path(2), path(3)),
    ]

    # 10) Performance test

    # 15,000 nested deleted events (10,000 folders, 5,000 files)
    # 15,000 nested moved events (10,000 folders, 5,000 files)
    # 4,995 unrelated created events
    file_events_test9 = [DirDeletedEvent(n * path(1)) for n in range(1, 10001)]
    file_events_test9 += [FileDeletedEvent(n * path(1) + '.txt') for n in range(1, 5001)]
    file_events_test9 += [DirMovedEvent(n * path(2), n * path(3)) for n in range(1, 10001)]
    file_events_test9 += [FileMovedEvent(n * path(2) + '.txt', n * path(3) + '.txt')
                          for n in range(1, 5001)]
    file_events_test9 += [FileCreatedEvent(path(n)) for n in range(5, 5001)]

    res9 = [
        DirDeletedEvent(path(1)),
        DirMovedEvent(path(2), path(3)),
        FileDeletedEvent(path(1) + '.txt'),
        FileMovedEvent(path(2) + '.txt', path(3) + '.txt'),
    ]
    res9 += [FileCreatedEvent(path(n)) for n in range(5, 5001)]

    sync = DummyUpDownSync()

    cleaned_file_events_test0 = sync._clean_local_events(file_events_test0)
    cleaned_file_events_test1 = sync._clean_local_events(file_events_test1)
    cleaned_file_events_test2 = sync._clean_local_events(file_events_test2)
    cleaned_file_events_test3 = sync._clean_local_events(file_events_test3)
    cleaned_file_events_test4 = sync._clean_local_events(file_events_test4)
    cleaned_file_events_test5 = sync._clean_local_events(file_events_test5)
    cleaned_file_events_test6 = sync._clean_local_events(file_events_test6)
    cleaned_file_events_test7 = sync._clean_local_events(file_events_test7)
    cleaned_file_events_test8 = sync._clean_local_events(file_events_test8)
    cleaned_file_events_test9 = sync._clean_local_events(file_events_test9)

    assert set(cleaned_file_events_test0) == set(res0)
    assert set(cleaned_file_events_test1) == set(res1)
    assert set(cleaned_file_events_test2) == set(res2)
    assert set(cleaned_file_events_test3) == set(res3)
    assert set(cleaned_file_events_test4) == set(res4)
    assert set(cleaned_file_events_test5) == set(res5)
    assert set(cleaned_file_events_test6) == set(res6)
    assert set(cleaned_file_events_test7) == set(res7)
    assert set(cleaned_file_events_test8) == set(res8)
    assert set(cleaned_file_events_test9) == set(res9)

    n_loops = 4
    duration = timeit.timeit(lambda: sync._clean_local_events(file_events_test9),
                             number=n_loops)

    assert duration < 5 * n_loops  # less than 5 sec per call


def test_ignore_local_events():

    dummy_dir = Path(os.getcwd()).parent / 'dropbox_dir'

    delete(dummy_dir)
    dummy_dir.mkdir()

    syncing = Event()
    startup = Event()
    syncing.set()

    sync = DummyUpDownSync(dummy_dir)
    fs_event_handler = FSEventHandler(syncing, startup, sync)

    observer = Observer()
    observer.schedule(fs_event_handler, str(dummy_dir), recursive=True)
    observer.start()

    try:

        # 1) Test that we recieve events

        new_dir = dummy_dir / 'parent'
        new_dir.mkdir()

        changes, local_cursor = sync.wait_for_local_changes()
        assert len(changes) == 1
        assert changes[0] == DirCreatedEvent(str(new_dir))

        delete(new_dir)
        sync.wait_for_local_changes()

        # 2) Test ignoring the creation of a directory tree

        with fs_event_handler.ignore(DirCreatedEvent(str(new_dir))):
            new_dir.mkdir()
            for i in range(10):
                file = new_dir / f'test_{i}'
                file.touch()

        changes, local_cursor = sync.wait_for_local_changes()
        assert len(changes) == 0

        delete(new_dir)
        sync.wait_for_local_changes()

        # 3) Test moving a directory tree

        new_dir.mkdir()
        for i in range(10):
            file = new_dir / f'test_{i}'
            file.touch()

        sync.wait_for_local_changes()

        new_dir_1 = dummy_dir / 'parent2'

        with fs_event_handler.ignore(DirMovedEvent(str(new_dir), str(new_dir_1))):
            move(new_dir, new_dir_1)

        changes, local_cursor = sync.wait_for_local_changes()
        assert len(changes) == 0

        delete(new_dir_1)
        sync.wait_for_local_changes()

        # 4) Test catching not-ignored events

        with fs_event_handler.ignore(DirCreatedEvent(str(new_dir)), recursive=False):
            new_dir.mkdir()
            for i in range(10):
                # may trigger FileCreatedEvent and FileModifiedVent
                file = new_dir / f'test_{i}'
                file.touch()

        changes, local_cursor = sync.wait_for_local_changes()
        assert all(not c.is_directory for c in changes)

        delete(new_dir)
        sync.wait_for_local_changes()

    finally:

        # cleanup
        delete(dummy_dir)

        observer.stop()
        observer.join()


# We do not test individual methods of `maestral.sync` but rather ensure an effective
# result: successful syncing and conflict resolution in standard and challenging cases.

def test_sync_cases():

    resources = osp.dirname(__file__) + '/resources'

    # inital setup

    m = Maestral('test-config')
    m._auth._account_id = os.environ.get('DROPBOX_ID')
    m._auth._access_token = os.environ.get('DROPBOX_TOKEN')

    m.create_dropbox_directory('~/Dropbox_Test')

    assert not m.pending_link
    assert not m.pending_dropbox_folder

    # exclude all existing items
    excluded_items = list(e['path_lower'] for e in m.list_folder('/'))
    m.set_excluded_items(excluded_items)

    assert set(m.excluded_items) == set(excluded_items)

    # helper functions

    test_folder_dbx = '/sync_tests'
    test_folder_local = m.dropbox_path + test_folder_dbx

    def wait_for_idle(minimum=4):
        # wait until idle for at least minimum sec
        t0 = time.time()
        while time.time() - t0 < minimum:
            if m.sync.lock.locked():
                m.monitor._wait_for_idle()
                t0 = time.time()  # reset start time
            else:
                time.sleep(0.1)

    def clean_remote():
        try:
            m.client.remove(test_folder_dbx)
        except NotFoundError:
            pass

        m.client.make_dir(test_folder_dbx)

    def assert_synced(local_folder, remote_folder):
        remote_items = m.list_folder(remote_folder, recursive=True)
        local_snapshot = DirectorySnapshot(local_folder)
        rev_index = m.sync.get_rev_index()

        for r in remote_items:
            dbx_path = r['path_display']
            local_path = m.to_local_path(dbx_path)

            remote_hash = r['content_hash'] if r['type'] == 'FileMetadata' else 'folder'
            remote_rev = r['rev'] if r['type'] == 'FileMetadata' else 'folder'

            assert get_local_hash(local_path) == remote_hash, f'different file content for "{dbx_path}"'
            assert m.sync.get_local_rev(dbx_path) == remote_rev, f'different revs for "{dbx_path}"'

        for path in rev_index:
            if is_child(path, remote_folder):
                matching_items = list(r for r in remote_items if r['path_lower'] == path)
                assert len(matching_items) == 1, f'indexed item "{path}" does not exist on dbx'

        for path in local_snapshot.paths:
            if not m.sync.is_excluded(path):
                dbx_path = m.sync.to_dbx_path(path).lower()
                matching_items = list(r for r in remote_items if r['path_lower'] == dbx_path)
                assert len(matching_items) == 1, f'local item "{path}" does not exist on dbx'

    def count_conflicts(entries):
        ccs = list(e for e in entries if '(1)' in e['path_lower']
                   or 'conflicted copy' in e['path_lower']
                   or 'conflicting copy' in e['path_lower'])
        return len(ccs)

    def count_originals(entries, name):
        originals = list(e for e in entries if e['path_lower'] == name)
        return len(originals)

    m.start_sync()
    wait_for_idle()

    try:

        # 1) Check local folder creation

        os.mkdir(test_folder_local)
        wait_for_idle()

        md = m.client.get_metadata(test_folder_dbx)

        assert isinstance(md, FolderMetadata)

        # 2) Check local file creation

        shutil.copy(resources + '/file.txt', test_folder_local)
        wait_for_idle()
        assert_synced(test_folder_local, test_folder_dbx)

        # 3) Check local file change

        with open(test_folder_local + '/file.txt', 'a') as f:
            f.write(' changed')

        wait_for_idle()
        assert_synced(test_folder_local, test_folder_dbx)

        # 4) Check remote file change

        m.client.upload(resources + '/file1.txt', test_folder_dbx + '/file.txt',
                        mode=WriteMode.overwrite)

        wait_for_idle()
        assert_synced(test_folder_local, test_folder_dbx)

        # 5) Check local and remote changed when paused

        m.pause_sync()
        wait_for_idle()

        with open(test_folder_local + '/file.txt', 'a') as f:
            f.write(' modified conflict')

        m.client.upload(resources + '/file2.txt', test_folder_dbx + '/file.txt',
                        mode=WriteMode.overwrite)

        m.resume_sync()

        wait_for_idle()
        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx)
        assert count_originals(entries, test_folder_dbx + '/file.txt') == 1, 'original missing'
        assert count_conflicts(entries) == 1, 'conflicting copy missing'
        assert len(entries) == 2

        # 5) Check local and remote deleted when paused

        m.pause_sync()
        wait_for_idle()

        for entry in os.scandir(test_folder_local):
            delete(entry.path)

        clean_remote()

        m.resume_sync()
        wait_for_idle()

        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx)
        assert len(entries) == 0

        # 6) Check local and remote created with equal content when paused

        m.pause_sync()
        wait_for_idle()

        shutil.copy(resources + '/file.txt', test_folder_local)
        m.client.upload(resources + '/file.txt', test_folder_dbx + '/file.txt')

        m.resume_sync()
        wait_for_idle()

        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx)
        assert len(entries) == 1

        # 6) Check local and remote created with different content when paused

        clean_remote()
        wait_for_idle()

        m.pause_sync()
        wait_for_idle()

        shutil.copy(resources + '/file.txt', test_folder_local)
        m.client.upload(resources + '/file1.txt', test_folder_dbx + '/file.txt')

        m.resume_sync()
        wait_for_idle()

        assert_synced(test_folder_local, test_folder_dbx)

        wait_for_idle()

        entries = m.list_folder(test_folder_dbx)
        assert count_originals(entries, test_folder_dbx + '/file.txt') == 1, 'original missing'
        assert count_conflicts(entries) == 1, 'conflicting copy missing'
        assert len(entries) == 2

        # 7) Check local file deleted during upload

        clean_remote()
        wait_for_idle()

        fake_created_event = FileCreatedEvent(test_folder_local + '/file.txt')
        m.monitor.fs_event_handler.local_file_event_queue.put(fake_created_event)

        wait_for_idle()

        assert_synced(test_folder_local, test_folder_dbx)
        entries = m.list_folder(test_folder_dbx)
        assert len(entries) == 0

        # 8) Check rapid local file changes. No conflicts should be created.

        for t in (0.1, 0.1, 0.5, 0.5, 1.0, 1.0, 2.0, 2.0):
            time.sleep(t)
            with open(test_folder_local + '/file.txt', 'a') as f:
                f.write(f' {t} ')

        wait_for_idle()
        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx)
        assert count_originals(entries, test_folder_dbx + '/file.txt') == 1, 'original missing'
        assert len(entries) == 1

        # 9) Check rapid remote file changes. No conflicts should be created.

        md = m.client.get_metadata(test_folder_dbx + '/file.txt')

        for t in (0.1, 0.1, 0.5, 0.5, 1.0, 1.0, 2.0, 2.0):
            time.sleep(t)
            with open(resources + '/file.txt', 'a') as f:
                f.write(f' {t} ')
            md = m.client.upload(resources + '/file.txt', test_folder_dbx + '/file.txt',
                                 mode=WriteMode.update(md.rev))

        with open(resources + '/file.txt', 'w') as f:
            f.write('content')  # reset file content

        wait_for_idle()
        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx)
        assert count_originals(entries, test_folder_dbx + '/file.txt') == 1, 'original missing'
        assert len(entries) == 1

        # 10) Check uploading a local tree

        clean_remote()
        wait_for_idle()

        shutil.copytree(resources + '/test_folder', test_folder_local + '/test_folder')

        wait_for_idle(10)
        assert_synced(test_folder_local, test_folder_dbx)

        # 11) Check deleting a local tree

        delete(test_folder_local + '/test_folder')

        wait_for_idle()
        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx)
        assert len(entries) == 0

        # 12) Check downloading a remote tree

        for i in range(1, 11):
            path = test_folder_dbx + i * '/nested_folder'
            m.client.make_dir(path)

        wait_for_idle()
        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx, recursive=True)
        assert len(entries) == 11

        # 13) Check deleting a remote tree

        m.client.remove(test_folder_dbx + '/nested_folder')

        wait_for_idle(10)
        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx)
        assert len(entries) == 0

        # 14) Check remote file replaced by folder

        shutil.copy(resources + '/file.txt', test_folder_local + '/file.txt')
        wait_for_idle()

        m.pause_sync()
        wait_for_idle()

        # replace remote file with folder
        m.client.remove(test_folder_dbx + '/file.txt')
        m.client.make_dir(test_folder_dbx + '/file.txt')

        m.resume_sync()
        wait_for_idle()

        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx)
        assert os.path.isdir(test_folder_local + '/file.txt')
        assert len(entries) == 1

        # 15) Check remote file replaced by folder and unsynced local changes

        # Check ctime and create CC of local file if necessary.

        clean_remote()
        wait_for_idle()

        shutil.copy(resources + '/file.txt', test_folder_local + '/file.txt')
        wait_for_idle()

        m.pause_sync()
        wait_for_idle()

        # replace remote file with folder
        m.client.remove(test_folder_dbx + '/file.txt')
        m.client.make_dir(test_folder_dbx + '/file.txt')

        # create local changes
        with open(test_folder_local + '/file.txt', 'a') as f:
            f.write(' modified')

        m.resume_sync()
        wait_for_idle()

        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx)
        assert count_originals(entries, test_folder_dbx + '/file.txt') == 1, 'original missing'
        assert count_conflicts(entries) == 1, 'conflicting copy missing'
        assert len(entries) == 2

        # 16) Check remote folder replaced by file

        clean_remote()
        wait_for_idle()

        os.mkdir(test_folder_local + '/folder')
        wait_for_idle()

        m.pause_sync()
        wait_for_idle()

        # replace remote folder with file
        m.client.remove(test_folder_dbx + '/folder')
        m.client.upload(resources + '/file.txt', test_folder_dbx + '/folder')

        m.resume_sync()
        wait_for_idle()

        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx)
        assert os.path.isfile(test_folder_local + '/folder')
        assert len(entries) == 1

        # 17) Check remote folder replaced by file and unsynced local changes

        # Recurse through ctimes of children and check if we have any un-synced changes.
        # If yes, create CCs for those items. Others will be deleted.

        clean_remote()
        wait_for_idle()

        os.mkdir(test_folder_local + '/folder')
        wait_for_idle()

        m.pause_sync()
        wait_for_idle()

        # replace remote folder with file
        m.client.remove(test_folder_dbx + '/folder')
        m.client.upload(resources + '/file.txt', test_folder_dbx + '/folder')

        # create local changes
        os.mkdir(test_folder_local + '/folder/subfolder')

        m.resume_sync()
        wait_for_idle()

        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx)

        assert count_originals(entries, test_folder_dbx + '/folder') == 1, 'original missing'
        assert count_conflicts(entries) == 1, 'conflicting copy missing'
        assert len(entries) == 2

        # 18) Check local folder replaced by file

        clean_remote()
        wait_for_idle()

        os.mkdir(test_folder_local + '/folder')
        wait_for_idle()

        m.pause_sync()

        # replace local folder with file
        delete(test_folder_local + '/folder')
        shutil.copy(resources + '/file.txt', test_folder_local + '/folder')

        m.resume_sync()
        wait_for_idle()

        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx)
        assert os.path.isfile(test_folder_local + '/folder')
        assert len(entries) == 1

        # 19) Check local folder replaced by file and unsynced remote changes

        # Remote folder is currently not checked for unsynced changes but replaced.

        clean_remote()
        wait_for_idle()

        os.mkdir(test_folder_local + '/folder')
        wait_for_idle()

        m.pause_sync()
        wait_for_idle()

        # replace local folder with file
        delete(test_folder_local + '/folder')
        shutil.copy(resources + '/file.txt', test_folder_local + '/folder')

        # create remote changes
        m.client.upload(resources + '/file1.txt', test_folder_dbx + '/folder/file.txt')

        m.resume_sync()
        wait_for_idle()

        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx)
        assert count_originals(entries, test_folder_dbx + '/folder') == 1, 'original missing'
        assert len(entries) == 1

        # 20) Check local file replaced by folder

        clean_remote()
        wait_for_idle()

        shutil.copy(resources + '/file.txt', test_folder_local + '/file.txt')
        wait_for_idle()

        m.pause_sync()
        wait_for_idle()

        # replace local file with folder
        os.unlink(test_folder_local + '/file.txt')
        os.mkdir(test_folder_local + '/file.txt')

        m.resume_sync()
        wait_for_idle()

        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx)
        assert os.path.isdir(test_folder_local + '/file.txt')
        assert len(entries) == 1

        # 21) Check local file replaced by folder and unsynced remote changes

        # Check if server-modified time > last_sync of file and only delete file if
        # older. Otherwise, let Dropbox handle creating a CC.

        clean_remote()
        wait_for_idle()

        shutil.copy(resources + '/file.txt', test_folder_local + '/file.txt')
        wait_for_idle()

        m.pause_sync()
        wait_for_idle()

        # replace local file with folder
        os.unlink(test_folder_local + '/file.txt')
        os.mkdir(test_folder_local + '/file.txt')

        # create remote changes
        m.client.upload(resources + '/file1.txt', test_folder_dbx + '/file.txt',
                        mode=WriteMode.overwrite)

        m.resume_sync()
        wait_for_idle()

        assert_synced(test_folder_local, test_folder_dbx)

        entries = m.list_folder(test_folder_dbx)

        assert count_originals(entries, test_folder_dbx + '/file.txt') == 1, 'original missing'
        assert count_conflicts(entries) == 1, 'conflicting copy missing'
        assert len(entries) == 2

        # 22) Check selective sync conflict

        clean_remote()
        wait_for_idle()

        os.mkdir(test_folder_local + '/folder')
        wait_for_idle()

        m.exclude_item(test_folder_dbx + '/folder')
        wait_for_idle()

        assert not osp.exists(test_folder_local + '/folder')

        os.mkdir(test_folder_local + '/folder')
        wait_for_idle()

        os.mkdir(test_folder_local + '/folder')
        wait_for_idle()

        assert not osp.exists(test_folder_local + '/folder')
        assert osp.isdir(test_folder_local + '/folder (selective sync conflict)')
        assert osp.isdir(test_folder_local + '/folder (selective sync conflict 1)')
        assert m.client.get_metadata(test_folder_dbx + '/folder')
        assert m.client.get_metadata(test_folder_dbx + '/folder (selective sync conflict)')
        assert m.client.get_metadata(test_folder_dbx + '/folder (selective sync conflict 1)')

        m.client.remove(test_folder_dbx + '/folder')
        wait_for_idle()

        assert test_folder_dbx + '/folder' not in m.excluded_items

        # 23) Check case conflict

        clean_remote()
        wait_for_idle()

        if IS_FS_CASE_SENSITIVE:
            os.mkdir(test_folder_local + '/folder')
            wait_for_idle()

            os.mkdir(test_folder_local + '/Folder')
            wait_for_idle()

            assert osp.isdir(test_folder_local + '/folder')
            assert osp.isdir(test_folder_local + '/Folder (case conflict)')
            assert m.client.get_metadata(test_folder_dbx + '/folder')
            assert m.client.get_metadata(test_folder_dbx + '/Folder (case conflict)')

            assert_synced(test_folder_local, test_folder_dbx)

            clean_remote()
            wait_for_idle()

    finally:

        # cleanup

        m.stop_sync()
        try:
            m.client.remove(test_folder_dbx)
        except NotFoundError:
            pass

        delete(m.dropbox_path)
        delete(m.sync.rev_file_path)
        delete(m.account_profile_pic_path)
        m._conf.cleanup()
        m._state.cleanup()

        log_dir = get_log_path('maestral')

        log_files = []

        for file_name in os.listdir(log_dir):
            if file_name.startswith(m.config_name):
                log_files.append(os.path.join(log_dir, file_name))

        for file in log_files:
            delete(file)
