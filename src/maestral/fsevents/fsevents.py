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
from watchdog.utils.dirsnapshot import DirectorySnapshot


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
            diff = new_snapshot - self.snapshot

            # add metadata modified events which will be missed by regular diff
            try:
                ctime_files_modified = set()

                for path in self.snapshot.paths & new_snapshot.paths:
                    if not self.snapshot.isdir(path):
                        if self.snapshot.inode(path) == new_snapshot.inode(path):
                            if (
                                self.snapshot.stat_info(path).st_ctime
                                != new_snapshot.stat_info(path).st_ctime
                            ):
                                ctime_files_modified.add(path)

                files_modified = set(ctime_files_modified) | set(diff.files_modified)
            except Exception as exc:
                print(exc)

            # replace cached snapshot
            self.snapshot = new_snapshot

            # Files.
            for src_path in diff.files_deleted:
                self.queue_event(FileDeletedEvent(src_path))
            for src_path in files_modified:
                self.queue_event(FileModifiedEvent(src_path))
            for src_path, dest_path in diff.files_moved:
                self.queue_event(FileMovedEvent(src_path, dest_path))
            for src_path in diff.files_created:
                self.queue_event(FileCreatedEvent(src_path))

            # Directories.
            for src_path in diff.dirs_deleted:
                self.queue_event(DirDeletedEvent(src_path))
            for src_path in diff.dirs_modified:
                self.queue_event(DirModifiedEvent(src_path))
            for src_path, dest_path in diff.dirs_moved:
                self.queue_event(DirMovedEvent(src_path, dest_path))
            for src_path in diff.dirs_created:
                self.queue_event(DirCreatedEvent(src_path))

            # free some memory
            del diff
            del files_modified


class OrderedFSEventsObserver(FSEventsObserver):
    def __init__(self, timeout=DEFAULT_OBSERVER_TIMEOUT):
        BaseObserver.__init__(
            self, emitter_class=OrderedFSEventsEmitter, timeout=timeout
        )
