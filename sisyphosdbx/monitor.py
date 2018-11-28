import os.path as osp
import logging
import time
import threading
from queue import Queue
import dropbox

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from watchdog.events import (EVENT_TYPE_CREATED, EVENT_TYPE_DELETED,
                             EVENT_TYPE_MODIFIED, EVENT_TYPE_MOVED)
from watchdog.events import (DirModifiedEvent, FileModifiedEvent,
                             DirCreatedEvent, FileCreatedEvent,
                             DirDeletedEvent, FileDeletedEvent)
from watchdog.utils.dirsnapshot import DirectorySnapshot

from sisyphosdbx.client import SESSION
from sisyphosdbx.config.main import CONF, SUBFOLDER
from sisyphosdbx.config.base import get_conf_path

configurationDirectory = get_conf_path(SUBFOLDER)

logger = logging.getLogger(__name__)
lock = threading.Lock()


class TimedQueue(Queue):

    def __init__(self):
        super(self.__class__, self).__init__()

        self.update_time = 0

    # Put a new item in the queue, remember time
    def _put(self, item):
        self.queue.append(item)
        self.update_time = time.time()


class FileEventHandler(FileSystemEventHandler):
    """
    Logs captured file events and adds them to :ivar:`event_q`.

    :ivar event_q: Qeueue with unprocessed local file events.
    """

    event_q = TimedQueue()

    def on_moved(self, event):
        logger.debug("Move detected: from '%s' to '%s'", event.src_path, event.dest_path)
        self.event_q.put(event)

    def on_created(self, event):
        logger.debug("Creation detected: '%s'", event.src_path)
        self.event_q.put(event)

    def on_deleted(self, event):
        logger.debug("Deletion detected: '%s'", event.src_path)
        self.event_q.put(event)

    def on_modified(self, event):
        logger.debug("Modification detected: '%s'", event.src_path)
        self.event_q.put(event)


class DropboxEventHandler(object):
    """
    Class that contains methods to sync local file events with Dropbox.
    The 'last_sync' entry in the config file is updated with the current time
    after every successfull sync. 'last_sync' is used to check for unsynced
    changes when SisyphosDBX is started or resumed.
    """

    def __init__(self, client):

        self.client = client

    def on_moved(self, event):
        """
        Call when local file is moved.

        :param class event: Watchdog file event.
        """

        path = event.src_path
        path2 = event.dest_path

        dbx_path = self.client.to_dbx_path(path)
        dbx_path2 = self.client.to_dbx_path(path2)

        # is file excluded?
        if self.client.is_excluded(dbx_path2):
            return

        # If the file name contains multiple periods it is likely a temporary
        # file created during a saving event on macOS. Irgnore such files.
        if osp.basename(path2).count(".") > 1:
            return

        self.client.move(dbx_path, dbx_path2)

        CONF.set("internal", "lastsync", time.time())

    def on_created(self, event):
        """
        Call when local file is created.

        :param class event: Watchdog file event.
        """
        path = event.src_path
        dbx_path = self.client.to_dbx_path(path)

        # is file excluded?
        if self.client.is_excluded(dbx_path):
            return

        # has event just been triggere by remote_monitor?
        if dbx_path in self.client.flagged:
            logging.info("'%s' has just been synced. Nothing to do.", dbx_path)
            self.client.flagged.remove(dbx_path)
            return

        if not event.is_directory:

            if osp.isfile(path):
                while True:  # wait until file is fully created
                    size1 = osp.getsize(path)
                    time.sleep(0.5)
                    size2 = osp.getsize(path)
                    if size1 == size2:
                        break

                rev = self.client.get_local_rev(dbx_path)
                # if truly a new file
                if rev is None:
                    mode = dropbox.files.WriteMode("add")
                # or a 'false' new file event triggered by saving the file
                # e.g., some programms create backup files and then swap them
                # in to replace the files you are editing on the disk
                else:
                    mode = dropbox.files.WriteMode("update", rev)
                self.client.upload(path, dbx_path, autorename=True, mode=mode)

        elif event.is_directory:
            result = self.client.list_folder(dbx_path)
            if result is not None:
                # directory is already on Dropbox
                return
            else:
                self.client.make_dir(dbx_path)

        CONF.set("internal", "lastsync", time.time())

    def on_deleted(self, event):
        """
        Call when local file is deleted.

        :param class event: Watchdog file event.
        """
        path = event.src_path
        dbx_path = self.client.to_dbx_path(path)

        # is file excluded?
        if self.client.is_excluded(dbx_path):
            return

        # has event just been triggere by remote_monitor?
        if dbx_path in self.client.flagged:
            self.client.flagged.remove(dbx_path)
            return

        rev = self.client.get_local_rev(dbx_path)
        if rev is not None:
            self.client.remove(dbx_path)

        CONF.set("internal", "lastsync", time.time())

    def on_modified(self, event):
        """
        Call when local file is modified.

        :param class event: Watchdog file event.
        """
        path = event.src_path
        dbx_path = self.client.to_dbx_path(path)

        # is file excluded?
        if self.client.is_excluded(dbx_path):
            return

        # has event just been triggere by remote_monitor?
        if dbx_path in self.client.flagged:
            self.client.flagged.remove(dbx_path)
            return

        if not event.is_directory:  # ignore directory modified events
            if osp.isfile(path):

                while True:  # wait until file is fully created
                    size1 = osp.getsize(path)
                    time.sleep(0.2)
                    size2 = osp.getsize(path)
                    if size1 == size2:
                        break

                rev = self.client.get_local_rev(dbx_path)
                mode = dropbox.files.WriteMode("update", rev)
                md = self.client.upload(path, dbx_path, autorename=True, mode=mode)
                logger.debug("Modified file: %s (old rev: %s, new rev %s)",
                             md.path_display, rev, md.rev)

        CONF.set("internal", "lastsync", time.time())


class GetRemoteChangesThread(threading.Thread):
    """
    Thread to sync changes of remote Dropbox with local folder.

    :ivar pause_event: If `pause_event.is_set()` all syncing is paused.
    :ivar stop_event: If `stop_event.is_set()`, the thread is stopped.
    """

    pause_event = threading.Event()
    stop_event = threading.Event()

    def __init__(self, client):
        super(self.__class__, self).__init__()
        self.client = client

    def run(self):
        while not self.stop_event.is_set():

            while self.pause_event.is_set():
                time.sleep(1)

            try:
                changes = self.client.wait_for_remote_changes()
                if changes:
                    logger.info("Syncing remote changes")
                    with lock:
                        self.client.get_remote_changes()
                    logger.info("Up to date")
                else:
                    logger.info("Up to date")

            except ConnectionError:  # TODO: determine correct exc to catch
                logger.debug("Connection lost")
                logger.info("Connecting...")  # TODO: handle lost connection
                # block until reconnect

    def pause(self):
        self.pause_event.set()

    def resume(self):
        self.pause_event.clear()

    def stop(self):
        self.stop_event.set()


class ProcessLocalChangesThread(threading.Thread):
    """
    Thread to sync local changes to remote Dropbox.

    :ivar pause_event: If `pause_event.is_set()` all syncing is paused.
    :ivar stop_event: If `stop_event.is_set()`, the thread is stopped.
    :ivar delay: Delay time to collect local changes and merge moved avents as
        appropriate.
    :ivar event_q: Queue containing all local file events to sync.
    """
    pause_event = threading.Event()
    stop_event = threading.Event()

    def __init__(self, dbx_handler, event_q):
        super(self.__class__, self).__init__()
        self.dbx_handler = dbx_handler
        self.event_q = event_q
        self.delay = 0.5

    def run(self):
        while not self.stop_event.is_set():
            # pause if instructed
            while self.pause_event.is_set():
                time.sleep(1)

            events = []

            events.append(self.event_q.get())  # blocks until something is in queue

            # wait for self.delay after last event has been registered
            t0 = time.time()
            while t0 - self.event_q.update_time < self.delay:
                time.sleep(self.delay)
                t0 = time.time()

            # get all events after folder has been idle for self.delay
            events = []
            while self.event_q.qsize() > 0:
                events.append(self.event_q.get())

            # check for folder move events
            def is_moved_folder(x):
                is_moved_event = (x.event_type is EVENT_TYPE_MOVED)
                return is_moved_event and x.is_directory

            moved_fodler_events = [x for x in events if is_moved_folder(x)]

            # check for children of moved folders
            def is_moved_child(x, parent_event):
                is_moved_event = (x.event_type is EVENT_TYPE_MOVED)
                is_child = (x.src_path.startswith(parent_event.src_path) and
                            x is not parent_event)
                return is_moved_event and is_child

            child_move_events = []
            for parent_event in moved_fodler_events:
                children = [x for x in events if is_moved_child(x, parent_event)]
                child_move_events += children

            # Remove all child_move_events from events, move the full folder at
            # once on Dropbox instead.
            events = set(events) - set(child_move_events)

            # process all events:
            try:
                with lock:
                    logger.info("Syncing local changes")
                    for event in events:
                        if event.event_type is EVENT_TYPE_CREATED:
                            self.dbx_handler.on_created(event)
                        elif event.event_type is EVENT_TYPE_MOVED:
                            self.dbx_handler.on_moved(event)
                        elif event.event_type is EVENT_TYPE_DELETED:
                            self.dbx_handler.on_deleted(event)
                        elif event.event_type is EVENT_TYPE_MODIFIED:
                            self.dbx_handler.on_modified(event)
                    logger.info("Up to date")
            except ConnectionError:  # TODO: determine correct exc to catch
                logger.debug("Connection lost")
                logger.info("Connecting...")
                # TODO: handle lost connection
                # block until reconnect
                # upon reconnect, call upload_local_changes_after_inactive

    def pause(self):
        self.pause_event.set()

    def resume(self):
        self.pause_event.clear()

    def stop(self):
        self.stop_event.set()


class RemoteMonitor(object):
    """
    Class to sync changes of remote Dropbox with local folder.

    :ivar thread: Thread to query and process remote changes.
    """
    def __init__(self, client):

        self.client = client

        self.thread = GetRemoteChangesThread(self.client)
        self.thread.pause()
        self.thread.start()

    def start(self):
        """Starts observation of remote Dropbox folder."""
        self.thread.resume()

    def stop(self):
        """Stops observation of remote Dropbox folder."""
        self.thread.pause()

    def __del__(self):
        try:
            self.thread.stop()
        except AttributeError:
            pass


class LocalMonitor(object):
    """
    Class to sync local changes toDropbox folder to remote Dropbox.

    :ivar observer: Watchdog obersver thread that detects local file system
        events and calls `file_handler`.
    :ivar file_handler: Handler to register file events and put in queue.
    :ivar dbx_handler: Handler to upload changes to Dropbox.
    :ivar thread: Thread that calls `dbx_handler` methods when file events have
        been queued.
    """
    def __init__(self, client):

        self.client = client

        self.file_handler = FileEventHandler()

        self.dbx_handler = DropboxEventHandler(self.client)
        self.thread = ProcessLocalChangesThread(self.dbx_handler, self.file_handler.event_q)
        self.thread.pause()
        self.thread.start()

    def start(self):
        """Start file system observer and Dropbox event handler."""
        self.observer = Observer()
        self.observer.schedule(self.file_handler, self.client.dropbox_path, recursive=True)
        self.observer.start()

        self.thread.resume()

    def stop(self):
        """Stop file system observer and Dropbox event handler."""
        self.observer.stop()
        self.observer.join()
        self.thread.pause()

    def get_local_changes(self):
        """
        Gets all local changes while app has not been running. Call this method
        on startup of `LocalMonitor` to upload all local changes.

        :return: Dictionary with all changes, keys are file paths relative to
            local Dropbox folder, entries are watchdog file changed events.
        :rtype: dict
        """
        changes = []
        snapshot = DirectorySnapshot(self.client.dropbox_path)
        # remove root entry from snapshot
        del snapshot._inode_to_path[snapshot.inode(self.client.dropbox_path)]
        del snapshot._stat_info[self.client.dropbox_path]
        # get lowercase paths
        lowercase_snapshot_paths = {x.lower() for x in snapshot.paths}

        # get paths of modified or added files / folders
        for path in snapshot.paths:
            if snapshot.mtime(path) > CONF.get('internal', 'lastsync'):
                # check if file/folder is already tracked or new
                if self.client.to_dbx_path(path).lower() in self.client.rev_dict:
                    # already tracking file
                    if osp.isdir(path):
                        event = DirModifiedEvent(path)
                    else:
                        event = FileModifiedEvent(path)
                    changes.append(event)
                else:
                    # new file, not excluded
                    if osp.isdir(path):
                        event = DirCreatedEvent(path)
                    else:
                        event = FileCreatedEvent(path)
                    changes.append(event)

        # get deleted files / folders
        for path in self.client.rev_dict:
            if self.client.to_local_path(path).lower() not in lowercase_snapshot_paths:
                if self.client.rev_dict[path] == 'folder':
                    event = DirDeletedEvent(self.client.to_local_path(path))
                else:
                    event = FileDeletedEvent(self.client.to_local_path(path))
                changes.append(event)

        return changes

    def upload_local_changes_after_inactive(self):
        """Push changes while client has not been running to Dropbox."""

        events = self.get_local_changes()

        logging.info("Uploading local changes.")

        for event in events:
            if event.event_type is EVENT_TYPE_CREATED:
                self.dbx_handler.on_created(event)
            elif event.event_type is EVENT_TYPE_DELETED:
                self.dbx_handler.on_deleted(event)
            elif event.event_type is EVENT_TYPE_MODIFIED:
                self.dbx_handler.on_modified(event)

    def __del__(self):
        try:
            self.observer.stop()
        except AttributeError:
            pass

        try:
            self.observer.join()
        except AttributeError:
            pass

        try:
            self.thread.stop()
        except AttributeError:
            pass
