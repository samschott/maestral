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

from watchdog.observers.fsevents import (  # type: ignore
    FSEventsEmitter,
    FSEventsObserver,
    DirectorySnapshot,
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


class OrderedFSEventsEmitter(FSEventsEmitter):
    """Ordered file system event emitter for macOS

    This subclasses FSEventsEmitter to guarantee an order of events which can be applied
    to reproduce the new state from the old state.
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
        BaseObserver.__init__(
            self, emitter_class=OrderedFSEventsEmitter, timeout=timeout
        )
