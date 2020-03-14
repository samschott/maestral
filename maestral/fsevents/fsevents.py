# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
from watchdog.observers.fsevents import (
    FSEventsEmitter, FSEventsObserver, DirectorySnapshot,
    FileDeletedEvent, FileModifiedEvent, FileMovedEvent, FileCreatedEvent,
    DirDeletedEvent, DirModifiedEvent, DirMovedEvent, DirCreatedEvent,
    DEFAULT_OBSERVER_TIMEOUT, BaseObserver
)


class OrderedFSEventsEmitter(FSEventsEmitter):
    """
    This subclasses FSEventsEmitter to guarantee an order of events which can be applied
    to reproduce the new state from the old state.

    Looking at the source code for DirectorySnapshotDiff, the event types are categorised
    as follows:

    Created event: The inode is unique to the new snapshot. The path may be unique to the
    new snapshot or exist in both. In the second case, there will be a preceding Deleted
    event or a Moved event with the path as starting point (the old item was deleted or
    moved away).

    Deleted event: The inode is unique to the old snapshot. The path may be unique to the
    old snapshot or exist in both. In the second case, there will be a subsequent Created
    event or a Moved event with the path as end point (something else was created at or
    moved to the location).

    Moved event: The inode exists in both snapshots but with different paths.

    Modified event: The inode exists in both snapshots and the mtime or file size are
    different. DirectorySnapshotDiff will always use the inode’s path from the old
    snapshot.

    From the above classification, there can be at most two created/deleted/moved events
    that share the same path in one snapshot diff:

        Deleted(path1)      + Created(path1)
        Moved(path1, path2) + Created(path1)
        Deleted(path1)      + Moved(path0, path1)

    And any Modified event will come before a Moved event or stand alone. Modified events
    will never be combined by themselves with created or deleted events because they
    require the inode to be present in both snapshots.

    From the above, we could achieve correct ordering for every path by always adding
    Deleted events to the queue first, Modified events second, Moved events third and
    Created events last:

        Deleted -> Modified -> Moved -> Created

    The ordering won’t be correct between unrelated paths and between files and folder.
    The first does not matter for syncing. We solve the second by assuming that when a
    directory is deleted, so are its children. And before a child is created, there must
    be a directory.

    MovedEvents which are not unique (their paths appear in other events) will be split
    into Deleted and Created events by Maestral.
    """

    def queue_events(self, timeout):
        with self._lock:
            if not self.watch.is_recursive and self.watch.path not in self.pathnames:
                return
            new_snapshot = DirectorySnapshot(self.watch.path, self.watch.is_recursive)
            events = new_snapshot - self.snapshot
            self.snapshot = new_snapshot

            # Files.
            for src_path in events.files_deleted:
                self.queue_event(FileDeletedEvent(src_path))
            for src_path in events.files_modified:
                self.queue_event(FileModifiedEvent(src_path))
            for src_path, dest_path in events.files_moved:
                self.queue_event(FileMovedEvent(src_path, dest_path))
            for src_path in events.files_created:
                self.queue_event(FileCreatedEvent(src_path))

            # Directories.
            for src_path in events.dirs_deleted:
                self.queue_event(DirDeletedEvent(src_path))
            for src_path in events.dirs_modified:
                self.queue_event(DirModifiedEvent(src_path))
            for src_path, dest_path in events.dirs_moved:
                self.queue_event(DirMovedEvent(src_path, dest_path))
            for src_path in events.dirs_created:
                self.queue_event(DirCreatedEvent(src_path))


class OrderedFSEventsObserver(FSEventsObserver):

    def __init__(self, timeout=DEFAULT_OBSERVER_TIMEOUT):
        BaseObserver.__init__(self, emitter_class=OrderedFSEventsEmitter, timeout=timeout)
