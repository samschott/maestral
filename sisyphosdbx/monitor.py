import os.path as osp
import logging
import time
from threading import Thread, Event, Lock
import requests
import queue
from blinker import signal
import dropbox

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from watchdog.events import (EVENT_TYPE_CREATED, EVENT_TYPE_DELETED,
                             EVENT_TYPE_MODIFIED, EVENT_TYPE_MOVED)
from watchdog.events import (DirModifiedEvent, FileModifiedEvent,
                             DirCreatedEvent, FileCreatedEvent,
                             DirDeletedEvent, FileDeletedEvent)
from watchdog.utils.dirsnapshot import DirectorySnapshot

from sisyphosdbx.config.main import CONF, SUBFOLDER
from sisyphosdbx.config.base import get_conf_path

configurationDirectory = get_conf_path(SUBFOLDER)

logger = logging.getLogger(__name__)
lock = Lock()  # lock to prevent simultaneous calls to Dropbox


class TimedQueue(queue.Queue):

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


class DropboxUploadSync(object):
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

        metadata = self.client.move(dbx_path, dbx_path2)

        # remove old revs
        self.set_local_rev(dbx_path, None)

        # add new revs
        if isinstance(metadata, dropbox.files.FileMetadata):
            self.set_local_rev(dbx_path2, metadata.rev)

        # and revs of children if folder
        elif isinstance(metadata, dropbox.files.FolderMetadata):
            self.set_local_rev(dbx_path2, "folder")
            results = self.list_folder(dbx_path2, recursive=True)
            results_dict = self.flatten_results_list(results)
            for md in results_dict.values():
                if isinstance(md, dropbox.files.FileMetadata):
                    self.set_local_rev(md.path_display, md.rev)
                elif isinstance(md, dropbox.files.FolderMetadata):
                    self.set_local_rev(md.path_display, "folder")

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
                md = self.client.upload(path, dbx_path, autorename=True, mode=mode)
                # save or update revision metadata
                self.set_local_rev(md.path_display, md.rev)

        elif event.is_directory:
            # check if directory is not yet on Dropbox, else leave alone
            md = self.client.files_get_metadata(dbx_path)
            if not md:
                md = self.client.make_dir(dbx_path)

            # save or update revision metadata
            self.set_local_rev(dbx_path, "folder")

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
            md = self.client.remove(dbx_path)
            # remove revision metadata
            self.set_local_rev(md.path_display, None)

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


def connection_helper(client, connected, running):

    disconnected_signal = signal("disconnected_signal")
    connected_signal = signal("connected_signal")

    account_usage = signal("account_usage")

    while True:
        try:
            with lock:
                # use an inexpensive call to get_space_usage to test connection
                res = client.get_space_usage()
            connected.set()
            connected_signal.send()
            account_usage.send(res)
            time.sleep(1)
        except requests.exceptions.RequestException:
            running.clear()
            connected.clear()
            disconnected_signal.send()
            logger.info("Connecting...")
            time.sleep(1)


def remote_changes_worker(client, running):
    """
    Wroker to sync changes of remote Dropbox with local folder.

    :param class client: :class:`SisyphosClient` instance.
    :param class running: If not `running.is_set()` the worker is paused.
    """

    disconnected_signal = signal("disconnected_signal")

    while True:

        running.wait()  # if not running, wait until resumed

        try:
            # wait for remote changes (times out after 120 secs)
            changes = client.wait_for_remote_changes(timeout=120)

            running.wait()  # if not running, wait until resumed

            # apply remote changes
            if changes:
                logger.info("Syncing remote changes")
                with lock:
                    client.get_remote_changes()
                logger.info("Up to date")
            else:
                logger.info("Up to date")

        except requests.exceptions.RequestException:
            logger.debug("Connection lost")
            disconnected_signal.send()
            running.clear()  # must be started again from outside


def local_changes_worker(dbx_uploader, event_q, running):
    """
    Worker to sync local changes to remote Dropbox.

    :param class dbx_uploader: Instance of :class:`DropboxUploadSync`.
    :param class event_q: Queue containing all local file events to sync.
    :param class running: If not `running.is_set()` the worker is paused.
    """

    disconnected_signal = signal("disconnected_signal")
    delay = 0.1

    while True:

        running.wait()  # if not running, wait until resumed

        events = []
        events.append(event_q.get())  # blocks until something is in queue

        # wait for delay after last event has been registered
        t0 = time.time()
        while t0 - event_q.update_time < delay:
            time.sleep(delay)
            t0 = time.time()

        # get all events after folder has been idle for self.delay
        while event_q.qsize() > 0:
            events.append(event_q.get())

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
                        dbx_uploader.on_created(event)
                    elif event.event_type is EVENT_TYPE_MOVED:
                        dbx_uploader.on_moved(event)
                    elif event.event_type is EVENT_TYPE_DELETED:
                        dbx_uploader.on_deleted(event)
                    elif event.event_type is EVENT_TYPE_MODIFIED:
                        dbx_uploader.on_modified(event)
                CONF.set("internal", "lastsync", time.time())
                logger.info("Up to date")
        except requests.exceptions.RequestException:
            logger.debug("Connection lost")
            disconnected_signal.send()
            running.clear()   # must be started again from outside


class Monitor(object):
    """
    Class to sync changes between Dropbox and local folder.

    :ivar observer: Watchdog obersver thread that detects local file system
        events and calls `file_handler`.
    :ivar file_handler: Handler to register file events and put in queue.
    :ivar dbx_uploader: Handler to upload changes to Dropbox.
    :ivar local_thread: Thread that calls `dbx_uploader` methods when file
        events have been queued.
    :ivar remote_thread: Thread to query and process remote changes.
    :ivar connected: Event that is set if connection to Dropbox API
        servers can be established.
    :ivar running: Event that controls worker theads.
    :ivar stopped_by_user: `True` if worker has been stopped by user, `False`.
        If `stopped_by_user` is `True`, syncing will not automatically resume
        once a connection is restablished.
    """

    connected = Event()
    running = Event()

    connected_signal = signal("connected_signal")
    disconnected_signal = signal("disconnected_signal")

    account_usage = signal("account_usage")

    stopped_by_user = True

    def __init__(self, client):

        self.client = client
        self.file_handler = FileEventHandler()
        self.dbx_uploader = DropboxUploadSync(self.client)
        self.event_q = self.file_handler.event_q

        self.connection_thread = Thread(
                target=connection_helper,
                args=(self.client, self.connected, self.running),
                name="SisyphosConnectionHelper")

        self.remote_thread = Thread(
                target=remote_changes_worker,
                args=(self.client, self.running),
                name="SisyphosRemoteSync")

        self.local_thread = Thread(
                target=local_changes_worker,
                args=(self.dbx_uploader, self.event_q, self.running),
                name="SisyphosLocalSync")

        self.connection_thread.start()
        self.remote_thread.start()
        self.local_thread.start()

        self.connected_signal.connect(self.start)
        self.disconnected_signal.connect(self.stop)

    def start(self, data=None):
        """Creates worker threads and starts syncing."""

        if self.running.is_set() or self.stopped_by_user:
            # do nothing if already running or stopped by user
            return

        res = self.upload_local_changes_after_inactive()

        if not res:
            return

        self.running.set()

        self.observer = Observer()
        self.observer.schedule(self.file_handler, self.client.dropbox_path,
                               recursive=True)
        self.observer.start()

    def stop(self, data=None):
        """Stops syncing and destroys worker threads."""

        if not self.running.is_set():
            # already stopped, nothing to do
            return

        self.running.clear()  # stops workers if running

        self.observer.stop()  # stop observer
        self.observer.join()  # wait to finish

    def upload_local_changes_after_inactive(self):
        """
        Push changes while client has not been running to Dropbox.

        :return: `True` on success, `False` if connection error has been caught.
        :rtype: bool
        """

        logger.info("Checking for changes...")

        events = self._get_local_changes()

        try:
            for event in events:
                if event.event_type is EVENT_TYPE_CREATED:
                    self.dbx_uploader.on_created(event)
                elif event.event_type is EVENT_TYPE_DELETED:
                    self.dbx_uploader.on_deleted(event)
                elif event.event_type is EVENT_TYPE_MODIFIED:
                    self.dbx_uploader.on_modified(event)

            CONF.set("internal", "lastsync", time.time())
            return True
        except requests.exceptions.RequestException:
            logger.debug("Connection lost")
            self.disconnected_signal.send()
            self.running.clear()   # must be started again from outside
            return False

    def _get_local_changes(self):
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

        # get modified or added items
        for path in snapshot.paths:
            stats = snapshot.stat_info(path)
            last_sync = CONF.get("internal", "lastsync")
            # check item was created or modified since last sync
            if max(stats.st_ctime, stats.st_mtime) > last_sync:
                # check if item is already tracked or new
                if self.client.to_dbx_path(path).lower() in self.client.rev_dict:
                    # already tracking item
                    if osp.isdir(path):
                        event = DirModifiedEvent(path)
                    else:
                        event = FileModifiedEvent(path)
                    changes.append(event)
                else:
                    # new item, not excluded
                    if osp.isdir(path):
                        event = DirCreatedEvent(path)
                    else:
                        event = FileCreatedEvent(path)
                    changes.append(event)

        # get deleted items
        for path in self.client.rev_dict:
            if self.client.to_local_path(path).lower() not in lowercase_snapshot_paths:
                if self.client.rev_dict[path] == "folder":
                    event = DirDeletedEvent(self.client.to_local_path(path))
                else:
                    event = FileDeletedEvent(self.client.to_local_path(path))
                changes.append(event)

        return changes
