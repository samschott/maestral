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

from maestral.sync import (
    FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent,
    DirCreatedEvent, DirDeletedEvent, DirMovedEvent,
)
from maestral.sync import FolderMetadata
from maestral.sync import delete, move
from maestral.sync import is_child
from maestral.sync import get_local_hash, DirectorySnapshot
from maestral.sync import UpDownSync, Observer, FSEventHandler
from maestral.main import Maestral
from maestral.main import get_log_path


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

    # Simple cases
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

    # Single file events, keep as is
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

    # Difficult move cases
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

    # Gedit save event
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

    # macOS safe-save event
    file_events_test4 = [
        FileMovedEvent(path(1), path(1) + '.sb-b78ef837-dLht38'),  # move to backup
        FileCreatedEvent(path(1)),                                 # create new version
        FileDeletedEvent(path(1) + '.sb-b78ef837-dLht38'),         # delete backup
    ]

    res4 = [
        FileModifiedEvent(path(1)),  # modified file
    ]

    # Word on macOS created event
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

    # simple type changes
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

    # difficult type changes
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

    # event hierarchies
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

    # performance test:
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

    # test that we recieve events

    new_dir = dummy_dir / 'parent'
    new_dir.mkdir()

    changes, local_cursor = sync.wait_for_local_changes()
    assert len(changes) == 1
    assert changes[0] == DirCreatedEvent(str(new_dir))

    delete(new_dir)
    sync.wait_for_local_changes()

    # test ignoring the creation of a directory tree

    with fs_event_handler.ignore(DirCreatedEvent(str(new_dir))):
        new_dir.mkdir()
        for i in range(10):
            file = new_dir / f'test_{i}'
            file.touch()

    changes, local_cursor = sync.wait_for_local_changes()
    assert len(changes) == 0

    delete(new_dir)
    sync.wait_for_local_changes()

    # test moving a directory tree

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

    # test catching not-ignored events

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

    # shut down

    delete(dummy_dir)

    observer.stop()
    observer.join()


# Create a Dropbox test account to automate the below test.
# We will not test individual methods of `maestral.sync` but rather ensure
# an effective result: successful syncing and conflict resolution in
# standard and challenging cases.

def test_sync_cases():
    # Currently those tests are performed manually with the following test cases:
    #
    # CC = conflicting copy
    #
    #  * Remote file replaced with a folder (OK): Check ctime and create CC of local file
    #    if necessary.
    #  * Remote folder replaced with a file (OK):
    #    Recurse through ctimes of children and check if we have any un-synced changes.
    #    If yes, create CCs for those items. Others will be deleted.
    #  * Local file replaced with a folder (OK):
    #    Check if server-modified time > laest_sync of file and only delete file if older.
    #    Otherwise, let Dropbox handle creating a CC.
    #  * Local folder replaced with a file (NOK):
    #    Remote folder is currently not checked for unsynced changes but a CC is created
    #    by default. We could recurse through all remote files and check for unsynced
    #    changes.
    #  * Remote and local items modified during sync pause (OK)
    #  * Remote and local items created during sync pause (OK)
    #  * Remote and local items deleted during sync pause (OK)
    #  * Local item created / modified -> registered for upload -> deleted before up: (OK)
    #    Ok because FileNotFoundError will be caught silently.
    #  * Local item created / modified -> uploaded -> deleted before re-download: (OK)
    #    Will not be re-downloaded if rev is still in index.
    #  * Local item created / modified -> uploaded -> modified before re-download: (OK)
    #    Will not be re-downloaded if rev is the same.
    #  * Local item deleted -> uploaded -> created before re-download: (OK)
    #    Local rev == remote rev == None. Deletion will not be carried out.
    #  * Remote item created -> registered -> local item created before download (OK):
    #    Local rev is None but file exists => CC created.
    #  * Remote item deleted -> registered -> local item created before download (OK):
    #    Local rev == remote rev == None. Deletion will not be carried out.
    #  * Remote item deleted -> registered -> local item deleted before download (OK):
    #    Local rev exists: deletion will be carried out locally and fail silently.
    #  * Remote item deleted -> registered -> local item modified before download (OK):
    #    Local rev != remote rev (= None), different file contents. Compare ctime and
    #    keep local item if local ctime > last_sync.
    #  * Remote item modified -> registered -> local item modified before download (OK):
    #    Local rev != remote rev. Compare ctime and create CC if ctime > last_sync and
    #    file contents are different.
    #

    resources = osp.dirname(__file__) + '/resources'

    # inital setup

    m = Maestral('test-config')
    m._auth._account_id = os.environ.get('DROPBOX_ID')
    m._auth._access_token = os.environ.get('DROPBOX_TOKEN')

    m.create_dropbox_directory('~/Dropbox Test')

    assert not m.pending_link
    assert not m.pending_dropbox_folder

    # exclude all existing items
    excluded_items = list(e['path_lower'] for e in m.list_folder('/'))
    m.set_excluded_items(excluded_items)

    assert set(m.excluded_items) == set(excluded_items)

    m.start_sync()
    m.monitor._wait_for_idle()

    # helper functions

    def wait_for_idle():
        time.sleep(2)
        m.monitor._wait_for_idle()

    def assert_synced(local_folder, remote_folder):
        remote_items = m.list_folder(remote_folder)
        local_snapshot = DirectorySnapshot(local_folder)
        rev_index = m.sync.get_rev_index()

        for r in remote_items:
            dbx_path = r['path_display']
            local_path = m.to_local_path(dbx_path)

            remote_hash = r['content_hash'] if r['type'] == 'FileMetadata' else 'folder'

            assert get_local_hash(local_path) == remote_hash, f'different file content for "{dbx_path}"'
            assert m.sync.get_local_rev(dbx_path) == r['rev'], f'different revs for "{dbx_path}"'

        for path in rev_index:
            if is_child(path, remote_folder):
                matching_items = list(r for r in remote_items if r['path_lower'] == path)
                assert len(matching_items) == 1, f'indexed item "{path}" does not exist on dbx'

        for path in local_snapshot.paths:
            if is_child(path, local_folder):
                dbx_path = m.sync.to_dbx_path(path).lower()
                matching_items = list(r for r in remote_items if r['path_lower'] == dbx_path)
                assert len(matching_items) == 1, f'local item "{path}" does not exist on dbx'

    # start simple, lets create a local directory for our tests and check that its synced

    sync_test_folder_dbx = '/sync_tests'
    sync_test_folder = m.dropbox_path + sync_test_folder_dbx

    os.mkdir(sync_test_folder)
    wait_for_idle()

    md = m.client.get_metadata(sync_test_folder_dbx)

    assert isinstance(md, FolderMetadata)

    # create a file and check if its synced

    shutil.copy(resources + '/test.txt', sync_test_folder)
    wait_for_idle()
    assert_synced(sync_test_folder, sync_test_folder_dbx)

    # cleanup

    m.stop_sync()
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
