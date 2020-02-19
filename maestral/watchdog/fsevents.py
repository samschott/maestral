from watchdog.observers.fsevents import (
    FSEventsEmitter, FSEventsObserver, DirectorySnapshot,
    FileDeletedEvent, FileModifiedEvent, FileMovedEvent, FileCreatedEvent,
    DirDeletedEvent, DirModifiedEvent, DirMovedEvent, DirCreatedEvent,
    DEFAULT_OBSERVER_TIMEOUT, BaseObserver
)


class OrderedFSEventsEmitter(FSEventsEmitter):

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
