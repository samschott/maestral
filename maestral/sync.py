# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
# system imports
import os
import os.path as osp
import logging
import time
from threading import Thread, Event, RLock
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue
from collections import OrderedDict
import functools
from enum import IntEnum

# external imports
import umsgpack
import dropbox
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata
from watchdog.events import FileSystemEventHandler
from watchdog.events import (EVENT_TYPE_CREATED, EVENT_TYPE_DELETED,
                             EVENT_TYPE_MODIFIED, EVENT_TYPE_MOVED)
from watchdog.events import (DirModifiedEvent, FileModifiedEvent, DirCreatedEvent,
                             FileCreatedEvent, DirDeletedEvent, FileDeletedEvent,
                             DirMovedEvent, FileMovedEvent)
from watchdog.utils.dirsnapshot import DirectorySnapshot
from atomicwrites import atomic_write

# local imports
from maestral.config import MaestralConfig, MaestralState, list_configs
from maestral.watchdog import Observer
from maestral.constants import (IDLE, SYNCING, PAUSED, STOPPED, DISCONNECTED,
                                REV_FILE, IS_FS_CASE_SENSITIVE)
from maestral.errors import (MaestralApiError, RevFileError, DropboxDeletedError,
                             DropboxAuthError, SyncError, ExcludedItemError,
                             PathError, InotifyError, NotFoundError)
from maestral.utils.content_hasher import DropboxContentHasher
from maestral.utils.notify import MaestralDesktopNotifier, FILECHANGE
from maestral.utils.path import is_child, path_exists_case_insensitive, delete
from maestral.utils.appdirs import get_data_path
from maestral.utils.housekeeping import migrate_maestral_index

logger = logging.getLogger(__name__)

for config_name in list_configs():
    migrate_maestral_index(config_name)

DIR_EVENTS = (DirModifiedEvent, DirCreatedEvent, DirDeletedEvent, DirMovedEvent)
FILE_EVENTS = (FileModifiedEvent, FileCreatedEvent, FileDeletedEvent, FileMovedEvent)

EXCLUDED_FILE_NAMES = (
    "desktop.ini", "thumbs.db", ".ds_store", "icon\r", ".dropbox.attr",
    ".com.apple.timemachine.supported", REV_FILE
)


# ========================================================================================
# Syncing functionality
# ========================================================================================

class Conflict(IntEnum):
    RemoteNewer = 0
    Conflict = 1
    Identical = 2
    LocalNewerOrIdentical = 2


class TimedQueue(queue.Queue):
    """
    A queue that remembers the time of the last put.

    :ivar update_time: Time of the last put.
    """

    __slots__ = ("update_time",)

    def __init__(self):
        super(self.__class__, self).__init__()
        self.update_time = 0.0

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

    __slots__ = (
        "syncing", "local_file_event_queue", "queue_downloading", "_renamed_items_cache"
    )

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
        :returns: ``True`` if the file is currently being downloaded, ``False`` otherwise.
        :rtype: bool
        """

        with self.queue_downloading.mutex:
            return any(local_path.lower() == p.lower() for p in self.queue_downloading.queue)

    # TODO: The logic for ignoring moved events of children will no longer work when
    #   renaming the parent's moved event. This will throw sync errors when trying to
    #   apply those events, but they are only temporary and therefore tolerable for now.
    def rename_on_case_conflict(self, event):
        """
        Checks for other items in the same directory with same name but a different case.
        Only needed for case sensitive file systems.

        :param event: Created or moved event.
        :returns: Modified event if conflict detected and file has been renamed, original
            event otherwise.
        """

        if not (event.event_type is EVENT_TYPE_CREATED or event.event_type is
                EVENT_TYPE_MOVED):
            return event

        # get the created items path (src_path or dest_path)
        created_path = _get_dest_path(event)

        # get all other items in the same directory
        try:
            parent_dir = osp.dirname(created_path)
            other_items = [osp.join(parent_dir, file) for file in os.listdir(parent_dir)]
            other_items.remove(created_path)
        except (FileNotFoundError, ValueError):
            # ValueError is raised when created_path is no longer in directory
            # FileNotFoundError is raised when directory no longer exists
            return event

        # check if we have any conflicting names with different cases
        if any(p.lower() == created_path.lower() for p in other_items):
            # try to find a unique new name of the form "(case conflict)"
            # or "(case conflict 1)"
            base, ext = osp.splitext(created_path)
            new_path = f"{base} (case conflict){ext}"
            i = 1
            while any(p.lower() == new_path.lower() for p in other_items):
                new_path = f"{base} (case conflict {i}){ext}"
                i += 1
            # rename newly created item
            self._renamed_items_cache.append(created_path)  # ignore temporarily
            os.rename(created_path, new_path)  # this will be picked up by watchdog
            logger.debug(f"Case conflict: renamed '{created_path}' to '{new_path}'")

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

        if not self.syncing.is_set():
            return

        # ignore files currently being downloaded
        if self.is_being_downloaded(event.src_path):
            return

        # rename target on case conflict
        if IS_FS_CASE_SENSITIVE:
            event = self.rename_on_case_conflict(event)

        # ignore files which have just been renamed
        if event.src_path in self._renamed_items_cache:
            self._renamed_items_cache.remove(event.src_path)
            return

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
            except SyncError as exc:
                file_name = os.path.basename(exc.dbx_path)
                logger.warning(f"Could not sync {file_name}", exc_info=True)
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


class InQueue:
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


class UpDownSync:
    """
    Class that contains methods to sync local file events with Dropbox and vice versa.

    :param client: MaestralApiClient client instance.
    :param local_file_event_queue: Queue with local file-changed events.
    :param queue_uploading: Queue with files currently being uploaded.
    :param queue_downloading: Queue with files currently being downloaded.

    :cvar failed_uploads: Queue with dbx file paths of failed uploads.
    :cvar failed_downloads: Queue with dbx file paths of failed downloads.
    :cvar sync_errors: Queue with full sync errors of all failed uploads / downloads.

    :cvar queued_folder_downloads: Queue with folders to download which have been newly
        included.
    """

    lock = RLock()

    _rev_lock = RLock()
    _last_sync_lock = RLock()

    failed_uploads = queue.Queue()
    failed_downloads = queue.Queue()
    sync_errors = queue.Queue()

    queued_for_download = queue.Queue()
    queued_for_upload = queue.Queue()

    queued_folder_downloads = queue.Queue()

    __slots__ = (
        "client", "config_name", "rev_file_path",
        "local_file_event_queue", "queue_uploading", "queue_downloading",
        "_dropbox_path", "_excluded_files", "_excluded_folders", "_rev_dict_cache",
        "_conf", "_state", "notifier", "_last_sync_for_path"
    )

    def __init__(self, client, local_file_event_queue, queue_uploading, queue_downloading):

        self.client = client
        self.config_name = self.client.config_name
        self.rev_file_path = get_data_path("maestral", f"{self.config_name}.index")

        self._conf = MaestralConfig(self.config_name)
        self._state = MaestralState(self.config_name)
        self.notifier = MaestralDesktopNotifier.for_config(self.config_name)

        self.local_file_event_queue = local_file_event_queue
        self.queue_uploading = queue_uploading
        self.queue_downloading = queue_downloading

        # load cached properties
        self._dropbox_path = self._conf.get("main", "path")
        self._excluded_files = self._conf.get("main", "excluded_files")
        self._excluded_folders = self._conf.get("main", "excluded_folders")
        self._rev_dict_cache = self._load_rev_dict_from_file()
        self._last_sync_for_path = dict()

    # ==== settings ======================================================================

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
        self._conf.set("main", "path", path)

    @property
    def excluded_files(self):
        """List containing all files excluded from sync. Changes are saved to the
        config file."""
        return self._excluded_files

    @excluded_files.setter
    def excluded_files(self, files_list):
        """Setter: excluded_folders"""
        self._excluded_files = files_list
        self._conf.set("main", "excluded_files", files_list)

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
        self._conf.set("main", "excluded_folders", clean_list)

    # ==== sync state ====================================================================

    @property
    def last_cursor(self):
        """Cursor from last sync with remote Dropbox. The value is updated and saved to
        the config file on every successful download of remote changes."""
        return self._state.get("sync", "cursor")

    @last_cursor.setter
    def last_cursor(self, cursor):
        """Setter: last_cursor"""
        logger.debug(f"Remote cursor saved: {cursor}")
        self._state.set("sync", "cursor", cursor)

    @property
    def last_sync(self):
        """Time stamp from last sync with remote Dropbox. The value is updated and
        saved to the config file on every successful upload of local changes."""
        return self._state.get("sync", "lastsync")

    @last_sync.setter
    def last_sync(self, last_sync):
        """Setter: last_cursor"""
        logger.debug(f"Local cursor saved: {last_sync}")
        self._state.set("sync", "lastsync", last_sync)

    def get_last_sync_for_path(self, dbx_path):
        with self._last_sync_lock:
            return max(self._last_sync_for_path.get(dbx_path.lower(), 0.0), self.last_sync)

    def set_last_sync_for_path(self, dbx_path, last_sync):
        with self._last_sync_lock:
            self._last_sync_for_path[dbx_path.lower()] = last_sync

    # ==== Rev file management ===========================================================

    def _load_rev_dict_from_file(self, raise_exception=False):
        """
        Loads Maestral's rev index from `rev_file_path` using u-msgpack.

        :param bool raise_exception: If ``True``, raises an exception when loading fails.
            If ``False``, an error message is logged instead.
        :raises: RevFileError
        """
        rev_dict_cache = dict()
        new_exc = None

        with self._rev_lock:
            try:
                with open(self.rev_file_path, "rb") as f:
                    rev_dict_cache = umsgpack.unpack(f)
                assert isinstance(rev_dict_cache, dict)
                assert all(isinstance(key, str) for key in rev_dict_cache.keys())
                assert all(isinstance(val, str) for val in rev_dict_cache.values())
            except (FileNotFoundError, IsADirectoryError):
                logger.info("Maestral index could not be found.")
            except (AssertionError, umsgpack.InsufficientDataException) as exc:
                title = "Corrupted index"
                msg = "Maestral index has become corrupted. Please rebuild."
                new_exc = RevFileError(title, msg).with_traceback(exc.__traceback__)
            except PermissionError as exc:
                title = "Could not load index"
                msg = ("Insufficient permissions for Dropbox folder. Please "
                       "make sure that you have read and write permissions.")
                new_exc = RevFileError(title, msg).with_traceback(exc.__traceback__)
            except OSError as exc:
                title = "Could not load index"
                msg = "Please resync your Dropbox to rebuild the index."
                new_exc = RevFileError(title, msg).with_traceback(exc.__traceback__)

            if new_exc and raise_exception:
                raise new_exc
            elif new_exc:
                exc_info = (type(new_exc), new_exc, new_exc.__traceback__)
                logger.error(title, exc_info=exc_info)

            return rev_dict_cache

    def _save_rev_dict_to_file(self, raise_exception=False):
        """
        Save Maestral's rev index to `rev_file_path` using u-msgpack.

        :param bool raise_exception: If ``True``, raises an exception when saving fails.
            If ``False``, an error message is logged instead.
        :raises: RevFileError
        """
        new_exc = None

        with self._rev_lock:
            try:
                with atomic_write(self.rev_file_path, mode="wb", overwrite=True) as f:
                    umsgpack.pack(self._rev_dict_cache, f)
            except PermissionError as exc:
                title = "Could not save index"
                msg = ("Insufficient permissions for Dropbox folder. Please "
                       "make sure that you have read and write permissions.")
                new_exc = RevFileError(title, msg).with_traceback(exc.__traceback__)
            except OSError as exc:
                title = "Could not save index"
                msg = "Please check the logs for more information"
                new_exc = RevFileError(title, msg).with_traceback(exc.__traceback__)

            if new_exc and raise_exception:
                raise new_exc
            elif new_exc:
                exc_info = (type(new_exc), new_exc, new_exc.__traceback__)
                logger.error(title, exc_info=exc_info)

    def get_rev_dict(self):
        """
        Returns a copy of the revision index containing the revision
        numbers for all synced files and folders.

        :returns: Copy of revision index.
        :rtype: dict
        """
        with self._rev_lock:
            return dict(self._rev_dict_cache)

    def get_local_rev(self, dbx_path):
        """
        Gets revision number of local file.

        :param str dbx_path: Dropbox file path.
        :returns: Revision number as str or `None` if no local revision number
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

    def clear_rev_index(self):
        with self._rev_lock:
            self._rev_dict_cache.clear()
            self._save_rev_dict_to_file()

    # ==== Helper functions ==============================================================

    @staticmethod
    def clean_excluded_folder_list(folder_list):
        """Removes all duplicates from the excluded folder list."""

        # remove duplicate entries by creating set, strip trailing "/"
        folder_list = set(f.lower().rstrip(osp.sep) for f in folder_list)

        # remove all children of excluded folders
        clean_list = list(folder_list)
        for folder in folder_list:
            clean_list = [f for f in clean_list if not is_child(f, folder)]

        return clean_list

    def ensure_dropbox_folder_present(self):
        """
        Checks if the Dropbox folder still exists where we expect it to be.

        :raises: DropboxDeletedError
        """

        if not osp.isdir(self.dropbox_path):
            title = "Dropbox folder has been moved or deleted"
            msg = ("Please move the Dropbox folder back to its original location "
                   "or restart Maestral to set up a new folder.")
            raise DropboxDeletedError(title, msg)

    def to_dbx_path(self, local_path):
        """
        Converts a local path to a path relative to the Dropbox folder.

        :param str local_path: Full path to file in local Dropbox folder.
        :returns: Relative path with respect to Dropbox folder.
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
            raise ValueError(f"Specified path '{local_path}' is not in Dropbox directory.")

        return "/{}".format("/".join(path_list[i:]))

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
        :returns: Corresponding local path on drive.
        :rtype: str
        :raises: ValueError if no path is specified.
        """

        dbx_path = dbx_path.replace("/", osp.sep)
        dbx_path_parent, dbx_path_basename = osp.split(dbx_path)

        local_parent = path_exists_case_insensitive(dbx_path_parent, self.dropbox_path)

        if local_parent == "":
            return osp.join(self.dropbox_path, dbx_path.lstrip(osp.sep))
        else:
            return osp.join(local_parent, dbx_path_basename)

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
        :returns: ``True`` or `False`.
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
        test0 = basename in EXCLUDED_FILE_NAMES

        # is temporary file?
        # 1) office temporary files
        test1 = basename.startswith("~$")
        test2 = basename.startswith(".~")
        # 2) other temporary files
        test3 = basename.startswith("~") and basename.endswith(".tmp")

        return any((test0, test1, test2, test3))

    def is_excluded_by_user(self, dbx_path):
        """
        Check if file has been excluded from sync by the user.

        :param str dbx_path: Path of folder on Dropbox.
        :returns: ``True`` or `False`.
        :rtype: bool
        """
        dbx_path = dbx_path.lower()

        # in excluded files?
        test0 = dbx_path in self.excluded_files
        # in excluded folders?
        test1 = any(dbx_path == f or is_child(dbx_path, f) for f in self.excluded_folders)

        return any((test0, test1))

    # ==== Upload sync ===================================================================

    def upload_local_changes_while_inactive(self):
        """
        Collects changes while sync has not been running and uploads them to Dropbox.
        Call this method when resuming sync.
        """

        logger.info("Indexing local changes...")

        try:
            events, local_cursor = self._get_local_changes_while_inactive()
        except FileNotFoundError:
            self.ensure_dropbox_folder_present()
            return

        if len(events) > 0:
            self.apply_local_changes(events, local_cursor)
            logger.debug("Uploaded local changes while inactive")
        else:
            self.last_sync = local_cursor
            logger.debug("No local changes while inactive")

    def _get_local_changes_while_inactive(self):

        changes = []
        now = time.time()
        snapshot = DirectorySnapshot(self.dropbox_path)

        # remove root entry from snapshot
        del snapshot._inode_to_path[snapshot.inode(self.dropbox_path)]
        del snapshot._stat_info[self.dropbox_path]
        # get lowercase paths
        lowercase_snapshot_paths = {x.lower() for x in snapshot.paths}

        # get modified or added items
        for path in snapshot.paths:
            stats = snapshot.stat_info(path)
            # check if item was created or modified since last sync
            dbx_path = self.to_dbx_path(path).lower()

            is_new = not self.get_local_rev(dbx_path) and not self.is_excluded(dbx_path)
            is_modified = (self.get_local_rev(dbx_path)
                           and now > max(stats.st_ctime, stats.st_mtime) > self.last_sync)

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

        del snapshot
        del lowercase_snapshot_paths

        return changes, now

    def wait_for_local_changes(self, timeout=2, delay=0.5):
        """
        Waits for local file changes. Returns a list of local changes, filtered to
        avoid duplicates.

        :param float timeout: If no changes are detected within timeout (sec), an empty
            list is returned.
        :param delay: Delay in sec to wait for subsequent changes that may be duplicates.
        :returns: (list of file events, time_stamp)
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

        logger.debug(f"Retrieved local file events:\n{events}")

        # clean up events to provide only one event per path
        events = self._clean_local_events(events)

        # REMOVE DIR_MODIFIED_EVENTS
        events = [e for e in events if not isinstance(e, DirModifiedEvent)]

        # COMBINE "MOVED" EVENTS OF FOLDERS AND THEIR CHILDREN INTO ONE EVENT
        dir_moved_events = [e for e in events if self._is_dir_moved(e)]

        if len(dir_moved_events) > 0:
            child_move_events = []

            for parent_event in dir_moved_events:
                children = [x for x in events if self._is_moved_child(x, parent_event)]
                child_move_events += children

            events = self._list_diff(events, child_move_events)

        # COMBINE "DELETED" EVENTS OF FOLDERS AND THEIR CHILDREN INTO ONE EVENT
        dir_deleted_events = [x for x in events if self._is_dir_deleted(x)]

        if len(dir_deleted_events) > 0:
            child_deleted_events = []

            for parent_event in dir_deleted_events:
                children = [x for x in events if self._is_deleted_child(x, parent_event)]
                child_deleted_events += children

            events = self._list_diff(events, child_deleted_events)

        logger.debug(f"Cleaned up local file events:\n{events}")

        return events, local_cursor

    def _filter_excluded_changes_local(self, events):
        """
        Checks for and removes file events referring to items which are excluded from
        syncing.

        :param events: List of file events.
        :returns: (``events_filtered``, ``events_excluded``)
        """

        logger.debug("Filtering excluded items from local file events")

        events_filtered = []
        events_excluded = []

        for event in events:

            local_path = _get_dest_path(event)
            dbx_path = self.to_dbx_path(local_path)

            if self.is_excluded(dbx_path):  # is excluded?
                events_excluded.append(event)
            elif self.is_excluded_by_user(dbx_path):  # is excluded by user?
                if event.event_type is EVENT_TYPE_DELETED:
                    self.clear_sync_error(local_path, dbx_path)
                else:
                    title = "Could not upload"
                    message = ("Another item with the same name already exists on "
                               "Dropbox but is excluded from syncing.")
                    exc = ExcludedItemError(title, message, dbx_path=dbx_path,
                                            local_path=local_path)
                    basename = osp.basename(dbx_path)
                    exc_info = (type(exc), exc, None)
                    logger.warning(f"Could not upload {basename}", exc_info=exc_info)
                    self.sync_errors.put(exc)
                    self.failed_uploads.put(event)
                events_excluded.append(event)
            else:
                events_filtered.append(event)

        logger.debug(f"Events to discard:\n{events_excluded}")

        return events_filtered, events_excluded

    @staticmethod
    def _clean_local_events(events):
        """
        Takes local file events within the monitored period and cleans them up so that
        there is only a single event per path.

        :param events: Iterable of :class:`watchdog.FileSystemEvents`.
        :returns: List of :class:`watchdog.FileSystemEvents`.
        :rtype: list
        """

        all_src_paths = [e.src_path for e in events]
        all_dest_paths = [e.dest_path for e in events if e.event_type == EVENT_TYPE_MOVED]

        all_paths = all_src_paths + all_dest_paths

        # Move events are difficult to combine with other event types
        # -> split up moved events into deleted and created events, but only if the
        # respective paths have other events associated with them
        new_events = []

        for e in events:
            if e.event_type == EVENT_TYPE_MOVED:
                related = tuple(p for p in all_paths if p in (e.src_path, e.dest_path))
                if len(related) > 2:
                    e_del = FileDeletedEvent(e.src_path)
                    e_new = FileCreatedEvent(e.dest_path)
                    new_events.append(e_del)
                    new_events.append(e_new)
                else:
                    new_events.append(e)
            else:
                new_events.append(e)

        events = new_events.copy()

        unique_paths = set(e.src_path for e in events)
        histories = [[e for e in events if e.src_path == path] for path in unique_paths]

        new_events.clear()

        for h in histories:
            if len(h) == 1:
                new_events += h
            else:
                path = h[0].src_path

                if isinstance(h[-1], FILE_EVENTS):  # final item is a file
                    CreatedEvent = FileCreatedEvent
                    ModifiedEvent = FileModifiedEvent
                    DeletedEvent = FileDeletedEvent
                else:  # final item is a directory
                    CreatedEvent = DirCreatedEvent
                    ModifiedEvent = DirModifiedEvent
                    DeletedEvent = DirDeletedEvent

                n_created = len([e for e in h if e.event_type == EVENT_TYPE_CREATED])
                n_deleted = len([e for e in h if e.event_type == EVENT_TYPE_DELETED])

                if n_created > n_deleted:  # file created
                    new_events.append(CreatedEvent(path))
                if n_created < n_deleted:  # file was only temporary
                    new_events.append(DeletedEvent(path))
                elif n_deleted == n_created:
                    if n_created == 0:  # file was modified
                        new_events.append(ModifiedEvent(path))
                    else:
                        first_created = h.index(next(e for e in h if e.event_type == EVENT_TYPE_CREATED))
                        first_deleted = h.index(next(e for e in h if e.event_type == EVENT_TYPE_DELETED))

                        if first_deleted < first_created:  # file was modified
                            new_events.append(ModifiedEvent(path))
                        else:  # file was only temporary
                            pass

        return new_events

    @staticmethod
    def _separate_event_types(events):
        """
        Sorts local events into DirEvents and FileEvents.

        :returns: Tuple of (folders, files)
        :rtype: tuple
        """

        folders = [x for x in events if isinstance(x, DIR_EVENTS)]
        files = [x for x in events if isinstance(x, FILE_EVENTS)]

        return folders, files

    def apply_local_changes(self, events, local_cursor):
        """
        Applies locally detected events to remote Dropbox.

        :param list events: List of local file changes.
        :param float local_cursor: Time stamp of last event in `events`.
        :returns: ``True`` if all changes have been uploaded successfully, ``False``
            otherwise.
        :rtype: bool
        """

        logger.debug("Beginning upload of local changes")

        events, _ = self._filter_excluded_changes_local(events)
        dir_events, file_events = self._separate_event_types(events)

        # update queues
        for e in events:
            self.queued_for_upload.put(_get_dest_path(e))

        # apply directory events first (the do not require any upload)
        for event in dir_events:
            self._apply_event(event)

        # apply file events in parallel
        num_threads = os.cpu_count() * 2
        success = []
        last_emit = time.time()
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            fs = (executor.submit(self._apply_event, e) for e in file_events)
            n_files = len(file_events)
            for f, n in zip(as_completed(fs), range(1, n_files + 1)):
                if time.time() - last_emit > 1 or n in (1, n_files):
                    # emit message at maximum every second
                    logger.info(f"Uploading {n}/{n_files}...")
                    last_emit = time.time()
                success.append(f.result())

        if all(success):
            self.last_sync = local_cursor  # save local cursor
            logger.debug("Upload of local changes succeeded")
            return True
        else:
            logger.debug("Upload of local changes failed")
            return False

    @staticmethod
    def _list_diff(list1, list2):
        """
        Subtracts elements of `list2` from `list1` while preserving the order of `list1`.

        :param list list1: List to subtract from.
        :param list list2: List of elements to subtract.
        :returns: Subtracted list.
        :rtype: list
        """
        return [item for item in list1 if item not in set(list2)]

    @staticmethod
    def _is_dir_moved(x):
        """Check for moved folders"""
        is_moved_event = (x.event_type is EVENT_TYPE_MOVED)
        return is_moved_event and x.is_directory

    @staticmethod
    def _is_moved_child(x, parent):
        """Check for children of moved folders"""
        is_moved_event = (x.event_type is EVENT_TYPE_MOVED)
        return (is_moved_event
                and is_child(x.src_path, parent.src_path)
                and is_child(x.dest_path, parent.dest_path))

    @staticmethod
    def _is_dir_deleted(x):
        """Check for deleted folders"""
        is_deleted_event = (x.event_type is EVENT_TYPE_DELETED)
        return is_deleted_event and x.is_directory

    @staticmethod
    def _is_deleted_child(x, parent):
        """Check for children of deleted folders"""
        is_deleted_event = (x.event_type is EVENT_TYPE_DELETED)
        return is_deleted_event and is_child(x.src_path, parent.src_path)

    @catch_sync_issues(sync_errors, failed_uploads)
    def _apply_event(self, event):
        """Apply a local file event `event` to the remote Dropbox. Clear any related
        sync errors with the file. Any new MaestralApiErrors will be caught by the
        decorator."""

        local_path = _get_dest_path(event)
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

        # does item exist on Dropbox?
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
            self.set_local_rev(md.path_lower, md.rev)
        # and revs of children if folder
        elif isinstance(md, dropbox.files.FolderMetadata):
            self.set_local_rev(md.path_lower, "folder")
            result = self.client.list_folder(dbx_path_new, recursive=True)
            for md in result.entries:
                if isinstance(md, dropbox.files.FileMetadata):
                    self.set_local_rev(md.path_lower, md.rev)
                elif isinstance(md, dropbox.files.FolderMetadata):
                    self.set_local_rev(md.path_lower, "folder")

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
            self.set_local_rev(md.path_lower, "folder")

        elif not event.is_directory:

            self._wait_for_creation(path)

            # check if file already exists with identical content
            md = self.client.get_metadata(dbx_path)
            if isinstance(md, FileMetadata):
                local_hash = get_local_hash(path)
                if local_hash == md.content_hash:
                    # file hashes are identical, do not upload
                    self.set_local_rev(md.path_lower, md.rev)
                    return

            rev = self.get_local_rev(dbx_path)
            # if truly a new file
            if rev in (None, "folder"):
                mode = dropbox.files.WriteMode("add")
            # or a 'false' new file event triggered by saving the file
            # e.g., some programs create backup files and then swap them
            # in to replace the files you are editing on the disk
            else:
                mode = dropbox.files.WriteMode("update", rev)
            try:
                md = self.client.upload(path, dbx_path, autorename=True, mode=mode)
            except NotFoundError:
                logger.debug("Could not upload '%s': the item does not exist.", event.src_path)
            else:
                self.set_local_rev(md.path_lower, md.rev)

        logger.debug("Created '%s' on Dropbox.", event.src_path)

    def _on_deleted(self, event):
        """
        Call when local file is deleted.

        :param class event: Watchdog file event.
        :raises: MaestralApiError on failure.
        """

        path = event.src_path
        dbx_path = self.to_dbx_path(path)
        local_rev = self.get_local_rev(dbx_path)
        local_rev = None if local_rev == "folder" else local_rev

        try:
            self.client.remove(dbx_path, parent_rev=local_rev)
        except (NotFoundError, PathError):
            logger.debug("Could not delete '%s': the item does not exist on Dropbox.", event.src_path)

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

            self._wait_for_creation(path)

            # check if item already exists with identical content
            md = self.client.get_metadata(dbx_path)
            if isinstance(md, FileMetadata):
                local_hash = get_local_hash(path)
                if local_hash == md.content_hash:
                    # file hashes are identical, do not upload
                    self.set_local_rev(md.path_lower, md.rev)
                    logger.debug("Modification of '%s' detected but file content is "
                                 "the same as on Dropbox.", event.src_path)
                    return

            rev = self.get_local_rev(dbx_path)
            if rev == "folder":
                mode = dropbox.files.WriteMode("overwrite")
            elif not rev:
                logger.debug("'%s' appears to have been modified but cannot "
                             "find old revision.", event.src_path)
                return
            else:
                mode = dropbox.files.WriteMode("update", rev)
            md = self.client.upload(path, dbx_path, autorename=True, mode=mode)
            # save or update revision metadata
            self.set_local_rev(md.path_lower, md.rev)
            logger.debug("Uploaded modified '%s' to Dropbox.", event.src_path)

    # ==== Download sync =================================================================

    @catch_sync_issues(sync_errors)
    def get_remote_dropbox(self, dbx_path="/", ignore_excluded=True):
        """
        Gets all files/folders from Dropbox and writes them to the local folder
        :ivar:`dropbox_path`. Call this method on first run of the Maestral. Indexing
        and downloading may take several minutes, depending on the size of the user's
        Dropbox folder.

        :param str dbx_path: Path to Dropbox folder. Defaults to root ("").
        :param bool ignore_excluded: If ``True``, do not index excluded folders.
        :returns: ``True`` on success, ``False`` otherwise.
        :rtype: bool
        """

        is_dbx_root = dbx_path in ("/", "")
        success = []

        if is_dbx_root:
            logger.info(f"Downloading your Dropbox")
        else:
            logger.info(f"Downloading folder {dbx_path}")

        if not any(folder.startswith(dbx_path) for folder in self.excluded_folders):
            # if there are no excluded subfolders, index and download all at once
            ignore_excluded = False

        # get a cursor for the folder
        cursor = self.client.get_latest_cursor(dbx_path)

        root_result = self.client.list_folder(dbx_path, recursive=(not ignore_excluded),
                                              include_deleted=False, limit=500)

        # download top-level folders / files first
        logger.info("Syncing...")
        _, s = self.apply_remote_changes(root_result, save_cursor=False)
        success.append(s)

        if ignore_excluded:
            # download sub-folders if not excluded
            for entry in root_result.entries:
                if isinstance(entry, FolderMetadata) and not self.is_excluded_by_user(
                        entry.path_display):
                    success.append(self.get_remote_dropbox(entry.path_display))

        if all(success) and is_dbx_root:
            # save cursor for global download
            self.last_cursor = cursor

        return all(success)

    @catch_sync_issues()
    def wait_for_remote_changes(self, last_cursor, timeout=40):
        """Wraps MaestralApiClient.wait_for_remote_changes and catches sync errors."""
        return self.client.wait_for_remote_changes(last_cursor, timeout=timeout)

    @catch_sync_issues()
    def list_remote_changes(self, last_cursor):
        """Wraps ``MaestralApiClient.list_remove_changes`` and catches sync errors."""
        return self.client.list_remote_changes(last_cursor)

    def filter_excluded_changes_remote(self, changes):
        """Removes all excluded items from the given list of changes.

        :param changes: :class:`dropbox.files.ListFolderResult` instance.
        :returns: (changes_filtered, changes_discarded)
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
        :returns: List of changes that were made to local files, bool indicating if all
            download syncs were successful.
        :rtype: list, bool
        """

        if not changes:
            return False

        # filter out excluded changes
        changes_included, changes_excluded = self.filter_excluded_changes_remote(changes)

        # update queue
        for md in changes_included.entries:
            self.queued_for_download.put(self.to_local_path(md.path_display))

        # remove all deleted items from the excluded list
        _, _, deleted_excluded = self._sort_remote_entries(changes_excluded)
        for d in deleted_excluded:
            new_excluded = [f for f in self.excluded_folders if not f.startswith(d.path_lower)]
            self.excluded_folders = new_excluded

        # sort changes into folders, files and deleted
        folders, files, deleted = self._sort_remote_entries(changes_included)

        # sort according to path hierarchy
        # do not create sub-folder / file before parent exists
        folders.sort(key=lambda x: x.path_display.count('/'))
        files.sort(key=lambda x: x.path_display.count('/'))
        deleted.sort(key=lambda x: x.path_display.count('/'))

        downloaded = []  # local list of all changes

        # create local folders, start with top-level and work your way down
        for folder in folders:
            downloaded.append(self._create_local_entry(folder))

        # apply deleted items
        for item in deleted:
            downloaded.append(self._create_local_entry(item))

        # apply created files
        n_files = len(files)
        last_emit = time.time()
        with ThreadPoolExecutor(max_workers=6) as executor:
            fs = (executor.submit(self._create_local_entry, file) for file in files)
            for f, n in zip(as_completed(fs), range(1, n_files + 1)):
                if time.time() - last_emit > 1 or n in (1, n_files):
                    # emit messages at maximum every second
                    logger.info(f"Downloading {n}/{n_files}...")
                    last_emit = time.time()
                downloaded.append(f.result())

        success = all(downloaded)

        if success and save_cursor:
            self.last_cursor = changes.cursor

        return [entry for entry in downloaded if not isinstance(entry, bool)], success

    def check_download_conflict(self, md):
        """
        Check if local item is conflicting with remote item. The equivalent check when
        uploading and item will be carried out by Dropbox itself.

        Checks are carried out against our index, reflecting the latest sync state.

        :param Metadata md: Dropbox SDK metadata.
        :rtype: Conflict
        :raises: MaestralApiError if the Dropbox item does not exist.
        """

        # get metadata of remote item
        if isinstance(md, FileMetadata):
            remote_rev = md.rev
            remote_hash = md.content_hash
        elif isinstance(md, FolderMetadata):
            remote_rev = "folder"
            remote_hash = "folder"
        else:  # DeletedMetadata
            remote_rev = None
            remote_hash = None

        dbx_path = md.path_display
        local_path = self.to_local_path(dbx_path)
        local_rev = self.get_local_rev(dbx_path)

        if remote_rev == local_rev:
            # Local change has the same rev. May be newer and
            # not yet synced or identical. Don't overwrite.
            logger.debug(f"Local item is the same or newer than on Dropbox:  {dbx_path}")
            return Conflict.LocalNewerOrIdentical

        elif remote_rev != local_rev:
            # Dropbox server version has a different rev, likely is newer.
            # If the local version has been modified while sync was stopped,
            # those changes will be uploaded before any downloads can begin.
            # Conflict resolution will then be handled by Dropbox.
            # If the local version has been modified while sync was running
            # but changes were not uploaded before the remote version was
            # changed as well, the local ctime will be larger than last_sync:
            # (a) The upload of the changed file has already started. Upload thread
            #     will hold the lock and we won't be here checking for conflicts.
            # (b) The upload has not started yet. Manually check for conflict.

            if get_ctime(local_path) <= self.get_last_sync_for_path(dbx_path):
                logger.debug(f"Remote item is newer: {dbx_path}")
                return Conflict.RemoteNewer
            elif not remote_rev:
                logger.debug(f"Local item has been modified since remote deletion: {dbx_path}")
                return Conflict.LocalNewerOrIdentical
            else:
                local_hash = get_local_hash(local_path)
                if remote_hash == local_hash:
                    logger.debug(f"Contents are equal. No conflict: {dbx_path}")
                    self.set_local_rev(dbx_path, remote_rev)  # update local rev
                    return Conflict.Identical
                else:
                    logger.debug(f"Local item was created since last upload. Conflict: {dbx_path}")
                    return Conflict.Conflict

    def notify_user(self, changes):
        """
        Sends system notification for file changes.

        :param list changes: List of Dropbox metadata which has been applied locally.
        """

        # get number of remote changes
        n_changed = len(changes)

        if n_changed == 0:
            return

        user_name = None
        change_type = "changed"

        # find out who changed the item(s), get the use name if its only a single user
        try:
            dbid_list = set(md.sharing_info.modified_by for md in changes)
            if len(dbid_list) == 1:
                # all files have been modified by the same user
                dbid = dbid_list.pop()
                if dbid == self._conf.get("account", "account_id"):
                    user_name = "You"
                else:
                    account_info = self.client.get_account_info(dbid)
                    user_name = account_info.name.display_name
        except AttributeError:
            pass

        if n_changed == 1:
            # display user name, file name, and type of change
            md = changes[0]
            file_name = os.path.basename(md.path_display)

            if isinstance(md, DeletedMetadata):
                change_type = "removed"
            elif isinstance(md, FileMetadata):
                revs = self.client.list_revisions(md.path_lower, limit=2)
                is_new_file = len(revs.entries) == 1
                change_type = "added" if is_new_file else "changed"
            elif isinstance(md, FolderMetadata):
                change_type = "added"

        else:
            # display user name if unique, number of files, and type of change
            file_name = f"{n_changed} items"

            if all(isinstance(x, DeletedMetadata) for x in changes):
                change_type = "removed"
            elif all(isinstance(x, FolderMetadata) for x in changes):
                change_type = "added"
                file_name = f"{n_changed} folders"
            elif all(isinstance(x, FileMetadata) for x in changes):
                file_name = f"{n_changed} files"

        if user_name:
            msg = f"{user_name} {change_type} {file_name}"
        else:
            msg = f"{file_name} {change_type}"

        self.notifier.notify(msg, level=FILECHANGE)

    @staticmethod
    def _sort_remote_entries(result):
        """
        Sorts entries in :class:`dropbox.files.ListFolderResult` into
        FolderMetadata, FileMetadata and DeletedMetadata.

        :returns: Tuple of (folders, files, deleted) containing instances of
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
        :returns: Copy of metadata if the change was downloaded, ``True`` if the change
            already existed locally and ``False`` if the download failed.
        :raises: MaestralApiError on failure.
        """

        local_path = self.to_local_path(entry.path_display)

        # book keeping
        self.clear_sync_error(dbx_path=entry.path_display)
        remove_from_queue(self.queued_for_download, local_path)

        conflict_check = self.check_download_conflict(entry)

        applied = None

        if conflict_check in (Conflict.Identical, Conflict.LocalNewerOrIdentical):
            return applied

        elif conflict_check == Conflict.Conflict:
            # rename local item and get remote
            base, ext = osp.splitext(local_path)
            new_local_file = "".join((base, " (conflicting copy)", ext))
            os.rename(local_path, new_local_file)

        if isinstance(entry, FileMetadata):
            # Store the new entry at the given path in your local state.
            # If the required parent folders don’t exist yet, create them.
            # If there’s already something else at the given path,
            # replace it and remove all its children.

            self._save_to_history(entry.path_display)

            with InQueue(local_path, self.queue_downloading, delay=0.5):

                if osp.isdir(local_path):
                    delete(local_path)

                md = self.client.download(entry.path_display, local_path)
                self.set_last_sync_for_path(md.path_lower, get_ctime(local_path))
                self.set_local_rev(md.path_lower, md.rev)

            logger.debug(f"Created local file '{entry.path_display}'")
            applied = entry

        elif isinstance(entry, FolderMetadata):
            # Store the new entry at the given path in your local state.
            # If the required parent folders don’t exist yet, create them.
            # If there’s already something else at the given path,
            # replace it but leave the children as they are.

            with InQueue(local_path, self.queue_downloading, delay=0.5):

                if osp.isfile(local_path):
                    delete(local_path)

                try:
                    os.makedirs(local_path)
                except FileExistsError:
                    pass
                self.set_last_sync_for_path(entry.path_lower, get_ctime(local_path))
                self.set_local_rev(entry.path_lower, "folder")

            logger.debug(f"Created local folder: {entry.path_display}")
            applied = entry

        elif isinstance(entry, DeletedMetadata):
            # If your local state has something at the given path,
            # remove it and all its children. If there’s nothing at the
            # given path, ignore this entry.

            with InQueue(local_path, self.queue_downloading, delay=0.5):
                err = delete(local_path)
                self.set_local_rev(entry.path_lower, None)

            if not err:
                logger.debug(f"Deleted local item '{entry.path_display}'")
                applied = entry
            else:
                logger.debug(f"Deletion failed: {err}")

        return applied

    def _save_to_history(self, dbx_path):
        # add new file to recent_changes
        recent_changes = self._state.get("sync", "recent_changes")
        recent_changes.append(dbx_path)
        # eliminate duplicates
        recent_changes = list(OrderedDict.fromkeys(recent_changes))
        # save last 30 changes
        self._state.set("sync", "recent_changes", recent_changes[-30:])


# ========================================================================================
# Workers for upload, download and connection monitoring threads
# ========================================================================================

def connection_helper(sync, syncing, paused_by_user, running, connected,
                      startup_requested, startup_done, check_interval=4):
    """
    A worker which periodically checks the connection to Dropbox servers.
    This is done through inexpensive calls to :method:`client.get_space_usage`.
    If the connection is lost, ``connection_helper`` pauses all syncing until a
    connection can be reestablished.

    :param UpDownSync sync: UpDownSync instance.
    :param Event syncing: Event that indicates if workers are running or paused.
    :param Event paused_by_user: Set if the syncing was paused by the user.
    :param Event running: Event to shutdown connection helper.
    :param Event connected: Event that indicates if we can connect to Dropbox.
    :param int check_interval: Time in seconds between connection checks.
    :param Event startup_done: Cleared when functions to run on startup have finished.
    :param Event startup_requested: Set when startup scripts have been requested.
    """

    while running.is_set():
        try:
            # use an inexpensive call to get_space_usage to test connection
            sync.client.get_space_usage()
            if not connected.is_set() and not paused_by_user.is_set():
                sync.clear_all_sync_errors()
                startup_requested.set()
                startup_done.clear()
                syncing.set()
            connected.set()
            time.sleep(check_interval)
        except ConnectionError:
            if connected.is_set():
                logger.debug(DISCONNECTED, exc_info=True)
                logger.info(DISCONNECTED)
            syncing.clear()
            connected.clear()
            time.sleep(check_interval / 2)
        except DropboxAuthError as e:
            running.clear()
            syncing.clear()
            startup_done.set()
            logger.error(e.title, exc_info=True)
        except Exception:
            running.clear()
            syncing.clear()
            startup_done.set()
            logger.error("Unexpected error", exc_info=True)


def download_worker(sync, syncing, running, connected, startup_done):
    """
    Worker to sync changes of remote Dropbox with local folder.

    :param UpDownSync sync: Instance of :class:`UpDownSync`.
    :param Event syncing: Event that indicates if workers are running or paused.
    :param Event running: Event to shutdown local file event handler and worker threads.
    :param Event connected: Event that indicates if we can connect to Dropbox.
    :param Event startup_done: Cleared when functions to run on startup have finished.
    """

    while running.is_set():

        syncing.wait()
        startup_done.wait()

        try:
            has_changes = sync.wait_for_remote_changes(sync.last_cursor, timeout=30)

            if not (running.is_set() and syncing.is_set() and startup_done.is_set()):
                continue

            if has_changes:
                with sync.lock:
                    logger.info(SYNCING)

                    changes = sync.list_remote_changes(sync.last_cursor)
                    downloaded, _ = sync.apply_remote_changes(changes)
                    sync.notify_user(downloaded)

                    logger.info(IDLE)

        except ConnectionError:
            syncing.clear()
            connected.clear()
            logger.debug(DISCONNECTED, exc_info=True)
            logger.info(DISCONNECTED)
        except MaestralApiError as e:
            running.clear()
            syncing.clear()
            startup_done.set()
            logger.error(e.title, exc_info=True)
        except Exception:
            running.clear()
            syncing.clear()
            startup_done.set()
            logger.error("Unexpected error", exc_info=True)


def download_worker_added_folder(sync, syncing, running, connected, startup_done):
    """
    Worker to download folders which have been newly included in sync.

    :param UpDownSync sync: Instance of :class:`UpDownSync`.
    :param Event syncing: Event that indicates if workers are running or paused.
    :param Event running: Event to shutdown local file event handler and worker threads.
    :param Event connected: Event that indicates if we can connect to Dropbox.
    :param Event startup_done: Cleared when functions to run on startup have finished.
    """

    while running.is_set():

        syncing.wait()
        startup_done.wait()

        dbx_path = sync.queued_folder_downloads.get()

        if not (running.is_set() and syncing.is_set() and startup_done.is_set()):
            sync.queued_folder_downloads.put(dbx_path)
            continue

        try:
            with sync.lock:
                sync.get_remote_dropbox(dbx_path)
            logger.info(IDLE)
        except ConnectionError:
            syncing.clear()
            connected.clear()
            sync.queued_folder_downloads.put(dbx_path)
            logger.debug(DISCONNECTED, exc_info=True)
            logger.info(DISCONNECTED)
        except MaestralApiError as e:
            running.clear()
            syncing.clear()
            startup_done.set()
            logger.error(e.title, exc_info=True)
        except Exception:
            running.clear()
            syncing.clear()
            startup_done.set()
            logger.error("Unexpected error", exc_info=True)


def upload_worker(sync, syncing, running, connected, startup_done):
    """
    Worker to sync local changes to remote Dropbox.

    :param UpDownSync sync: Instance of :class:`UpDownSync`.
    :param Event syncing: Event that indicates if workers are running or paused.
    :param Event running: Event to shutdown local file event handler and worker threads.
    :param Event connected: Event that indicates if we can connect to Dropbox.
    :param Event startup_done: Cleared when functions to run on startup have finished.
    """

    while running.is_set():

        syncing.wait()
        startup_done.wait()

        try:
            events, local_cursor = sync.wait_for_local_changes(timeout=5)

            if not (running.is_set() and syncing.is_set() and startup_done.is_set()):
                continue

            if len(events) > 0:
                with sync.lock:
                    logger.info(SYNCING)
                    sync.apply_local_changes(events, local_cursor)
                    logger.info(IDLE)
            else:
                sync.last_sync = local_cursor

        except ConnectionError:
            syncing.clear()
            connected.clear()
            logger.debug(DISCONNECTED, exc_info=True)
            logger.info(DISCONNECTED)
        except MaestralApiError as e:
            running.clear()
            syncing.clear()
            startup_done.set()
            logger.error(e.title, exc_info=True)
        except Exception:
            running.clear()
            syncing.clear()
            startup_done.set()
            logger.error("Unexpected error", exc_info=True)


def startup_worker(sync, syncing, running, connected, startup_requested, startup_done):
    """
    Worker to sync local changes to remote Dropbox.

    :param UpDownSync sync: Instance of :class:`UpDownSync`.
    :param Event syncing: Event that indicates if workers are running or paused.
    :param Event running: Event to shutdown local file event handler and worker threads.
    :param Event connected: Event that indicates if we can connect to Dropbox.
    :param Event startup_done: Cleared when functions to run on startup have finished.
    :param Event startup_requested: Set when startup scripts have been requested.
    """

    while running.is_set():

        syncing.wait()
        startup_requested.wait()

        try:
            with sync.lock:
                # run / resume initial download
                # local changes during this download will be registered
                # by the local FileSystemObserver but only uploaded after
                # `startup_done` has been set
                if sync.last_cursor == '':
                    sync.get_remote_dropbox()
                    sync.last_sync = time.time()

                if not (syncing.is_set() and running.is_set()):
                    continue

                # upload changes while inactive
                sync.upload_local_changes_while_inactive()

            startup_done.set()
            startup_requested.clear()

            logger.info(IDLE)

        except ConnectionError:
            syncing.clear()
            connected.clear()
            logger.debug(DISCONNECTED, exc_info=True)
            logger.info(DISCONNECTED)
        except MaestralApiError as e:
            running.clear()
            syncing.clear()
            startup_done.set()
            logger.error(e.title, exc_info=True)
        except Exception:
            running.clear()
            syncing.clear()
            startup_done.set()
            logger.error("Unexpected error", exc_info=True)


# ========================================================================================
# Main Monitor class to start, stop and coordinate threads
# ========================================================================================

class MaestralMonitor:
    """
    Class to sync changes between Dropbox and a local folder. It creates four
    threads: `observer` to catch local file events, `upload_thread` to upload
    caught changes to Dropbox, `download_thread` to query for and download
    remote changes, and `connection_thread` which periodically checks the
    connection to Dropbox servers.

    :param MaestralApiClient client: The Dropbox API client, a wrapper around the Dropbox
        Python SDK.

    :ivar Thread local_observer_thread: Watchdog observer thread that detects local file
        system events.
    :ivar Thread upload_thread: Thread that sorts uploads local changes.
    :ivar Thread download_thread: Thread to query for and download remote changes.
    :ivar Thread file_handler: Handler to queue file events from `observer` for upload.
    :ivar UpDownSync sync: Object to coordinate syncing. This is the brain of Maestral.
        It contains the logic to process local and remote file events and to apply them
        while checking for conflicts.

    :ivar Event connected: Set when connected to Dropbox servers.
    :ivar Event syncing: Set when sync is running.
    :ivar Event running: Set when the sync threads are alive.
    :ivar Event paused_by_user: Set when sync is paused by the user.

    :ivar Event startup_requested: Set when startup scripts have to be run after syncing
        was inactive, for instance when Maestral is started, the internet connection is
        reestablished or syncing is resumed after pausing.
    :ivar Event startup_done: Set when startup scripts have completed. Sync threads will
        wait for this event to be set.

    :cvar Queue queue_downloading: Holds *local file paths* that are being downloaded.
    :cvar Queue queue_uploading: Holds *local file paths* that are being uploaded.
    :cvar TimedQueue local_file_event_queue: Holds *file events* to be uploaded.
    """

    queue_downloading = queue.Queue()
    queue_uploading = queue.Queue()

    local_file_event_queue = TimedQueue()

    def __init__(self, client):

        self.connected = Event()
        self.syncing = Event()
        self.running = Event()
        self.paused_by_user = Event()
        self.paused_by_user.set()

        self.startup_requested = Event()
        self.startup_done = Event()

        self.client = client
        self.config_name = self.client.config_name
        self.file_handler = FileEventHandler(
            self.syncing, self.local_file_event_queue, self.queue_downloading
        )

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

    def start(self):
        """Creates observer threads and starts syncing."""

        if self.running.is_set():
            # do nothing if already running
            return

        self.running = Event()  # create new event to let old threads shut down

        self.local_observer_thread = Observer(timeout=0.1)
        self.local_observer_thread.schedule(
            self.file_handler, self.sync.dropbox_path, recursive=True
        )

        self.connection_thread = Thread(
            target=connection_helper,
            daemon=True,
            args=(
                self.sync, self.syncing, self.paused_by_user, self.running,
                self.connected, self.startup_requested, self.startup_done
            ),
            name=f"maestral-connection-helper-{self.config_name}"
        )

        self.startup_thread = Thread(
            target=startup_worker,
            daemon=True,
            args=(
                self.sync, self.syncing, self.running, self.connected,
                self.startup_requested, self.startup_done
            ),
            name=f"maestral-startup-worker-{self.config_name}"
        )

        self.download_thread = Thread(
            target=download_worker,
            daemon=True,
            args=(
                self.sync, self.syncing, self.running, self.connected, self.startup_done
            ),
            name=f"maestral-download-{self.config_name}"
        )

        self.download_thread_added_folder = Thread(
            target=download_worker_added_folder,
            daemon=True,
            args=(
                self.sync, self.syncing, self.running, self.connected, self.startup_done
            ),
            name=f"maestral-folder-download-{self.config_name}"
        )

        self.upload_thread = Thread(
            target=upload_worker,
            daemon=True,
            args=(
                self.sync, self.syncing, self.running, self.connected, self.startup_done
            ),
            name=f"maestral-upload-{self.config_name}"
        )

        try:
            self.local_observer_thread.start()
        except OSError as exc:
            if "inotify" in exc.args[0]:
                title = "Inotify limit reached"
                msg = ("Changes to your Dropbox folder cannot be monitored because it "
                       "contains too many items. Please increase the inotify limit in "
                       "your system by adding the following line to /etc/sysctl.conf:\n\n"
                       "fs.inotify.max_user_watches=524288")
                new_exc = InotifyError(title, msg).with_traceback(exc.__traceback__)
                exc_info = (type(new_exc), new_exc, new_exc.__traceback__)
                logger.error(title, exc_info=exc_info)
                return
            else:
                raise exc

        self.running.set()
        self.syncing.set()
        self.connected.set()
        self.startup_requested.set()
        self.startup_done.clear()

        self.connection_thread.start()
        self.startup_thread.start()
        self.upload_thread.start()
        self.download_thread.start()
        self.download_thread_added_folder.start()

        self.paused_by_user.clear()

        logger.info("Syncing started")
        logger.info(IDLE)

    def pause(self):
        """Pauses syncing."""

        self.paused_by_user.set()
        self.syncing.clear()
        logger.info(PAUSED)

    def resume(self):
        """Checks for changes while idle and starts syncing."""

        if self.syncing.is_set():
            return

        self.sync.clear_all_sync_errors()

        self.startup_requested.set()
        self.startup_done.clear()
        self.syncing.set()
        self.paused_by_user.clear()

        logger.info(IDLE)

    def stop(self):
        """Stops syncing and destroys worker threads."""

        if not self.running.is_set():
            return

        logger.debug("Shutting down threads...")

        self.running.clear()
        self.syncing.clear()
        self.paused_by_user.clear()
        self.startup_requested.clear()
        self.startup_done.set()

        self.local_observer_thread.stop()
        self.local_observer_thread.join()
        self.connection_thread.join()
        self.upload_thread.join()
        # self.download_thread.join()

        logger.info(STOPPED)

    def _threads_alive(self):
        """Returns ``True`` if all threads are alive, ``False`` otherwise."""

        try:
            threads = (
                self.local_observer_thread,
                self.upload_thread, self.download_thread,
                self.download_thread_added_folder,
                self.connection_thread,
                self.startup_thread
            )
        except AttributeError:
            return False

        base_threads_alive = (t.is_alive() for t in threads)
        watchdog_emitters_alive = (e.is_alive() for e in self.local_observer_thread.emitters)

        return all(base_threads_alive) and all(watchdog_emitters_alive)

    def rebuild_rev_file(self):
        """
        Rebuilds the rev file by comparing remote with local files and updating rev
        numbers from the Dropbox server. Files are compared by their content hashes
        and conflicting copies are created if the contents differ.
        Reindexing may take several minutes, depending on the size of your Dropbox. If
        a file is modified during this process before it has been re-indexed, any changes
        to it will be flagged as sync conflicts. If a file is deleted before it has been
        re-indexed, the deletion will be reversed.
        """

        logger.info("Rebuilding index...")

        was_running = self.running.is_set()

        self.stop()  # stop all sync threads
        self.upload_thread.join()

        # reset sync state
        # if Maestral is killed while rebuilding, this will trigger a new download
        self.sync.last_sync = 0.0
        self.sync.last_cursor = ""
        self.sync.clear_rev_index()

        # Re-download Dropbox from server. If a local file already exists, content hashes
        # are compared. If files are identical, the local rev will be set accordingly,
        # otherwise a conflicting copy will be created.

        completed = False
        while not completed:
            try:
                self.sync.get_remote_dropbox(ignore_excluded=True)
                completed = True
            except ConnectionError:
                logger.info(DISCONNECTED)

        self.sync.last_sync = time.time()

        # Resume syncing. This will upload all changes which occurred
        # while rebuilding, including conflicting copies. Files that were
        # deleted before re-indexing will be downloaded again. Files changes
        # which occurred before the file was re-indexed will result in a conflicting
        # copy.

        if was_running:
            self.start()


# ========================================================================================
# Helper functions
# ========================================================================================


def _get_dest_path(e):
    return getattr(e, "dest_path", e.src_path)


def get_local_hash(local_path):
    """
    Computes content hash of a local file.

    :param str local_path: Path to local file.
    :returns: Content hash to compare with Dropbox's content hash,
        or "folder" if the path points to a directory. ``None`` if there
        is nothing at the path.
    :rtype: str
    """

    hasher = DropboxContentHasher()

    try:
        with open(local_path, "rb") as f:
            while True:
                chunk = f.read(1024)
                if len(chunk) == 0:
                    break
                hasher.update(chunk)

        return str(hasher.hexdigest())
    except IsADirectoryError:
        return "folder"
    except FileNotFoundError:
        return None
    finally:
        del hasher


def get_ctime(local_path):
    """
    Returns the ctime of a local item or -1.0 if there is nothing at the path.

    :param str local_path:
    :returns: Ctime or -1.0.
    :rtype: float
    """

    try:
        return os.stat(local_path).st_ctime
    except FileNotFoundError:
        return -1.0


def remove_from_queue(q, item):
    """
    Tries to remove an item from a queue.

    :param Queue q: Queue to remove item from.
    :param item: Item to remove
    """

    with q.mutex:
        try:
            q.queue.remove(item)
        except ValueError:
            pass
