# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
from watchdog.observers.polling import (
    PollingEmitter, PollingObserver, DirectorySnapshotDiff,
    FileDeletedEvent, FileModifiedEvent, FileMovedEvent, FileCreatedEvent,
    DirDeletedEvent, DirModifiedEvent, DirMovedEvent, DirCreatedEvent,
    DEFAULT_OBSERVER_TIMEOUT, BaseObserver
)


class OrderedPollingEmitter(PollingEmitter):
    """
    Platform-independent emitter that polls a directory to detect file
    system changes.
    """

    def queue_events(self, timeout):
        # We don't want to hit the disk continuously.
        # timeout behaves like an interval for polling emitters.
        if self.stopped_event.wait(timeout):
            return

        with self._lock:
            if not self.should_keep_running():
                return

            # Get event diff between fresh snapshot and previous snapshot.
            # Update snapshot.
            try:
                new_snapshot = self._take_snapshot()
            except OSError:
                self.queue_event(DirDeletedEvent(self.watch.path))
                self.stop()
                return

            events = DirectorySnapshotDiff(self._snapshot, new_snapshot)
            self._snapshot = new_snapshot

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


class OrderedPollingObserver(PollingObserver):

    def __init__(self, timeout=DEFAULT_OBSERVER_TIMEOUT):
        BaseObserver.__init__(self, emitter_class=OrderedPollingEmitter, timeout=timeout)
