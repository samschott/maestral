# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

import os
import os.path as osp
import shutil
import logging
import time
import threading
from threading import Thread, Event
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict
import requests
import queue

import umsgpack
from blinker import signal
import dropbox
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from watchdog.events import (EVENT_TYPE_CREATED, EVENT_TYPE_DELETED,
                             EVENT_TYPE_MODIFIED, EVENT_TYPE_MOVED)
from watchdog.events import (DirModifiedEvent, FileModifiedEvent,
                             DirCreatedEvent, FileCreatedEvent,
                             DirDeletedEvent, FileDeletedEvent)
from watchdog.utils.dirsnapshot import DirectorySnapshot

from maestral.config.main import CONF
from maestral.utils.content_hasher import DropboxContentHasher
from maestral.utils.notify import Notipy


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

REV_FILE = ".dropbox"


# ========================================================================================
# Syncing functionality
# ========================================================================================

def path_exists_case_insensitive(path, root="/"):
    """
    Checks if a `path` exists in given `root` directory, similar to
    `os.path.exists` but case-insensitive. If there are multiple
    case-insensitive matches, the first one is returned. If there is no match,
    an empty string is returned.

    :param str path: Relative path of file/folder to find in the `root`
        directory.
    :param str root: Directory where we will look for `path`.
    :return: Absolute and case-sensitive path to search result on hard drive.
    :rtype: str
    """

    if not osp.isdir(root):
        raise ValueError("'{0}' is not a directory.".format(root))

    if path in ["", "/"]:
        return root

    path_list = path.lstrip(osp.sep).split(osp.sep)
    path_list_lower = [x.lower() for x in path_list]

    i = 0
    local_paths = []
    for root, dirs, files in os.walk(root):
        for d in list(dirs):
            if not d.lower() == path_list_lower[i]:
                dirs.remove(d)
        for f in list(files):
            if not f.lower() == path_list_lower[i]:
                files.remove(f)

        local_paths = [osp.join(root, name) for name in dirs + files]

        i += 1
        if i == len(path_list_lower):
            break

    if len(local_paths) == 0:
        return ''
    else:
        return local_paths[0]


def get_local_hash(dst_path):
    """
    Computes content hash of local file.

    :param str dst_path: Path to local file.
    :return: content hash to compare with ``content_hash`` attribute of
        :class:`dropbox.files.FileMetadata` object.
    """

    hasher = DropboxContentHasher()

    with open(dst_path, 'rb') as f:
        while True:
            chunk = f.read(1024)
            if len(chunk) == 0:
                break
            hasher.update(chunk)

    return hasher.hexdigest()


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


class CorruptedRevFileError(Exception):
    """Raised when the rev file exists but cannot be read."""
    pass


class UpDownSync(object):
    """
    Class that contains methods to sync local file events with Dropbox and vice versa.

    :param client: Maestral client instance.
    :param local_q: Queue with local file-changed events.
    """

    _dropbox_path = CONF.get("main", "path")

    notify = Notipy()
    lock = threading.RLock()

    _rev_lock = threading.Lock()

    def __init__(self, client, local_q):

        self.client = client
        self.local_q = local_q

        # cache dropbox path
        self._dropbox_path = CONF.get("main", "path")

        # cache of revision dictionary
        self._rev_dict_cache = self._load_rev_dict_from_file()

    @property
    def rev_file_path(self):
        """Path to file with revision index (read only)."""
        return osp.join(self.dropbox_path, REV_FILE)

    @property
    def dropbox_path(self):
        """Path to local Dropbox folder, as loaded from config file. Before changing
        :ivar`dropbox_path`, make sure that all syncing is paused. Make sure to move
        the local Dropbox directory before resuming the sync. Changes are saved to the
        config file."""
        return self._dropbox_path

    @dropbox_path.setter
    def dropbox_path(self, path):
        """Setter: dropbox_path"""
        self._dropbox_path = path
        CONF.set("main", "path", path)

    @property
    def last_cursor(self):
        """Cursor from last sync with remote Dropbox. The value is updated and saved to
        config file on every successful sync. This should not be modified manually."""
        return CONF.get("internal", "cursor")

    @last_cursor.setter
    def last_cursor(self, cursor):
        """Setter: last_cursor"""
        CONF.set("internal", "cursor", cursor)

    @property
    def last_sync(self):
        """Time stamp from last sync with remote Dropbox. The value is updated and
        saved to config file on every successful sync. This should not be modified
        manually."""
        return CONF.get("internal", "lastsync")

    @last_sync.setter
    def last_sync(self, last_sync):
        """Setter: last_cursor"""
        CONF.set("internal", "lastsync", last_sync)

    @property
    def excluded_files(self):
        """List containing all files excluded from sync (read only). This only contains
        system files such as '.DS_STore' and internal files such as '.dropbox'."""
        return CONF.get("main", "excluded_files")

    @property
    def excluded_folders(self):
        """List containing all files excluded from sync. Changes are saved to the
        config file."""
        return CONF.get("main", "excluded_folders")

    @excluded_folders.setter
    def excluded_folders(self, folders_list):
        """Setter: excluded_folders"""
        CONF.set("main", "excluded_folders", folders_list)

    # ====================================================================================
    #  Helper functions
    # ====================================================================================

    def to_dbx_path(self, local_path):
        """
        Converts a local path to a path relative to the Dropbox folder.

        :param str local_path: Full path to file in local Dropbox folder.
        :return: Relative path with respect to Dropbox folder.
        :rtype: str
        :raises ValueError: If no path is specified or path is outside of local
            Dropbox folder.
        """

        if not local_path:
            raise ValueError("No path specified.")

        dbx_root_list = osp.normpath(self.dropbox_path).split(osp.sep)
        path_list = osp.normpath(local_path).split(osp.sep)

        # Work out how much of the file path is shared by dropbox_path and path.
        # noinspection PyTypeChecker
        i = len(osp.commonprefix([dbx_root_list, path_list]))

        if i == len(path_list):  # path corresponds to dropbox_path
            return "/"
        elif not i == len(dbx_root_list):  # path is outside of to dropbox_path
            raise ValueError(
                "Specified path '%s' is not in Dropbox directory." % local_path)

        relative_path = "/" + "/".join(path_list[i:])

        return relative_path

    def to_local_path(self, dbx_path):
        """
        Converts a Dropbox folder to the corresponding local path.

        The `path_display` attribute returned by the Dropbox API only
        guarantees correct casing of the basename (file name or folder name)
        and not of the full path. This is because Dropbox itself is not case
        sensitive and stores all paths in lowercase internally.

        Therefore, if the parent directory is already present on the local
        drive, it's casing is used. Otherwise, the casing given by the Dropbox
        API metadata is used. This aims to preserve the correct casing as
        uploaded to Dropbox and prevents the creation of duplicate folders
        with different casing on the local drive.

        :param str dbx_path: Path to file relative to Dropbox folder.
        :return: Corresponding local path on drive.
        :rtype: str
        :raises ValueError: If no path is specified.
        """

        if not dbx_path:
            raise ValueError("No path specified.")

        dbx_path = dbx_path.replace("/", osp.sep)
        dbx_path_parent, dbx_path_basename,  = osp.split(dbx_path)

        local_parent = path_exists_case_insensitive(dbx_path_parent, self.dropbox_path)

        if local_parent == "":
            return osp.join(self.dropbox_path, dbx_path.lstrip(osp.sep))
        else:
            return osp.join(local_parent, dbx_path_basename)

    def _load_rev_dict_from_file(self, path=None, raise_exception=False):
        path = self.rev_file_path if not path else path
        with self._rev_lock:
            try:
                with open(path, "rb") as f:
                    rev_dict_cache = umsgpack.unpack(f)
                assert isinstance(rev_dict_cache, dict)
                assert all(isinstance(key, str) for key in rev_dict_cache.keys())
                assert all(isinstance(val, str) for val in rev_dict_cache.values())
            except FileNotFoundError:
                rev_dict_cache = dict()
                logger.warning("Maestral index could not be found. Rebuild if necessary.")
            except (AssertionError, IsADirectoryError):
                msg = "Maestral index has become corrupted. Please rebuild."
                if raise_exception:
                    raise CorruptedRevFileError(msg)
                else:
                    rev_dict_cache = dict()
                    logger.error(msg)
            except PermissionError:
                msg = ("Insufficient permissions for Dropbox folder. Please " +
                       "make sure that you have read and write permissions.")
                if raise_exception:
                    raise CorruptedRevFileError(msg)
                else:
                    rev_dict_cache = dict()
                    logger.error(msg)

            return rev_dict_cache

    def get_rev_dict(self):
        """
        Returns a copy of the revision index containing the revision
        numbers for all synced files and folders.

        :return: Copy of revision index.
        :rtype: dict
        """
        with self._rev_lock:
            return dict(self._rev_dict_cache)

    def get_local_rev(self, dbx_path):
        """
        Gets revision number of local file.

        :param str dbx_path: Dropbox file path.
        :return: Revision number as str or `None` if no local revision number
            has been saved.
        :rtype: str
        """
        with self._rev_lock:
            dbx_path = dbx_path.lower()
            rev = self._rev_dict_cache.get(dbx_path, None)

            return rev

    def set_local_rev(self, dbx_path, rev):
        """
        Saves revision number `rev` for local file. If `rev` is `None`, the
        entry for the file is removed.

        :param str dbx_path: Relative Dropbox file path.
        :param rev: Revision number as string or `None`.
        """
        with self._rev_lock:
            dbx_path = dbx_path.lower()

            if rev is None:
                # remove entry and all its children revs
                for path in dict(self._rev_dict_cache):
                    if path.startswith(dbx_path):
                        self._rev_dict_cache.pop(path, None)
            else:
                # add entry
                self._rev_dict_cache[dbx_path] = rev
                # set all parent revs to 'folder'
                dirname = osp.dirname(dbx_path)
                while dirname is not "/":
                    self._rev_dict_cache[dirname] = "folder"
                    dirname = osp.dirname(dirname)

            # save changes to file
            # don't wrap in try statement but raise all errors
            with open(self.rev_file_path, "wb+") as f:
                umsgpack.pack(self._rev_dict_cache, f)

    # ====================================================================================
    #  Upload sync
    # ====================================================================================

    def wait_for_local_changes(self, timeout=2, delay=0.5):
        """
        Waits for local file changes. Returns a list of local changes, filtered to
        avoid duplicates.

        :param float timeout: If no changes are detected within timeout (sec), an empty
            list is returned.
        :param delay: Delay in sec to wait for subsequent changes that may be duplicates.

        :return: List of watchdog file events.
        :rtype: list
        """
        try:
            events = [self.local_q.get(timeout)]  # blocks until event is in queue
        except queue.Empty:
            return []
        else:
            # wait for delay after last event has been registered
            t0 = time.time()
            while t0 - self.local_q.update_time < delay:
                time.sleep(delay)
                t0 = time.time()

            # get all events after folder has been idle for self.delay
            while self.local_q.qsize() > 0:
                events.append(self.local_q.get())

            # COMBINE MOVED EVENTS OF FOLDERS AND THEIR CHILDREN INTO ONE EVENT
            moved_folder_events = [x for x in events if self._is_moved_folder(x)]
            child_move_events = []

            for parent_event in moved_folder_events:
                children = [x for x in events if self._is_moved_child(x, parent_event)]
                child_move_events += children

            events = set(events) - set(child_move_events)

            # COMBINE DELETED EVENTS OF FOLDERS AND THEIR CHILDREN INTO ONE EVENT
            deleted_folder_events = [x for x in events if self._is_deleted_folder(x)]
            child_deleted_events = []

            for parent_event in deleted_folder_events:
                children = [x for x in events if self._is_deleted_child(x, parent_event)]
                child_deleted_events += children

            events = set(events) - set(child_deleted_events)

            # COMBINE CREATED AND MODIFIED EVENTS OF THE SAME FILE
            created_file_events = [x for x in events if self._is_created(x)]
            duplicate_modified_events = []

            for event in created_file_events:
                duplicates = [x for x in events if self._is_modified_duplicate(x, event)]
                duplicate_modified_events += duplicates

            # remove all events with duplicate effects
            events = set(events) - set(duplicate_modified_events)

            return events

    @staticmethod
    def _is_moved_folder(x):
        """check for moved folders"""
        is_moved_event = (x.event_type is EVENT_TYPE_MOVED)
        return is_moved_event and x.is_directory

    @staticmethod
    def _is_moved_child(x, parent):
        """check for children of moved folders"""
        is_moved_event = (x.event_type is EVENT_TYPE_MOVED)
        is_child = (x.src_path.startswith(parent.src_path) and
                    x is not parent)
        return is_moved_event and is_child

    @staticmethod
    def _is_deleted_folder(x):
        """check for deleted folders"""
        is_deleted_event = (x.event_type is EVENT_TYPE_DELETED)
        return is_deleted_event and x.is_directory

    @staticmethod
    def _is_deleted_child(x, parent):
        """check for children of deleted folders"""
        is_deleted_event = (x.event_type is EVENT_TYPE_DELETED)
        is_child = (x.src_path.startswith(parent.src_path) and
                    x is not parent)
        return is_deleted_event and is_child

    @staticmethod
    def _is_created(x):
        """check for created items"""
        return x.event_type is EVENT_TYPE_CREATED

    @staticmethod
    def _is_modified_duplicate(x, original):
        """check modified items that have just been created"""
        is_modified_event = (x.event_type is EVENT_TYPE_MODIFIED)
        is_duplicate = (x.src_path == original.src_path)
        return is_modified_event and is_duplicate

    def apply_local_changes(self, events):
        """Applies locally detected events to remote Dropbox."""
        num_threads = os.cpu_count() * 2
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            fs = [executor.submit(self._apply_event, e) for e in events]
            n_files = len(events)
            for (f, n) in zip(as_completed(fs), range(1, n_files + 1)):
                logger.info("Uploading {0}/{1}...".format(n, n_files))

        self.last_sync = time.time()

    def _apply_event(self, evnt):
        if evnt.event_type is EVENT_TYPE_CREATED:
            self._on_created(evnt)
        elif evnt.event_type is EVENT_TYPE_MOVED:
            self._on_moved(evnt)
        elif evnt.event_type is EVENT_TYPE_DELETED:
            self._on_deleted(evnt)
        elif evnt.event_type is EVENT_TYPE_MODIFIED:
            self._on_modified(evnt)

    def _on_moved(self, event):
        """
        Call when local file is moved.

        :param class event: Watchdog file event.
        """

        logger.debug("Move detected: from '%s' to '%s'",
                     event.src_path, event.dest_path)

        path = event.src_path
        path2 = event.dest_path

        dbx_path = self.to_dbx_path(path)
        dbx_path2 = self.to_dbx_path(path2)

        # is file excluded?
        if self.is_excluded(dbx_path2):
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
            result = self.client.list_folder(dbx_path2, recursive=True)
            for md in result.entries:
                if isinstance(md, dropbox.files.FileMetadata):
                    self.set_local_rev(md.path_display, md.rev)
                elif isinstance(md, dropbox.files.FolderMetadata):
                    self.set_local_rev(md.path_display, "folder")

        self.last_sync = time.time()

    def _on_created(self, event):
        """
        Call when local file is created.

        :param class event: Watchdog file event.
        """

        logger.debug("Creation detected: '%s'", event.src_path)

        path = event.src_path
        dbx_path = self.to_dbx_path(path)

        # is file excluded?
        if self.is_excluded(dbx_path):
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
                md = self.client.get_metadata(dbx_path)
                if md:
                    local_hash = get_local_hash(path)
                    if local_hash == md.content_hash:
                        # file hashes are identical, do not upload
                        self.last_sync = time.time()
                        return

                rev = self.get_local_rev(dbx_path)
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
                self.set_local_rev(md.path_display, md.rev)

        elif event.is_directory:
            # check if directory is not yet on Dropbox, else leave alone
            md = self.client.get_metadata(dbx_path)
            if not md:
                self.client.make_dir(dbx_path)

            # save or update revision metadata
            self.set_local_rev(dbx_path, "folder")

        self.last_sync = time.time()

    def _on_deleted(self, event):
        """
        Call when local file is deleted.

        :param class event: Watchdog file event.
        """

        logger.debug("Deletion detected: '%s'", event.src_path)

        path = event.src_path
        dbx_path = self.to_dbx_path(path)

        # do not propagate deletions that result from excluding a folder!
        if self.is_excluded_by_user(dbx_path):
            return

        md = self.client.remove(dbx_path)  # returns false if file did not exist
        # remove revision metadata
        # don't check if remove was successful
        self.set_local_rev(md.path_display, None)

        self.last_sync = time.time()

    def _on_modified(self, event):
        """
        Call when local file is modified.

        :param class event: Watchdog file event.
        """

        logger.debug("Modification detected: '%s'", event.src_path)

        path = event.src_path
        dbx_path = self.to_dbx_path(path)

        # is file excluded?
        if self.is_excluded(dbx_path):
            return

        if not event.is_directory:  # ignore directory modified events
            if osp.isfile(path):

                while True:  # wait until file is fully created
                    size1 = osp.getsize(path)
                    time.sleep(0.2)
                    size2 = osp.getsize(path)
                    if size1 == size2:
                        break

                rev = self.get_local_rev(dbx_path)
                mode = dropbox.files.WriteMode("update", rev)
                md = self.client.upload(path, dbx_path, autorename=True, mode=mode)
                logger.debug("Modified file: %s (old rev: %s, new rev %s)",
                             md.path_display, rev, md.rev)

        self.last_sync = time.time()

    # ====================================================================================
    #  Download sync
    # ====================================================================================

    # TODO: speed up by neglecting excluded folders
    def get_remote_dropbox(self, dbx_path=""):
        """
        Gets all files/folders from Dropbox and writes them to local folder
        :ivar:`dropbox_path`. Call this method on first run of client. Indexing
        and downloading may take some time, depending on the size of the users
        Dropbox folder.

        :param str dbx_path: Path to Dropbox folder. Defaults to root ("").
        :return: `True` on success, `False` otherwise.
        :rtype: bool
        """
        logger.info("Indexing...")
        result = self.client.list_folder(dbx_path, recursive=True,
                                         include_deleted=False, limit=500)
        if not result:
            return False

        # apply remote changes, don't update the global cursor when downloading
        # a single folder only
        save_cursor = (dbx_path == "")
        logger.info("Syncing...")
        success = self.apply_remote_changes(result, save_cursor)
        logger.info("Up to date")
        return success

    def filter_excluded_changes(self, changes):

        # filter changes from non-excluded folders
        entries_filtered = [e for e in changes.entries if not self.is_excluded_by_user(
            e.path_lower)]

        result_filtered = dropbox.files.ListFolderResult(
            entries=entries_filtered, cursor=changes.cursor, has_more=False)

        return result_filtered

    def apply_remote_changes(self, changes, save_cursor=True):
        """
        Applies remote changes to local folder. Call this on the result of
        :method:`list_remote_changes`. The saved cursor is updated after a set
        of changes has been successfully applied.

        :param changes: :class:`dropbox.files.ListFolderResult` instance
            or `False` if requests failed.
        :param bool save_cursor: If True, :ivar:`last_cursor` will be updated
            from the last applied changes.
        :return: `True` on success, `False` otherwise.
        :rtype: bool
        """

        if not changes:
            return

        # sort changes into folders, files and deleted
        folders, files, deleted = self._sort_entries(changes)

        # sort according to path hierarchy
        # do not create sub-folder / file before parent exists
        folders.sort(key=lambda x: len(x.path_display.split('/')))
        files.sort(key=lambda x: len(x.path_display.split('/')))
        deleted.sort(key=lambda x: len(x.path_display.split('/')))

        # create local folders, start with top-level and work your way down
        for folder in folders:
            success = self._create_local_entry(folder)
            if success is False:
                return False

        # apply deleted items
        for item in deleted:
            success = self._create_local_entry(item)
            if success is False:
                return False

        # apply created files
        n_files = len(files)
        success = []
        with ThreadPoolExecutor(max_workers=15) as executor:
            fs = [executor.submit(self._create_local_entry, file) for file in files]
            for (f, n) in zip(as_completed(fs), range(1, n_files+1)):
                logger.info("Downloading {0}/{1}...".format(n, n_files))
                success += [f.result()]

        if all(success) is False:
            return False

        # save cursor
        if save_cursor:
            self.last_cursor = changes.cursor

        return True

    def is_excluded_by_user(self, dbx_path):
        """
        Check if file is excluded from sync.

        :param str dbx_path: Path of folder on Dropbox.
        :return: `True` or `False`.
        :rtype: bool
        """
        dbx_path = dbx_path.lower()

        excluded = False

        # in excluded folders?
        for excluded_folder in self.excluded_folders:
            if not osp.commonpath([dbx_path, excluded_folder]) in ["/", ""]:
                excluded = True

        return excluded

    def is_excluded(self, dbx_path):
        """
        Check if file is excluded from sync.

        :param str dbx_path: Path of folder on Dropbox.
        :return: `True` or `False`.
        :rtype: bool
        """
        dbx_path = dbx_path.lower()

        excluded = False

        # is root folder?
        if dbx_path in ["/", ""]:
            excluded = True

        # in excluded files?
        if osp.basename(dbx_path) in self.excluded_files:
            excluded = True

        # If the file name contains multiple periods it is likely a temporary
        # file created during a saving event on macOS. Ignore such files.
        if osp.basename(dbx_path).count(".") > 1:
            excluded = True

        return excluded

    def check_download_conflict(self, dbx_path):
        """
        Check if local file is conflicting with remote file.

        :param str dbx_path: Path of folder on Dropbox.
        :return: 0 for no conflict, 1 for conflict, 2 if files are identical.
            Returns -1 if metadata request to Dropbox API fails.
        :rtype: int
        """
        # get corresponding local path
        dst_path = self.to_local_path(dbx_path)

        # get metadata of remote file
        md = self.client.get_metadata(dbx_path)
        if not md:
            logging.info("Could not get metadata for '%s'.")
            return -1

        # no conflict if local file does not exist yet
        if not osp.exists(dst_path):
            logger.debug("Local file '%s' does not exist. No conflict.", dbx_path)
            return 0

        local_rev = self.get_local_rev(dbx_path)

        if local_rev is None:
            # We likely have a conflict: files with the same name have been
            # created on Dropbox and locally independent of each other.
            # Check actual content first before declaring conflict!

            local_hash = get_local_hash(dst_path)

            if not md.content_hash == local_hash:
                logger.debug("Conflicting copy without rev.")
                return 1  # files are conflicting
            else:
                logger.debug("Contents are equal. No conflict.")
                self.set_local_rev(dbx_path, md.rev)  # update local rev
                return 2  # files are already the same

        elif md.rev == local_rev:
            # files have the same revision, trust that they are equal
            logger.debug(
                    "Local file is the same as on Dropbox (rev %s).",
                    local_rev)
            return 2  # files are already the same

        elif md.rev != local_rev:
            # Dropbox server version has a different rev, must be newer.
            # If the local version has been modified while sync was stopped,
            # those changes will be uploaded before any downloads can begin.
            # If the local version has been modified while sync was running
            # but changes were not uploaded before the remote version was
            # changed as well, either:
            # (a) The upload of the changed file has already started. The
            #     the remote version will be downloaded and saved and
            #     the Dropbox server will create a conflicting copy once the
            #     upload comes through.
            # (b) The upload has not started yet. In this case, the local
            #     changes may be overwritten by the remote version if the
            #     download completes before the upload starts. This is a bug.

            logger.debug(
                    "Local file has rev %s, newer file on Dropbox has rev %s.",
                    local_rev, md.rev)
            return 0

    def notify_user(self, changes):
        # count remote changes
        n_changed_total = len(changes.entries)
        n_changed_included = len(changes.entries)

        # notify user
        if n_changed_included == 1:
            md = changes.entries[0]
            file_name = md.path_display.strip("/")
            if isinstance(md, DeletedMetadata):
                if self.get_local_rev(md.path_display):
                    # file has been deleted from remote
                    self.notify.send("%s removed" % file_name)
            elif isinstance(md, FileMetadata):
                if self.get_local_rev(md.path_display) is None:
                    # file has been added to remote
                    self.notify.send("%s added" % file_name)
                elif not self.get_local_rev(md.path_display) == md.rev:
                    # file has been modified on remote
                    self.notify.send("%s modified" % file_name)
            elif isinstance(md, FolderMetadata):
                if self.get_local_rev(md.path_display) is None:
                    # folder has been deleted from remote
                    self.notify.send("%s added" % file_name)

        elif n_changed_included > 1:
            self.notify.send("%s files changed" % n_changed_included)

        n_changed_outside = n_changed_total - n_changed_included
        if n_changed_outside > 99:
            # always notify for changes of 100 files and more
            self.notify.send("%s files changed" % n_changed_outside)

    @staticmethod
    def _sort_entries(result):
        """
        Sorts entries in :class:`dropbox.files.ListFolderResult` into
        FolderMetadata, FileMetadata and DeletedMetadata.

        :return: Tuple of (folders, files, deleted) containing instances of
            :class:`DeletedMetadata`, `:class:FolderMetadata`,
            and :class:`FileMetadata` respectively.
        :rtype: tuple
        """

        folders = [x for x in result.entries if isinstance(x, FolderMetadata)]
        files = [x for x in result.entries if isinstance(x, FileMetadata)]
        deleted = [x for x in result.entries if isinstance(x, DeletedMetadata)]

        return folders, files, deleted

    def _create_local_entry(self, entry, check_excluded=True):
        """
        Creates local file / folder for remote entry.

        :param class entry: Dropbox FileMetadata|FolderMetadata|DeletedMetadata.
        :return: `True` on success, `False` otherwise.
        :rtype: bool
        """

        self.excluded_folders = CONF.get("main", "excluded_folders")

        if self.is_excluded(entry.path_display):
            return True

        if check_excluded and self.is_excluded_by_user(entry.path_display):
            return True

        local_path = self.to_local_path(entry.path_display)

        if isinstance(entry, FileMetadata):
            # Store the new entry at the given path in your local state.
            # If the required parent folders don’t exist yet, create them.
            # If there’s already something else at the given path,
            # replace it and remove all its children.

            self._save_to_history(entry.path_display)

            # check for sync conflicts
            conflict = self.check_download_conflict(entry.path_display)
            if conflict == -1:  # could not get metadata
                return False
            if conflict == 0:  # no conflict
                pass
            elif conflict == 1:  # conflict! rename local file
                parts = osp.splitext(local_path)
                new_local_file = parts[0] + " (conflicting copy)" + parts[1]
                os.rename(local_path, new_local_file)
            elif conflict == 2:  # Dropbox files corresponds to local file, nothing to do
                return True

            md = self.client.download(entry.path_display, local_path)
            if md is False:
                return False

            # save revision metadata
            self.set_local_rev(md.path_display, md.rev)

            logger.debug("Created local file '{0}'".format(entry.path_display))

            return True

        elif isinstance(entry, FolderMetadata):
            # Store the new entry at the given path in your local state.
            # If the required parent folders don’t exist yet, create them.
            # If there’s already something else at the given path,
            # replace it but leave the children as they are.

            os.makedirs(local_path, exist_ok=True)

            # save revision metadata
            self.set_local_rev(entry.path_display, "folder")

            logger.debug("Created local directory '{0}'".format(entry.path_display))

            return True

        elif isinstance(entry, DeletedMetadata):
            # If your local state has something at the given path,
            # remove it and all its children. If there’s nothing at the
            # given path, ignore this entry.

            try:
                if osp.isdir(local_path):
                    shutil.rmtree(local_path)
                elif osp.isfile(local_path):
                    os.remove(local_path)
            except FileNotFoundError as e:
                logger.debug("FileNotFoundError: {0}".format(e))
            else:
                logger.debug("Deleted local item '{0}'".format(entry.path_display))

            self.set_local_rev(entry.path_display, None)

            return True

    @staticmethod
    def _save_to_history(dbx_path):
        # add new file to recent_changes
        recent_changes = CONF.get("internal", "recent_changes")
        recent_changes.append(dbx_path)
        # eliminate duplicates
        recent_changes = list(OrderedDict.fromkeys(recent_changes))
        # save last 30 changes
        CONF.set("internal", "recent_changes", recent_changes[-30:])


# ========================================================================================
# Workers for upload, download and connection monitoring threads
# ========================================================================================

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


def download_worker(sync, running, shutdown, flagged):
    """
    Worker to sync changes of remote Dropbox with local folder. All files about
    to change are temporarily excluded from the local file monitor by adding
    their paths to the `flagged` deque.

    :param sync: Instance of :class:`UpDownSync`.
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
            has_changes = sync.client.wait_for_remote_changes(
                sync.last_cursor, timeout=120)

            running.wait()  # if not running, wait until resumed

            # apply remote changes
            if has_changes:
                logger.info(SYNCING)
                with sync.lock:
                    # get changes
                    changes = sync.client.list_remote_changes(sync.last_cursor)
                    # filter out excluded folders
                    changes = sync.filter_excluded_changes(changes)
                    # notify user about changes
                    sync.notify_user(changes)

                    # flag changes to temporarily exclude from upload
                    for item in changes.entries:
                        local_path = sync.to_local_path(item.path_display)
                        flagged.append(local_path)
                    time.sleep(1)

                    # apply remote changes to local Dropbox folder
                    sync.apply_remote_changes(changes)
                    time.sleep(2)

                    # clear flagged list
                    flagged.clear()

            logger.info(IDLE)
        except CONNECTION_ERRORS as e:
            logger.debug(e)
            logger.info(DISCONNECTED)
            disconnected_signal.send()
            running.clear()  # must be started again from outside


def upload_worker(sync, running, shutdown):
    """
    Worker to sync local changes to remote Dropbox. It collects the most recent
    local file events from `local_q`, prunes them from duplicates, and
    processes the remaining events by calling methods of
    :class:`DropboxUploadSync`.


    :param sync: Instance of :class:`UpDownSync`.
    :param running: Event to pause local event handler and download worker.
        Will be set if the connection to the Dropbox server fails, or if
        syncing is paused by the user.
    :param shutdown: Event to shutdown local event handler and workers.
    """

    disconnected_signal = signal("disconnected_signal")

    while not shutdown.is_set():

        events = sync.wait_for_local_changes(timeout=2)

        if len(events) > 0:
            with sync.lock:
                try:
                    logger.info(SYNCING)
                    sync.apply_local_changes(events)
                    logger.info(IDLE)
                except CONNECTION_ERRORS as e:
                    logger.info(DISCONNECTED)
                    logger.debug(e)
                    disconnected_signal.send()
                    running.clear()   # must be started again from outside


# ========================================================================================
# Main Monitor class to start, stop and coordinate threads
# ========================================================================================

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
    :ivar sync: Class to coordinate syncing. This is the brain of Maestral.
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
        self.file_handler = FileEventHandler(self.flagged)
        self.local_q = self.file_handler.local_q

        self.sync = UpDownSync(self.client, self.local_q)

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
                self.file_handler, self.sync.dropbox_path, recursive=True)

        self.download_thread = Thread(
                target=download_worker, daemon=True,
                args=(self.sync, self.running, self.shutdown, self.flagged),
                name="MaestralDownloader")

        self.upload_thread = Thread(
                target=upload_worker, daemon=True,
                args=(self.sync, self.running, self.shutdown),
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
            self.sync._rev_dict_cache = self.sync._load_rev_dict_from_file(
                raise_exception=True)
        except CorruptedRevFileError:
            self.sync._rev_dict_cache = dict()
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
        os.unlink(self.sync.rev_file_path)  # delete rev file

        # Rebuild dropbox from server. If local file already exists,
        # content hashes are compared. If files are identical, the
        # local rev will be set accordingly, otherwise a conflicting copy
        # will be created.
        self.sync.get_remote_dropbox()

        # Resume syncing. This will upload all changes which occurred
        # while rebuilding, including conflicting copies. Files that were
        # deleted before re-indexing will be downloaded again.
        self.start()

    def upload_local_changes_after_inactive(self):
        """
        Push changes while sync has not been running to Dropbox.
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
        snapshot = DirectorySnapshot(self.sync.dropbox_path)
        # remove root entry from snapshot
        del snapshot._inode_to_path[snapshot.inode(self.sync.dropbox_path)]
        del snapshot._stat_info[self.sync.dropbox_path]
        # get lowercase paths
        lowercase_snapshot_paths = {x.lower() for x in snapshot.paths}

        # get modified or added items
        for path in snapshot.paths:
            stats = snapshot.stat_info(path)
            last_sync = CONF.get("internal", "lastsync")
            # check if item was created or modified since last sync
            dbx_path = self.sync.to_dbx_path(path).lower()
            if max(stats.st_ctime, stats.st_mtime) > last_sync:
                # check if item is already tracked or new
                if self.sync.get_local_rev(dbx_path) is not None:
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
        rev_dict_copy = self.sync.get_rev_dict()
        for path in rev_dict_copy:
            if self.sync.to_local_path(path).lower() not in lowercase_snapshot_paths:
                if rev_dict_copy[path] == "folder":
                    event = DirDeletedEvent(self.sync.to_local_path(path))
                else:
                    event = FileDeletedEvent(self.sync.to_local_path(path))
                changes.append(event)

        return changes
