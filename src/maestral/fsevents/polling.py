#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
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
