

from watchdog.observers.fsevents import *
from watchdog.events import EVENT_TYPE_DELETED, EVENT_TYPE_MOVED


class OrderedFSFSEventsEmitter(FSEventsEmitter):

    def queue_events(self, timeout):
        with self._lock:
            if not self.watch.is_recursive and self.watch.path not in self.pathnames:
                return
            new_snapshot = DirectorySnapshot(self.watch.path, self.watch.is_recursive)
            events = new_snapshot - self.snapshot

            _all_changes = []

            # Files.
            for src_path in events.files_deleted:
                _all_changes.append(FileDeletedEvent(src_path))
            for src_path in events.files_modified:
                _all_changes.append(FileModifiedEvent(src_path))
            for src_path in events.files_created:
                _all_changes.append(FileCreatedEvent(src_path))
            for src_path, dest_path in events.files_moved:
                _all_changes.append(FileMovedEvent(src_path, dest_path))

            # Directories.
            for src_path in events.dirs_deleted:
                _all_changes.append(DirDeletedEvent(src_path))
            for src_path in events.dirs_modified:
                _all_changes.append(DirModifiedEvent(src_path))
            for src_path in events.dirs_created:
                _all_changes.append(DirCreatedEvent(src_path))
            for src_path, dest_path in events.dirs_moved:
                _all_changes.append(DirMovedEvent(src_path, dest_path))

            # sort according to mtime
            _all_changes.sort(key=lambda e: self._get_mtime(e, self.snapshot, new_snapshot))

            for event in _all_changes:
                self.queue_event(event)

            self.snapshot = new_snapshot

    @staticmethod
    def _get_mtime(event, old_snapshot, new_snapshot):
        if event.event_type in (EVENT_TYPE_MOVED, EVENT_TYPE_DELETED):
            # use old snapshot for mtime
            mtime = old_snapshot.mtime(event.src_path)
        else:
            # use new snapshot for mtime
            mtime = new_snapshot.mtime(event.src_path)

        return mtime


class OrderedFSEventsObserver(FSEventsObserver):

    def __init__(self, timeout=DEFAULT_OBSERVER_TIMEOUT):
        BaseObserver.__init__(self, emitter_class=OrderedFSFSEventsEmitter, timeout=timeout)
