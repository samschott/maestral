# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

import os
import os.path as osp
import logging
import time
from threading import Thread, Event
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
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

from maestral.config.main import CONF
from maestral.client import REV_FILE, CorruptedRevFileError

logger = logging.getLogger(__name__)


CONNECTION_ERRORS = (
         requests.exceptions.Timeout,
         requests.exceptions.ConnectionError,
         requests.exceptions.HTTPError
    )

IDLE = "Up to date"
SYNCING = "Syncing..."
PAUSED = "Syncing paused"
DISCONNECTED = "Connecting..."


class TimedQueue(queue.Queue):
    """
    A queue that remembers the time of the last put.

    :ivar update_time: Time of the last put.
    """

    def __init__(self):
        super(self.__class__, self).__init__()

        self.update_time = 0

    # Put a new item in the queue, remember time
    def _put(self, item):
        self.queue.append(item)
        self.update_time = time.time()


class FileEventHandler(FileSystemEventHandler):
    """
    Logs captured file events and adds them to :ivar:`local_q` to be processed
    by :class:`upload_worker`. This acts as a translation layer between between
    `watchdog.Observer` and :class:`upload_worker`.

    :ivar local_q: Queue with unprocessed local file events.
    :ivar flagged: Deque with files to be ignored. This is primarily used to
         exclude files and folders from monitoring if they are currently being
         downloaded. All entries in `flagged` should be temporary only.s
    :ivar running: Event to turn the queueing of uploads on / off.
    """

    def __init__(self, flagged):
        self.local_q = TimedQueue()
        self.running = Event()
        self.flagged = flagged

    def is_flagged(self, local_path):
        for path in self.flagged:
            if local_path.lower().startswith(path.lower()):
                logger.debug("'{0}' is flagged, no upload.".format(local_path))
                return True
        return False

    def on_any_event(self, event):
        if os.path.basename(event.src_path) == REV_FILE:  # TODO: find a better place
            # do not upload file with rev index
            return

        if self.running.is_set() and not self.is_flagged(event.src_path):
            self.local_q.put(event)


class DropboxUploadSync(object):
    """
    Class that contains methods to sync local file events with Dropbox. It
    takes watchdog file events and translates them to uploads, deletions or
    moves of Dropbox files, performed by the Maestral Dropbox API client.

    The 'last_sync' entry in the config file and `client.last_sync` are updated
    with the current time after every successful sync. 'last_sync' is used to
    detect changes while :class:`MaestralMonitor` was not running.

    :param client: Maestral client instance.
    """

    def __init__(self, client):

        self.client = client

    def on_moved(self, event):
        """
        Call when local file is moved.

        :param class event: Watchdog file event.
        """

        logger.debug("Move detected: from '%s' to '%s'",
                     event.src_path, event.dest_path)

        path = event.src_path
        path2 = event.dest_path

        dbx_path = self.client.to_dbx_path(path)
        dbx_path2 = self.client.to_dbx_path(path2)

        # is file excluded?
        if self.client.is_excluded(dbx_path2):
            return

        metadata = self.client.move(dbx_path, dbx_path2)

        # remove old revs
        self.client.set_local_rev(dbx_path, None)

        # add new revs
        if isinstance(metadata, dropbox.files.FileMetadata):
            self.client.set_local_rev(dbx_path2, metadata.rev)

        # and revs of children if folder
        elif isinstance(metadata, dropbox.files.FolderMetadata):
            self.client.set_local_rev(dbx_path2, "folder")
            result = self.client.list_folder(dbx_path2, recursive=True)
            for md in result.entries:
                if isinstance(md, dropbox.files.FileMetadata):
                    self.client.set_local_rev(md.path_display, md.rev)
                elif isinstance(md, dropbox.files.FolderMetadata):
                    self.client.set_local_rev(md.path_display, "folder")

        CONF.set("internal", "lastsync", time.time())

    def on_created(self, event):
        """
        Call when local file is created.

        :param class event: Watchdog file event.
        """

        logger.debug("Creation detected: '%s'", event.src_path)

        path = event.src_path
        dbx_path = self.client.to_dbx_path(path)

        # is file excluded?
        if self.client.is_excluded(dbx_path):
            return

        if not event.is_directory:

            if osp.isfile(path):
                while True:  # wait until file is fully created
                    size1 = osp.getsize(path)
                    time.sleep(0.5)
                    size2 = osp.getsize(path)
                    if size1 == size2:
                        break

                # check if file already exists with identical content
                chk = self.client.check_conflict(dbx_path)

                if chk == 2:
                    # file hashes are identical, do not upload
                    CONF.set("internal", "lastsync", time.time())
                    return

                rev = self.client.get_local_rev(dbx_path)
                # if truly a new file
                if rev is None:
                    mode = dropbox.files.WriteMode("add")
                # or a 'false' new file event triggered by saving the file
                # e.g., some programs create backup files and then swap them
                # in to replace the files you are editing on the disk
                else:
                    mode = dropbox.files.WriteMode("update", rev)
                md = self.client.upload(path, dbx_path, autorename=True, mode=mode)
                # save or update revision metadata
                self.client.set_local_rev(md.path_display, md.rev)

        elif event.is_directory:
            # check if directory is not yet on Dropbox, else leave alone
            md = self.client.get_metadata(dbx_path)
            if not md:
                self.client.make_dir(dbx_path)

            # save or update revision metadata
            self.client.set_local_rev(dbx_path, "folder")

        CONF.set("internal", "lastsync", time.time())

    def on_deleted(self, event):
        """
        Call when local file is deleted.

        :param class event: Watchdog file event.
        """

        logger.debug("Deletion detected: '%s'", event.src_path)

        path = event.src_path
        dbx_path = self.client.to_dbx_path(path)

        # do not propagate deletions that result from excluding a folder!
        if self.client.is_excluded_by_user(dbx_path):
            return

        md = self.client.remove(dbx_path)  # returns false if file did not exist
        # remove revision metadata
        # don't check if remove was successful
        self.client.set_local_rev(md.path_display, None)

        CONF.set("internal", "lastsync", time.time())

    def on_modified(self, event):
        """
        Call when local file is modified.

        :param class event: Watchdog file event.
        """

        logger.debug("Modification detected: '%s'", event.src_path)

        path = event.src_path
        dbx_path = self.client.to_dbx_path(path)

        # is file excluded?
        if self.client.is_excluded(dbx_path):
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


def connection_helper(client, connected, running, shutdown):
    """
    A worker which periodically checks the connection to Dropbox servers.
    This is done through inexpensive calls to :method:`client.get_space_usage`.
    If the connection is lost, ``connection_helper`` pauses all syncing until a
    connection can be reestablished.

    :param client: Maestral client instance.
    :param connected: Event that indicates if connection to Dropbox is established.
    :param running: Event that indicates if workers should be running or paused.
    :param shutdown: Event to shutdown local event handler and workers.
    """

    disconnected_signal = signal("disconnected_signal")
    connected_signal = signal("connected_signal")
    account_usage_signal = signal("account_usage_signal")

    while not shutdown.is_set():
        try:
            # use an inexpensive call to get_space_usage to test connection
            res = client.get_space_usage()
            if not connected.is_set():
                connected.set()
                connected_signal.send()
            account_usage_signal.send(res)
            time.sleep(5)
        except CONNECTION_ERRORS as e:
            logger.debug(e)
            running.clear()
            connected.clear()
            disconnected_signal.send()
            logger.info(DISCONNECTED)
            time.sleep(1)


def download_worker(client, running, shutdown, flagged):
    """
    Worker to sync changes of remote Dropbox with local folder. All files about
    to change are temporarily excluded from the local file monitor by adding
    their paths to the `flagged` deque.

    :param client: :class:`MaestralClient` instance.
    :param running: If not `running.is_set()` the worker is paused. This event
        will be set if the connection to the Dropbox server fails, or if
        syncing is paused by the user.
    :param shutdown: Event to shutdown local event handler and workers.
    :param deque flagged: Flagged paths for local observer to ignore.
    """

    disconnected_signal = signal("disconnected_signal")

    while not shutdown.is_set():

        running.wait()  # if not running, wait until resumed

        try:
            # wait for remote changes (times out after 120 secs)
            logger.info(IDLE)
            has_changes = client.wait_for_remote_changes(timeout=120)

            running.wait()  # if not running, wait until resumed

            # apply remote changes
            if has_changes:
                logger.info(SYNCING)
                with client.lock:
                    # get changes
                    result = client.list_remote_changes()
                    for item in result.entries:
                        local_path = client.to_local_path(item.path_display)
                        flagged.append(local_path)
                    time.sleep(1)
                    # apply remote changes to local Dropbox folder
                    client.apply_remote_changes(result)
                    time.sleep(2)

                    # clear flagged list
                    flagged.clear()

            logger.info(IDLE)
        except CONNECTION_ERRORS as e:
            logger.debug(e)
            logger.info(DISCONNECTED)
            disconnected_signal.send()
            running.clear()  # must be started again from outside


def upload_worker(dbx_uploader, local_q, running, shutdown):
    """
    Worker to sync local changes to remote Dropbox. It collects the most recent
    local file events from `local_q`, prunes them from duplicates, and
    processes the remaining events by calling methods of
    :class:`DropboxUploadSync`.


    :param dbx_uploader: Instance of :class:`DropboxUploadSync`.
    :param local_q: Queue containing all local file events to sync.
    :param running: Event to pause local event handler and download worker.
        Will be set if the connection to the Dropbox server fails, or if
        syncing is paused by the user.
    :param shutdown: Event to shutdown local event handler and workers.
    """

    disconnected_signal = signal("disconnected_signal")
    delay = 0.5

    # check for moved folders
    def is_moved_folder(x):
        is_moved_event = (x.event_type is EVENT_TYPE_MOVED)
        return is_moved_event and x.is_directory

    # check for children of moved folders
    def is_moved_child(x, parent):
        is_moved_event = (x.event_type is EVENT_TYPE_MOVED)
        is_child = (x.src_path.startswith(parent.src_path) and
                    x is not parent)
        return is_moved_event and is_child

    # check for deleted folders
    def is_deleted_folder(x):
        is_deleted_event = (x.event_type is EVENT_TYPE_DELETED)
        return is_deleted_event and x.is_directory

    # check for children of deleted folders
    def is_deleted_child(x, parent):
        is_deleted_event = (x.event_type is EVENT_TYPE_DELETED)
        is_child = (x.src_path.startswith(parent.src_path) and
                    x is not parent)
        return is_deleted_event and is_child

    # check for created items
    def is_created(x):
        return x.event_type is EVENT_TYPE_CREATED

    # check modified items that have just been created
    def is_modified_duplicate(x, original):
        is_modified_event = (x.event_type is EVENT_TYPE_MODIFIED)
        is_duplicate = (x.src_path == original.src_path)
        return is_modified_event and is_duplicate

    # process all events
    def dispatch_event(evnt):
        if evnt.event_type is EVENT_TYPE_CREATED:
            dbx_uploader.on_created(evnt)
        elif evnt.event_type is EVENT_TYPE_MOVED:
            dbx_uploader.on_moved(evnt)
        elif evnt.event_type is EVENT_TYPE_DELETED:
            dbx_uploader.on_deleted(evnt)
        elif evnt.event_type is EVENT_TYPE_MODIFIED:
            dbx_uploader.on_modified(evnt)

    while not shutdown.is_set():

        try:
            events = [local_q.get(timeout=2)]  # blocks until event is in queue
        except queue.Empty:
            pass
        else:
            # wait for delay after last event has been registered
            t0 = time.time()
            while t0 - local_q.update_time < delay:
                time.sleep(delay)
                t0 = time.time()

            # get all events after folder has been idle for self.delay
            while local_q.qsize() > 0:
                events.append(local_q.get())

            # COMBINE MOVED EVENTS OF FOLDERS AND THEIR CHILDREN INTO ONE EVENT
            moved_folder_events = [x for x in events if is_moved_folder(x)]
            child_move_events = []

            for parent_event in moved_folder_events:
                children = [x for x in events if is_moved_child(x, parent_event)]
                child_move_events += children

            events = set(events) - set(child_move_events)

            # COMBINE DELETED EVENTS OF FOLDERS AND THEIR CHILDREN INTO ONE EVENT
            deleted_folder_events = [x for x in events if is_deleted_folder(x)]
            child_deleted_events = []

            for parent_event in deleted_folder_events:
                children = [x for x in events if is_deleted_child(x, parent_event)]
                child_deleted_events += children

            events = set(events) - set(child_deleted_events)

            # COMBINE CREATED AND MODIFIED EVENTS OF THE SAME FILE
            created_file_events = [x for x in events if is_created(x)]
            duplicate_modified_events = []

            for event in created_file_events:
                duplicates = [x for x in events if is_modified_duplicate(x, event)]
                duplicate_modified_events += duplicates

            # remove all events with duplicate effects
            events = set(events) - set(duplicate_modified_events)

            with dbx_uploader.client.lock:
                try:
                    logger.info(SYNCING)

                    num_threads = os.cpu_count()*2
                    with ThreadPoolExecutor(max_workers=num_threads) as executor:
                        fs = [executor.submit(dispatch_event, e) for e in events]
                        n_files = len(events)
                        for (f, n) in zip(as_completed(fs), range(1, n_files+1)):
                            logger.info("Uploading {0}/{1}...".format(n, n_files))

                    CONF.set("internal", "lastsync", time.time())
                    logger.info(IDLE)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except CONNECTION_ERRORS as e:
                    logger.debug(e)
                    logger.info(DISCONNECTED)
                    disconnected_signal.send()
                    running.clear()   # must be started again from outside


class MaestralMonitor(object):
    """
    Class to sync changes between Dropbox and local folder. It creates four
    threads: `observer` to catch local file events, `upload_thread` to upload
    caught changes to Dropbox, `download_thread` to query for and download
    remote changes, and `connection_thread` which periodically checks the
    connection to Dropbox servers.

    :ivar local_observer_thread: Watchdog observer thread that detects local file
        system events.
    :ivar upload_thread: Thread that sorts file events and uploads them with
        `dbx_uploader`.
    :ivar download_thread: Thread to query for and download remote changes.
    :ivar file_handler: Handler to queue file events from `observer` for upload.
    :ivar dbx_uploader: Class instance to convert file events to
        `MaestralClient` calls.
    :cvar connected: Event that is set if connection to Dropbox API servers can
        be established.
    :cvar running: Event is set if worker threads are running.
    :cvar shutdown: Event to shutdown worker threads.
    """

    connected = Event()
    running = Event()
    shutdown = Event()
    flagged = deque()

    connected_signal = signal("connected_signal")
    disconnected_signal = signal("disconnected_signal")
    account_usage_signal = signal("account_usage_signal")

    _auto_resume_on_connect = False

    def __init__(self, client):

        logger.info(IDLE)

        self.client = client
        self.dbx_uploader = DropboxUploadSync(self.client)

        self.file_handler = FileEventHandler(self.flagged)
        self.local_q = self.file_handler.local_q

        self.connection_thread = Thread(
                target=connection_helper, daemon=True,
                args=(self.client, self.connected, self.running, self.shutdown),
                name="MaestralConnectionHelper")
        self.connection_thread.start()

    def start(self, overload=None):
        """Creates observer threads and starts syncing."""

        self._auto_resume_on_connect = True

        if self.running.is_set():
            # do nothing if already running
            return

        self.local_observer_thread = Observer()
        self.local_observer_thread.schedule(
                self.file_handler, self.client.dropbox_path, recursive=True)

        self.download_thread = Thread(
                target=download_worker, daemon=True,
                args=(self.client, self.running, self.shutdown, self.flagged),
                name="MaestralDownloader")

        self.upload_thread = Thread(
                target=upload_worker, daemon=True,
                args=(self.dbx_uploader, self.local_q, self.running, self.shutdown),
                name="MaestralUploader")

        self.local_observer_thread.start()
        self.download_thread.start()
        self.upload_thread.start()

        self.connected_signal.connect(self._resume_on_connect)
        self.disconnected_signal.connect(self._pause_on_disconnect)

        self.upload_local_changes_after_inactive()

        self.running.set()  # resumes download_thread
        self.file_handler.running.set()  # starts local file event handler

    def pause(self, overload=None):
        """Pauses syncing."""

        self._auto_resume_on_connect = False
        self._pause_on_disconnect()

        logger.info(PAUSED)

    def resume(self, overload=None):
        """Checks for changes while idle and starts syncing."""

        self._auto_resume_on_connect = True
        self._resume_on_connect()

        logger.info(IDLE)

    def _pause_on_disconnect(self, overload=None):
        """Pauses syncing."""

        self.running.clear()  # pauses download_thread
        self.file_handler.running.clear()  # stops local file event handler

    def _resume_on_connect(self, overload=None):
        """Checks for changes while idle and starts syncing."""

        if self.running.is_set() or not self._auto_resume_on_connect:
            # do nothing if already running or paused by user
            return

        self.upload_local_changes_after_inactive()

        self.running.set()  # resumes download_thread
        self.file_handler.running.set()  # starts local file event handler

    def stop(self, overload=None, blocking=False):
        """Stops syncing and destroys worker threads."""

        self._auto_resume_on_connect = False

        logger.debug('Shutting down threads...')

        self.local_observer_thread.stop()  # stop observer
        self.local_observer_thread.join()  # wait to finish

        if blocking:
            self.upload_thread.join()  # wait to finish (up to 2 sec)

        self.shutdown.set()  # stops our own threads

        logger.debug('Stopped.')

    def check_rev_file(self):
        """
        Pauses syncing and checks if rev file contains up-to-date entries for all files
        and folders. If the check passes, the rev file is guaranteed to be ok. If it
        fails, the rev file may be corrupted.

        :return: ``True`` if rev file is up-to-date, ``False`` otherwise.
        :rtype: bool
        """

        # check that rev file can be loaded and has the expected format
        try:
            self.client._rev_dict_cache = self.client._load_rev_dict_from_file(
                raise_exception=True)
        except CorruptedRevFileError:
            self.client._rev_dict_cache = dict()
            return False

        self.stop(blocking=True)  # stop all sync threads

        # restart syncing
        # this may detect changes which are not yet synced
        self.start()

        self.stop(blocking=True)  # stop all sync threads

        # verify that all local files have a rev number

        ok = True

        changes = self._get_local_changes()
        if any(isinstance(c, (DirCreatedEvent, FileCreatedEvent)) for c in changes):
            print("Rev file contains entries which do not correspond to synced items.")
            ok = False
        if any(isinstance(c, (DirDeletedEvent, FileDeletedEvent)) for c in changes):
            print("Dropbox folder contains items which are not tracked.")
            ok = False
        if any(isinstance(c, (DirModifiedEvent, FileModifiedEvent)) for c in changes):
            print("Dropbox folder contains un-synced changes.")
            ok = False

        if ok:
            self.start()
        else:
            print("Rev file may be corrupted.")

        return ok

    def rebuild_rev_file(self):
        """Rebuilds the rev file by comparing local with remote files and updating rev
        numbers from the Dropbox server. Files are compared by their content hashes and
        reindexing may take several minutes, depending on the size of your Dropbox. If
        a file is modified during this process before it has been re-indexed,
        any changes to will be flagged as sync conflicts. If a file is deleted before
        it has been re-indexed, the deletion will be reversed.

        """

        print("""Rebuilding the revision index. This process may
        take several minutes, depending on the size of your Dropbox.
        Any changes to local files during this process may be
        flagged as sync conflicts and local deletions may be reversed
        (if the modified or deleted file has not yet been re-indexed). """)

        self.stop(blocking=True)  # stop all sync threads
        os.unlink(self.client.rev_file)  # delete rev file

        # Rebuild dropbox from server. If local file already exists,
        # content hashes are compared. If files are identical, the
        # local rev will be set accordingly, otherwise a conflicting copy
        # will be created.
        self.client.get_remote_dropbox()

        # Resume syncing. This will upload all changes which occurred
        # while rebuilding, including conflicting copies. Files that were
        # deleted before re-indexing will be downloaded again.
        self.start()

    def upload_local_changes_after_inactive(self):
        """
        Push changes while client has not been running to Dropbox.
        """

        logger.info("Indexing...")

        events = self._get_local_changes()

        # queue changes for upload
        for event in events:
            self.local_q.put(event)

        logger.info(IDLE)

    def _get_local_changes(self):
        """
        Gets all local changes while app has not been running. Call this method
        on startup of `MaestralMonitor` to upload all local changes.

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
            # check if item was created or modified since last sync
            dbx_path = self.client.to_dbx_path(path).lower()
            if max(stats.st_ctime, stats.st_mtime) > last_sync:
                # check if item is already tracked or new
                if self.client.get_local_rev(dbx_path) is not None:
                    # already tracking item
                    if osp.isdir(path):
                        event = DirModifiedEvent(path)
                    else:
                        event = FileModifiedEvent(path)
                    changes.append(event)
                else:
                    # new item
                    if osp.isdir(path):
                        event = DirCreatedEvent(path)
                    else:
                        event = FileCreatedEvent(path)
                    changes.append(event)

        # get deleted items
        rev_dict_copy = self.client.get_rev_dict()
        for path in rev_dict_copy:
            if self.client.to_local_path(path).lower() not in lowercase_snapshot_paths:
                if rev_dict_copy[path] == "folder":
                    event = DirDeletedEvent(self.client.to_local_path(path))
                else:
                    event = FileDeletedEvent(self.client.to_local_path(path))
                changes.append(event)

        return changes
