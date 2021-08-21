# -*- coding: utf-8 -*-
"""This module contains the main syncing functionality."""

# system imports
import sys
import os
import os.path as osp
import time
import random
import uuid
import urllib.parse
import enum
import sqlite3
import logging
import gc
from stat import S_ISDIR
from pprint import pformat
from threading import Event, Condition, RLock, current_thread
from concurrent.futures import ThreadPoolExecutor
from queue import Queue, Empty
from collections import abc, defaultdict
from contextlib import contextmanager
from tempfile import NamedTemporaryFile
from typing import (
    Optional,
    Any,
    Set,
    List,
    Dict,
    Tuple,
    Union,
    Iterator,
    Callable,
    DefaultDict,
    cast,
)

# external imports
import click
from pathspec import PathSpec
from dropbox.files import Metadata, DeletedMetadata, FileMetadata, FolderMetadata, WriteMode, ListFolderResult  # type: ignore
from watchdog.events import FileSystemEventHandler  # type: ignore
from watchdog.events import (
    EVENT_TYPE_CREATED,
    EVENT_TYPE_DELETED,
    EVENT_TYPE_MOVED,
    EVENT_TYPE_MODIFIED,
)
from watchdog.events import (
    DirModifiedEvent,
    FileModifiedEvent,
    DirCreatedEvent,
    FileCreatedEvent,
    DirDeletedEvent,
    FileDeletedEvent,
    DirMovedEvent,
    FileMovedEvent,
    FileSystemEvent,
)

# local imports
from . import notify
from .config import MaestralConfig, MaestralState
from .constants import (
    IDLE,
    EXCLUDED_FILE_NAMES,
    EXCLUDED_DIR_NAMES,
    MIGNORE_FILE,
    FILE_CACHE,
)
from .errors import (
    SyncError,
    CancelledError,
    NoDropboxDirError,
    CacheDirError,
    PathError,
    NotFoundError,
    FileConflictError,
    FolderConflictError,
    InvalidDbidError,
    DatabaseError,
)
from .client import (
    DropboxClient,
    os_to_maestral_error,
    convert_api_errors,
)
from .database import (
    SyncEvent,
    HashCacheEntry,
    IndexEntry,
    SyncDirection,
    SyncStatus,
    ItemType,
    ChangeType,
)
from .logging import scoped_logger
from .utils import removeprefix, sanitize_string, exc_info_tuple
from .utils.caches import LRUCache
from .utils.integration import (
    cpu_usage_percent,
    CPU_COUNT,
)
from .utils.path import (
    generate_cc_name,
    move,
    delete,
    is_child,
    is_equal_or_child,
    content_hash,
    walk,
    normalize,
    normalize_case,
    normalize_unicode,
    equivalent_path_candidates,
)
from .utils.orm import Database, Manager
from .utils.appdirs import get_data_path


__all__ = [
    "Conflict",
    "FSEventHandler",
    "SyncEngine",
]

umask = os.umask(0o22)
os.umask(umask)


# ======================================================================================
# Syncing functionality
# ======================================================================================


class Conflict(enum.Enum):
    """Enumeration of sync conflict types"""

    RemoteNewer = "remote newer"
    Conflict = "conflict"
    Identical = "identical"
    LocalNewerOrIdentical = "local newer or identical"


class _Ignore:
    def __init__(
        self,
        event: FileSystemEvent,
        start_time: float,
        ttl: Optional[float],
        recursive: bool,
    ) -> None:
        self.event = event
        self.start_time = start_time
        self.ttl = ttl
        self.recursive = recursive

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}(event={self.event}, "
            f"recursive={self.recursive}, ttl={self.ttl})>"
        )


class FSEventHandler(FileSystemEventHandler):
    """A local file event handler

    Handles captured file events and adds them to :class:`SyncEngine`'s file event queue
    to be uploaded by :meth:`upload_worker`. This acts as a translation layer between
    :class:`watchdog.Observer` and :class:`SyncEngine`.

    White lists of event types to handle are supplied as ``file_event_types`` and
    ``dir_event_types``. This is for forward compatibility as additional event types
    may be added to watchdog in the future.

    :param file_event_types: Types of file events to handle. This acts as a whitelist.
    :param dir_event_types: Types of folder events to handle. This acts as a whitelist.

    :cvar float ignore_timeout: Timeout in seconds after which filters for ignored
        events will expire.
    """

    _ignored_events: Set[_Ignore]
    local_file_event_queue: "Queue[FileSystemEvent]"

    def __init__(
        self,
        file_event_types: Tuple[str, ...] = (
            EVENT_TYPE_CREATED,
            EVENT_TYPE_DELETED,
            EVENT_TYPE_MODIFIED,
            EVENT_TYPE_MOVED,
        ),
        dir_event_types: Tuple[str, ...] = (
            EVENT_TYPE_CREATED,
            EVENT_TYPE_DELETED,
            EVENT_TYPE_MOVED,
        ),
    ) -> None:
        super().__init__()

        self._enabled = False
        self.has_events = Condition()

        self.file_event_types = file_event_types
        self.dir_event_types = dir_event_types

        self.file_event_types = file_event_types
        self.dir_event_types = dir_event_types

        self._ignored_events = set()
        self.ignore_timeout = 2.0
        self.local_file_event_queue = Queue()

    @property
    def enabled(self) -> bool:
        """Whether queuing of events is enabled."""
        return self._enabled

    def enable(self) -> None:
        """Turn on queueing of events."""
        self._enabled = True

    def disable(self) -> None:
        """Turn off queueing of new events and remove all events from queue."""
        self._enabled = False

        while True:
            try:
                self.local_file_event_queue.get_nowait()
            except Empty:
                break

    @contextmanager
    def ignore(
        self, *events: FileSystemEvent, recursive: bool = True
    ) -> Iterator[None]:
        """A context manager to ignore local file events

        Once a matching event has been registered, further matching events will no
        longer be ignored unless ``recursive`` is ``True``. If no matching event has
        occurred before leaving the context, the event will be ignored for
        :attr:`ignore_timeout` sec after leaving then context and then discarded. This
        accounts for possible delays in the emission of local file system events.

        This context manager is used to filter out file system events caused by maestral
        itself, for instance during a download or when moving a conflict.

        :Example:

            Prevent triggereing a sync event when creating a local file:

            >>> from watchdog.events import FileCreatedEvent
            >>> from maestral.main import Maestral
            >>> m = Maestral()
            >>> with m.sync.fs_events.ignore(FileCreatedEvent('path')):
            ...     open('path').close()

        :param events: Local events to ignore.
        :param recursive: If ``True``, all child events of a directory event will be
            ignored as well. This parameter will be ignored for file events.
        """

        now = time.time()
        new_ignores = set()
        for e in events:
            new_ignores.add(
                _Ignore(
                    event=e,
                    start_time=now,
                    ttl=None,
                    recursive=recursive and e.is_directory,
                )
            )
        self._ignored_events.update(new_ignores)  # this is atomic

        try:
            yield
        finally:
            for ignore in new_ignores:
                ignore.ttl = time.time() + self.ignore_timeout

    def expire_ignored_events(self) -> None:
        """Removes all expired ignore entries."""

        now = time.time()
        for ignore in self._ignored_events.copy():
            if ignore.ttl and ignore.ttl < now:
                self._ignored_events.discard(ignore)

    def _is_ignored(self, event: FileSystemEvent) -> bool:
        """
        Checks if a file system event should been explicitly ignored because it was
        triggered by Maestral itself.

        :param event: Local file system event.
        :returns: Whether the event should be ignored.
        """

        for ignore in self._ignored_events.copy():

            # check for expired events
            if ignore.ttl and ignore.ttl < time.time():
                self._ignored_events.discard(ignore)
                continue

            ignore_event = ignore.event
            recursive = ignore.recursive

            if event == ignore_event:

                if not recursive:
                    self._ignored_events.discard(ignore)

                return True

            elif recursive:

                type_match = event.event_type == ignore_event.event_type
                src_match = is_equal_or_child(event.src_path, ignore_event.src_path)
                dest_match = is_equal_or_child(
                    get_dest_path(event), get_dest_path(ignore_event)
                )

                if type_match and src_match and dest_match:
                    return True

        return False

    def on_any_event(self, event: FileSystemEvent) -> None:
        """
        Checks if the system file event should be ignored. If not, adds it to the queue
        for events to upload. If syncing is paused or stopped, all events will be
        ignored.

        :param event: Watchdog file event.
        """

        # ignore events if asked to do so
        if not self._enabled:
            return

        # handle only whitelisted dir event types
        if event.is_directory and event.event_type not in self.dir_event_types:
            return

        # handle only whitelisted file event types
        if not event.is_directory and event.event_type not in self.file_event_types:
            return

        # check if event should be ignored
        if self._is_ignored(event):
            return

        self.queue_event(event)

    def queue_event(self, event: FileSystemEvent) -> None:
        """
        Queues an individual file system event. Notifies / wakes up all threads that are
        waiting with :meth:`wait_for_event`.

        :param event: File system event to queue.
        """
        with self.has_events:
            self.local_file_event_queue.put(event)
            self.has_events.notify_all()

    def wait_for_event(self, timeout: float = 40) -> bool:
        """
        Blocks until an event is available in the queue or a timeout occurs, whichever
        comes first. You can use with method to wait for file system events in another
        thread.

        .. note:: If there are multiple threads waiting for events, all of them will be
            notified. If one of those threads starts getting events from
            :attr:`local_file_event_queue`, other threads may find that the queue is
            empty despite being woken. You should therefore be prepared to handle an
            empty queue even if this method returns ``True``.

        :param timeout: Maximum time to block in seconds.
        :returns: ``True`` if an event is available, ``False`` if the call returns due
            to a timeout.
        """

        with self.has_events:
            if self.local_file_event_queue.qsize() > 0:
                return True
            self.has_events.wait(timeout)
            return self.local_file_event_queue.qsize() > 0


class PersistentStateMutableSet(abc.MutableSet):
    """Wraps a list in our state file as a MutableSet

    :param config_name: Name of config (determines name of state file).
    :param section: Section name in state file.
    :param option: Option name in state file.
    """

    def __init__(self, config_name: str, section: str, option: str) -> None:
        super().__init__()
        self.config_name = config_name
        self.section = section
        self.option = option
        self._state = MaestralState(config_name)
        self._lock = RLock()

    def __iter__(self) -> Iterator[Any]:
        with self._lock:
            return iter(self._state.get(self.section, self.option))

    def __contains__(self, entry: Any) -> bool:
        with self._lock:
            return entry in self._state.get(self.section, self.option)

    def __len__(self):
        with self._lock:
            return len(self._state.get(self.section, self.option))

    def add(self, entry: Any) -> None:
        with self._lock:
            state_list = self._state.get(self.section, self.option)
            state_list = set(state_list)
            state_list.add(entry)
            self._state.set(self.section, self.option, list(state_list))

    def discard(self, entry: Any) -> None:
        with self._lock:
            state_list = self._state.get(self.section, self.option)
            state_list = set(state_list)
            state_list.discard(entry)
            self._state.set(self.section, self.option, list(state_list))

    def update(self, *others: Any) -> None:
        with self._lock:
            state_list = self._state.get(self.section, self.option)
            state_list = set(state_list)
            state_list.update(*others)
            self._state.set(self.section, self.option, list(state_list))

    def difference_update(self, *others: Any) -> None:
        with self._lock:
            state_list = self._state.get(self.section, self.option)
            state_list = set(state_list)
            state_list.difference_update(*others)
            self._state.set(self.section, self.option, list(state_list))

    def clear(self) -> None:
        """Clears all elements."""
        with self._lock:
            self._state.set(self.section, self.option, [])

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}(section='{self.section}',"
            f"option='{self.option}', entries={list(self)})>"
        )


class SyncEngine:
    """Class that handles syncing with Dropbox

    Provides methods to wait for local or remote changes and sync them, including
    conflict resolution and updates to our index.

    :param client: Dropbox API client instance.
    """

    sync_errors: Set[SyncError]
    syncing: Dict[str, SyncEvent]
    _case_conversion_cache: LRUCache

    _max_history = 1000
    _num_threads = min(32, CPU_COUNT * 3)

    def __init__(self, client: DropboxClient):

        self.client = client
        self.config_name = self.client.config_name
        self.fs_events = FSEventHandler()
        self._logger = scoped_logger(__name__, self.config_name)

        self.sync_lock = RLock()
        self._db_lock = RLock()

        self._conf = MaestralConfig(self.config_name)
        self._state = MaestralState(self.config_name)
        self.reload_cached_config()

        self.desktop_notifier = notify.MaestralDesktopNotifier(self.config_name)

        # upload_errors / download_errors: contains failed uploads / downloads
        # (from sync errors) to retry later
        self.upload_errors = PersistentStateMutableSet(
            self.config_name, section="sync", option="upload_errors"
        )
        self.download_errors = PersistentStateMutableSet(
            self.config_name, section="sync", option="download_errors"
        )
        # pending_uploads / pending_downloads: contains interrupted uploads / downloads
        # to retry later. Running uploads / downloads can be stored in these lists to be
        # resumed if Maestral quits unexpectedly. This used for downloads which are not
        # part of the regular sync cycle and are therefore not restarted automatically.
        self.pending_downloads = PersistentStateMutableSet(
            self.config_name, section="sync", option="pending_downloads"
        )
        self.pending_uploads = PersistentStateMutableSet(
            self.config_name, section="sync", option="pending_uploads"
        )

        # data structures for internal communication
        self.sync_errors = set()
        self._cancel_requested = Event()

        # data structures for user information
        self.syncing = {}

        # initialize SQLite database
        self._db_path = get_data_path("maestral", f"{self.config_name}.db")

        if not osp.exists(self._db_path):
            # reset sync state if DB is missing
            self.remote_cursor = ""
            self.local_cursor = 0.0

        self._db = Database(self._db_path, check_same_thread=False)
        self._db_manager_index = Manager(self._db, IndexEntry)
        self._db_manager_history = Manager(self._db, SyncEvent)
        self._db_manager_hash_cache = Manager(self._db, HashCacheEntry)

        # caches
        self._case_conversion_cache = LRUCache(capacity=5000)

        # clean our file cache
        self.clean_cache_dir(raise_error=False)

    def reload_cached_config(self) -> None:
        """
        Reloads all config and state values that are otherwise cached by this class for
        faster access. Call this method if config or state values where modified
        directly instead of using :class:`SyncEngine` APIs.
        """

        self._dropbox_path = self._conf.get("sync", "path")
        self._mignore_path = osp.join(self._dropbox_path, MIGNORE_FILE)
        self._file_cache_path = osp.join(self._dropbox_path, FILE_CACHE)

        self._excluded_items = self._conf.get("sync", "excluded_items")
        self._max_cpu_percent = self._conf.get("sync", "max_cpu_percent") * CPU_COUNT
        self._local_cursor = self._state.get("sync", "lastsync")

        self.load_mignore_file()

    # ==== config access ===============================================================

    @property
    def dropbox_path(self) -> str:
        """
        Path to local Dropbox folder, as loaded from the config file. Before changing
        :attr:`dropbox_path`, make sure that syncing is paused. Move the dropbox folder
        to the new location before resuming the sync. Changes are saved to the config
        file.
        """
        return self._dropbox_path

    @dropbox_path.setter
    def dropbox_path(self, path: str) -> None:
        """Setter: dropbox_path"""

        path = osp.realpath(path)

        with self.sync_lock:
            self._dropbox_path = path
            self._mignore_path = osp.join(self._dropbox_path, MIGNORE_FILE)
            self._file_cache_path = osp.join(self._dropbox_path, FILE_CACHE)
            self._conf.set("sync", "path", path)

    @property
    def database_path(self) -> str:
        """Path SQLite database."""
        return self._db_path

    @property
    def file_cache_path(self) -> str:
        """Path to cache folder for temporary files (read only). The cache folder
        '.maestral.cache' is located inside the local Dropbox folder to prevent file
        transfer between different partitions or drives during sync."""
        return self._file_cache_path

    @property
    def excluded_items(self) -> List[str]:
        """List of all files and folders excluded from sync. Changes are saved to the
        config file. If a parent folder is excluded, its children will automatically be
        removed from the list. If only children are given but not the parent folder, any
        new items added to the parent will be synced. Change this property *before*
        downloading newly included items or deleting excluded items."""
        return self._excluded_items

    @excluded_items.setter
    def excluded_items(self, folder_list: List[str]) -> None:
        """Setter: excluded_items"""

        with self.sync_lock:
            clean_list = self.clean_excluded_items_list(folder_list)
            self._excluded_items = clean_list
            self._conf.set("sync", "excluded_items", clean_list)

    @staticmethod
    def clean_excluded_items_list(folder_list: List[str]) -> List[str]:
        """
        Removes all duplicates and children of excluded items from the excluded items
        list.

        :param folder_list: Dropbox paths to exclude.
        :returns: Cleaned up items.
        """

        # remove duplicate entries by creating set, strip trailing '/'
        folder_set = {normalize(f).rstrip("/") for f in folder_list}

        # remove all children of excluded folders
        clean_list = list(folder_set)
        for folder in folder_set:
            clean_list = [f for f in clean_list if not is_child(f, folder)]

        return clean_list

    @property
    def max_cpu_percent(self) -> float:
        """Maximum CPU usage for parallel downloads or uploads in percent of the total
        available CPU time per core. Individual workers in a thread pool will pause
        until the usage drops below this value. Tasks in the main thread such as
        indexing file changes may still use more CPU time. Setting this to 200% means
        that two full logical CPU core can be used."""
        return self._max_cpu_percent

    @max_cpu_percent.setter
    def max_cpu_percent(self, percent: float) -> None:
        """Setter: max_cpu_percent."""
        self._max_cpu_percent = percent
        self._conf.set("sync", "max_cpu_percent", percent // CPU_COUNT)

    # ==== sync state ==================================================================

    @property
    def remote_cursor(self) -> str:
        """Cursor from last sync with remote Dropbox. The value is updated and saved to
        the config file on every successful download of remote changes."""
        return self._state.get("sync", "cursor")

    @remote_cursor.setter
    def remote_cursor(self, cursor: str) -> None:
        """Setter: last_cursor"""
        with self.sync_lock:
            self._state.set("sync", "cursor", cursor)

        self._logger.debug("Remote cursor saved: %s", cursor)

    @property
    def local_cursor(self) -> float:
        """Time stamp from last sync with remote Dropbox. The value is updated and saved
        to the config file on every successful upload of local changes."""
        return self._local_cursor

    @local_cursor.setter
    def local_cursor(self, last_sync: float) -> None:
        """Setter: local_cursor"""
        with self.sync_lock:
            self._local_cursor = last_sync
            self._state.set("sync", "lastsync", last_sync)

        self._logger.debug("Local cursor saved: %s", last_sync)

    @property
    def last_change(self) -> float:
        """The time stamp of the last file change or 0.0 if there are no file changes in
        our history."""

        with self._database_access():

            res = self._db.execute("SELECT MAX(last_sync) FROM 'index'").fetchone()

            if res:
                return res["MAX(sync_time)"] or 0.0
            else:
                return 0.0

    @property
    def last_reindex(self) -> float:
        """Time stamp of last full indexing. This is used to determine when the next
        full indexing should take place."""
        return self._state.get("sync", "last_reindex")

    @property
    def history(self) -> List[SyncEvent]:
        """A list of the last SyncEvents in our history. History will be kept for the
        interval specified by the config value ``keep_history`` (defaults to two weeks)
        but at most 1,000 events will be kept."""
        with self._database_access():

            sync_events = self._db_manager_history.query_to_objects(
                "SELECT * FROM history ORDER BY IFNULL(change_time, sync_time)"
            )
            return cast(List[SyncEvent], sync_events)

    def clear_sync_history(self) -> None:
        """Clears the sync history."""
        with self._database_access():
            self._db.execute("DROP TABLE history")
            self._db_manager_history.create_table()
            self._db_manager_history.clear_cache()

    def reset_sync_state(self) -> None:
        """Resets all saved sync state. Settings are not affected."""

        if self.busy():
            raise RuntimeError("Cannot reset sync state while syncing.")

        self.remote_cursor = ""
        self.local_cursor = 0.0
        self.clear_index()
        self.clear_sync_history()

        self._logger.debug("Sync state reset")

    # ==== index management ============================================================

    def get_index(self) -> List[IndexEntry]:
        """
        Returns a copy of the local index of synced files and folders.

        :returns: List of index entries.
        """
        with self._database_access():
            return cast(List[IndexEntry], self._db_manager_index.all())

    def iter_index(self) -> Iterator[IndexEntry]:
        """
        Returns an iterator over the local index of synced files and folders.

        :returns: Iterator over index entries.
        """
        with self._database_access():
            for entries in self._db_manager_index.iter_all():
                for entry in entries:
                    yield cast(IndexEntry, entry)

    def index_count(self) -> int:
        """
        Returns the number if items in our index without loading any items.

        :returns: Number of index entries.
        """

        with self._database_access():
            return self._db_manager_index.count()

    def get_local_rev(self, dbx_path_lower: str) -> Optional[str]:
        """
        Gets revision number of local file.

        :param dbx_path_lower: Normalized lower case Dropbox path.
        :returns: Revision number as str or ``None`` if no local revision number has
            been saved.
        """

        entry = self.get_index_entry(dbx_path_lower)

        if entry:
            return entry.rev
        else:
            return None

    def get_last_sync(self, dbx_path_lower: str) -> float:
        """
        Returns the timestamp of last sync for an individual path.

        :param dbx_path_lower: Normalized lower case Dropbox path.
        :returns: Time of last sync.
        """

        entry = self.get_index_entry(dbx_path_lower)

        if entry:
            last_sync = entry.last_sync or 0.0
        else:
            last_sync = 0.0

        return max(last_sync, self.local_cursor)

    def get_index_entry(self, dbx_path_lower: str) -> Optional[IndexEntry]:
        """
        Gets the index entry for the given Dropbox path.

        :param dbx_path_lower: Normalized lower case Dropbox path.
        :returns: Index entry or ``None`` if no entry exists for the given path.
        """

        with self._database_access():
            entry = self._db_manager_index.get(dbx_path_lower)
            return cast(Optional[IndexEntry], entry)

    def get_local_hash(self, local_path: str) -> Optional[str]:
        """
        Computes content hash of a local file.

        :param local_path: Absolute path on local drive.
        :returns: Content hash to compare with Dropbox's content hash, or 'folder' if
            the path points to a directory. ``None`` if there is nothing at the path.
        """

        try:
            stat = os.stat(local_path)
        except (FileNotFoundError, NotADirectoryError):
            # remove any existing cache entries for path
            with self._database_access():
                cache_entry = self._db_manager_hash_cache.get(local_path)
                cache_entry = cast(Optional[HashCacheEntry], cache_entry)
                if cache_entry:
                    self._db_manager_hash_cache.delete(cache_entry)
            return None
        except OSError as err:
            raise os_to_maestral_error(err)

        if S_ISDIR(stat.st_mode):
            # take shortcut: return 'folder'
            return "folder"

        mtime: Optional[float] = stat.st_mtime

        with self._database_access():
            # check cache for an up-to-date content hash and return if it exists
            cache_entry = self._db_manager_hash_cache.get(local_path)
            cache_entry = cast(Optional[HashCacheEntry], cache_entry)

            if cache_entry and cache_entry.mtime == mtime:
                return cache_entry.hash_str

        with convert_api_errors():
            hash_str, mtime = content_hash(local_path)

        self._save_local_hash(local_path, hash_str, mtime)

        return hash_str

    def _save_local_hash(
        self, local_path: str, hash_str: Optional[str], mtime: Optional[float]
    ) -> None:
        """
        Save the content hash for a file in our cache.

        :param local_path: Absolute path on local drive.
        :param hash_str: Hash string to save. If None, the existing cache entry will be
            deleted.
        :param mtime: Mtime of the file when the hash was computed.
        """

        with self._database_access():

            cache_entry = self._db_manager_hash_cache.get(local_path)
            cache_entry = cast(Optional[HashCacheEntry], cache_entry)

            if hash_str:

                if cache_entry:
                    cache_entry.hash_str = hash_str
                    cache_entry.mtime = mtime

                    self._db_manager_hash_cache.update(cache_entry)

                else:
                    cache_entry = HashCacheEntry(
                        local_path=local_path, hash_str=hash_str, mtime=mtime
                    )
                    self._db_manager_hash_cache.save(cache_entry)
            else:
                if cache_entry:
                    self._db_manager_hash_cache.delete(cache_entry)
                else:
                    pass

    def clear_hash_cache(self) -> None:
        """Clears the sync history."""
        with self._database_access():
            self._db.execute("DROP TABLE hash_cache")
            self._db_manager_hash_cache.clear_cache()
            self._db_manager_hash_cache.create_table()

    def update_index_from_sync_event(self, event: SyncEvent) -> None:
        """
        Updates the local index from a SyncEvent.

        :param event: SyncEvent from download.
        """

        if event.change_type is not ChangeType.Removed and not event.rev:
            raise ValueError("Rev required to update index")

        dbx_path_lower = event.dbx_path_lower

        with self._database_access():

            # remove any entries for deleted or moved items

            if event.change_type is ChangeType.Removed:
                self.remove_node_from_index(dbx_path_lower)
            elif event.change_type is ChangeType.Moved:
                self.remove_node_from_index(event.dbx_path_from_lower)

            # add or update entries for created or modified items

            if event.change_type is not ChangeType.Removed:

                entry = self.get_index_entry(dbx_path_lower)

                if entry:
                    # update existing entry
                    entry.dbx_id = event.dbx_id
                    entry.dbx_path_cased = event.dbx_path
                    entry.item_type = event.item_type
                    entry.last_sync = self._get_ctime(event.local_path)
                    entry.rev = event.rev
                    entry.content_hash = event.content_hash

                    self._db_manager_index.update(entry)

                else:
                    # create new entry
                    entry = IndexEntry(
                        dbx_path_cased=event.dbx_path,
                        dbx_path_lower=dbx_path_lower,
                        dbx_id=event.dbx_id,
                        item_type=event.item_type,
                        last_sync=self._get_ctime(event.local_path),
                        rev=event.rev,
                        content_hash=event.content_hash,
                    )

                    self._db_manager_index.save(entry)

    def update_index_from_dbx_metadata(
        self, md: Metadata, client: Optional[DropboxClient] = None
    ) -> None:
        """
        Updates the local index from Dropbox metadata.

        :param md: Dropbox metadata.
        :param client: DropboxClient instance to use. If not given, use the global
            instance.
        """

        client = client or self.client

        with self._database_access():

            if isinstance(md, DeletedMetadata):
                self.remove_node_from_index(md.path_lower)

            else:

                if isinstance(md, FileMetadata):
                    rev = md.rev
                    hash_str = md.content_hash
                    item_type = ItemType.File
                else:
                    rev = "folder"
                    hash_str = "folder"
                    item_type = ItemType.Folder

                # construct correct display path from ancestors
                dbx_path_cased = self.correct_case(md.path_display, client)

                # update existing / create new entry
                entry = self.get_index_entry(md.path_lower)

                if entry:
                    entry.dbx_id = md.id
                    entry.dbx_path_cased = dbx_path_cased
                    entry.item_type = item_type
                    entry.last_sync = None
                    entry.rev = rev
                    entry.content_hash = hash_str

                    self._db_manager_index.update(entry)

                else:
                    entry = IndexEntry(
                        dbx_path_cased=dbx_path_cased,
                        dbx_path_lower=md.path_lower,
                        dbx_id=md.id,
                        item_type=item_type,
                        last_sync=None,
                        rev=rev,
                        content_hash=hash_str,
                    )

                    self._db_manager_index.save(entry)

    def remove_node_from_index(self, dbx_path_lower: str) -> None:
        """
        Removes any local index entries for the given path and all its children.

        :param dbx_path_lower: Normalized lower case Dropbox path.
        """

        with self._database_access():

            dbx_path_lower = dbx_path_lower.rstrip("/")

            try:
                self._db.execute(
                    "DELETE FROM 'index' WHERE dbx_path_lower = ?", dbx_path_lower
                )
                self._db.execute(
                    "DELETE FROM 'index' WHERE dbx_path_lower LIKE ?",
                    f"{dbx_path_lower}/%",
                )
            except UnicodeEncodeError:
                return

            self._db_manager_index.clear_cache()

    def clear_index(self) -> None:
        """Clears the revision index."""
        with self._database_access():
            self._db.execute("DROP TABLE 'index'")
            self._db_manager_index.clear_cache()
            self._db_manager_index.create_table()

    # ==== mignore management ==========================================================

    @property
    def mignore_path(self) -> str:
        """Path to mignore file on local drive (read only)."""
        return self._mignore_path

    @property
    def mignore_rules(self) -> PathSpec:
        """List of mignore rules following git wildmatch syntax (read only)."""
        return self._mignore_rules

    def load_mignore_file(self) -> None:
        """
        Loads rules from mignore file. No rules are loaded if the file does
        not exist or cannot be read.

        :returns: PathSpec instance with ignore patterns.
        """
        try:
            with open(self.mignore_path) as f:
                spec = f.read()
        except OSError as err:
            self._logger.debug("Could not load mignore rules: %s", err.strerror)
            spec = ""

        self._mignore_rules = PathSpec.from_lines("gitwildmatch", spec.splitlines())

    # ==== helper functions ============================================================

    def ensure_dropbox_folder_present(self) -> None:
        """
        Checks if the Dropbox folder still exists where we expect it to be.

        :raises NoDropboxDirError: When local Dropbox directory does not exist.
        """

        if not osp.isdir(self.dropbox_path):
            title = "Dropbox folder missing"
            msg = (
                "Please move the Dropbox folder back to its original location "
                "or restart Maestral to set up a new folder."
            )
            raise NoDropboxDirError(title, msg)

    def ensure_cache_dir_present(self) -> None:
        """
        Checks for or creates a directory at :attr:`file_cache_path`.

        :raises CacheDirError: When local cache directory cannot be created.
        """

        retries = 0
        max_retries = 10

        while not osp.isdir(self.file_cache_path):
            try:
                # This will raise FileExistsError if file_cache_path
                # exists but is a file instead of a directory.
                os.makedirs(self.file_cache_path, exist_ok=True)
                return
            except FileExistsError:
                # Remove the file that's in our way, retry creation.
                self.clean_cache_dir()
            except NotADirectoryError:
                # Ensure that parent directories exist as expected.
                self.ensure_dropbox_folder_present()
            except OSError as err:
                raise CacheDirError(
                    f"Cannot create cache directory: {err.strerror}",
                    "Please check if you have write permissions for "
                    f"{self._file_cache_path}.",
                )

            if retries > max_retries:
                raise CacheDirError(
                    "Cannot create cache directory",
                    "Exceeded maximum number of retries",
                )

            time.sleep(0.01)
            retries += 1

    def clean_cache_dir(self, raise_error: bool = True) -> None:
        """
        Removes all items in the cache directory.

        :param raise_error: Whether errors should raised or only logged.
        """

        with self.sync_lock:
            try:
                delete(self._file_cache_path, raise_error=True)
            except (FileNotFoundError, IsADirectoryError):
                pass
            except OSError as err:
                exc = CacheDirError(
                    f"Cannot create cache directory: {err.strerror}",
                    "Please check if you have write permissions for "
                    f"{self._file_cache_path}.",
                )

                if raise_error:
                    raise exc

                self._logger.error(exc.title, exc_info=exc_info_tuple(exc))
                self.desktop_notifier.notify(exc.title, exc.message, level=notify.ERROR)

    def _new_tmp_file(self) -> str:
        """Returns a new temporary file name in our cache directory."""
        self.ensure_cache_dir_present()
        try:
            with NamedTemporaryFile(dir=self.file_cache_path, delete=False) as f:
                try:
                    os.chmod(f.fileno(), 0o666 & ~umask)
                except OSError as err:
                    # Can occur on file system's that don't support POSIX permissions
                    # such as NTFS mounted without the permissions option.
                    self._logger.debug("Cannot set permissions: %s", err.strerror)
                return f.name
        except OSError as err:
            raise CacheDirError(
                f"Cannot create cache directory: {err.strerror}",
                "Please check if you have write permissions for "
                f"{self._file_cache_path}.",
            )

    def correct_case(
        self, dbx_path: str, client: Optional[DropboxClient] = None
    ) -> str:
        """
        Converts a Dropbox path with correctly cased basename to a fully cased path.
        This is useful because the Dropbox API guarantees the correct casing for the
        basename only. In practice, casing of parent directories is often incorrect.
        This method retrieves the correct casing of of all ancestors in the path, either
        from our cache, our database, or from Dropbox servers.

        Performance may vary significantly with the number of parent folders and the
        method used to resolve the casing of all parent directory names:

        1) If the parent directory is already in our cache, performance is O(1).
        2) If the parent directory is already in our sync index, performance is slower
           because it requires a sqlite query but still O(1).
        3) If the parent directory is unknown to us, its metadata (including the correct
           casing of directory's basename) is queried from Dropbox. This is used to
           construct a correctly cased path by calling :meth:`correct_case` again. At
           best, performance will be of O(2) if the parent directory is known to us, at
           worst if will be of order O(n) involving queries to Dropbox servers for each
           parent directory.

        When calling :meth:`correct_case` repeatedly for paths from the same tree, it is
        therefore best to do so in hierarchical order.

        :param dbx_path: Dropbox path with correctly cased basename, as provided by
            :attr:`dropbox.files.Metadata.path_display` or
            :attr:`dropbox.files.Metadata.name`.
        :param client: Client instance to use. If not given, use the instance provided
            in the constructor.
        :returns: Correctly cased Dropbox path.
        """

        dbx_path_lower = normalize(dbx_path)

        client = client or self.client

        dirname, basename = osp.split(dbx_path)
        dirname_lower = osp.dirname(dbx_path_lower)

        dirname_cased = self._correct_case_helper(dirname, dirname_lower, client)
        path_cased = osp.join(dirname_cased, basename)

        # add our result to the cache
        self._case_conversion_cache.put(dbx_path_lower, path_cased)

        return path_cased

    def _correct_case_helper(
        self, dbx_path: str, dbx_path_lower: str, client: DropboxClient
    ) -> str:
        """
        :param dbx_path: Uncased or randomly cased Dropbox path.
        :param dbx_path_lower: Normalized fully lower cased Dropbox path.
        :param client: Client instance to use.
        :returns: Correctly cased Dropbox path.
        """

        # check for root folder
        if dbx_path == "/":
            return dbx_path

        # check in our conversion cache
        dbx_path_cased = self._case_conversion_cache.get(dbx_path_lower)

        if dbx_path_cased:
            return dbx_path_cased

        # try to get casing from our index, this is slower
        with self._database_access():
            entry = self.get_index_entry(dbx_path_lower)

        if entry:
            dbx_path_cased = entry.dbx_path_cased
        else:
            # fall back to querying from server
            md = client.get_metadata(dbx_path)
            if md:
                # recurse over parent directories
                dbx_path_cased = self.correct_case(md.path_display, client)
            else:
                # give up
                dbx_path_cased = dbx_path

        # add our result to the cache
        self._case_conversion_cache.put(dbx_path_lower, dbx_path_cased)

        return dbx_path_cased

    def to_dbx_path(self, local_path: str) -> str:
        """
        Converts a local path to a path relative to the Dropbox folder. Casing of the
        given ``local_path`` will be preserved.

        :param local_path: Absolute path on local drive.
        :returns: Relative path with respect to Dropbox folder.
        :raises ValueError: When the path lies outside of the local Dropbox folder.
        """

        if not is_equal_or_child(local_path, self.dropbox_path):
            raise ValueError(f'"{local_path}" is not in "{self.dropbox_path}"')
        return "/" + removeprefix(local_path, self.dropbox_path).lstrip("/")

    def to_dbx_path_lower(self, local_path: str) -> str:
        """
        Converts a local path to a path relative to the Dropbox folder. The path will be
        normalized as on Dropbox servers (lower case and some additional
        normalisations).

        :param local_path: Absolute path on local drive.
        :returns: Relative path with respect to Dropbox folder.
        :raises ValueError: When the path lies outside of the local Dropbox folder.
        """
        return normalize(self.to_dbx_path(local_path))

    def to_local_path_from_cased(self, dbx_path_cased: str) -> str:
        """
        Converts a correctly cased Dropbox path to the corresponding local path. This is
        more efficient than :meth:`to_local_path` which accepts uncased paths.

        :param dbx_path_cased: Path relative to Dropbox folder, correctly cased.
        :returns: Corresponding local path on drive.
        """

        return f"{self.dropbox_path}{dbx_path_cased}"

    def to_local_path(
        self, dbx_path: str, client: Optional[DropboxClient] = None
    ) -> str:
        """
        Converts a Dropbox path to the corresponding local path. Only the basename must
        be correctly cased, as guaranteed by the Dropbox API for the ``display_path``
        attribute of file or folder metadata.

        This method slower than :meth:`to_local_path_from_cased`.

        :param dbx_path: Path relative to Dropbox folder, must be correctly cased in its
            basename.
        :param client: Client instance to use. If not given, use the instance provided
            in the constructor.
        :returns: Corresponding local path on drive.
        """

        client = client or self.client

        dbx_path_cased = self.correct_case(dbx_path, client)

        return f"{self.dropbox_path}{dbx_path_cased}"

    def has_sync_errors(self) -> bool:
        """Returns ``True`` in case of sync errors, ``False`` otherwise."""
        return len(self.sync_errors) > 0

    def clear_sync_error(
        self, local_path: Optional[str] = None, dbx_path: Optional[str] = None
    ) -> None:
        """
        Clears all sync errors for ``local_path`` or ``dbx_path``.

        :param local_path: Absolute path on local drive.
        :param dbx_path: Path relative to Dropbox folder.
        """

        if local_path and not dbx_path:
            dbx_path = self.to_dbx_path(local_path)
        elif not dbx_path and not local_path:
            return

        dbx_path = cast(str, dbx_path)
        dbx_path_lower = normalize(dbx_path)

        if self.has_sync_errors():
            for error in self.sync_errors.copy():
                if error.dbx_path and is_equal_or_child(
                    normalize(error.dbx_path), dbx_path_lower
                ):
                    try:
                        self.sync_errors.remove(error)
                    except KeyError:
                        pass

        self.upload_errors.discard(dbx_path_lower)
        self.download_errors.discard(dbx_path_lower)

    def clear_sync_errors(self) -> None:
        """Clears all sync errors."""
        self.sync_errors.clear()
        self.upload_errors.clear()
        self.download_errors.clear()

    @staticmethod
    def is_excluded(path: str) -> bool:
        """
        Checks if a file is excluded from sync. Certain file names are always excluded
        from syncing, following the Dropbox support article:

        https://help.dropbox.com/installs-integrations/sync-uploads/files-not-syncing

        This includes file system files such as 'desktop.ini' and '.DS_Store' and some
        temporary files as well as caches used by Dropbox or Maestral. `is_excluded`
        accepts both local and Dropbox paths.

        :param path: Can be an absolute path, a path relative to the Dropbox folder or
            just a file name. Does not need to be normalized.
        :returns: Whether the path is excluded from syncing.
        """

        # is root folder?
        if path in ("/", ""):
            return True

        dirname, basename = osp.split(path)
        # in excluded files?
        if basename in EXCLUDED_FILE_NAMES:
            return True

        # in excluded dirs?
        root_dir = next(iter(part for part in dirname.split("/", 2) if part), "")

        if root_dir in EXCLUDED_DIR_NAMES:
            return True

        if "~" in basename:  # is temporary file?
            # 1) office temporary files
            if basename.startswith("~$"):
                return True
            if basename.startswith(".~"):
                return True
            # 2) other temporary files
            if basename.startswith("~") and basename.endswith(".tmp"):
                return True

        return False

    def is_excluded_by_user(self, dbx_path_lower: str) -> bool:
        """
        Check if file has been excluded through "selective sync" by the user.

        :param dbx_path_lower: Normalised lower case Dropbox path.
        :returns: Whether the path is excluded from download syncing by the user.
        """

        return any(is_equal_or_child(dbx_path_lower, p) for p in self.excluded_items)

    def is_mignore(self, event: SyncEvent) -> bool:
        """
        Check if local file change has been excluded by an mignore pattern.

        :param event: SyncEvent for local file event.
        :returns: Whether the path is excluded from upload syncing by the user.
        """
        if len(self.mignore_rules.patterns) == 0:
            return False

        return self._is_mignore_path(
            event.dbx_path, is_dir=event.is_directory
        ) and not self.get_local_rev(event.dbx_path_lower)

    def _is_mignore_path(self, dbx_path: str, is_dir: bool = False) -> bool:

        relative_path = dbx_path.lstrip("/")

        if is_dir:
            relative_path = f"{relative_path}/"

        return self.mignore_rules.match_file(relative_path)

    def _slow_down(self) -> None:
        """
        Pauses if CPU usage is too high if called from one of our thread pools.
        """

        if self._max_cpu_percent == 100 * CPU_COUNT:
            return

        cpu_usage = cpu_usage_percent()

        if cpu_usage > self._max_cpu_percent:

            thread_name = current_thread().name
            self._logger.debug(f"{thread_name}: {cpu_usage}% CPU usage - throttling")

            while cpu_usage > self._max_cpu_percent:
                cpu_usage = cpu_usage_percent(0.5 + 2 * random.random())

            self._logger.debug(
                f"{thread_name}: {cpu_usage}% CPU usage - end throttling"
            )

    def cancel_sync(self) -> None:
        """
        Raises a :class:`maestral.errors.CancelledError` in all sync threads and waits
        for them to shut down.
        """

        self._cancel_requested.set()

        # Wait until we can acquire the sync lock => we are idle.
        self.sync_lock.acquire()
        self.sync_lock.release()

        self._cancel_requested.clear()

        self._logger.info("Sync aborted")

    def busy(self) -> bool:
        """
        Checks if we are currently syncing.

        :returns: ``True`` if :attr:`sync_lock` cannot be acquired, ``False`` otherwise.
        """

        idle = self.sync_lock.acquire(blocking=False)
        if idle:
            self.sync_lock.release()

        return not idle

    def _handle_sync_error(self, err: SyncError, direction: SyncDirection) -> None:
        """
        Handles a sync error. Fills out any missing path information and adds the error
        to the persistent state for later resync.

        :param err: The sync error to handle.
        :param direction: The sync direction (up or down) for which the error occurred.
        """

        # fill out missing dbx_path or local_path
        if err.dbx_path and not err.local_path:
            err.local_path = self.to_local_path_from_cased(err.dbx_path)
        if err.local_path and not err.dbx_path:
            err.dbx_path = self.to_dbx_path(err.local_path)

        # fill out missing dbx_path_dst or local_path_dst
        if err.dbx_path_dst and not err.local_path_dst:
            err.local_path_dst = self.to_local_path_from_cased(err.dbx_path_dst)
        if err.local_path_dst and not err.dbx_path_dst:
            err.dbx_path_dst = self.to_dbx_path(err.local_path_dst)

        if err.dbx_path:
            # we have a file / folder associated with the sync error
            # use sanitised path so that the error can be printed to the terminal, etc
            file_name = sanitize_string(osp.basename(err.dbx_path))

            self._logger.info("Could not sync %s", file_name, exc_info=True)

            def callback():
                if err.local_path:
                    click.launch(err.local_path, locate=True)
                else:
                    url_path = urllib.parse.quote(err.dbx_path)
                    click.launch(f"https://www.dropbox.com/preview{url_path}")

            self.desktop_notifier.notify(
                "Sync error",
                f"Could not sync {file_name}",
                level=notify.SYNCISSUE,
                actions={"Show": callback},
            )
            self.sync_errors.add(err)

            # save download errors to retry later
            if direction == SyncDirection.Down:
                self.download_errors.add(normalize(err.dbx_path))
            elif direction == SyncDirection.Up:
                self.upload_errors.add(normalize(err.dbx_path))

    @contextmanager
    def _database_access(self, raise_error: bool = True) -> Iterator[None]:
        """
        A context manager to synchronises access to the SQLite database. Catches
        exceptions raised by sqlite3 and converts them to a MaestralApiError if we know
        how to handle them.

        :param raise_error: Whether errors should be raised or logged.
        """

        title = ""
        msg = ""
        new_exc = None

        try:
            with self._db_lock:
                yield
        except sqlite3.OperationalError as exc:
            title = "Database transaction error"
            msg = (
                f'The index file at "{self._db_path}" cannot be read. '
                "Please check that you have sufficient permissions and "
                "rebuild the index if necessary."
            )
            new_exc = DatabaseError(title, msg).with_traceback(exc.__traceback__)
        except sqlite3.IntegrityError as exc:
            title = "Database integrity error"
            msg = "Please rebuild the index to continue syncing."
            new_exc = DatabaseError(title, msg).with_traceback(exc.__traceback__)
        except sqlite3.DatabaseError as exc:
            title = "Database transaction error"
            msg = (
                "Please restart Maestral to continue syncing. "
                "Rebuild the index if this issue persists."
            )
            new_exc = DatabaseError(title, msg).with_traceback(exc.__traceback__)

        if new_exc:
            if raise_error:
                raise new_exc
            self._logger.error(title, exc_info=exc_info_tuple(new_exc))
            self.desktop_notifier.notify(title, msg, level=notify.ERROR)

    def _clear_caches(self) -> None:
        """
        Frees memory by clearing internal caches.
        """

        self._case_conversion_cache.clear()
        self.fs_events.expire_ignored_events()

    # ==== Upload sync =================================================================

    def upload_local_changes_while_inactive(self) -> None:
        """
        Collects changes while sync has not been running and uploads them to Dropbox.
        Call this method when resuming sync.
        """

        with self.sync_lock:

            self._logger.info("Indexing local changes...")

            try:
                events, local_cursor = self._get_local_changes_while_inactive()
            except OSError as err:
                if err.filename == self.dropbox_path:
                    self.ensure_dropbox_folder_present()

                raise os_to_maestral_error(err)

            events = self._clean_local_events(events)
            sync_events = [SyncEvent.from_file_system_event(e, self) for e in events]
            del events

            if len(sync_events) > 0:
                self.apply_local_changes(sync_events)
                self._logger.debug("Uploaded local changes while inactive")
            else:
                self._logger.debug("No local changes while inactive")

            del sync_events
            gc.collect()

            self.local_cursor = local_cursor

            self._clear_caches()

    def _get_local_changes_while_inactive(self) -> Tuple[List[FileSystemEvent], float]:
        """
        Retrieves all local changes since the last sync by performing a full scan of the
        local folder. Changes are detected by comparing the new directory snapshot to
        our index.

        Added items: Are present in the snapshot but not in our index.
        Deleted items: Are present in our index but not in the snapshot.
        Modified items: Are present in both but have a mtime newer than the last sync.

        Note that the client sets mtimes for files explicitly but never to a value in
        the future. mtime > last_sync therefore indicates a recent content change. We do
        not use the ctime here to avoid resyncing the entire folder after it has been
        moved (moving between partitions and on some file systems can change the ctime).

        :returns: Tuple containing local file system events and a cursor / timestamp
            for the changes.
        """

        changes = []
        snapshot_time = time.time()

        # Get modified or added items.
        for path, stat in walk(self.dropbox_path, self._scandir_with_ignore):

            is_dir = S_ISDIR(stat.st_mode)
            dbx_path_lower = self.to_dbx_path_lower(path)
            index_entry = self.get_index_entry(dbx_path_lower)

            if index_entry:
                is_new = False
                last_sync = index_entry.last_sync or 0.0
            else:
                is_new = True
                last_sync = 0.0

            last_sync = max(last_sync, self.local_cursor)

            # Check if item was created or modified since last sync
            # but before we started the FileEventHandler (~snapshot_time).

            mtime_check = snapshot_time > stat.st_mtime > last_sync

            # always upload untracked items, check ctime of tracked items
            is_modified = mtime_check and not is_new

            if is_new:
                if is_dir:
                    event = DirCreatedEvent(path)
                else:
                    event = FileCreatedEvent(path)
                changes.append(event)

            elif is_modified:
                if is_dir and index_entry.is_directory:  # type: ignore
                    # We don't emit `DirModifiedEvent`s.
                    pass
                elif not is_dir and not index_entry.is_directory:  # type: ignore
                    event = FileModifiedEvent(path)
                    changes.append(event)
                elif is_dir:
                    event0 = FileDeletedEvent(path)
                    event1 = DirCreatedEvent(path)
                    changes += [event0, event1]
                elif not is_dir:
                    event0 = DirDeletedEvent(path)
                    event1 = FileCreatedEvent(path)
                    changes += [event0, event1]

        # Get deleted items.
        for entry in self.iter_index():
            local_path = self.to_local_path_from_cased(entry.dbx_path_cased)
            is_mignore = self._is_mignore_path(entry.dbx_path_cased, entry.is_directory)

            if is_mignore or not osp.exists(local_path):
                if entry.is_directory:
                    event = DirDeletedEvent(local_path)
                else:
                    event = FileDeletedEvent(local_path)
                changes.append(event)

        duration = time.time() - snapshot_time
        self._logger.debug("Local indexing completed in %s sec", duration)
        self._logger.debug("Retrieved local changes:\n%s", pf_repr(changes))

        return changes, snapshot_time

    def wait_for_local_changes(self, timeout: float = 40) -> bool:
        """
        Blocks until local changes are available.

        :param timeout: Maximum time in seconds to wait.
        :returns: ``True`` if changes are available, ``False`` otherwise.
        """

        self._logger.debug(
            "Waiting for local changes since cursor: %s", self.local_cursor
        )

        return self.fs_events.wait_for_event(timeout)

    def upload_sync_cycle(self):
        """
        Performs a full upload sync cycle by calling in order:

            1) :meth:`list_local_changes`
            2) :meth:`apply_local_changes`

        Handles updating the local cursor for you. If monitoring for local file events
        was interrupted, call :meth:`upload_local_changes_while_inactive` instead.
        """

        with self.sync_lock:

            changes, cursor = self.list_local_changes()
            self.apply_local_changes(changes)

            self.local_cursor = cursor

            # Free memory early to prevent fragmentation.
            del changes
            self._clear_caches()
            gc.collect()

            if self._cancel_requested.is_set():
                raise CancelledError("Sync cancelled")

    def list_local_changes(self, delay: float = 1) -> Tuple[List[SyncEvent], float]:
        """
        Waits for local file changes. Returns a list of local changes with at most one
        entry per path.

        :param delay: Delay in sec to wait for subsequent changes before returning.
        :returns: (list of sync times events, time_stamp)
        """

        events = []
        local_cursor = time.time()

        # keep collecting events until idle for `delay`
        while True:
            try:
                event = self.fs_events.local_file_event_queue.get(timeout=delay)
                events.append(event)
                local_cursor = time.time()
            except Empty:
                break

        self._logger.debug("Retrieved local file events:\n%s", pf_repr(events))

        events = self._clean_local_events(events)
        sync_events = [SyncEvent.from_file_system_event(e, self) for e in events]

        # Free memory early to prevent fragmentation.
        del events
        gc.collect()

        return sync_events, local_cursor

    def apply_local_changes(self, sync_events: List[SyncEvent]) -> List[SyncEvent]:
        """
        Applies locally detected changes to the remote Dropbox. Changes which should be
        ignored (mignore or always ignored files) are skipped.

        :param sync_events: List of local file system events.
        """

        results: List[SyncEvent] = []

        if len(sync_events) == 0:
            return results

        # Sort all sync events into deleted, dir_moved and other. Discard items
        # which are excluded by mignore or the internal exclusion list. Deleted and
        # dir_moved events will never be nested (we have already combined such nested
        # events) but all other events might be. We order and apply them hierarchically.

        deleted: List[SyncEvent] = []
        dir_moved: List[SyncEvent] = []
        other: DefaultDict[int, List[SyncEvent]] = defaultdict(list)

        for event in sync_events:

            if self.is_excluded(event.dbx_path) or self.is_mignore(event):
                continue

            if event.is_deleted:
                deleted.append(event)
            elif event.is_directory and event.is_moved:
                dir_moved.append(event)
            else:
                level = event.dbx_path.count("/")
                other[level].append(event)

            # Housekeeping.
            self.syncing[event.local_path] = event

        self._logger.debug("Filtered deleted events:\n%s", pf_repr(deleted))
        self._logger.debug("Filtered dir moved events:\n%s", pf_repr(deleted))
        self._logger.debug("Filtered other events:\n%s", pf_repr(other))

        # Apply deleted events first, folder moved events second.
        # Neither event type requires an actual upload.
        if deleted:
            self._logger.info("Uploading deletions...")

        with ThreadPoolExecutor(
            max_workers=self._num_threads,
            thread_name_prefix="maestral-upload-pool",
        ) as executor:
            res = executor.map(self._create_remote_entry, deleted)

            n_items = len(deleted)
            for n, r in enumerate(res):
                throttled_log(self._logger, f"Deleting {n + 1}/{n_items}...")
                results.append(r)

        if dir_moved:
            self._logger.info("Moving folders...")

        for event in dir_moved:
            self._logger.info(f"Moving {event.dbx_path_from}...")
            r = self._create_remote_entry(event)
            results.append(r)

        # Apply other events in parallel, processing each hierarchy level successively.

        for level in sorted(other):
            items = other[level]

            with ThreadPoolExecutor(
                max_workers=self._num_threads,
                thread_name_prefix="maestral-upload-pool",
            ) as executor:
                res = executor.map(self._create_remote_entry, items)

                n_items = len(items)
                for n, r in enumerate(res):
                    throttled_log(self._logger, f"Syncing ↑ {n + 1}/{n_items}")
                    results.append(r)

        self._clean_history()

        return results

    def _clean_local_events(
        self, events: List[FileSystemEvent]
    ) -> List[FileSystemEvent]:
        """
        Takes local file events and cleans them up as follows:

        1) Keep only a single event per path, unless the item type changed (e.g., from
           file to folder).
        2) Collapses moved and deleted events of folders with those of their children.

        The order of events will be preserved according to the first event registered
        for that path.

        :param events: Iterable of :class:`watchdog.FileSystemEvent`.
        :returns: List of :class:`watchdog.FileSystemEvent`.
        """

        # COMBINE EVENTS TO ONE EVENT PER PATH

        # Move events are difficult to combine with other event types, we split them
        # into deleted and created events and recombine them later if neither the source
        # of the destination path of has other events associated with it or is excluded
        # from sync.

        # mapping of path -> event history
        events_for_path: DefaultDict[str, List[FileSystemEvent]] = defaultdict(list)

        # mapping of "event id" -> [source event, destination event]
        moved_events: DefaultDict[str, List[FileSystemEvent]] = defaultdict(list)

        for event in events:
            if event.event_type == EVENT_TYPE_MOVED:
                deleted, created = split_moved_event(event)  # type: ignore
                events_for_path[deleted.src_path].append(deleted)
                events_for_path[created.src_path].append(created)
            else:
                events_for_path[event.src_path].append(event)

        # For every path, keep only a single event which represents all changes,
        # unless we deal with a type change.

        for path in list(events_for_path):
            events = events_for_path[path]

            if len(events) == 1:

                # There is only a single event for this path. If it is a split moved
                # event, mark it for possible recombination later.
                event = events[0]
                if hasattr(event, "move_id"):
                    moved_events[event.move_id].append(event)

            else:

                # Count how often the file / folder was created vs deleted.
                # Remember if it was first created or deleted.

                n_created = 0
                n_deleted = 0

                first_created_index = -1
                first_deleted_index = -1

                for i in reversed(range(len(events))):
                    event = events[i]

                    if event.event_type == EVENT_TYPE_CREATED:
                        n_created += 1
                        first_created_index = i

                    if event.event_type == EVENT_TYPE_DELETED:
                        n_deleted += 1
                        first_deleted_index = i

                if n_created > n_deleted:  # Item was created.
                    if events[-1].is_directory:
                        events_for_path[path] = [DirCreatedEvent(path)]
                    else:
                        events_for_path[path] = [FileCreatedEvent(path)]

                elif n_created < n_deleted:  # Item was deleted.
                    if events[0].is_directory:
                        events_for_path[path] = [DirDeletedEvent(path)]
                    else:
                        events_for_path[path] = [FileDeletedEvent(path)]

                else:  # Same number of deleted and created events.

                    if n_created == 0 or first_deleted_index < first_created_index:
                        # Item was modified.
                        if events[0].is_directory and events[-1].is_directory:
                            # Both first and last events are from folders.
                            events_for_path[path] = [DirModifiedEvent(path)]
                        elif not events[0].is_directory and not events[-1].is_directory:
                            # Both first and last events are from files.
                            events_for_path[path] = [FileModifiedEvent(path)]
                        elif events[0].is_directory:
                            # Type change folder -> file.
                            events_for_path[path] = [
                                DirDeletedEvent(path),
                                FileCreatedEvent(path),
                            ]
                        elif events[-1].is_directory:
                            # Type change file -> folder.
                            events_for_path[path] = [
                                FileDeletedEvent(path),
                                DirCreatedEvent(path),
                            ]
                    else:
                        # Item was likely only temporary. We still trigger a rescan of
                        # the path because some atomic modifications may be reported as
                        # out-of-order created and deleted events on macOS.
                        del events_for_path[path]
                        self.rescan(path)

        # Recombine moved events if we have retained both sides of event during the
        # above consolidation.

        for split_events in moved_events.values():
            if len(split_events) == 2:

                src_path = split_events[0].src_path
                dest_path = split_events[1].src_path

                if split_events[0].is_directory:
                    new_event = DirMovedEvent(src_path, dest_path)
                else:
                    new_event = FileMovedEvent(src_path, dest_path)

                # Only recombine events if neither has an excluded path: We want to
                # treat renaming from / to an excluded path as a creation / deletion,
                # respectively.
                if not self._should_split_excluded(new_event):
                    del events_for_path[src_path]
                    events_for_path[dest_path] = [new_event]

        # At this point, `events_for_path` will contain a single event per path or
        # exactly two events (deleted and created) in case of a type change.

        # COMBINE MOVED AND DELETED EVENTS OF FOLDERS AND THEIR CHILDREN INTO ONE EVENT

        # Avoid nested iterations over all events here, they are on the order of O(n^2)
        # which becomes costly when the user moves or deletes folder with a large number
        # of children. Benchmark: aim to stay below 1 sec for 20,000 nested events on
        # representative laptop PCs.

        # 0) Collect all moved and deleted events in sets.

        dir_moved_paths: Set[Tuple[str, str]] = set()
        dir_deleted_paths: Set[str] = set()

        for events in events_for_path.values():
            event = events[0]
            if isinstance(event, DirMovedEvent):
                dir_moved_paths.add((event.src_path, event.dest_path))
            elif isinstance(event, DirDeletedEvent):
                dir_deleted_paths.add(event.src_path)

        # 1) Combine moved events of folders and their children into one event.

        if len(dir_moved_paths) > 0:
            child_moved_dst_paths: Set[str] = set()

            # For each event, check if it is a child of a moved event discard it if yes.
            for events in events_for_path.values():
                event = events[0]
                if event.event_type == EVENT_TYPE_MOVED:
                    dirnames = (
                        osp.dirname(event.src_path),
                        osp.dirname(event.dest_path),  # type: ignore
                    )
                    if dirnames in dir_moved_paths:
                        child_moved_dst_paths.add(event.dest_path)  # type: ignore

            for path in child_moved_dst_paths:
                del events_for_path[path]

        # 2) Combine deleted events of folders and their children to one event.

        if len(dir_deleted_paths) > 0:
            child_deleted_paths: Set[str] = set()

            for events in events_for_path.values():
                event = events[0]
                if event.event_type == EVENT_TYPE_DELETED:
                    dirname = osp.dirname(event.src_path)
                    if dirname in dir_deleted_paths:
                        child_deleted_paths.add(event.src_path)

            for path in child_deleted_paths:
                del events_for_path[path]

        # PREPARE RETURN VALUE AND FREE MEMORY

        cleaned_events = []

        for events in events_for_path.values():
            cleaned_events.extend(events)

        # Free memory early to prevent fragmentation.
        del events_for_path
        del moved_events
        del dir_moved_paths
        del dir_deleted_paths
        gc.collect()

        return cleaned_events

    def _should_split_excluded(self, event: Union[FileMovedEvent, DirMovedEvent]):

        if event.event_type != EVENT_TYPE_MOVED:
            raise ValueError("Can only split moved events")

        dbx_src_path = self.to_dbx_path(event.src_path)
        dbx_dest_path = self.to_dbx_path(event.dest_path)

        if (
            self.is_excluded(event.src_path)
            or self.is_excluded(event.dest_path)
            or self.is_excluded_by_user(normalize(dbx_src_path))
            or self.is_excluded_by_user(normalize(dbx_dest_path))
        ):
            return True

        elif len(self.mignore_rules.patterns) == 0:
            return False
        else:
            src_is_mignore = self._is_mignore_path(dbx_src_path, event.is_directory)
            dest_is_mignore = self._is_mignore_path(dbx_dest_path, event.is_directory)

            return src_is_mignore or dest_is_mignore

    def _handle_normalization_conflict(self, event: SyncEvent) -> bool:
        """
        Checks for other items in the same directory with a different name but the same
        normalization.

        :param event: SyncEvent for local created or moved event.
        :returns: Whether a case conflict was detected and handled.
        """

        if not (event.is_added or event.is_moved):
            return False

        dirname, basename = osp.split(event.local_path)
        equivalent_paths = equivalent_path_candidates(basename, root=dirname)

        if len(equivalent_paths) > 1:

            # We have different file names that would map to the same normalized path!

            conflict_path = next(p for p in equivalent_paths if p != event.local_path)

            # Check if we have a case conflict or a unicode conflict.

            if normalize_case(event.local_path) == normalize_case(conflict_path):
                suffix = "case conflict"
            elif normalize_unicode(event.local_path) == normalize_case(conflict_path):
                suffix = "unicode conflict"
            else:
                suffix = "normalization conflict"

            local_path_cc = generate_cc_name(
                event.local_path,
                suffix=suffix,
            )

            event_cls = DirMovedEvent if osp.isdir(event.local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(event.local_path, local_path_cc)):
                with convert_api_errors():
                    move(event.local_path, local_path_cc, raise_error=True)

                self.rescan(local_path_cc)

            self._logger.info(
                'Normalization conflict: renamed "%s" to "%s"',
                event.local_path,
                local_path_cc,
            )

            return True
        else:
            return False

    def _handle_selective_sync_conflict(self, event: SyncEvent) -> bool:
        """
        Checks for items in the local directory with same path as an item which is
        excluded by selective sync. Renames items if necessary.

        :param event: SyncEvent for local created or moved event.
        :returns: Whether a selective sync conflict was detected and handled.
        """

        if not (event.is_added or event.is_moved):
            return False

        if self.is_excluded_by_user(event.dbx_path_lower):
            local_path_cc = generate_cc_name(
                event.local_path,
                suffix="selective sync conflict",
            )

            event_cls = DirMovedEvent if osp.isdir(event.local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(event.local_path, local_path_cc)):
                with convert_api_errors():
                    move(event.local_path, local_path_cc, raise_error=True)

                self.rescan(local_path_cc)

            self._logger.info(
                'Selective sync conflict: renamed "%s" to "%s"',
                event.local_path,
                local_path_cc,
            )
            return True
        else:
            return False

    def _create_remote_entry(self, event: SyncEvent) -> SyncEvent:
        """
        Applies a local file system event to the remote Dropbox and clears any existing
        sync errors belonging to that path. Any :class:`maestral.errors.SyncError` will
        be caught and logged as appropriate.

        This method always uses a new copy of client and closes the network session
        afterwards.

        :param event: SyncEvent for local file event.
        :returns: SyncEvent with updated status.
        """

        if self._cancel_requested.is_set():
            raise CancelledError("Sync cancelled")

        self._slow_down()

        self.clear_sync_error(local_path=event.local_path)
        self.clear_sync_error(local_path=event.local_path_from)
        event.status = SyncStatus.Syncing

        try:

            with self.client.clone_with_new_session() as client:
                if event.is_added:
                    res = self._on_local_created(event, client)
                elif event.is_moved:
                    res = self._on_local_moved(event, client)
                elif event.is_changed:
                    res = self._on_local_modified(event, client)
                elif event.is_deleted:
                    res = self._on_local_deleted(event, client)
                else:
                    res = None

            if res is not None:
                event.status = SyncStatus.Done
            else:
                event.status = SyncStatus.Skipped

        except SyncError as err:
            self._handle_sync_error(err, direction=SyncDirection.Up)
            event.status = SyncStatus.Failed
        finally:
            self.syncing.pop(event.local_path, None)

        # add to history database
        if event.status == SyncStatus.Done:
            with self._database_access():
                self._db_manager_history.save(event)

        return event

    @staticmethod
    def _wait_for_creation(local_path: str) -> None:
        """
        Wait for a file at a path to be created or modified.

        :param local_path: Absolute path to file on drive.
        """
        try:
            while True:
                size1 = osp.getsize(local_path)
                time.sleep(0.2)
                size2 = osp.getsize(local_path)
                if size1 == size2:
                    return
        except OSError:
            return

    def _on_local_moved(
        self, event: SyncEvent, client: Optional[DropboxClient] = None
    ) -> Optional[Metadata]:
        """
        Call when a local item is moved.

        Keep in mind that we may be moving a whole tree of items. But its better deal
        with the complexity than to delete and re-uploading everything. Thankfully, in
        case of directories, we always process the top-level first. Trying to move the
        children will then be delegated to `on_create` (because the old item no longer
        lives on Dropbox) and that won't upload anything because file contents have
        remained the same.

        :param event: SyncEvent for local moved event.
        :param client: Client instance to use. If not given, use the instance provided
            in the constructor.
        :returns: Metadata for created remote item at destination.
        :raises MaestralApiError: For any issues when syncing the item.
        """

        client = client or self.client

        if event.local_path_from == self.dropbox_path:
            self.ensure_dropbox_folder_present()

        # fail fast on badly decoded paths
        validate_encoding(event.local_path)

        if self._handle_selective_sync_conflict(event):
            return None
        if self._handle_normalization_conflict(event):
            return None

        dbx_path_from = cast(str, event.dbx_path_from)
        md_from_old = client.get_metadata(dbx_path_from)

        # If not on Dropbox, e.g., because its old name was invalid,
        # create it instead of moving it.
        if not md_from_old:
            if event.is_directory:
                new_event = DirCreatedEvent(event.local_path)
            else:
                new_event = FileCreatedEvent(event.local_path)

            new_sync_event = SyncEvent.from_file_system_event(new_event, self)

            return self._on_local_created(new_sync_event, client)

        md_to_new = client.move(dbx_path_from, event.dbx_path, autorename=True)

        self.remove_node_from_index(event.dbx_path_from_lower)

        if md_to_new.name != osp.basename(event.local_path):
            # TODO: test this
            # conflicting copy created during upload, mirror remote changes locally
            local_path_cc = self.to_local_path(md_to_new.path_display, client)
            event_cls = DirMovedEvent if osp.isdir(event.local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(event.local_path, local_path_cc)):
                with convert_api_errors():
                    move(event.local_path, local_path_cc, raise_error=True)

            # Delete entry of old path.
            self.remove_node_from_index(event.dbx_path_lower)
            self._logger.info(
                'Upload conflict: renamed "%s" to "%s"',
                event.dbx_path,
                md_to_new.path_display,
            )

        else:
            self._logger.debug(
                'Moved "%s" to "%s" on Dropbox', dbx_path_from, event.dbx_path
            )

        self._update_index_recursive(md_to_new, client)

        return md_to_new

    def _update_index_recursive(self, md: Metadata, client: DropboxClient) -> None:

        self.update_index_from_dbx_metadata(md, client)

        if isinstance(md, FolderMetadata):
            result = client.list_folder(md.path_lower, recursive=True)
            for md in result.entries:
                self.update_index_from_dbx_metadata(md, client)

    def _on_local_created(
        self, event: SyncEvent, client: Optional[DropboxClient] = None
    ) -> Optional[Metadata]:
        """
        Call when a local item is created.

        :param event: SyncEvent corresponding to local created event.
        :param client: Client instance to use. If not given, use the instance provided
            in the constructor.
        :returns: Metadata for created item or None if no remote item is created.
        :raises MaestralApiError: For any issues when syncing the item.
        """

        client = client or self.client

        # fail fast on badly decoded paths
        validate_encoding(event.local_path)

        if self._handle_selective_sync_conflict(event):
            return None
        if self._handle_normalization_conflict(event):
            return None

        self._wait_for_creation(event.local_path)

        if event.is_directory:
            try:
                md_new = client.make_dir(event.dbx_path, autorename=False)
            except FolderConflictError:
                self._logger.debug(
                    'No conflict for "%s": the folder already exists', event.local_path
                )
                try:
                    md = client.get_metadata(event.dbx_path)
                    if isinstance(md, FolderMetadata):
                        self.update_index_from_dbx_metadata(md, client)
                except NotFoundError:
                    pass

                return None
            except FileConflictError:
                md_new = client.make_dir(event.dbx_path, autorename=True)

        else:
            # check if file already exists with identical content
            md_old = client.get_metadata(event.dbx_path)
            if isinstance(md_old, FileMetadata):
                if event.content_hash == md_old.content_hash:
                    # file hashes are identical, do not upload
                    self.update_index_from_dbx_metadata(md_old, client)
                    return None

            local_entry = self.get_index_entry(event.dbx_path_lower)

            if not local_entry:
                # file is new to us, let Dropbox rename it if something is in the way
                mode = WriteMode.add
            elif local_entry.is_directory:
                # try to overwrite the destination, this will fail...
                mode = WriteMode.overwrite
            else:
                # file has been modified, update remote if matching rev,
                # create conflict otherwise
                self._logger.debug(
                    '"%s" appears to have been created but we are '
                    "already tracking it",
                    event.dbx_path,
                )
                mode = WriteMode.update(local_entry.rev)
            try:
                md_new = client.upload(
                    event.local_path,
                    event.dbx_path,
                    autorename=True,
                    mode=mode,
                    sync_event=event,
                )
            except NotFoundError:
                self._logger.debug(
                    'Could not upload "%s": the item does not exist', event.local_path
                )
                return None

        if md_new.name != osp.basename(event.local_path):
            # conflicting copy created during upload, mirror remote changes locally
            local_path_cc = self.to_local_path(md_new.path_display, client)
            event_cls = DirMovedEvent if osp.isdir(event.local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(event.local_path, local_path_cc)):
                with convert_api_errors():
                    move(event.local_path, local_path_cc, raise_error=True)

            # Delete entry of old path
            self.remove_node_from_index(event.dbx_path_lower)
            self._logger.debug(
                'Upload conflict: renamed "%s" to "%s"',
                event.dbx_path,
                md_new.path_display,
            )
        else:
            self._logger.debug('Created "%s" on Dropbox', event.dbx_path)

        self.update_index_from_dbx_metadata(md_new, client)

        return md_new

    def _on_local_modified(
        self, event: SyncEvent, client: Optional[DropboxClient] = None
    ) -> Optional[Metadata]:
        """
        Call when local item is modified.

        :param event: SyncEvent for local modified event.
        :param client: Client instance to use. If not given, use the instance provided
            in the constructor.
        :returns: Metadata corresponding to modified remote item or None if no remote
            item is modified.
        :raises MaestralApiError: For any issues when syncing the item.
        """

        client = client or self.client

        if event.is_directory:  # ignore directory modified events
            return None

        self._wait_for_creation(event.local_path)

        # check if item already exists with identical content
        md_old = client.get_metadata(event.dbx_path)
        if isinstance(md_old, FileMetadata):
            if event.content_hash == md_old.content_hash:
                # file hashes are identical, do not upload
                self.update_index_from_dbx_metadata(md_old, client)
                self._logger.debug(
                    'Modification of "%s" detected but file content is '
                    "the same as on Dropbox",
                    event.dbx_path,
                )
                self.update_index_from_dbx_metadata(md_old, client)
                return None

        local_entry = self.get_index_entry(event.dbx_path_lower)

        if not local_entry:
            self._logger.debug(
                '"%s" appears to have been modified but cannot ' "find old revision",
                event.dbx_path,
            )
            mode = WriteMode.add
        elif local_entry.is_directory:
            mode = WriteMode.overwrite
        else:
            mode = WriteMode.update(local_entry.rev)

        try:
            md_new = client.upload(
                event.local_path,
                event.dbx_path,
                autorename=True,
                mode=mode,
                sync_event=event,
            )
        except NotFoundError:
            self._logger.debug(
                'Could not upload "%s": the item does not exist', event.dbx_path
            )
            return None

        if md_new.name != osp.basename(event.local_path):
            # Conflicting copy created during upload, mirror remote changes locally.
            local_path_cc = self.to_local_path(md_new.path_display, client)
            with self.fs_events.ignore(FileMovedEvent(event.local_path, local_path_cc)):
                try:
                    os.rename(event.local_path, local_path_cc)
                except OSError:
                    with self.fs_events.ignore(FileDeletedEvent(event.local_path)):
                        delete(event.local_path)

            # Delete revs of old path.
            self.remove_node_from_index(event.dbx_path_lower)
            self._logger.debug(
                'Upload conflict: renamed "%s" to "%s"',
                event.dbx_path,
                md_new.path_display,
            )
        else:
            # everything went well
            self._logger.debug('Uploaded modified "%s" to Dropbox', md_new.path_lower)

        self.update_index_from_dbx_metadata(md_new, client)

        return md_new

    def _on_local_deleted(
        self, event: SyncEvent, client: Optional[DropboxClient] = None
    ) -> Optional[Metadata]:
        """
        Call when local item is deleted. We try not to delete remote items which have
        been modified since the last sync.

        :param event: SyncEvent for local deletion.
        :param client: Client instance to use. If not given, use the instance provided
            in the constructor.
        :returns: Metadata for deleted item or None if no remote item is deleted.
        :raises MaestralApiError: For any issues when syncing the item.
        """

        client = client or self.client

        if event.local_path == self.dropbox_path:
            self.ensure_dropbox_folder_present()

        if self.is_excluded_by_user(event.dbx_path_lower):
            self._logger.debug(
                'Not deleting "%s": is excluded by selective sync', event.dbx_path
            )
            return None

        local_rev = self.get_local_rev(event.dbx_path_lower)

        md = client.get_metadata(event.dbx_path, include_deleted=True)

        if event.is_directory and isinstance(md, FileMetadata):
            self._logger.debug(
                'Expected folder at "%s" but found a file instead, checking '
                "which one is newer",
                md.path_display,
            )
            # don't delete a remote file if it was modified since last sync
            if md.server_modified.timestamp() >= self.get_last_sync(
                event.dbx_path_lower
            ):
                self._logger.debug(
                    'Skipping deletion: remote item "%s" has been modified '
                    "since last sync",
                    md.path_display,
                )
                # mark local folder as untracked
                self.remove_node_from_index(event.dbx_path_lower)
                return None

        if event.is_file and isinstance(md, FolderMetadata):
            # don't delete a remote folder if we were expecting a file
            # TODO: Delete the folder if its children did not change since last sync.
            #   Is there a way of achieving this without listing the folder or listing
            #   all changes and checking when they occurred?
            self._logger.debug(
                'Skipping deletion: expected file at "%s" but found a '
                "folder instead",
                md.path_display,
            )
            # mark local file as untracked
            self.remove_node_from_index(event.dbx_path_lower)
            return None

        try:
            # will only perform delete if Dropbox remote rev matches `local_rev`
            md_deleted = client.remove(
                event.dbx_path, parent_rev=local_rev if event.is_file else None
            )
        except NotFoundError:
            self._logger.debug(
                'Could not delete "%s": the item no longer exists on Dropbox',
                event.dbx_path,
            )
            md_deleted = None
        except PathError:
            self._logger.debug(
                'Could not delete "%s": the item has been changed ' "since last sync",
                event.dbx_path,
            )
            md_deleted = None

        # remove revision metadata
        self.remove_node_from_index(event.dbx_path_lower)

        return md_deleted

    # ==== Download sync ===============================================================

    def get_remote_item(
        self, dbx_path: str, client: Optional[DropboxClient] = None
    ) -> bool:
        """
        Downloads a remote file or folder and updates its local rev. If the remote item
        does not exist, any corresponding local items will be deleted. If ``dbx_path``
        refers to a folder, the download will be handled by :meth:`_get_remote_folder`.
        If it refers to a single file, the download will be performed by
        :meth:`_create_local_entry`.

        This method can be used to fetch individual items outside of the regular sync
        cycle, for instance when including a previously excluded file or folder.

        :param dbx_path: Path relative to Dropbox folder.
        :param client: Client instance to use. If not given, use the instance provided
            in the constructor.
        :returns: Whether download was successful.
        """

        client = client or self.client

        self._logger.info(f"Syncing ↓ {dbx_path}")

        with self.sync_lock:

            md = client.get_metadata(dbx_path, include_deleted=True)

            dbx_path_lower = normalize(dbx_path)

            if md is None:
                # create a fake deleted event
                index_entry = self.get_index_entry(dbx_path_lower)
                cased_path = index_entry.dbx_path_cased if index_entry else dbx_path

                md = DeletedMetadata(
                    name=osp.basename(dbx_path),
                    path_lower=dbx_path_lower,
                    path_display=cased_path,
                )

            event = SyncEvent.from_dbx_metadata(md, self)

            if event.is_directory:
                success = self._get_remote_folder(dbx_path, client)
            else:
                self.syncing[event.local_path] = event
                e = self._create_local_entry(event)
                success = e.status in (SyncStatus.Done, SyncStatus.Skipped)

            self._clear_caches()

            return success

    def _get_remote_folder(
        self, dbx_path: str, client: Optional[DropboxClient] = None
    ) -> bool:
        """
        Gets all files/folders from a Dropbox folder and writes them to the local folder
        :attr:`dropbox_path`.

        :param dbx_path: Path relative to Dropbox folder.
        :param client: Client instance to use. If not given, use the instance provided
            in the constructor.
        :returns: Whether download was successful.
        """

        client = client or self.client

        with self.sync_lock:

            try:

                idx = 0

                # iterate over index and download results
                list_iter = client.list_folder_iterator(dbx_path, recursive=True)

                for res in list_iter:

                    idx += len(res.entries)

                    if idx > 0:
                        self._logger.info(f"Indexing {idx}...")

                    res.entries.sort(key=lambda x: x.path_lower.count("/"))

                    # convert metadata to sync_events
                    sync_events = [
                        SyncEvent.from_dbx_metadata(md, self) for md in res.entries
                    ]
                    download_res = self.apply_remote_changes(sync_events)

                    success = all(
                        e.status in (SyncStatus.Done, SyncStatus.Skipped)
                        for e in download_res
                    )

                    if self._cancel_requested.is_set():
                        raise CancelledError("Sync cancelled")

            except SyncError as e:
                self._handle_sync_error(e, direction=SyncDirection.Down)
                return False

            return success

    def wait_for_remote_changes(
        self,
        last_cursor: str,
        timeout: int = 40,
        client: Optional[DropboxClient] = None,
    ) -> bool:
        """
        Blocks until changes to the remote Dropbox are available.

        :param last_cursor: Cursor form last sync.
        :param timeout: Timeout in seconds before returning even if there are no
            changes. Dropbox adds random jitter of up to 90 sec to this value.
        :param client: Client instance to use. If not given, use the instance provided
            in the constructor.
        :returns: ``True`` if changes are available, ``False`` otherwise.
        """

        client = client or self.client

        self._logger.debug("Waiting for remote changes since cursor:\n%s", last_cursor)
        has_changes = client.wait_for_remote_changes(last_cursor, timeout=timeout)

        # For for 2 sec. This delay is typically only necessary folders are shared /
        # un-shared with other Dropbox accounts.
        time.sleep(2)

        self._logger.debug("Detected remote changes: %s", has_changes)
        return has_changes

    def download_sync_cycle(self, client: Optional[DropboxClient] = None) -> None:
        """
        Performs a full download sync cycle by calling in order:

            1) :meth:`list_remote_changes_iterator`
            2) :meth:`apply_remote_changes`

        Handles updating the remote cursor and resuming interrupted syncs for you.
        Calling this method will perform a full indexing if this is the first download.

        :param client: Client instance to use. If not given, use the instance provided
            in the constructor.
        """

        client = client or self.client

        with self.sync_lock:

            if self.remote_cursor == "":

                self._state.set("sync", "last_reindex", time.time())
                self._state.set("sync", "did_finish_indexing", False)
                self._state.set("sync", "indexing_counter", 0)

            idx = self._state.get("sync", "indexing_counter")
            is_indexing = not self._state.get("sync", "did_finish_indexing")

            if is_indexing and idx == 0:
                self._logger.info("Indexing remote Dropbox")
            elif is_indexing:
                self._logger.info("Resuming indexing")
            else:
                self._logger.info("Fetching remote changes")

            changes_iter = self.list_remote_changes_iterator(self.remote_cursor, client)

            # Download changes in chunks to reduce memory usage.
            for changes, cursor in changes_iter:

                idx += len(changes)

                if idx > 0:
                    self._logger.info(f"Indexing {idx}...")

                downloaded = self.apply_remote_changes(changes)

                # Save (incremental) remote cursor.
                self.remote_cursor = cursor
                self._state.set("sync", "indexing_counter", idx)

                # Send desktop notifications when not indexing.
                if not is_indexing:
                    self.notify_user(downloaded, client)

                if self._cancel_requested.is_set():
                    raise CancelledError("Sync cancelled")

                # Free memory early to prevent fragmentation.
                del changes
                del downloaded
                gc.collect()

            self._state.set("sync", "did_finish_indexing", True)
            self._state.set("sync", "indexing_counter", 0)

            if idx > 0:
                self._logger.info(IDLE)

            self._clear_caches()

    def list_remote_changes_iterator(
        self, last_cursor: str, client: Optional[DropboxClient] = None
    ) -> Iterator[Tuple[List[SyncEvent], str]]:
        """
        Get remote changes since the last download sync, as specified by
        ``last_cursor``. If the ``last_cursor`` is from paginating through a previous
        set of changes, continue where we left off. If ``last_cursor`` is an emtpy
        string, tart a full indexing of the Dropbox folder.

        :param last_cursor: Cursor from last download sync.
        :param client: Client instance to use. If not given, use the instance provided
            in the constructor.
        :returns: Iterator yielding tuples with remote changes and corresponding cursor.
        """

        client = client or self.client

        if last_cursor == "":
            # We are starting from the beginning, do a full indexing.
            changes_iter = client.list_folder_iterator("/", recursive=True)
        else:
            # Pick up where we left off. This may be an interrupted indexing /
            # pagination through changes or a completely new set of changes.
            self._logger.debug("Fetching remote changes since cursor: %s", last_cursor)
            changes_iter = client.list_remote_changes_iterator(last_cursor)

        for changes in changes_iter:

            changes = self._clean_remote_changes(changes)

            self._logger.debug("Remote changes:\n%s", pf_repr(changes.entries))

            changes.entries.sort(key=lambda x: x.path_lower.count("/"))
            sync_events = [
                SyncEvent.from_dbx_metadata(md, self) for md in changes.entries
            ]

            self._logger.debug("Converted remote changes to SyncEvents")

            yield sync_events, changes.cursor

    def apply_remote_changes(self, sync_events: List[SyncEvent]) -> List[SyncEvent]:
        """
        Applies remote changes to local folder. Call this on the result of
        :meth:`list_remote_changes`. The saved cursor is updated after a set of changes
        has been successfully applied. Entries in the local index are created after
        successful completion.

        :param sync_events: List of remote changes.
        :returns: List of changes that were made to local files and bool indicating if
            all download syncs were successful.
        """

        results: List[SyncEvent] = []

        if len(sync_events) == 0:
            return results

        # Sort changes into folders, files and deleted items. Discard excluded items
        # and remove and deleted items from our excluded list.
        # Sort according to path hierarchy:
        # - Do not create sub-folder / file before parent exists.
        # - Delete parents before deleting children to save some work.

        files: List[SyncEvent] = []
        folders: DefaultDict[int, List[SyncEvent]] = defaultdict(list)
        deleted: DefaultDict[int, List[SyncEvent]] = defaultdict(list)

        new_excluded = self.excluded_items

        for event in sync_events:

            is_excluded = self.is_excluded_by_user(
                event.dbx_path_lower
            ) or self.is_excluded(event.dbx_path)

            if is_excluded:
                if event.is_deleted:
                    # Remove deleted item and its children from the excluded list.
                    new_excluded = [
                        path
                        for path in new_excluded
                        if not is_equal_or_child(path, event.dbx_path_lower)
                    ]

            else:

                level = event.dbx_path.count("/")

                if event.is_deleted:
                    deleted[level].append(event)
                elif event.is_file:
                    files.append(event)
                elif event.is_directory:
                    folders[level].append(event)

                # Housekeeping.
                self.syncing[event.local_path] = event

        self.excluded_items = new_excluded

        # Apply deleted items.
        if deleted:
            self._logger.info("Applying deletions...")

        for level in sorted(deleted):
            items = deleted[level]
            with ThreadPoolExecutor(
                max_workers=self._num_threads,
                thread_name_prefix="maestral-download-pool",
            ) as executor:
                res = executor.map(self._create_local_entry, items)

                n_items = len(items)
                for n, r in enumerate(res):
                    throttled_log(self._logger, f"Deleting {n + 1}/{n_items}...")
                    results.append(r)

        # Create local folders, start with top-level and work your way down.
        if folders:
            self._logger.info("Creating folders...")

        for level in sorted(folders):
            items = folders[level]
            with ThreadPoolExecutor(
                max_workers=self._num_threads,
                thread_name_prefix="maestral-download-pool",
            ) as executor:
                res = executor.map(self._create_local_entry, items)

                n_items = len(items)
                for n, r in enumerate(res):
                    throttled_log(self._logger, f"Creating folder {n + 1}/{n_items}...")
                    results.append(r)

        # Apply created files.
        with ThreadPoolExecutor(
            max_workers=self._num_threads, thread_name_prefix="maestral-download-pool"
        ) as executor:
            res = executor.map(self._create_local_entry, files)

            n_items = len(files)
            for n, r in enumerate(res):
                throttled_log(self._logger, f"Syncing ↓ {n + 1}/{n_items}")
                results.append(r)

        self._clean_history()

        return results

    def notify_user(
        self, sync_events: List[SyncEvent], client: Optional[DropboxClient] = None
    ) -> None:
        """
        Shows a desktop notification for the given file changes.

        :param sync_events: List of SyncEvents from download sync.
        :param client: Client instance to use. If not given, use the instance provided
            in the constructor.
        """

        client = client or self.client

        buttons: Dict[str, Callable]

        changes = [e for e in sync_events if e.status != SyncStatus.Skipped]

        # get number of remote changes
        n_changed = len(changes)

        if n_changed == 0:
            return

        # find out who changed the item(s), show the user name if its only a single user
        user_name: Optional[str]
        dbid_list = {e.change_dbid for e in changes if e.change_dbid is not None}
        if len(dbid_list) == 1:
            # all files have been modified by the same user
            dbid = dbid_list.pop()
            if dbid == client.account_id:
                user_name = "You"
            else:
                try:
                    account_info = client.get_account_info(dbid)
                except InvalidDbidError:
                    user_name = None
                else:
                    user_name = account_info.name.display_name
        else:
            user_name = None

        if n_changed == 1:
            # display user name, file name, and type of change
            event = changes[0]
            file_name = osp.basename(event.dbx_path)
            change_type = event.change_type.value

            def callback():
                click.launch(event.local_path, locate=True)

            buttons = {"Show": callback}

        else:

            if all(e.change_type == sync_events[0].change_type for e in sync_events):
                change_type = sync_events[0].change_type.value
            else:
                change_type = "changed"

            if all(e.is_file for e in sync_events):
                file_name = f"{n_changed} files"
            elif all(e.is_directory for e in sync_events):
                file_name = f"{n_changed} folders"
            else:
                file_name = f"{n_changed} items"

            buttons = {}

        if change_type == ChangeType.Removed.value:

            def callback():
                # show dropbox website with deleted files
                click.launch("https://www.dropbox.com/deleted_files")

            buttons = {"Show": callback}

        if user_name:
            msg = f"{user_name} {change_type} {file_name}"
        else:
            msg = f"{file_name} {change_type}"

        self.desktop_notifier.notify("Items synced", msg, actions=buttons)

    def _check_download_conflict(self, event: SyncEvent) -> Conflict:
        """
        Check if a local item is conflicting with remote change. The equivalent check
        when uploading and a change will be carried out by Dropbox itself.

        Checks are carried out against our index, reflecting the latest sync state.
        We compare the following values:

        1) Local vs remote rev: 'folder' for folders, actual revision for files and None
           for deleted or not present items.
        2) Local vs remote content hash: 'folder' for folders, actual hash for files and
           None for deletions.
        3) Local ctime vs last sync time: This is calculated recursively for folders.

        :param event: Download SyncEvent.
        :returns: Conflict check result.
        """

        local_rev = self.get_local_rev(event.dbx_path_lower)

        if event.rev == local_rev:
            # Local change has the same rev. The local item (or deletion) must be newer
            # and not yet synced or identical to the remote state. Don't overwrite.
            self._logger.debug(
                'Equal revs for "%s": local item is the same or newer '
                "than on Dropbox",
                event.dbx_path,
            )
            return Conflict.LocalNewerOrIdentical

        elif event.content_hash == self.get_local_hash(event.local_path):
            # Content hashes are equal, therefore items are identical. Folders will
            # have a content hash of 'folder'.
            self._logger.debug(
                'Equal content hashes for "%s": no conflict', event.dbx_path
            )
            return Conflict.Identical
        elif any(
            is_equal_or_child(p, event.dbx_path_lower) for p in self.upload_errors
        ):
            # Local version could not be uploaded due to a sync error. Do not over-
            # write unsynced changes but declare a conflict.
            self._logger.debug(
                'Unresolved upload error for "%s": conflict', event.dbx_path
            )
            return Conflict.Conflict
        elif not self._ctime_newer_than_last_sync(event.local_path):
            # Last change time of local item (recursive for folders) is older than
            # the last time the item was synced. Remote must be newer.
            self._logger.debug(
                'Local item "%s" has no unsynced changes: remote item is newer',
                event.dbx_path,
            )
            return Conflict.RemoteNewer
        elif event.is_deleted:
            # Remote item was deleted but local item has been modified since then.
            self._logger.debug(
                'Local item "%s" has unsynced changes and remote was '
                "deleted: local item is newer",
                event.dbx_path,
            )
            return Conflict.LocalNewerOrIdentical
        else:
            # Both remote and local items have unsynced changes: conflict.
            self._logger.debug(
                'Local item "%s" has unsynced local changes: conflict',
                event.dbx_path,
            )
            return Conflict.Conflict

    def _ctime_newer_than_last_sync(self, local_path: str) -> bool:
        """
        Checks if a local item has any unsynced changes. This is by comparing its ctime
        to the ``last_sync`` time saved in our index. In case of folders, we recursively
        check the ctime of children.

        :param local_path: Local path of item to check.
        :returns: Whether the local item has unsynced changes.
        """

        if self.is_excluded(local_path):
            # excluded names such as .DS_Store etc never count as unsynced changes
            return False

        dbx_path_lower = self.to_dbx_path_lower(local_path)
        index_entry = self.get_index_entry(dbx_path_lower)

        with convert_api_errors():  # catch OSErrors

            try:
                stat = os.stat(local_path)
            except (FileNotFoundError, NotADirectoryError):
                # don't check ctime for deleted items (os won't give stat info)
                # but confirm absence from index
                return index_entry is not None

            if S_ISDIR(stat.st_mode):

                # don't check ctime for folders but compare to index entry type
                if index_entry is None or index_entry.is_file:
                    return True

                # recurse over children
                with os.scandir(local_path) as it:
                    for entry in it:
                        if entry.is_dir():
                            if self._ctime_newer_than_last_sync(entry.path):
                                return True
                        elif not self.is_excluded(entry.name):
                            child_dbx_path_lower = self.to_dbx_path_lower(entry.path)
                            ctime = entry.stat().st_ctime
                            if ctime > self.get_last_sync(child_dbx_path_lower):
                                return True

                return False

            else:
                # Check our ctime against index.
                return stat.st_ctime > self.get_last_sync(dbx_path_lower)

    def _get_ctime(self, local_path: str) -> float:
        """
        Returns the ctime of a local item or -1.0 if there is nothing at the path. If
        the item is a directory, return the largest ctime of it and its children. Items
        which are excluded from syncing (eg., .DS_Store files) are ignored.

        :param local_path: Absolute path on local drive.
        :returns: Ctime or -1.0.
        """
        try:
            stat = os.stat(local_path)
            if S_ISDIR(stat.st_mode):
                ctime = stat.st_ctime
                with os.scandir(local_path) as it:
                    for entry in it:
                        if entry.is_dir():
                            child_ctime = self._get_ctime(entry.path)
                        elif not self.is_excluded(entry.name):
                            child_ctime = entry.stat().st_ctime
                        else:
                            child_ctime = -1.0

                        ctime = max(ctime, child_ctime)

                return ctime
            else:
                return stat.st_ctime
        except (FileNotFoundError, NotADirectoryError):
            return -1.0

    def _clean_remote_changes(self, changes: ListFolderResult) -> ListFolderResult:
        """
        Takes remote file events since last sync and cleans them up so that there is
        only a single event per path.

        Dropbox will sometimes report multiple changes per path. Once such instance is
        when sharing a folder: ``files/list_folder/continue`` will report the shared
        folder and its children as deleted and then created because the folder *is*
        actually deleted from the user's Dropbox and recreated as a shared folder which
        then gets mounted to the user's Dropbox. Ideally, we want to deal with this
        without re-downloading all its contents.

        :param changes: Result from Dropbox API call to retrieve remote changes.
        :returns: Cleaned up changes with a single Metadata entry per path.
        """

        # Note: we won't have to deal with modified or moved events,
        # Dropbox only reports DeletedMetadata or FileMetadata / FolderMetadata

        histories: DefaultDict[str, List[Metadata]] = defaultdict(list)

        for entry in changes.entries:
            histories[entry.path_lower].append(entry)

        new_entries = []

        for h in histories.values():
            if len(h) == 1:
                new_entries.extend(h)
            else:
                last_event = h[-1]
                local_entry = self.get_index_entry(last_event.path_lower)
                was_dir = local_entry and local_entry.is_directory

                # Dropbox guarantees that applying events in the provided order will
                # reproduce the state in the cloud. We therefore keep only the last
                # event, unless there is a change in item type.
                if (
                    was_dir
                    and isinstance(last_event, FileMetadata)
                    or not was_dir
                    and isinstance(last_event, FolderMetadata)
                ):
                    deleted_event = DeletedMetadata(
                        name=last_event.name,
                        path_lower=last_event.path_lower,
                        path_display=last_event.path_display,
                        parent_shared_folder_id=last_event.parent_shared_folder_id,
                    )
                    new_entries.append(deleted_event)
                    new_entries.append(last_event)
                else:
                    new_entries.append(last_event)

        changes.entries = new_entries

        return changes

    def _create_local_entry(self, event: SyncEvent) -> SyncEvent:
        """
        Applies a file / folder change from Dropbox servers to the local Dropbox folder.
        Any :class:`maestral.errors.MaestralApiError` will be caught and logged as
        appropriate. Entries in the local index are created after successful completion.

        :param event: Dropbox metadata.
        :returns: Copy of the Dropbox metadata if the change was applied successfully,
            ``True`` if the change already existed, ``False`` in case of a sync error
            and ``None`` if cancelled.
        """

        if self._cancel_requested.is_set():
            raise CancelledError("Sync cancelled")

        self._slow_down()

        self.clear_sync_error(dbx_path=event.dbx_path)
        event.status = SyncStatus.Syncing

        try:
            if event.is_deleted:
                res = self._on_remote_deleted(event)
            elif event.is_file:
                with self.client.clone_with_new_session() as client:
                    res = self._on_remote_file(event, client)
            elif event.is_directory:
                res = self._on_remote_folder(event)
            else:
                res = None

            if res is not None:
                event.status = SyncStatus.Done
            else:
                event.status = SyncStatus.Skipped

        except SyncError as e:
            self._handle_sync_error(e, direction=SyncDirection.Down)
            event.status = SyncStatus.Failed
        finally:
            self.syncing.pop(event.local_path, None)

        # add to history database
        if event.status == SyncStatus.Done:
            with self._database_access():
                self._db_manager_history.save(event)

        return event

    def _on_remote_file(
        self, event: SyncEvent, client: Optional[DropboxClient]
    ) -> Optional[SyncEvent]:
        """
        Applies a remote file change or creation locally.

        :param event: SyncEvent for file download.
        :param client: Client instance to use. If not given, use the instance provided
            in the constructor.
        :returns: SyncEvent corresponding to local item or None if no local changes
            are made.
        """

        client = client or self.client

        self._apply_case_change(event)

        # Store the new entry at the given path in your local state. If the required
        # parent folders don’t exist yet, create them. If there’s already something else
        # at the given path, replace it and remove all its children.

        conflict_check = self._check_download_conflict(event)

        if conflict_check is Conflict.Identical:
            self.update_index_from_sync_event(event)
            return None
        elif conflict_check is Conflict.LocalNewerOrIdentical:
            return None

        local_path = event.local_path

        # we download to a temporary file first (this may take some time)
        tmp_fname = self._new_tmp_file()

        try:
            md = client.download(f"rev:{event.rev}", tmp_fname, sync_event=event)
            event = SyncEvent.from_dbx_metadata(md, self)
        except SyncError as err:
            # replace rev number with path
            err.dbx_path = event.dbx_path
            raise err

        # re-check for conflict and move the conflict
        # out of the way if anything has changed
        if self._check_download_conflict(event) == Conflict.Conflict:
            new_local_path = generate_cc_name(local_path)
            event_cls = DirMovedEvent if osp.isdir(local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(local_path, new_local_path)):
                with convert_api_errors():
                    move(local_path, new_local_path, raise_error=True)

            self._logger.debug(
                'Download conflict: renamed "%s" to "%s"', local_path, new_local_path
            )
            self.rescan(new_local_path)

        if osp.isdir(local_path):
            with self.fs_events.ignore(DirDeletedEvent(local_path)):
                delete(local_path)

        # check if we should preserve permissions of destination file
        old_entry = self.get_index_entry(event.dbx_path_lower)

        preserve_permissions = bool(old_entry and event.dbx_id == old_entry.dbx_id)

        ignore_events = [FileMovedEvent(tmp_fname, local_path)]

        if preserve_permissions:
            # ignore FileModifiedEvent when changing permissions
            ignore_events.append(FileModifiedEvent(local_path))

        if osp.isfile(local_path):
            # ignore FileDeletedEvent when replacing old file
            ignore_events.append(FileDeletedEvent(local_path))

        # move the downloaded file to its destination
        with self.fs_events.ignore(*ignore_events):

            mtime = os.stat(tmp_fname).st_mtime

            with convert_api_errors(dbx_path=event.dbx_path, local_path=local_path):
                move(
                    tmp_fname,
                    local_path,
                    preserve_dest_permissions=preserve_permissions,
                    raise_error=True,
                )

        self.update_index_from_sync_event(event)
        self._save_local_hash(event.local_path, event.content_hash, mtime)

        self._logger.debug('Created local file "%s"', event.dbx_path)

        return event

    def _on_remote_folder(self, event: SyncEvent) -> Optional[SyncEvent]:
        """
        Applies a remote folder creation locally.

        :param event: SyncEvent for folder download.
        :returns: SyncEvent corresponding to local item or None if no local changes
            are made.
        """

        self._apply_case_change(event)

        # Store the new entry at the given path in your local state. If the required
        # parent folders don’t exist yet, create them. If there’s already something else
        # at the given path, replace it but leave the children as they are.

        conflict_check = self._check_download_conflict(event)

        if conflict_check is Conflict.Identical:
            self.update_index_from_sync_event(event)
            return None
        elif conflict_check is Conflict.LocalNewerOrIdentical:
            return None

        if conflict_check == Conflict.Conflict:
            new_local_path = generate_cc_name(event.local_path)
            event_cls = DirMovedEvent if osp.isdir(event.local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(event.local_path, new_local_path)):
                with convert_api_errors():
                    move(event.local_path, new_local_path, raise_error=True)

            self._logger.debug(
                'Download conflict: renamed "%s" to "%s"',
                event.local_path,
                new_local_path,
            )
            self.rescan(new_local_path)

        if osp.isfile(event.local_path):
            event_cls = (
                DirDeletedEvent if osp.isdir(event.local_path) else FileDeletedEvent
            )
            with self.fs_events.ignore(event_cls(event.local_path)):
                delete(event.local_path)

        try:
            with self.fs_events.ignore(
                DirCreatedEvent(event.local_path), recursive=False
            ):
                os.makedirs(event.local_path)
        except FileExistsError:
            pass
        except OSError as err:
            raise os_to_maestral_error(err, dbx_path=event.dbx_path)

        self.update_index_from_sync_event(event)

        self._logger.debug('Created local folder "%s"', event.dbx_path)

        return event

    def _on_remote_deleted(self, event: SyncEvent) -> Optional[SyncEvent]:
        """
        Applies a remote deletion locally.

        :param event: Dropbox deleted metadata.
        :returns: SyncEvent corresponding to local deletion or None if no local changes
            are made.
        """

        self._apply_case_change(event)

        # If your local state has something at the given path, remove it and all its
        # children. If there’s nothing at the given path, ignore this entry.

        conflict_check = self._check_download_conflict(event)

        if conflict_check is Conflict.Identical:
            self.update_index_from_sync_event(event)
            return None
        elif conflict_check is Conflict.LocalNewerOrIdentical:
            return None

        event_cls = DirDeletedEvent if osp.isdir(event.local_path) else FileDeletedEvent
        with self.fs_events.ignore(event_cls(event.local_path)):
            exc = delete(event.local_path)

        if not exc:
            self.update_index_from_sync_event(event)
            self._logger.debug('Deleted local item "%s"', event.dbx_path)
            return event
        elif isinstance(exc, (FileNotFoundError, NotADirectoryError)):
            self.update_index_from_sync_event(event)
            self._logger.debug('Deletion failed: "%s" not found', event.dbx_path)
            return None
        else:
            raise os_to_maestral_error(exc, dbx_path=event.dbx_path)

    def _apply_case_change(self, event: SyncEvent) -> None:
        """
        Applies any changes in casing of the remote item locally. This should be called
        before any system calls using ``local_path`` because the actual path on the file
        system may have a different casing on case-sensitive file systems. On case-
        insensitive file systems, this causes only a cosmetic change.

        :param event: Download SyncEvent.
        """

        with self._database_access():
            entry = self.get_index_entry(event.dbx_path_lower)

        if entry and entry.dbx_path_cased != event.dbx_path:

            local_path_old = self.to_local_path_from_cased(entry.dbx_path_cased)

            event_cls = DirMovedEvent if osp.isdir(local_path_old) else FileMovedEvent
            with self.fs_events.ignore(event_cls(local_path_old, event.local_path)):
                move(local_path_old, event.local_path)

            with self._database_access():
                entry.dbx_path_cased = event.dbx_path
                self._db_manager_index.update(entry)

            self._logger.debug('Renamed "%s" to "%s"', local_path_old, event.local_path)

    def rescan(self, local_path: str) -> None:
        """
        Forces a rescan of a local path: schedules created events for every folder,
        modified events for every file and deleted events for every deleted item
        (compared to our index).

        :param local_path: Path to rescan.
        """

        self._logger.debug('Rescanning "%s"', local_path)

        if osp.isfile(local_path):
            self.fs_events.queue_event(FileModifiedEvent(local_path))

        elif osp.isdir(local_path):
            self.fs_events.queue_event(DirCreatedEvent(local_path))

            # add created and deleted events of children as appropriate

            for path, stat in walk(local_path, self._scandir_with_ignore):

                if S_ISDIR(stat.st_mode):
                    self.fs_events.queue_event(DirCreatedEvent(path))
                else:
                    self.fs_events.queue_event(FileModifiedEvent(path))

            # add deleted events

            local_path_lower = normalize(local_path)

            with self._database_access():

                entries = self._db_manager_index.query_to_objects(
                    "SELECT * FROM 'index' WHERE dbx_path_lower LIKE ?",
                    f"{local_path_lower}%",
                )

            for entry in entries:
                entry = cast(IndexEntry, entry)
                child_path = self.to_local_path_from_cased(entry.dbx_path_cased)
                if not osp.exists(child_path):
                    if entry.is_directory:
                        self.fs_events.queue_event(DirDeletedEvent(child_path))
                    else:
                        self.fs_events.queue_event(FileDeletedEvent(child_path))

        elif not osp.exists(local_path):
            dbx_path_lower = self.to_dbx_path_lower(local_path)

            local_entry = self.get_index_entry(dbx_path_lower)

            if local_entry:
                if local_entry.is_directory:
                    self.fs_events.queue_event(DirDeletedEvent(local_path))
                else:
                    self.fs_events.queue_event(FileDeletedEvent(local_path))

    def _clean_history(self):
        """Commits new events and removes all events older than ``_keep_history`` from
        history."""

        with self._database_access():

            # drop all entries older than keep_history
            now = time.time()
            keep_history = self._conf.get("sync", "keep_history")

            self._db.execute(
                "DELETE FROM history WHERE IFNULL(change_time, sync_time) < ?",
                now - keep_history,
            )
            self._db_manager_history.clear_cache()

    def _scandir_with_ignore(
        self, path: Union[str, os.PathLike]
    ) -> Iterator[os.DirEntry]:

        with os.scandir(path) as it:
            for entry in it:
                dbx_path = self.to_dbx_path(entry.path)
                if not self.is_excluded(entry.path) and not self._is_mignore_path(
                    dbx_path, entry.is_dir()
                ):
                    yield entry


# ======================================================================================
# Helper functions
# ======================================================================================


def get_dest_path(event: FileSystemEvent) -> str:
    """
    Returns the dest_path of a file system event if present (moved events only)
    otherwise returns the src_path (which is also the "destination").

    :param event: Watchdog file system event.
    :returns: Destination path for moved event, source path otherwise.
    """
    return getattr(event, "dest_path", event.src_path)


def split_moved_event(
    event: Union[FileMovedEvent, DirMovedEvent]
) -> Tuple[FileSystemEvent, FileSystemEvent]:
    """
    Splits a FileMovedEvent or DirMovedEvent into "deleted" and "created" events of the
    same type. A new attribute ``move_id`` is added to both instances.

    :param event: Original event.
    :returns: Tuple of deleted and created events.
    """

    if event.is_directory:
        created_event_cls = DirCreatedEvent
        deleted_event_cls = DirDeletedEvent
    else:
        created_event_cls = FileCreatedEvent
        deleted_event_cls = FileDeletedEvent

    deleted_event = deleted_event_cls(event.src_path)
    created_event = created_event_cls(event.dest_path)

    move_id = uuid.uuid4()

    deleted_event.move_id = move_id
    created_event.move_id = move_id

    return deleted_event, created_event


class pf_repr:
    """
    Class that wraps an object and creates a pretty formatted representation for it.
    This can be used to get pretty formatting in log messages while deferring the actual
    formatting until the message is created.

    :param obj: Object to wrap.
    """

    def __init__(self, obj: Any) -> None:
        self.obj = obj

    def __repr__(self) -> str:
        return pformat(self.obj)


_last_emit = time.time()


def throttled_log(
    log: logging.Logger, msg: str, level: int = logging.INFO, limit: int = 2
) -> None:
    """
    Emits the given log message only if the previous message was emitted more than
    ``limit`` seconds ago. This can be used to prevent spamming a log with frequent
    updates.

    :param log: Logger used to emit the message.
    :param msg: Log message.
    :param level: Log level.
    :param limit: Minimum time between log messages.
    """

    global _last_emit

    if time.time() - _last_emit > limit:
        log.log(level=level, msg=msg)
        _last_emit = time.time()


def validate_encoding(local_path: str) -> None:
    """
    Validate that the path contains only characters in the reported file system
    encoding. On Unix, paths are fundamentally bytes and some platforms do not enforce
    a uniform encoding of file names despite reporting a file system encoding. Such
    paths will be handed to us in Python with "surrogate escapes" in place of the
    unknown characters.

    Since the Dropbox API and our database both require utf-8 encoded paths, we use this
    method to check and fail early on unknown characters.

    :param local_path: Path to check.
    :raises PathError: if the path contains characters with an unknown encoding.
    """

    try:
        local_path.encode()
    except UnicodeEncodeError:

        fs_encoding = sys.getfilesystemencoding()

        error = PathError(
            "Could not upload item",
            f"The file name contains characters outside of the "
            f"{fs_encoding} encoding of your file system",
        )

        error.local_path = local_path

        raise error
