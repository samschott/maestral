# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

"""
import os
import os.path as osp
from pathlib import Path
from threading import Event
import timeit
from unittest import TestCase

from maestral.sync import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
)
from maestral.sync import delete, move
from maestral.sync import SyncEngine, DropboxClient, Observer, FSEventHandler
from maestral.sync import SyncDirection, ItemType, ChangeType
from maestral.utils.appdirs import get_home_dir
from maestral.utils.housekeeping import remove_configuration


def ipath(i):
    """Returns path names '/test 1', '/test 2', ... """
    return f"/test {i}"


class TestCleanLocalEvents(TestCase):
    def setUp(self):
        # noinspection PyTypeChecker
        self.sync = SyncEngine(DropboxClient("test-config"), None)
        self.sync.dropbox_path = "/"

    def tearDown(self):
        remove_configuration("test-config")

    def test_single_file_events(self):

        # only a single event for every path -> no consolidation

        file_events = [
            FileModifiedEvent(ipath(1)),
            FileCreatedEvent(ipath(2)),
            FileDeletedEvent(ipath(3)),
            FileMovedEvent(ipath(4), ipath(5)),
        ]

        res = [
            FileModifiedEvent(ipath(1)),
            FileCreatedEvent(ipath(2)),
            FileDeletedEvent(ipath(3)),
            FileMovedEvent(ipath(4), ipath(5)),
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_single_path_cases(self):

        file_events = [
            # created + deleted -> None
            FileCreatedEvent(ipath(1)),
            FileDeletedEvent(ipath(1)),
            # deleted + created -> modified
            FileDeletedEvent(ipath(2)),
            FileCreatedEvent(ipath(2)),
            # created + modified -> created
            FileCreatedEvent(ipath(3)),
            FileModifiedEvent(ipath(3)),
        ]

        res = [
            # created + deleted -> None
            # deleted + created -> modified
            FileModifiedEvent(ipath(2)),
            # created + modified -> created
            FileCreatedEvent(ipath(3)),
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_move_events(self):

        file_events = [
            # created + moved -> created
            FileCreatedEvent(ipath(1)),
            FileMovedEvent(ipath(1), ipath(2)),
            # moved + deleted -> deleted
            FileMovedEvent(ipath(1), ipath(4)),
            FileDeletedEvent(ipath(4)),
            # moved + moved back -> modified
            FileMovedEvent(ipath(5), ipath(6)),
            FileMovedEvent(ipath(6), ipath(5)),
            # moved + moved -> deleted + created
            # (this is currently not handled as a single moved)
            FileMovedEvent(ipath(7), ipath(8)),
            FileMovedEvent(ipath(8), ipath(9)),
        ]

        res = [
            # created + moved -> created
            FileCreatedEvent(ipath(2)),
            # moved + deleted -> deleted
            FileDeletedEvent(ipath(1)),
            # moved + moved back -> modified
            FileModifiedEvent(ipath(5)),
            # moved + moved -> deleted + created
            # (this is currently not handled as a single moved)
            FileDeletedEvent(ipath(7)),
            FileCreatedEvent(ipath(9)),
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_gedit_save(self):

        file_events = [
            FileCreatedEvent(".gedit-save-UR4EC0"),  # save new version to tmp file
            FileModifiedEvent(".gedit-save-UR4EC0"),  # modify tmp file
            FileMovedEvent(ipath(1), ipath(1) + "~"),  # move old version to backup
            FileMovedEvent(
                ".gedit-save-UR4EC0", ipath(1)
            ),  # replace old version with tmp
        ]

        res = [
            FileModifiedEvent(ipath(1)),  # modified file
            FileCreatedEvent(ipath(1) + "~"),  # backup
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_macos_safe_save(self):

        file_events = [
            FileMovedEvent(
                ipath(1), ipath(1) + ".sb-b78ef837-dLht38"
            ),  # move to backup
            FileCreatedEvent(ipath(1)),  # create new version
            FileDeletedEvent(ipath(1) + ".sb-b78ef837-dLht38"),  # delete backup
        ]

        res = [
            FileModifiedEvent(ipath(1)),  # modified file
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_msoffice_created(self):

        file_events = [
            FileCreatedEvent(ipath(1)),
            FileDeletedEvent(ipath(1)),
            FileCreatedEvent(ipath(1)),
            FileCreatedEvent("~$" + ipath(1)),
        ]

        res = [
            FileCreatedEvent(ipath(1)),  # created file
            FileCreatedEvent("~$" + ipath(1)),  # backup
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_type_changes(self):

        file_events = [
            # keep as is
            FileDeletedEvent(ipath(1)),
            DirCreatedEvent(ipath(1)),
            # keep as is
            DirDeletedEvent(ipath(2)),
            FileCreatedEvent(ipath(2)),
        ]

        res = [
            # keep as is
            FileDeletedEvent(ipath(1)),
            DirCreatedEvent(ipath(1)),
            # keep as is
            DirDeletedEvent(ipath(2)),
            FileCreatedEvent(ipath(2)),
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_type_changes_difficult(self):

        file_events = [
            # convert to FileDeleted -> DirCreated
            FileModifiedEvent(ipath(1)),
            FileDeletedEvent(ipath(1)),
            FileCreatedEvent(ipath(1)),
            FileDeletedEvent(ipath(1)),
            DirCreatedEvent(ipath(1)),
            # convert to FileDeleted(path1) -> DirCreated(path2)
            FileModifiedEvent(ipath(2)),
            FileDeletedEvent(ipath(2)),
            FileCreatedEvent(ipath(2)),
            FileDeletedEvent(ipath(2)),
            DirCreatedEvent(ipath(2)),
            DirMovedEvent(ipath(2), ipath(3)),
        ]

        res = [
            FileDeletedEvent(ipath(1)),
            DirCreatedEvent(ipath(1)),
            FileDeletedEvent(ipath(2)),
            DirCreatedEvent(ipath(3)),
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_nested_events(self):

        file_events = [
            # convert to a single DirDeleted
            DirDeletedEvent(ipath(1)),
            FileDeletedEvent(ipath(1) + "/file1.txt"),
            FileDeletedEvent(ipath(1) + "/file2.txt"),
            DirDeletedEvent(ipath(1) + "/sub"),
            FileDeletedEvent(ipath(1) + "/sub/file3.txt"),
            # convert to a single DirMoved
            DirMovedEvent(ipath(2), ipath(3)),
            FileMovedEvent(ipath(2) + "/file1.txt", ipath(3) + "/file1.txt"),
            FileMovedEvent(ipath(2) + "/file2.txt", ipath(3) + "/file2.txt"),
            DirMovedEvent(ipath(2) + "/sub", ipath(3) + "/sub"),
            FileMovedEvent(ipath(2) + "/sub/file3.txt", ipath(3) + "/sub/file3.txt"),
        ]

        res = [
            DirDeletedEvent(ipath(1)),
            DirMovedEvent(ipath(2), ipath(3)),
        ]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

    def test_performance(self):

        # 10,000 nested deleted events (5,000 folders, 5,000 files)
        file_events = [DirDeletedEvent(n * ipath(1)) for n in range(1, 5001)]
        file_events += [FileDeletedEvent(n * ipath(1) + ".txt") for n in range(1, 5001)]

        # 10,000 nested moved events (5,000 folders, 5,000 files)
        file_events += [
            DirMovedEvent(n * ipath(2), n * ipath(3)) for n in range(1, 5001)
        ]
        file_events += [
            FileMovedEvent(n * ipath(2) + ".txt", n * ipath(3) + ".txt")
            for n in range(1, 5001)
        ]

        # 4,995 unrelated created events
        file_events += [FileCreatedEvent(ipath(n)) for n in range(5, 5001)]

        res = [
            DirDeletedEvent(ipath(1)),
            DirMovedEvent(ipath(2), ipath(3)),
            FileDeletedEvent(ipath(1) + ".txt"),
            FileMovedEvent(ipath(2) + ".txt", ipath(3) + ".txt"),
        ]
        res += [FileCreatedEvent(ipath(n)) for n in range(5, 5001)]

        cleaned_events = self.sync._clean_local_events(file_events)
        self.assertEqual(set(cleaned_events), set(res))

        n_loops = 4
        duration = timeit.timeit(
            lambda: self.sync._clean_local_events(file_events), number=n_loops
        )

        self.assertLess(duration, 10 * n_loops)


class TestIgnoreLocalEvents(TestCase):
    def setUp(self):

        syncing = Event()
        startup = Event()
        syncing.set()

        local_dir = osp.join(get_home_dir(), "dummy_dir")
        os.mkdir(local_dir)

        self.sync = SyncEngine(
            DropboxClient("test-config"), FSEventHandler(syncing, startup)
        )

        self.sync.dropbox_path = local_dir

        self.observer = Observer()
        self.observer.schedule(
            self.sync.fs_events, self.sync.dropbox_path, recursive=True
        )
        self.observer.start()

    def tearDown(self):

        self.observer.stop()
        self.observer.join()

        remove_configuration("test-config")
        delete(self.sync.dropbox_path)

    def test_receiving_events(self):

        new_dir = Path(self.sync.dropbox_path, "parent")
        new_dir.mkdir()

        sync_events, local_cursor = self.sync.wait_for_local_changes()

        self.assertEqual(len(sync_events), 1)

        try:
            ctime = os.stat(new_dir).st_birthtime
        except AttributeError:
            ctime = None

        event = sync_events[0]
        self.assertEqual(event.direction, SyncDirection.Up)
        self.assertEqual(event.item_type, ItemType.Folder)
        self.assertEqual(event.change_type, ChangeType.Added)
        self.assertEqual(event.change_time, ctime)
        self.assertEqual(event.local_path, str(new_dir))

    def test_ignore_tree_creation(self):

        new_dir = Path(self.sync.dropbox_path, "parent")

        with self.sync.fs_events.ignore(DirCreatedEvent(str(new_dir))):
            new_dir.mkdir()
            for i in range(10):
                file = new_dir / f"test_{i}"
                file.touch()

        sync_events, local_cursor = self.sync.wait_for_local_changes()
        self.assertEqual(len(sync_events), 0)

    def test_ignore_tree_move(self):

        new_dir = Path(self.sync.dropbox_path, "parent")

        new_dir.mkdir()
        for i in range(10):
            file = new_dir / f"test_{i}"
            file.touch()

        self.sync.wait_for_local_changes()

        new_dir_1 = Path(self.sync.dropbox_path, "parent2")

        with self.sync.fs_events.ignore(DirMovedEvent(str(new_dir), str(new_dir_1))):
            move(new_dir, new_dir_1)

        sync_events, local_cursor = self.sync.wait_for_local_changes()
        self.assertEqual(len(sync_events), 0)

    def test_catching_non_ignored_events(self):

        new_dir = Path(self.sync.dropbox_path, "parent")

        with self.sync.fs_events.ignore(DirCreatedEvent(str(new_dir)), recursive=False):
            new_dir.mkdir()
            for i in range(10):
                # may trigger FileCreatedEvent and FileModifiedVent
                file = new_dir / f"test_{i}"
                file.touch()

        sync_events, local_cursor = self.sync.wait_for_local_changes()
        self.assertTrue(all(not si.is_directory for si in sync_events))
