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
from concurrent.futures import ThreadPoolExecutor
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

logger = logging.getLogger(__name__)


CONNECTION_ERRORS = (
         requests.exceptions.Timeout,
         requests.exceptions.ConnectionError,
         requests.exceptions.HTTPError
    )


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
    :ivar flagged: Deque with paths to be temporarily ignored. This is mostly
        used to exclude files and folders which are currently being downloaded
        from monitoring.
    :ivar running: Threading Event which turns off any queuing of uploads.
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

    def on_moved(self, event):
        if self.running.is_set():
            logger.debug("Move detected: from '%s' to '%s'",
                         event.src_path, event.dest_path)
            self.local_q.put(event)

    def on_created(self, event):
        if self.running.is_set() and not self.is_flagged(event.src_path):
            logger.debug("Creation detected: '%s'", event.src_path)
            self.local_q.put(event)

    def on_deleted(self, event):
        if self.running.is_set() and not self.is_flagged(event.src_path):
            logger.debug("Deletion detected: '%s'", event.src_path)
            self.local_q.put(event)

    def on_modified(self, event):
        if self.running.is_set() and not self.is_flagged(event.src_path):
            logger.debug("Modification detected: '%s'", event.src_path)
            self.local_q.put(event)


class DropboxUploadSync(object):
    """
    Class that contains methods to sync local file events with Dropbox. It
    takes watchdog file events and translates them to uploads, deletions or
    moves of Dropbox files, performed by the Maestral Dropbox API client.

    The 'last_sync' entry in the config file and `client.last_sync` are updated
    with the current time after every successful sync. 'last_sync' is used to
    detect changes while :class:`MaestralMonitor` was not running.
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
        # file created during a saving event on macOS. Ignore such files.
        if osp.basename(path2).count(".") > 1:
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
            results = self.client.list_folder(dbx_path2, recursive=True)
            results_list = self.client.flatten_results_list(results)
            for md in results_list:
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
                md = self.client.make_dir(dbx_path)

            # save or update revision metadata
            self.client.set_local_rev(dbx_path, "folder")

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

        rev = self.client.get_local_rev(dbx_path)
        if rev is not None:
            md = self.client.remove(dbx_path)
            # remove revision metadata
            if md:
                self.client.set_local_rev(md.path_display, None)

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


def connection_helper(client, connected, running, stop):
    """
    A worker which periodically checks the connection to Dropbox servers.
    This is done through inexpensive calls to :method:`client.get_space_usage`.
    If the connection is lost, ``connection_helper`` pauses all syncing until a
    connection can be reestablished.

    :param client: Maestral client instance.
    :param connected: Event that indicates if connection to Dropbox is established.
    :param running: Event that indicates if workers are running or paused.
    :param stop: Event to stop local event handler and workers.
    """

    disconnected_signal = signal("disconnected_signal")
    connected_signal = signal("connected_signal")
    account_usage_signal = signal("account_usage_signal")

    while not stop.is_set():
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
            logger.info("Connecting...")
            time.sleep(1)


def download_worker(client, running, stop, flagged):
    """
    Worker to sync changes of remote Dropbox with local folder. All files about
    to change are temporarily excluded from the local file monitor by adding
    their paths to the `flagged` deque.

    :param client: :class:`MaestralClient` instance.
    :param running: If not `running.is_set()` the worker is paused. This event
        will be set if the connection to the Dropbox server fails, or if
        syncing is paused by the user.
    :param stop: Event to stop local event handler and workers.
    :param deque flagged: Flagged paths for local observer to ignore.
    """

    disconnected_signal = signal("disconnected_signal")

    while not stop.is_set():

        running.wait()  # if not running, wait until resumed

        try:
            # wait for remote changes (times out after 120 secs)
            logger.info("Up to date")
            has_changes = client.wait_for_remote_changes(timeout=120)

            running.wait()  # if not running, wait until resumed

            # apply remote changes
            if has_changes:
                logger.info("Syncing...")
                with client.lock:
                    # get changes
                    changes = client.list_remote_changes()
                    # flag changes to be ignored by local monitor
                    flat_changes = client.flatten_results_list(changes)
                    for item in flat_changes:
                        local_path = client.to_local_path(item.path_lower)
                        flagged.append(local_path)
                    time.sleep(1)
                    # apply remote changes to local Dropbox folder
                    client.apply_remote_changes(changes)
                    time.sleep(2)
                    # clear flagged list
                    flagged.clear()

            logger.info("Up to date")
        except CONNECTION_ERRORS as e:
            logger.debug(e)
            logger.info("Connecting...")
            disconnected_signal.send()
            running.clear()  # must be started again from outside


def upload_worker(dbx_uploader, local_q, running, stop):
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
    :param stop: Event to stop local event handler and workers.
    """

    disconnected_signal = signal("disconnected_signal")
    delay = 0.5

    # check for moved folders
    def is_moved_folder(x):
        is_moved_event = (x.event_type is EVENT_TYPE_MOVED)
        return (is_moved_event and x.is_directory)

    # check for children of moved folders
    def is_moved_child(x, parent):
        is_moved_event = (x.event_type is EVENT_TYPE_MOVED)
        is_child = (x.src_path.startswith(parent.src_path) and
                    x is not parent)
        return (is_moved_event and is_child)

    # check for deleted folders
    def is_deleted_folder(x):
        is_deleted_event = (x.event_type is EVENT_TYPE_DELETED)
        return (is_deleted_event and x.is_directory)

    # check for children of deleted folders
    def is_deleted_child(x, parent_event):
        is_deleted_event = (x.event_type is EVENT_TYPE_DELETED)
        is_child = (x.src_path.startswith(parent_event.src_path) and
                    x is not parent_event)
        return (is_deleted_event and is_child)

    # check for created items
    def is_created(x):
        return (x.event_type is EVENT_TYPE_CREATED)

    # check modified items that have just been created
    def is_modified_duplicate(x, original):
        is_modified_event = (x.event_type is EVENT_TYPE_MODIFIED)
        is_duplicate = (x.src_path == original.src_path)
        return (is_modified_event and is_duplicate)

    while not stop.is_set():

        events = [local_q.get()]  # blocks until event is in queue

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

        with dbx_uploader.client.lock:
            try:
                logger.info("Syncing...")

                num_threads = os.cpu_count()*2

                with ThreadPoolExecutor(max_workers=num_threads) as executor:
                    executor.map(dispatch_event, events)
                CONF.set("internal", "lastsync", time.time())
                logger.info("Up to date")
            except (KeyboardInterrupt, SystemExit):
                raise
            except CONNECTION_ERRORS as e:
                logger.debug(e)
                logger.info("Connecting...")
                disconnected_signal.send()
                running.clear()   # must be started again from outside


class MaestralMonitor(object):
    """
    Class to sync changes between Dropbox and local folder. It creates four
    threads: `observer` to catch local file events, `upload_thread` to upload
    caught changes to Dropbox, `download_thread` to query for and download
    remote changes, and `connection_thread` which periodically checks the
    connection to Dropbox servers.

    :ivar observer: Watchdog observer thread that detects local file system
        events.
    :ivar file_handler: Handler to queue file events from `observer` for upload.
    :ivar dbx_uploader: Class instance to convert file events to
        `MaestralClient` calls.
    :ivar upload_thread: Thread that sorts file events and uploads them with
        `dbx_uploader`.
    :ivar download_thread: Thread to query for and download remote changes.
    :ivar connected: Event that is set if connection to Dropbox API servers can
        be established.
    :ivar running: Event is set if worker threads are running.
    :ivar stop: Event to stop worker threads.
    :ivar paused_by_user: `True` if worker has been stopped by user, `False`.
        If `paused_by_user` is `True`, syncing will not automatically resume
        once a connection is established.
    """

    connected = Event()
    running = Event()
    stop = Event()
    flagged = deque()

    connected_signal = signal("connected_signal")
    disconnected_signal = signal("disconnected_signal")
    account_usage_signal = signal("account_usage_signal")

    paused_by_user = True

    def __init__(self, client):

        self.client = client
        self.dbx_uploader = DropboxUploadSync(self.client)

        self.file_handler = FileEventHandler(self.flagged)
        self.local_q = self.file_handler.local_q

        self.connection_thread = Thread(
                target=connection_helper,
                args=(self.client, self.connected, self.running, self.stop),
                name="MaestralConnectionHelper")
        self.connection_thread.setDaemon(True)
        self.connection_thread.start()

    def start(self, overload=None):
        """Creates observer threads and starts syncing."""

        if self.running.is_set() or self.paused_by_user:
            # do nothing if already running or stopped by user
            return

        self.local_observer_thread = Observer()
        self.local_observer_thread.schedule(
                self.file_handler, self.client.dropbox_path, recursive=True)

        self.download_thread = Thread(
                target=download_worker,
                args=(self.client, self.running, self.stop, self.flagged),
                name="MaestralDownloader")

        self.upload_thread = Thread(
                target=upload_worker,
                args=(self.dbx_uploader, self.local_q, self.running, self.stop),
                name="MaestralUploader")

        self.local_observer_thread.start()
        self.download_thread.start()
        self.upload_thread.start()

        self.connected_signal.connect(self.resume)
        self.disconnected_signal.connect(self.pause)

        self.upload_local_changes_after_inactive()

        self.running.set()  # starts download_thread
        self.file_handler.running.set()  # starts local file event handler

    def resume(self, overload=None):
        """Checks for changes while idle and starts syncing."""

        if self.running.is_set() or self.paused_by_user:
            # do nothing if already running or stopped by user
            return

        self.upload_local_changes_after_inactive()

        self.running.set()  # starts download_thread
        self.file_handler.running.set()  # starts local file event handler

    def pause(self, overload=None):
        """Pauses syncing."""

        self.running.clear()  # stops download_thread
        self.file_handler.running.clear()  # stops local file event handler

    def stop(self, overload=None):
        """Stops syncing and destroys worker threads."""

        self.running.clear()  # pauses threads
        self.stop.set()  # stops threads
        self.file_handler.running.clear()  # stops local file event handler

        self.local_observer_thread.stop()  # stop observer
        self.local_observer_thread.join()  # wait to finish

        self.upload_thread.join()
        self.download_thread.join()
        self.connection_thread.join()

    def upload_local_changes_after_inactive(self):
        """
        Push changes while client has not been running to Dropbox.
        """

        logger.info("Indexing...")

        events = self._get_local_changes()

        # queue changes for upload
        for event in events:
            self.local_q.put(event)

        logger.info("Up to date")

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
