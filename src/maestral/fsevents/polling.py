# -*- coding: utf-8 -*-
"""
Looking at the source code for :class:`watchdog.utils.dirsnapshot.DirectorySnapshotDiff`,
the event types are categorised as follows:

* Created event: The inode is unique to the new snapshot. The path may be unique to the
  new snapshot or exist in both. In the second case, there will be a preceding Deleted
  event or a Moved event with the path as starting point (the old item was deleted or
  moved away).

* Deleted event: The inode is unique to the old snapshot. The path may be unique to the
  old snapshot or exist in both. In the second case, there will be a subsequent Created
  event or a Moved event with the path as end point (something else was created at or
  moved to the location).

* Moved event: The inode exists in both snapshots but with different paths.

* Modified event: The inode exists in both snapshots and the mtime or file size are
  different. DirectorySnapshotDiff will always use the inode’s path from the old
  snapshot.

From the above classification, there can be at most two created/deleted/moved events
that share the same path in one snapshot diff:

    * Deleted(path1)      + Created(path1)
    * Moved(path1, path2) + Created(path1)
    * Deleted(path1)      + Moved(path0, path1)

Any Modified event will come before a Moved event or stand alone. Modified events will
never be combined by themselves with created or deleted events because they require the
inode to be present in both snapshots.

From the above, we can achieve correct ordering for unique path by always adding Deleted
events to the queue first, Modified events second, Moved events third and Created events
last:

    Deleted -> Modified -> Moved -> Created

The ordering won’t be correct between unrelated paths and between files and folder. The
first does not matter for syncing. We solve the second by assuming that when a directory
is deleted, so are its children. And before a child is created, its parent directory
must exist.

MovedEvents which are not unique (their paths appear in other events) will be split
into Deleted and Created events by Maestral.
"""

# Copyright 2011 Yesudeep Mangalapilly <yesudeep@gmail.com>
# Copyright 2012 Google, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from watchdog.observers.polling import (  # type: ignore
    PollingEmitter,
    PollingObserver,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    DirCreatedEvent,
    DEFAULT_OBSERVER_TIMEOUT,
    BaseObserver,
)
from watchdog.utils.dirsnapshot import DirectorySnapshotDiff


class OrderedPollingEmitter(PollingEmitter):
    """Ordered polling file system event emitter

    Platform-independent emitter that polls a directory to detect file system changes.
    Events are emitted in an order which can be used to produce the new file system
    state from the old one.
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
        BaseObserver.__init__(
            self, emitter_class=OrderedPollingEmitter, timeout=timeout
        )
