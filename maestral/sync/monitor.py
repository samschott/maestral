# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
# system imports
import os
import os.path as osp
import platform
import shutil
import logging
import time
from threading import Thread, Event, RLock
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue
from collections import OrderedDict
import functools

# external packages
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
                             DirDeletedEvent, FileDeletedEvent,
                             DirMovedEvent, FileMovedEvent)
from watchdog.utils.dirsnapshot import DirectorySnapshot

# maestral modules
from maestral.config.main import CONF
from maestral.sync.utils import delete_file_or_folder
from maestral.sync.utils.content_hasher import DropboxContentHasher
from maestral.sync.utils.notify import Notipy
from maestral.sync.errors import (CONNECTION_ERRORS, MaestralApiError, CursorResetError,
                                  RevFileError, DropboxDeletedError, DropboxAuthError,
                                  ExcludedItemError, PathError)


logger = logging.getLogger(__name__)


IDLE = "Up to date"
SYNCING = "Syncing..."
PAUSED = "Syncing paused"
STOPPED = "Syncing stopped"
DISCONNECTED = "Connecting..."
SYNC_ERROR = "Sync error"

REV_FILE = ".maestral"
OLD_REV_FILE = ".dropbox"


# ========================================================================================
# Syncing functionality
# ========================================================================================

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
    Handles captured file events and adds them to :ivar:`local_q` to be processed
    by :class:`upload_worker`. This acts as a translation layer between
    `watchdog.Observer` and :class:`upload_worker`.

    :ivar syncing: Event that needs to be set for file events to be passed on.
    :ivar local_file_event_queue: Queue with unprocessed local file events.
    :ivar queue_downloading: Deque with files to be ignored. This is primarily used to
         exclude files and folders from monitoring if they are currently being
         downloaded. All entries in :ivar:`queue_downloading` should be temporary only.
    """

    def __init__(self, syncing, local_file_event_queue, queue_downloading):

        self.syncing = syncing
        self.local_file_event_queue = local_file_event_queue
        self.queue_downloading = queue_downloading

        self._renamed_items_cache = []

    def is_being_downloaded(self, local_path):
        """
        Checks if a file is currently being downloaded and should therefore not trigger
        any file events.

        :param str local_path: Local path to file.
        :return: ``True`` if the file is currently being downloaded, ``False`` otherwise.
        :rtype: bool
        """

        with self.queue_downloading.mutex:
            queue_downloading = tuple(self.queue_downloading.queue)
            for flagged_path in queue_downloading:
                if local_path.lower() == flagged_path.lower():
                    logger.debug("'{0}' is being downloaded, ignore.".format(local_path))
                    self.queue_downloading.queue.remove(flagged_path)

                return True
        return False

    @staticmethod
    def is_rev_file(local_path):
        """
        Checks if :param:`local_path` refers to our rev file.

        :param str local_path: Local path to file.
        :return: ``True`` if yes, ``False`` otherwise.
        :rtype: bool
        """

        return osp.basename(local_path) == REV_FILE

    # TODO: Our logic for ignoring moved events of children will no longer work when
    #   renaming the parent's moved event. This will throw sync errors when trying to
    #   apply those events, but they are only temporary and therefore tolerable for now.
    def rename_on_case_conflict(self, event):
        """
        Checks for other items in the same directory with same name but a different case.
        Will only run those check on Linux because Apple's APFS or journaled file systems
        are not case sensitive.

        :param event: Created or moved event.
        :returns: Modified event if conflict detected and file has been
            renamed, original event otherwise.
        """

        if platform.system() == "Darwin":
            return event

        if not (event.event_type is EVENT_TYPE_CREATED or event.event_type is
                EVENT_TYPE_MOVED):
            return event

        # get the created items path (src_path or dest_path)
        created_path = getattr(event, "dest_path", event.src_path)

        # get all other items in the same directory
        try:
            parent_dir = osp.dirname(created_path)
            other_items = [osp.join(parent_dir, file) for file in os.listdir(parent_dir)]
            other_items.remove(created_path)
        except FileNotFoundError:
            return event

        # check if we have any conflicting names with different cases
        if any(p.lower() == created_path.lower() for p in other_items):
            # try to find a unique new name of the form "(case conflict)"
            # or "(case conflict 1)"
            base, ext = osp.splitext(created_path)
            new_path = base + " (case conflict)" + ext
            i = 1
            while any(p.lower() == new_path.lower() for p in other_items):
                new_path = base + " (case conflict {})".format(i) + ext
                i += 1
            # rename newly created item
            self._renamed_items_cache.append(created_path)  # ignore temporarily
            os.rename(created_path, new_path)  # this will be picked up by watchdog
            logger.info("Case conflict: renamed '{0}' "
                        "to '{1}'".format(created_path, new_path))

            if isinstance(event, DirCreatedEvent):
                return DirCreatedEvent(src_path=new_path)
            elif isinstance(event, FileCreatedEvent):
                return FileCreatedEvent(src_path=new_path)
            elif isinstance(event, DirMovedEvent):
                return DirMovedEvent(src_path=event.src_path, dest_path=new_path)
            elif isinstance(event, FileMovedEvent):
                return FileMovedEvent(src_path=event.src_path, dest_path=new_path)

        return event

    def on_any_event(self, event):
        """
        Checks if the system file event should be ignored for any reason. If not, adds it
        to the queue for events to upload.

        :param event: Watchdog file event.
        """

        # ignore files currently being downloaded
        if self.is_being_downloaded(event.src_path):
            return

        # ignore changes to the rev file
        if self.is_rev_file(event.src_path):
            return

        # rename target on case conflict
        event = self.rename_on_case_conflict(event)

        # ignore files which have been renamed
        if event.src_path in self._renamed_items_cache:
            self._renamed_items_cache.remove(event.src_path)
            return

        if self.syncing.is_set():
            self.local_file_event_queue.put(event)


def catch_sync_issues(sync_errors=None, failed_items=None):
    """
    Decorator that catches all MaestralApiErrors and logs them. This should only be used
    to decorate UpDownSync methods.

    :param sync_errors: Queue for sync errors.
    :param failed_items: Queue for failed syc items themselves. These will be passed as
        the second argument of ``func`` and will be either a watchdog Event (for uploads)
        or a Dropbox metadata item (for downloads).
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                res = func(self, *args, **kwargs)
                if res is None:
                    res = True
            except MaestralApiError as exc:
                logger.warning(SYNC_ERROR, exc_info=True)
                file_name = os.path.basename(exc.dbx_path)
                self.notify.send("Could not sync {0}".format(file_name))
                if exc.dbx_path is not None:
                    if exc.local_path is None:
                        exc.local_path = self.to_local_path(exc.dbx_path)
                    if sync_errors:
                        sync_errors.put(exc)
                    if failed_items:
                        failed_items.put(args[0])
                res = False

            return res

        return wrapper

    return decorator


class InQueue(object):
    """
    A context manager that puts `name` into `custom_queue` when entering the context and
    removes it when exiting, after an optional delay.
    """
    def __init__(self, name, custom_queue, delay=0):
        """
        :param str name: Item to put in queue.
        :param custom_queue: Instance of :class:`queue.Queue`.
        :param float delay: Delay before removing item from queue. Defaults to 0.
        """
        self.name = name
        self.custom_queue = custom_queue
        self._delay = delay

    def __enter__(self):
        self.custom_queue.put(self.name)

    def __exit__(self, err_type, err_value, err_traceback):
        time.sleep(self._delay)
        remove_from_queue(self.custom_queue, self.name)


class UpDownSync(object):
    """
    Class that contains methods to sync local file events with Dropbox and vice versa.

    :param client: MaestralApiClient client instance.
    :param local_file_event_queue: Queue with local file-changed events.
    :param queue_uploading: Queue with files currently being uploaded.
    :param queue_downloading: Queue with files currently being downloaded.
    """

    _dropbox_path = CONF.get("main", "path")

    notify = Notipy()
    lock = RLock()

    _rev_lock = RLock()

    failed_uploads = queue.Queue()
    failed_downloads = queue.Queue()
    sync_errors = queue.Queue()

    queued_for_download = queue.Queue()
    queued_for_upload = queue.Queue()

    def __init__(self, client, local_file_event_queue, queue_uploading, queue_downloading):

        self.client = client
        self.local_file_event_queue = local_file_event_queue
        self.queue_uploading = queue_uploading
        self.queue_downloading = queue_downloading

        # migrate rev file
        self._migrate_rev_file()

        # load cached properties
        self._dropbox_path = CONF.get("main", "path")
        self._excluded_files = CONF.get("main", "excluded_files")
        self._excluded_folders = CONF.get("main", "excluded_folders")
        self._rev_dict_cache = self._load_rev_dict_from_file()

    def _migrate_rev_file(self):
        if os.path.isfile(self._old_rev_file_path):
            shutil.copyfile(self._old_rev_file_path, self.rev_file_path)
            os.remove(self._old_rev_file_path)

    @property
    def _old_rev_file_path(self):
        """Path to file with revision index (read only)."""
        return osp.join(self.dropbox_path, OLD_REV_FILE)

    @property
    def rev_file_path(self):
        """Path to file with revision index (read only). This will a hidden file
        '.maestral' in the user's Dropbox directory."""
        return osp.join(self.dropbox_path, REV_FILE)

    @property
    def dropbox_path(self):
        """Path to local Dropbox folder, as loaded from the config file. Before changing
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
        the config file on every successful sync. Do not modify manually."""
        return CONF.get("internal", "cursor")

    @last_cursor.setter
    def last_cursor(self, cursor):
        """Setter: last_cursor"""
        logger.debug("Remote cursor saved: {}".format(cursor))
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
        logger.debug("Local cursor saved: {}".format(last_sync))
        CONF.set("internal", "lastsync", last_sync)

    @property
    def excluded_files(self):
        """List containing all files excluded from sync. Changes are saved to the
        config file."""
        return self._excluded_files

    @excluded_files.setter
    def excluded_files(self, files_list):
        """Setter: excluded_folders"""
        self._excluded_files = files_list
        CONF.set("main", "excluded_files", files_list)

    @property
    def excluded_folders(self):
        """List containing all folders excluded from sync. Changes are saved to the
        config file. If a parent folder is excluded, its children will automatically be
        removed from the list. If only children are given but not the parent folder,
        any new items added to the parent will be synced."""
        return self._excluded_folders

    @excluded_folders.setter
    def excluded_folders(self, folder_list):
        """Setter: excluded_folders"""
        clean_list = self.clean_excluded_folder_list(folder_list)
        self._excluded_folders = clean_list
        CONF.set("main", "excluded_folders", clean_list)

    # ====================================================================================
    #  Helper functions
    # ====================================================================================

    @staticmethod
    def clean_excluded_folder_list(folder_list):
        """Removes all duplicates from the excluded folder list."""

        # remove duplicate entries by creating set, strip trailing "/"
        folder_list = set(f.lower().rstrip(osp.sep) for f in folder_list)

        # remove all children of excluded folders
        clean_folders_list = list(folder_list)
        for folder in folder_list:
            clean_folders_list = [f for f in clean_folders_list if not is_child(f, folder)]

        return clean_folders_list

    def ensure_dropbox_folder_present(self):
        """
        Checks if the Dropbox folder still exists where we expect it to be.

        :raises: DropboxDeletedError
        """

        if not osp.isdir(self.dropbox_path):
            raise DropboxDeletedError("Dropbox folder has been moved or deleted.")

    def to_dbx_path(self, local_path):
        """
        Converts a local path to a path relative to the Dropbox folder.

        :param str local_path: Full path to file in local Dropbox folder.
        :return: Relative path with respect to Dropbox folder.
        :rtype: str
        :raises: ValueError if no path is specified or path is outside of local
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
        Converts a Dropbox path to the corresponding local path.

        The `path_display` attribute returned by the Dropbox API only
        guarantees correct casing of the basename (file name or folder name)
        and not of the full path. This is because Dropbox itself is not case
        sensitive and stores all paths in lowercase internally.

        Therefore, if the parent directory is already present on the local
        drive, it's casing is used. Otherwise, the casing given by the Dropbox
        API metadata is used. This aims to preserve the correct casing of file and
        folder names and prevents the creation of duplicate folders with different
        casing on the local drive.

        :param str dbx_path: Path to file relative to Dropbox folder.
        :return: Corresponding local path on drive.
        :rtype: str
        :raises: ValueError if no path is specified.
        """

        dbx_path = dbx_path.replace("/", osp.sep)
        dbx_path_parent, dbx_path_basename,  = osp.split(dbx_path)

        local_parent = path_exists_case_insensitive(dbx_path_parent, self.dropbox_path)

        if local_parent == "":
            return osp.join(self.dropbox_path, dbx_path.lstrip(osp.sep))
        else:
            return osp.join(local_parent, dbx_path_basename)

    def _load_rev_dict_from_file(self, raise_exception=False):
        """
        Attempts to load Maestral's rev index from `rev_file_path`. The rev file will be
        loaded using u-msgpack.

        :param bool raise_exception: If ``True``, raises an exception when loading fails.
            If ``False``, no exception is raised but an error message with exc_info is
            logged.
        :raises: RevFileError, PermissionError, OSError
        """
        rev_dict_cache = dict()
        with self._rev_lock:
            try:
                with open(self.rev_file_path, "rb") as f:
                    rev_dict_cache = umsgpack.unpack(f)
                assert isinstance(rev_dict_cache, dict)
                assert all(isinstance(key, str) for key in rev_dict_cache.keys())
                assert all(isinstance(val, str) for val in rev_dict_cache.values())
            except (FileNotFoundError, IsADirectoryError):
                logger.warning("Maestral index could not be found.")
            except (AssertionError, umsgpack.InsufficientDataException) as exc:
                msg = "Maestral index has become corrupted. Please rebuild."
                new_exc = RevFileError(msg).with_traceback(exc.__traceback__)
                if raise_exception:
                    raise new_exc
                else:
                    exc_info = (type(new_exc), new_exc, new_exc.__traceback__)
                    logger.error(msg, exc_info=exc_info)
            except PermissionError as exc:
                msg = ("Insufficient permissions for Dropbox folder. Please " +
                       "make sure that you have read and write permissions.")
                new_exc = RevFileError(msg).with_traceback(exc.__traceback__)
                if raise_exception:
                    raise RevFileError(msg)
                else:
                    exc_info = (type(new_exc), new_exc, new_exc.__traceback__)
                    logger.error(msg, exc_info=exc_info)
            except OSError as exc:
                if raise_exception:
                    raise exc
                else:
                    logger.error("Could not load revision index.", exc_info=True)

            return rev_dict_cache

    def _save_rev_dict_to_file(self, raise_exception=False):
        """
        Attempts to save Maestral's rev index to `rev_file_path`. The rev file will be
        saved using u-msgpack.

        :param bool raise_exception: If ``True``, raises an exception when saving fails.
            Defaults to ``False``.
        :raises: PermissionError, OSError
        """
        with self._rev_lock:
            try:
                with open(self.rev_file_path, "w+b") as f:
                    umsgpack.pack(self._rev_dict_cache, f)
            except PermissionError as exc:
                msg = ("Insufficient permissions for Dropbox folder. Please " +
                       "make sure that you have read and write permissions.")
                new_exc = RevFileError(msg).with_traceback(exc.__traceback__)
                if raise_exception:
                    raise new_exc
                else:
                    exc_info = (type(new_exc), new_exc, new_exc.__traceback__)
                    logger.error(msg, exc_info=exc_info)
            except OSError as exc:
                if raise_exception:
                    raise exc
                else:
                    logger.error("Could not save revision index.", exc_info=True)

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

            if rev == self._rev_dict_cache.get(dbx_path, None):
                # rev is already set, nothing to do
                return

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
                while dirname != "/":
                    self._rev_dict_cache[dirname] = "folder"
                    dirname = osp.dirname(dirname)

            # save changes to file
            self._save_rev_dict_to_file()

    def has_sync_errors(self):
        """Returns ``True`` in case of sync errors, ``False`` otherwise."""
        return self.sync_errors.qsize() > 0

    def clear_sync_error(self, local_path=None, dbx_path=None):
        """
        Clears all sync errors related to the item defined by :param:`local_path`
        or :param:`local_path.

        :param str local_path: Path to local file.
        :param str dbx_path: Path to file on Dropbox.
        """
        assert local_path or dbx_path
        if self.has_sync_errors():
            if not dbx_path:
                dbx_path = self.to_dbx_path(local_path)
            for error in list(self.sync_errors.queue):
                if error.dbx_path.lower() == dbx_path.lower():
                    remove_from_queue(self.sync_errors, error)

    def clear_all_sync_errors(self):
        """Clears all sync errors."""
        if self.has_sync_errors():
            with self.sync_errors.mutex:
                self.sync_errors.queue.clear()

    @staticmethod
    def is_excluded(dbx_path):
        """
        Check if file is excluded from sync.

        :param str dbx_path: Path of folder on Dropbox.
        :return: ``True`` or `False`.
        :rtype: bool
        """
        dbx_path = dbx_path.lower()

        # is root folder?
        if dbx_path in ["/", ""]:
            return True

        # information about excluded files:
        # https://help.dropbox.com/installs-integrations/sync-uploads/files-not-syncing

        basename = osp.basename(dbx_path)

        # in excluded files?
        test0 = basename in ["desktop.ini",  "thumbs.db", ".ds_store", "icon\r",
                             ".dropbox.attr", OLD_REV_FILE, REV_FILE]

        # is temporary file?
        # 1) macOS autosave files
        test1 = basename.count(".") > 1 and osp.splitext(basename)[-1].startswith(".sb-")
        # 2) office temporary files
        test2 = basename.startswith("~$")
        test3 = basename.startswith(".~")
        # 3) other temporary files
        test4 = basename.startswith("~") and basename.endswith(".tmp")

        return any((test0, test1, test2, test3, test4))

    def is_excluded_by_user(self, dbx_path):
        """
        Check if file has been excluded from sync by the user.

        :param str dbx_path: Path of folder on Dropbox.
        :return: ``True`` or `False`.
        :rtype: bool
        """
        dbx_path = dbx_path.lower()

        # in excluded files?
        test0 = dbx_path in self.excluded_files
        # in excluded folders?
        test1 = any(dbx_path == f or is_child(dbx_path, f) for f in self.excluded_folders)

        return any((test0, test1))

    # ====================================================================================
    #  Upload sync
    # ====================================================================================

    def get_local_changes_while_inactive(self):
        """
        Collects changes while sync has not been running and puts them in the
        `queue_upload`. Only file which occurred before calling this method will be
        returned.
        """

        logger.info("Indexing...")

        try:
            events = self._get_local_changes_while_inactive()
        except FileNotFoundError:
            self.ensure_dropbox_folder_present()
            return

        # queue changes for upload
        for event in events:
            self.local_file_event_queue.put(event)

        logger.info(IDLE)

    def _get_local_changes_while_inactive(self):
        """
        Gets all local changes while app has not been running. Call this method
        on startup of `MaestralMonitor` to upload all local changes.

        :return: Dictionary with all changes, keys are file paths relative to
            local Dropbox folder, entries are watchdog file changed events.
        :rtype: dict
        """

        now = time.time()

        changes = []
        snapshot = DirectorySnapshot(self.dropbox_path)
        # remove root entry from snapshot
        del snapshot._inode_to_path[snapshot.inode(self.dropbox_path)]
        del snapshot._stat_info[self.dropbox_path]
        # get lowercase paths
        lowercase_snapshot_paths = {x.lower() for x in snapshot.paths}

        # get modified or added items
        for path in snapshot.paths:
            stats = snapshot.stat_info(path)
            last_sync = CONF.get("internal", "lastsync")
            # check if item was created or modified since last sync
            dbx_path = self.to_dbx_path(path).lower()

            is_new = (self.get_local_rev(dbx_path) is None and
                      not self.is_excluded(dbx_path))
            is_modified = (self.get_local_rev(dbx_path) and
                           now > max(stats.st_ctime, stats.st_mtime) > last_sync)

            if is_new:
                if snapshot.isdir(path):
                    event = DirCreatedEvent(path)
                else:
                    event = FileCreatedEvent(path)
                changes.append(event)
            elif is_modified:
                if snapshot.isdir(path):
                    event = DirModifiedEvent(path)
                else:
                    event = FileModifiedEvent(path)
                changes.append(event)

        # get deleted items
        rev_dict_copy = self.get_rev_dict()
        for path in rev_dict_copy:
            if self.to_local_path(path).lower() not in lowercase_snapshot_paths:
                if rev_dict_copy[path] == "folder":
                    event = DirDeletedEvent(self.to_local_path(path))
                else:
                    event = FileDeletedEvent(self.to_local_path(path))
                changes.append(event)

        return changes

    def wait_for_local_changes(self, timeout=2, delay=0.5):
        """
        Waits for local file changes. Returns a list of local changes, filtered to
        avoid duplicates.

        :param float timeout: If no changes are detected within timeout (sec), an empty
            list is returned.
        :param delay: Delay in sec to wait for subsequent changes that may be duplicates.
        :return: (list of file events, time_stamp)
        :rtype: (list, float)
        """
        self.ensure_dropbox_folder_present()
        try:
            events = [self.local_file_event_queue.get(timeout=timeout)]
        except queue.Empty:
            return [], time.time()

        # keep collecting events until no more changes happen for at least `delay` sec
        t0 = time.time()
        has_more = True
        while has_more:
            try:
                events.append(self.local_file_event_queue.get(timeout=delay))
            except queue.Empty:
                has_more = False
                t0 = time.time()

        # save timestamp
        local_cursor = t0

        logger.debug("***************** Original events ************************")
        logger.debug(events)
        logger.debug("**********************************************************")

        # REMOVE DIR_MODIFIED_EVENTS
        events = [e for e in events if not isinstance(e, DirModifiedEvent)]

        # COMBINE "MOVED" EVENTS OF FOLDERS AND THEIR CHILDREN INTO ONE EVENT
        moved_folder_events = [x for x in events if self._is_moved_folder(x)]
        child_move_events = []

        for parent_event in moved_folder_events:
            children = [x for x in events if self._is_moved_child(x, parent_event)]
            child_move_events += children

        events = self._list_diff(events, child_move_events)

        # COMBINE "DELETED" EVENTS OF FOLDERS AND THEIR CHILDREN INTO ONE EVENT
        deleted_folder_events = [x for x in events if self._is_deleted_folder(x)]
        child_deleted_events = []

        for parent_event in deleted_folder_events:
            children = [x for x in events if self._is_deleted_child(x, parent_event)]
            child_deleted_events += children

        events = self._list_diff(events, child_deleted_events)

        # COMBINE "CREATED" AND "MODIFIED" EVENTS OF THE SAME FILE TO "CREATED"
        created_file_events = [x for x in events if self._is_created(x)]
        duplicate_modified_events = []

        for event in created_file_events:
            duplicates = [x for x in events if self._is_modified_duplicate(x, event)]
            duplicate_modified_events += duplicates

        # remove all events with duplicate effects
        events = self._list_diff(events, duplicate_modified_events)

        # REMOVE SUBSEQUENT "CREATED" AND "DELETED" EVENTS OF THE SAME FILE
        to_remove = []

        for event in created_file_events:
            subsequent_delete = self._get_subsequent_deleted_event(event, events)
            if subsequent_delete is not None:
                to_remove.append(event)
                to_remove.append(subsequent_delete)

        events = self._list_diff(events, to_remove)

        # COMBINE SUBSEQUENT "DELETED" AND "CREATED" EVENTS TO "MODIFIED"
        deleted_file_events = [x for x in events if isinstance(x, FileDeletedEvent)]
        to_remove = []
        to_add = []

        for event in deleted_file_events:
            subsequent_create = self._get_subsequent_created_event(event, events)
            if subsequent_create is not None:
                to_remove.append(event)
                to_remove.append(subsequent_create)

                modified_event = FileModifiedEvent(event.src_path)
                to_add.append(modified_event)

        events = self._list_diff(events, to_remove)
        events += to_add

        logger.debug("******************* Cleaned up events ********************")
        logger.debug(events)
        logger.debug("**********************************************************")

        return events, local_cursor

    def filter_excluded_changes_local(self, events):

        events_filtered = []
        events_excluded = []

        for event in events:

            local_path = getattr(event, "dest_path", event.src_path)
            dbx_path = self.to_dbx_path(local_path)

            if self.is_excluded(dbx_path):  # is excluded?
                events_excluded.append(event)
            elif self.is_excluded_by_user(dbx_path):  # is excluded by user?
                if event.event_type is EVENT_TYPE_DELETED:
                    self.clear_sync_error(local_path, dbx_path)
                else:
                    title = "Could not upload"
                    message = ("Another item with the same name already exists on " +
                               "Dropbox but is excluded from syncing.")
                    exc = ExcludedItemError(title, message, dbx_path=dbx_path,
                                            local_path=local_path)
                    logger.warning(SYNC_ERROR, exc_info=(type(exc), exc, None))
                    self.sync_errors.put(exc)
                    self.failed_uploads.put(event)
                events_excluded.append(event)
            else:
                events_filtered.append(event)

        return events_filtered, events_excluded

    @staticmethod
    def _sort_local_events(events):
        """
        Sorts local file events into DirEvents and FileEvents.

        :return: Tuple of (folders, files)
        :rtype: tuple
        """

        folders = [x for x in events if isinstance(x, (DirCreatedEvent, DirMovedEvent,
                                                       DirDeletedEvent))]
        files = [x for x in events if x not in folders]

        return folders, files

    def apply_local_changes(self, events, local_cursor):
        """
        Applies locally detected events to remote Dropbox.

        :param list events: List of local file changes.
        :param float local_cursor: Time stamp of last event in `events`.
        :return: ``True`` if all changes have been uploaded successfully, ``False``
            otherwise.
        :rtype: bool
        """

        filtered_events, _ = self.filter_excluded_changes_local(events)
        dir_events, file_events = self._sort_local_events(filtered_events)

        # update queues
        for e in filtered_events:
            self.queued_for_upload.put(getattr(e, "dest_path", e.src_path))

        # apply directory events first (the do not require any upload)
        for event in dir_events:
            self._apply_event(event)

        # apply file events in parallel
        num_threads = os.cpu_count() * 2
        success = []
        last_emit = time.time()
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            fs = [executor.submit(self._apply_event, e) for e in file_events]
            n_files = len(file_events)
            for (f, n) in zip(as_completed(fs), range(1, n_files+1)):
                if time.time() - last_emit > 1 or n in (1, n_files):
                    # emit message at maximum every second
                    logger.info("Uploading {0}/{1}...".format(n, n_files))
                    last_emit = time.time()
                success += [f.result()]

        if all(success):
            self.last_sync = local_cursor  # save local cursor
            return True
        else:
            return False

    @staticmethod
    def _list_diff(list1, list2):
        """
        Subtracts elements of `list2` from `list1` while preserving the order of
        list1.

        :param list list1: List to subtract from.
        :param list list2: List of elements to subtract.
        :returns: Subtracted list.
        :rtype: list
        """
        return [l for l in list1 if l not in list2]

    @staticmethod
    def _is_moved_folder(x):
        """Check for moved folders"""
        is_moved_event = (x.event_type is EVENT_TYPE_MOVED)
        return is_moved_event and x.is_directory

    @staticmethod
    def _is_moved_child(x, parent):
        """Check for children of moved folders"""
        is_moved_event = (x.event_type is EVENT_TYPE_MOVED)
        return is_moved_event and is_child(x.src_path, parent.src_path)

    @staticmethod
    def _is_deleted_folder(x):
        """Check for deleted folders"""
        is_deleted_event = (x.event_type is EVENT_TYPE_DELETED)
        return is_deleted_event and x.is_directory

    @staticmethod
    def _is_deleted_child(x, parent):
        """Check for children of deleted folders"""
        is_deleted_event = (x.event_type is EVENT_TYPE_DELETED)
        return is_deleted_event and is_child(x.src_path, parent.src_path)

    @staticmethod
    def _is_tmp_file(x, events):
        """Check if file has already been deleted."""
        is_created_event = (x.event_type is EVENT_TYPE_CREATED)
        has_subsequent_deleted_event = (e.src_path == x.src_path for e in events)
        return is_created_event and has_subsequent_deleted_event

    @staticmethod
    def _is_created(x):
        """Check for created items."""
        return x.event_type is EVENT_TYPE_CREATED

    @staticmethod
    def _is_modified_duplicate(x, original):
        """Check for modified items that have just been created"""
        is_modified_event = (x.event_type is EVENT_TYPE_MODIFIED)
        is_duplicate = (x.src_path == original.src_path)
        return is_modified_event and is_duplicate

    @staticmethod
    def _get_subsequent_deleted_event(event, all_events):
        """Get any subsequent deleted event of the same item following `event`."""
        return next((x for x in all_events if x.src_path == event.src_path and
                     all_events.index(x) > all_events.index(event) and
                     isinstance(x, FileDeletedEvent)), None)

    @staticmethod
    def _get_subsequent_created_event(event, all_events):
        """Get any subsequent created event of the same item following `event`."""
        return next((x for x in all_events if x.src_path == event.src_path and
                     all_events.index(x) > all_events.index(event) and
                     isinstance(x, FileCreatedEvent)), None)

    @catch_sync_issues(sync_errors, failed_uploads)
    def _apply_event(self, event):
        """Apply a local file event `event` to the remote Dropbox. Clear any related
        sync errors with the file. Any new MaestralApiErrors will be caught by the
        decorator."""

        local_path = getattr(event, "dest_path", event.src_path)
        remove_from_queue(self.queued_for_upload, local_path)
        self.clear_sync_error(local_path=local_path)

        with InQueue(local_path, self.queue_uploading):
            # apply event
            if event.event_type is EVENT_TYPE_CREATED:
                self._on_created(event)
            elif event.event_type is EVENT_TYPE_MOVED:
                self._on_moved(event)
            elif event.event_type is EVENT_TYPE_MODIFIED:
                self._on_modified(event)
            elif event.event_type is EVENT_TYPE_DELETED:
                self._on_deleted(event)

    @staticmethod
    def _wait_for_creation(path):
        """
        Wait for a file at a path to be created or modified.
        :param str path: absolute path to file
        """
        try:
            while True:
                size1 = osp.getsize(path)
                time.sleep(0.5)
                size2 = osp.getsize(path)
                if size1 == size2:
                    return
        except OSError:
            return

    def _on_moved(self, event):
        """
        Call when local file is moved.

        :param event: Watchdog file event.
        :raises: MaestralApiError on failure.
        """

        dbx_path_old = self.to_dbx_path(event.src_path)
        dbx_path_new = self.to_dbx_path(event.dest_path)

        # do items exist on Dropbox?
        md_old = self.client.get_metadata(dbx_path_old)

        if not md_old:
            # If not on Dropbox, e.g., because its old name was invalid,
            # create it instead of moving it.
            if isinstance(event, DirMovedEvent):
                new_event = DirCreatedEvent(event.dest_path)
            else:
                new_event = FileCreatedEvent(event.dest_path)
            self._on_created(new_event)
            # remove old revs
            self.set_local_rev(dbx_path_old, None)
            return
        else:
            # otherwise, just move it
            md = self.client.move(dbx_path_old, dbx_path_new)
            # remove old revs
            self.set_local_rev(dbx_path_old, None)

        # add new revs
        if isinstance(md, dropbox.files.FileMetadata):
            self.set_local_rev(md.path_display, md.rev)
        # and revs of children if folder
        elif isinstance(md, dropbox.files.FolderMetadata):
            self.set_local_rev(md.path_display, "folder")
            result = self.client.list_folder(dbx_path_new, recursive=True)
            for md in result.entries:
                if isinstance(md, dropbox.files.FileMetadata):
                    self.set_local_rev(md.path_display, md.rev)
                elif isinstance(md, dropbox.files.FolderMetadata):
                    self.set_local_rev(md.path_display, "folder")

        logger.debug("Moved '%s' to '%s' on Dropbox.", event.src_path, event.dest_path)

    def _on_created(self, event):
        """
        Call when local file is created.

        :param class event: Watchdog file event.
        :raises: MaestralApiError on failure.
        """

        path = event.src_path
        dbx_path = self.to_dbx_path(path)

        if event.is_directory:
            # check if directory is not yet on Dropbox, else leave alone
            md = self.client.get_metadata(dbx_path)
            if not md:
                md = self.client.make_dir(dbx_path)

            # save or update revision metadata
            self.set_local_rev(md.path_display, "folder")

        elif not event.is_directory:

            UpDownSync._wait_for_creation(path)

            # check if file already exists with identical content
            md = self.client.get_metadata(dbx_path)
            if md:
                local_hash = get_local_hash(path)
                if local_hash == md.content_hash:
                    # file hashes are identical, do not upload
                    self.set_local_rev(md.path_display, "folder")
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

        logger.debug("Created '%s' on Dropbox.", event.src_path)

    def _on_deleted(self, event):
        """
        Call when local file is deleted.

        :param class event: Watchdog file event.
        :raises: MaestralApiError on failure.
        """

        path = event.src_path
        dbx_path = self.to_dbx_path(path)

        try:
            self.client.remove(dbx_path)
        except PathError:
            logger.debug("Could not delete '{0}': the item does not exist on Dropbox.",
                         event.src_path)
        else:
            logger.debug("Deleted '%s' from Dropbox.", event.src_path)

        # remove revision metadata
        self.set_local_rev(dbx_path, None)

    def _on_modified(self, event):
        """
        Call when local file is modified.

        :param class event: Watchdog file event.
        :raises: MaestralApiError on failure.
        """

        path = event.src_path
        dbx_path = self.to_dbx_path(path)

        if not event.is_directory:  # ignore directory modified events

            UpDownSync._wait_for_creation(path)

            # check if file already exists with identical content
            md = self.client.get_metadata(dbx_path)
            if md:
                local_hash = get_local_hash(path)
                if local_hash == md.content_hash:
                    # file hashes are identical, do not upload
                    self.set_local_rev(md.path_display, md.rev)
                    logger.debug("Modification of '%s' detected but file content is "
                                 "the same as on Dropbox.", event.src_path)
                    return

            rev = self.get_local_rev(dbx_path)
            if rev == "folder":
                mode = dropbox.files.WriteMode("overwrite")
            else:
                mode = dropbox.files.WriteMode("update", rev)
            md = self.client.upload(path, dbx_path, autorename=True, mode=mode)
            logger.debug("Modified file: %s (old rev: %s, new rev %s)",
                         md.path_display, rev, md.rev)
            # save or update revision metadata
            self.set_local_rev(md.path_display, md.rev)

            logger.debug("Uploaded modified '%s' to Dropbox.", event.src_path)

    # ====================================================================================
    #  Download sync
    # ====================================================================================

    @catch_sync_issues(sync_errors)
    def get_remote_dropbox(self, dbx_path="", ignore_excluded=True):
        """
        Gets all files/folders from Dropbox and writes them to the local folder
        :ivar:`dropbox_path`. Call this method on first run of the Maestral. Indexing
        and downloading may take several minutes, depending on the size of the user's
        Dropbox folder.

        :param str dbx_path: Path to Dropbox folder. Defaults to root ("").
        :param bool ignore_excluded: If ``True``, do not index excluded folders.
        :return: ``True`` on success, ``False`` otherwise.
        :rtype: bool
        """

        is_dbx_root = (dbx_path == "")
        success = []

        if not any(folder.startswith(dbx_path) for folder in self.excluded_folders):
            # if there are no excluded subfolders, index and download all at once
            ignore_excluded = False

        cursor = self.client.get_latest_cursor(dbx_path)  # get a global cursor

        logger.info("Indexing...")
        root_result = self.client.list_folder(dbx_path, recursive=(not ignore_excluded),
                                              include_deleted=False, limit=500)

        # download top-level folders / files first
        logger.info("Syncing...")
        success.append(self.apply_remote_changes(root_result, save_cursor=False))

        if ignore_excluded:
            # download sub-folders if not excluded
            for entry in root_result.entries:
                if isinstance(entry, FolderMetadata) and not self.is_excluded_by_user(
                        entry.path_display):
                    success.append(self.get_remote_dropbox(entry.path_display))

        if all(success) and is_dbx_root:
            self.last_cursor = cursor

        logger.info("Up to date")

        return all(success)

    @catch_sync_issues()
    def wait_for_remote_changes(self, last_cursor, timeout=40):
        """Wraps MaestralApiClient.wait_for_remote_changes and catches sync errors."""
        return self.client.wait_for_remote_changes(last_cursor, timeout=timeout)

    @catch_sync_issues()
    def list_remote_changes(self, last_cursor):
        """Wraps MaestralApiClient.list_remove_changes and catches sync errors."""
        return self.client.list_remote_changes(last_cursor)

    def filter_excluded_changes_remote(self, changes):
        """Removes all excluded items from the given list of changes.

        :param changes: :class:`dropbox.files.ListFolderResult` instance.
        :return: (changes_filtered, changes_discarded)
        :rtype: tuple[:class:`dropbox.files.ListFolderResult`]
        """
        # filter changes from non-excluded folders
        entries_filtered = [e for e in changes.entries if not self.is_excluded_by_user(
            e.path_lower) or self.is_excluded(e.path_lower)]
        entries_discarded = list(set(changes.entries) - set(entries_filtered))

        changes_filtered = dropbox.files.ListFolderResult(
            entries=entries_filtered, cursor=changes.cursor, has_more=False)
        changes_discarded = dropbox.files.ListFolderResult(
            entries=entries_discarded, cursor=changes.cursor, has_more=False)

        return changes_filtered, changes_discarded

    def apply_remote_changes(self, changes, save_cursor=True):
        """
        Applies remote changes to local folder. Call this on the result of
        :method:`list_remote_changes`. The saved cursor is updated after a set
        of changes has been successfully applied.

        :param changes: :class:`dropbox.files.ListFolderResult` instance
            or ``False`` if requests failed.
        :param bool save_cursor: If True, :ivar:`last_cursor` will be updated
            from the last applied changes. Take care to only save a "global" and
            "recursive" cursor which represents the state of the entire Dropbox
        :return: ``True`` on success, ``False`` otherwise.
        :rtype: bool
        """

        if not changes:
            return False

        # filter out excluded changes
        changes_filtered, changes_excluded = self.filter_excluded_changes_remote(changes)

        # update queue
        for md in changes_filtered.entries:
            self.queued_for_download.put(self.to_local_path(md.path_display))

        # remove all deleted items from the excluded list
        _, _, deleted_excluded = self._sort_remote_entries(changes_excluded)
        for d in deleted_excluded:
            new_excluded = [f for f in self.excluded_folders if not f.startswith(d.path_lower)]
            self.excluded_folders = new_excluded

        # sort changes into folders, files and deleted
        folders, files, deleted = self._sort_remote_entries(changes_filtered)

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
        last_emit = time.time()
        with ThreadPoolExecutor(max_workers=15) as executor:
            fs = [executor.submit(self._create_local_entry, file) for file in files]
            for (f, n) in zip(as_completed(fs), range(1, n_files+1)):
                if time.time() - last_emit > 1 or n in (1, n_files):
                    # emit messages at maximum every second
                    logger.info("Downloading {0}/{1}...".format(n, n_files))
                    last_emit = time.time()
                success += [f.result()]

        time.sleep(2)
        with self.queue_downloading.mutex:
            self.queue_downloading.queue.clear()

        if not all(success):
            return False

        # save cursor
        if save_cursor:
            self.last_cursor = changes.cursor

        return True

    def check_download_conflict(self, dbx_path):
        """
        Check if local file is conflicting with remote file. The equivalent check when
        uploading ("check_upload_conflict") will be carried out by Dropbox itself.

        :param str dbx_path: Path of folder on Dropbox.
        :return: 0 for no conflict, 1 for conflict, 2 if files are identical.
        :rtype: int
        :raises: MaestralApiError if the Dropbox item does not exist.
        """
        # get corresponding local path
        local_path = self.to_local_path(dbx_path)

        # get metadata of remote file
        md = self.client.get_metadata(dbx_path)
        if not md:
            raise PathError(
                "Could not download file",
                "The file no longer exist on Dropbox",
                dbx_path=dbx_path
            )

        # no conflict if local file does not exist yet
        if not osp.exists(local_path):
            logger.debug("Local file '%s' does not exist. No conflict.", dbx_path)
            return 0

        local_rev = self.get_local_rev(dbx_path)

        if local_rev is None:
            # We likely have a conflict: files with the same name have been
            # created on Dropbox and locally independent of each other.
            # Check actual content first before declaring conflict!

            local_hash = get_local_hash(local_path)

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
        """Sends system notifications for files changed."""

        # get number of remote changes
        n_changed = len(changes.entries)

        if n_changed == 0:
            return

        # find out who changed the file(s), get the name if its only a single user
        try:
            dbid_list = [md.sharing_info.modified_by for md in changes.entries]
            if all(dbid == dbid_list[0] for dbid in dbid_list):
                # all files have been modified by the same user
                if dbid_list[0] == CONF.get("account", "account_id"):
                    user_name = "You"
                else:
                    account_info = self.client.get_account_info(dbid_list[0])
                    user_name = account_info.name.display_name
            else:
                user_name = None
        except AttributeError:
            user_name = None

        # notify user
        if n_changed == 1:
            # for a single change, display user name, file name and type of change
            md = changes.entries[0]
            file_name = os.path.basename(md.path_display)

            if isinstance(md, DeletedMetadata):
                # file has been deleted from remote
                change_type = "removed"
            elif isinstance(md, FileMetadata):
                if self.get_local_rev(md.path_lower):
                    is_new_file = False
                else:
                    revs = self.client.list_revisions(md.path_lower, limit=2)
                    is_new_file = len(revs.entries) == 1
                change_type = "added" if is_new_file else "changed"

            elif isinstance(md, FolderMetadata):
                change_type = "added"

            if user_name:
                self.notify.send("{0} {1} {2}".format(user_name, change_type, file_name))
            else:
                self.notify.send("{0} {1}".format(file_name, change_type))

        elif n_changed > 1:
            # for multiple changes, display user name if all equal
            if all(isinstance(x, DeletedMetadata) for x in changes.entries):
                change_type = "removed"
            else:
                change_type = "changed"
            if user_name:
                self.notify.send("{0} {1} {2} files".format(user_name, change_type, n_changed))
            else:
                self.notify.send("{0} files {1}".format(n_changed, change_type))

    @staticmethod
    def _sort_remote_entries(result):
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

    @catch_sync_issues(sync_errors, failed_downloads)
    def _create_local_entry(self, entry):
        """
        Creates local file / folder for remote entry.

        :param class entry: Dropbox FileMetadata|FolderMetadata|DeletedMetadata.
        :raises: MaestralApiError on failure.
        """

        local_path = self.to_local_path(entry.path_display)

        self.clear_sync_error(dbx_path=entry.path_display)
        remove_from_queue(self.queued_for_download, local_path)
        self.queue_downloading.put(local_path)  # will be removed by FileSystemEventHandler

        if isinstance(entry, FileMetadata):
            # Store the new entry at the given path in your local state.
            # If the required parent folders don’t exist yet, create them.
            # If there’s already something else at the given path,
            # replace it and remove all its children.

            self._save_to_history(entry.path_display)

            # check for sync conflicts
            conflict = self.check_download_conflict(entry.path_display)
            if conflict == 0:
                # no conflict
                pass
            elif conflict == 1:
                # conflict! rename local file
                base, ext = osp.splitext(local_path)
                new_local_file = base + " (conflicting copy)" + ext
                os.rename(local_path, new_local_file)
            elif conflict == 2:
                # Dropbox file corresponds to local file => nothing to do
                # rev number has been updated by `check_download_conflict`
                return

            md = self.client.download(entry.path_display, local_path)

            # save revision metadata
            self.set_local_rev(md.path_display, md.rev)

            logger.debug("Created local file '{0}'".format(entry.path_display))

        elif isinstance(entry, FolderMetadata):
            # Store the new entry at the given path in your local state.
            # If the required parent folders don’t exist yet, create them.
            # If there’s already something else at the given path,
            # replace it but leave the children as they are.

            os.makedirs(local_path, exist_ok=True)

            # save revision metadata
            self.set_local_rev(entry.path_display, "folder")

            logger.debug("Created local directory '{0}'".format(entry.path_display))

        elif isinstance(entry, DeletedMetadata):
            # If your local state has something at the given path,
            # remove it and all its children. If there’s nothing at the
            # given path, ignore this entry.

            success, err = delete_file_or_folder(local_path, return_error=True)
            if success:
                logger.debug("Deleted local item '{0}'".format(entry.path_display))
            else:
                logger.debug("FileNotFoundError: {0}".format(err))

            self.set_local_rev(entry.path_display, None)

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

def connection_helper(client, syncing, running, connected):
    """
    A worker which periodically checks the connection to Dropbox servers.
    This is done through inexpensive calls to :method:`client.get_space_usage`.
    If the connection is lost, ``connection_helper`` pauses all syncing until a
    connection can be reestablished.

    :param client: Maestral client instance.
    :param syncing: Event that indicates if workers are running or paused.
    :param running: Event to shutdown connection helper.
    :param connected: Event that indicates if a connection to Dropbox can be established.
    """

    disconnected_signal = signal("disconnected_signal")
    connected_signal = signal("connected_signal")
    account_usage_signal = signal("account_usage_signal")

    while running.is_set():
        try:
            # use an inexpensive call to get_space_usage to test connection
            res = client.get_space_usage()
            if not connected.is_set():
                connected.set()
                connected_signal.send()
            account_usage_signal.send(res)
            time.sleep(5)
        except CONNECTION_ERRORS:
            if connected.is_set():
                logger.debug(DISCONNECTED, exc_info=True)  # debug signal w/ traceback
                logger.info(DISCONNECTED)  # info signal w/o traceback
            syncing.clear()
            connected.clear()
            disconnected_signal.send()
            time.sleep(1)
        except DropboxAuthError as e:
            syncing.clear()  # stop syncing
            running.clear()  # shutdown threads
            logger.error("{0}: {1}".format(e.title, e.message), exc_info=True)


def download_worker(sync, syncing, running, connected):
    """
    Worker to sync changes of remote Dropbox with local folder. All files about
    to change are temporarily excluded from the local file monitor by adding
    their paths to the `queue_downloading`.

    :param UpDownSync sync: Instance of :class:`UpDownSync`.
    :param Event syncing: Event that indicates if workers are running or paused.
    :param Event running: Event to shutdown local file event handler and worker threads.
    :param Event connected: Event that indicates if a connection to Dropbox can be
        established.
    """

    disconnected_signal = signal("disconnected_signal")

    while running.is_set():

        syncing.wait()  # if not running, wait until resumed

        try:

            if not sync.last_cursor:
                # run the initial Dropbox download
                with sync.lock:
                    sync.get_remote_dropbox()
            else:
                # wait for remote changes (times out after 120 secs)
                has_changes = sync.wait_for_remote_changes(sync.last_cursor, timeout=120)

                syncing.wait()  # if not running, wait until resumed

                # apply remote changes
                if has_changes:
                    logger.info(SYNCING)
                    with sync.lock:
                        # get changes
                        changes = sync.list_remote_changes(sync.last_cursor)

                        # notify user about changes
                        if CONF.get("app", "notifications"):
                            sync.notify_user(changes)

                        # apply remote changes to local Dropbox folder
                        sync.apply_remote_changes(changes)

                        logger.info(IDLE)

        except CONNECTION_ERRORS:
            syncing.clear()
            connected.clear()
            disconnected_signal.send()
            logger.debug(DISCONNECTED, exc_info=True)
            logger.info(DISCONNECTED)
        except CursorResetError:
            syncing.clear()  # stop syncing
            running.clear()  # shutdown threads
            logger.error(SYNC_ERROR, exc_info=True)
        except Exception:
            logger.error("Unexpected error", exc_info=True)


def upload_worker(sync, syncing, running, connected):
    """
    Worker to sync local changes to remote Dropbox. It collects the most recent
    local file events from `local_q`, prunes them from duplicates, and
    processes the remaining events by calling methods of
    :class:`DropboxUploadSync`.

    :param sync: Instance of :class:`UpDownSync`.
    :param syncing: Event that indicates if workers are running or paused.
    :param running: Event to shutdown local file event handler and worker threads.
    :param connected: Event that indicates if a connection to Dropbox can be established.
    """

    disconnected_signal = signal("disconnected_signal")

    sync.get_local_changes_while_inactive()

    while running.is_set():

        try:
            if not syncing.is_set():
                syncing.wait()  # wait until resumed
                sync.get_local_changes_while_inactive()  # get changes while inactive

            # wait for local changes
            events, local_cursor = sync.wait_for_local_changes(timeout=2)

            if len(events) > 0:
                # apply changes
                with sync.lock:
                    logger.info(SYNCING)
                    sync.apply_local_changes(events, local_cursor)
                    logger.info(IDLE)
            else:
                # just update local cursor
                if syncing.is_set():
                    sync.last_sync = local_cursor
        except CONNECTION_ERRORS:
            syncing.clear()
            connected.clear()
            disconnected_signal.send()
            logger.debug(DISCONNECTED, exc_info=True)
            logger.info(DISCONNECTED)
        except DropboxDeletedError:
            syncing.clear()  # stop syncing
            running.clear()  # shutdown threads
            logger.error("Dropbox folder has been moved or deleted.", exc_info=True)
        except Exception:
            logger.error("Unexpected error", exc_info=True)


# ========================================================================================
# Main Monitor class to start, stop and coordinate threads
# ========================================================================================

class MaestralMonitor(object):
    """
    Class to sync changes between Dropbox and a local folder. It creates four
    threads: `observer` to catch local file events, `upload_thread` to upload
    caught changes to Dropbox, `download_thread` to query for and download
    remote changes, and `connection_thread` which periodically checks the
    connection to Dropbox servers.

    :ivar local_observer_thread: Watchdog observer thread that detects local file
        system events.
    :ivar upload_thread: Thread that sorts uploads local changes.
    :ivar download_thread: Thread to query for and download remote changes.
    :ivar file_handler: Handler to queue file events from `observer` for upload.
    :ivar sync: `UpDownSync` instance to coordinate syncing. This is the brain of
        Maestral. It contains the logic to process local and remote file events and to
        apply them while checking for conflicts.

    :ivar connected: Event that is set when connected to Dropbox servers.
    :ivar running: Event that is set when the threads are running.
    :ivar syncing: Event that is set when syncing is not paused.

    :cvar queue_downloading: Queue with *local file paths* that are being downloaded.
    :cvar queue_uploading: Queue with *local file paths* that are being uploaded.
    :cvar local_file_event_queue: Queue with *file events* to be uploaded.
    """

    queue_downloading = queue.Queue()
    queue_uploading = queue.Queue()

    local_file_event_queue = TimedQueue()

    connected_signal = signal("connected_signal")
    disconnected_signal = signal("disconnected_signal")
    account_usage_signal = signal("account_usage_signal")

    _auto_resume_on_connect = False

    def __init__(self, client):

        self.connected = Event()
        self.syncing = Event()
        self.running = Event()

        self.client = client
        self.file_handler = FileEventHandler(
            self.syncing, self.local_file_event_queue, self.queue_downloading)

        self.sync = UpDownSync(self.client, self.local_file_event_queue,
                               self.queue_uploading, self.queue_downloading)

    @property
    def uploading(self):
        """Returns a list of all items currently uploading."""
        return tuple(self.queue_uploading.queue)

    @property
    def downloading(self):
        """Returns a list of all items currently downloading."""
        return tuple(self.queue_downloading.queue)

    @property
    def queued_for_upload(self):
        """Returns a list of all items queued for upload."""
        return tuple(self.sync.queued_for_upload.queue)

    @property
    def queued_for_download(self):
        """Returns a list of all items queued for download."""
        return tuple(self.sync.queued_for_download.queue)

    def start(self, overload=None):
        """Creates observer threads and starts syncing."""

        self._auto_resume_on_connect = True

        if self.running.is_set() or self.syncing.is_set():
            # do nothing if already running
            return

        self.local_observer_thread = Observer()
        self.local_observer_thread.schedule(
            self.file_handler, self.sync.dropbox_path, recursive=True
        )

        self.connection_thread = Thread(
            target=connection_helper,
            daemon=True,
            args=(self.client, self.syncing, self.running, self.connected),
            name="Maestral connection helper"
        )

        self.download_thread = Thread(
            target=download_worker,
            daemon=True,
            args=(self.sync, self.syncing, self.running, self.connected),
            name="Maestral downloader"
        )

        self.upload_thread = Thread(
            target=upload_worker,
            daemon=True,
            args=(self.sync, self.syncing, self.running, self.connected),
            name="Maestral uploader"
        )

        self.running.set()

        self.connection_thread.start()
        self.local_observer_thread.start()
        self.download_thread.start()
        self.upload_thread.start()

        self.syncing.set()  # starts upload_thread and download_thread

        self.connected_signal.connect(self._resume_on_connect)
        self.disconnected_signal.connect(self._pause_on_disconnect)

        logger.info("Syncing started")
        logger.info(IDLE)

    def pause(self, overload=None):
        """Pauses syncing."""

        self._auto_resume_on_connect = False
        self._pause_on_disconnect()

        logger.info(PAUSED)

    def resume(self, overload=None):
        """Checks for changes while idle and starts syncing."""

        self._auto_resume_on_connect = True
        self._resume_on_connect()

    def _pause_on_disconnect(self, overload=None):
        """Pauses syncing."""
        self.syncing.clear()  # pauses upload_thread, download_thread and file handler

    def _resume_on_connect(self, overload=None):
        """Resumes syncing."""

        if self.syncing.is_set():
            logger.debug("Syncing was already running")
            return

        if not self._auto_resume_on_connect:
            return

        self.sync.clear_all_sync_errors()  # clear all previous sync errors
        self.syncing.set()  # resumes upload_thread, download_thread and file handler
        logger.info(IDLE)

    def stop(self, overload=None, blocking=False):
        """Stops syncing and destroys worker threads."""

        if not self.running.is_set():
            logger.debug("Syncing was already stopped")
            return

        self._auto_resume_on_connect = False

        logger.debug("Shutting down threads...")

        self.local_observer_thread.stop()  # stop observer
        self.local_observer_thread.join()  # wait to finish

        self.running.clear()  # stops our own threads

        if blocking:
            self.upload_thread.join()  # wait to finish (up to 2 sec)

        logger.info(STOPPED)

    def rebuild_rev_file(self):
        """
        Rebuilds the rev file by comparing remote with local files and updating rev
        numbers from the Dropbox server. Files are compared by their content hashes and
        reindexing may take several minutes, depending on the size of your Dropbox. If
        a file is modified during this process before it has been re-indexed,
        any changes to will be flagged as sync conflicts. If a file is deleted before
        it has been re-indexed, the deletion will be reversed.
        """

        logger.info("Rebuilding index...")

        was_running = self.running.is_set()
        was_paused = not self.syncing.is_set()

        self.stop(blocking=True)  # stop all sync threads and wait for them to return
        try:
            os.unlink(self.sync.rev_file_path)  # delete rev file
        except OSError:
            pass
        self.sync._rev_dict_cache = dict()

        # Re-download Dropbox from server. If a local file already exists, content hashes
        # are compared. If files are identical, the local rev will be set accordingly,
        # otherwise a conflicting copy will be created.

        completed = False
        while not completed:
            try:
                self.sync.get_remote_dropbox(ignore_excluded=False)
                completed = True
            except CONNECTION_ERRORS:
                logger.info(DISCONNECTED)

        self.sync.last_sync = time.time()

        # Resume syncing. This will upload all changes which occurred
        # while rebuilding, including conflicting copies. Files that were
        # deleted before re-indexing will be downloaded again. If restart==False,
        # this should be done manually.
        if was_running:
            self.start()
        if was_paused:
            self.pause()


# ========================================================================================
# Helper functions
# ========================================================================================

def path_exists_case_insensitive(path, root="/"):
    """
    Checks if a `path` exists in given `root` directory, similar to
    `os.path.exists` but case-insensitive. If there are multiple
    case-insensitive matches, the first one is returned. If there is no match,
    an empty string is returned.

    :param str path: Relative path of item to find in the `root` directory.
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


def is_child(path1, path2):
    """
    Checks if :param:`path1` semantically is inside folder :param:`path2`. Neither
    path must refer to an actual item on the drive. This function is case sensitive.

    :param str path1: Folder path.
    :param str path2: Parent folder path.
    :returns: ``True`` if :param:`path1` semantically is a subfolder of :param:`path2`,
        ``False`` otherwise (including ``path1 == path2``.
    :rtype: bool
    """
    assert isinstance(path1, str)
    assert isinstance(path2, str)

    path2.rstrip(osp.sep)

    return path1.startswith(path2 + osp.sep) and not path1 == path2


def get_local_hash(local_path):
    """
    Computes content hash of a local file.

    :param str local_path: Path to local file.
    :return: content hash to compare with ``content_hash`` attribute of
        :class:`dropbox.files.FileMetadata` object.
    """

    hasher = DropboxContentHasher()

    with open(local_path, 'rb') as f:
        while True:
            chunk = f.read(1024)
            if len(chunk) == 0:
                break
            hasher.update(chunk)

    return hasher.hexdigest()


def remove_from_queue(queue, item):
    """
    Tries to remove an item from a queue.

    :param Queue queue: Queue to remove item from.
    :param item: Item to remove
    """

    with queue.mutex:
        try:
            queue.queue.remove(item)
        except ValueError:
            pass
