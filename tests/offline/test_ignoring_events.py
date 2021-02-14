# -*- coding: utf-8 -*-

import os
from pathlib import Path

from watchdog.events import DirCreatedEvent, DirMovedEvent

from maestral.sync import SyncDirection, ItemType, ChangeType
from maestral.utils.path import move


def ipath(i):
    """Returns path names '/test 1', '/test 2', ... """
    return f"/test {i}"


def test_receiving_events(sync):

    new_dir = Path(sync.dropbox_path) / "parent"
    new_dir.mkdir()

    sync.wait_for_local_changes()
    sync_events, _ = sync.list_local_changes()

    assert len(sync_events) == 1

    try:
        ctime = os.stat(new_dir).st_birthtime
    except AttributeError:
        ctime = None

    event = sync_events[0]
    assert event.direction == SyncDirection.Up
    assert event.item_type == ItemType.Folder
    assert event.change_type == ChangeType.Added
    assert event.change_time == ctime
    assert event.local_path == str(new_dir)


def test_ignore_tree_creation(sync):

    new_dir = Path(sync.dropbox_path) / "parent"

    with sync.fs_events.ignore(DirCreatedEvent(str(new_dir))):
        new_dir.mkdir()
        for i in range(10):
            file = new_dir / f"test_{i}"
            file.touch()

    sync.wait_for_local_changes()
    sync_events, _ = sync.list_local_changes()
    assert len(sync_events) == 0


def test_ignore_tree_move(sync):

    new_dir = Path(sync.dropbox_path) / "parent"

    new_dir.mkdir()
    for i in range(10):
        file = new_dir / f"test_{i}"
        file.touch()

    sync.wait_for_local_changes()
    sync.list_local_changes()

    new_dir_1 = Path(sync.dropbox_path) / "parent2"

    with sync.fs_events.ignore(DirMovedEvent(str(new_dir), str(new_dir_1))):
        move(new_dir, new_dir_1)

    sync.wait_for_local_changes()
    sync_events, _ = sync.list_local_changes()
    assert len(sync_events) == 0


def test_catching_non_ignored_events(sync):

    new_dir = Path(sync.dropbox_path) / "parent"

    with sync.fs_events.ignore(DirCreatedEvent(str(new_dir)), recursive=False):
        new_dir.mkdir()
        for i in range(10):
            # may trigger FileCreatedEvent and FileModifiedVent
            file = new_dir / f"test_{i}"
            file.touch()

    sync.wait_for_local_changes()
    sync_events, _ = sync.list_local_changes()
    assert all(not si.is_directory for si in sync_events)
