# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

This module is the heart of Maestral, it contains the classes for sync functionality.

"""

# system imports
import os
import os.path as osp
from stat import S_ISDIR
import resource
import logging
import gc
import time
import tempfile
import random
import json
from threading import Thread, Event, RLock, current_thread
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty
from collections import abc
from contextlib import contextmanager
import enum
import pprint
import socket
from datetime import timezone
from typing import Optional, Any, List, Dict, Tuple, Union, Iterator, Callable, Type, cast
from types import TracebackType

# external imports
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import Column, Integer, String, Enum, Float, create_engine
import pathspec  # type: ignore
import dropbox  # type: ignore
from dropbox.files import Metadata, DeletedMetadata, FileMetadata, FolderMetadata  # type: ignore
from watchdog.events import FileSystemEventHandler  # type: ignore
from watchdog.events import (
    EVENT_TYPE_CREATED, EVENT_TYPE_DELETED, EVENT_TYPE_MOVED, EVENT_TYPE_MODIFIED
)
from watchdog.events import (
    DirModifiedEvent, FileModifiedEvent, DirCreatedEvent, FileCreatedEvent,
    DirDeletedEvent, FileDeletedEvent, DirMovedEvent, FileMovedEvent, FileSystemEvent
)
from watchdog.utils.dirsnapshot import DirectorySnapshot  # type: ignore
from atomicwrites import atomic_write

# local imports
from maestral.config import MaestralConfig, MaestralState
from maestral.fsevents import Observer
from maestral.constants import (
    IDLE, SYNCING, PAUSED, STOPPED, DISCONNECTED, EXCLUDED_FILE_NAMES,
    EXCLUDED_DIR_NAMES, MIGNORE_FILE, FILE_CACHE
)
from maestral.errors import (
    SyncError, RevFileError, NoDropboxDirError, CacheDirError, InvalidDbidError,
    PathError, NotFoundError, FileConflictError, FolderConflictError, IsAFolderError
)
from maestral.client import DropboxClient, os_to_maestral_error, fswatch_to_maestral_error
from maestral.utils.content_hasher import DropboxContentHasher
from maestral.utils.notify import MaestralDesktopNotifier
from maestral.utils.path import (
    generate_cc_name, cased_path_candidates, to_cased_path, is_fs_case_sensitive,
    move, delete, is_child, is_equal_or_child
)
from maestral.utils.appdirs import get_data_path, get_home_dir


logger = logging.getLogger(__name__)
_cpu_count = os.cpu_count() or 1  # os.cpu_count can return None

Base = declarative_base()
Session = sessionmaker()

ExecInfoType = Tuple[Type[BaseException], BaseException, Optional[TracebackType]]
_FT = Callable[..., Any]


# ========================================================================================
# Syncing functionality
# ========================================================================================

class Conflict(enum.Enum):
    """
    Enumeration of sync conflict types.

    :cvar RemoteNewer: Remote item is newer.
    :cvar Conflict: Conflict.
    :cvar Identical: Items are identical.
    :cvar LocalNewerOrIdentical: Local item is newer or identical.
    """
    RemoteNewer = 'remote newer'
    Conflict = 'conflict'
    Identical = 'identical'
    LocalNewerOrIdentical = 'local newer or identical'


class SyncDirection(enum.Enum):
    """
    Enumeration of sync direction.

    :cvar Up: Upload.
    :cvar Down: Download.
    """
    Up = 'up'
    Down = 'down'


class SyncStatus(enum.Enum):
    """
    Enumeration of sync status values.

    :cvar Queued: Queued for syncing.
    :cvar Syncing: Sync in progress.
    :cvar Done: Sync successfully completed.
    :cvar Failed: Sync failed.
    :cvar Skipped: Item was already in sync.
    """
    Queued = 'queued'
    Syncing = 'syncing'
    Done = 'done'
    Failed = 'failed'
    Skipped = 'skipped'
    Aborted = 'aborted'


class ItemType(enum.Enum):
    """
    Enumeration of sync item types.

    :cvar File: File type.
    :cvar Folder: Folder type.
    """
    File = 'file'
    Folder = 'folder'


class ChangeType(enum.Enum):
    """
    Enumeration of sync change types.

    :cvar Added: An added file or folder.
    :cvar Deleted: A deleted file or folder.
    :cvar Moved: A moved file or folder.
    :cvar Changed: A changed file. Does not apply to folders
    """
    Added = 'added'
    Deleted = 'deleted'
    Moved = 'moved'
    Changed = 'changed'


class InQueue:
    """
    A context manager that puts ``items`` into ``queue`` when entering the context and
    removes them when exiting. This is used by maestral to keep track of uploads and
    downloads.
    """

    def __init__(self, queue: Queue, *items) -> None:
        """
        :param queue: Instance of :class:`queue.Queue`.
        :param items: Items to put in queue.
        """
        self.items = items
        self.queue = queue

    def __enter__(self) -> None:
        for item in self.items:
            self.queue.put(item)

    def __exit__(self, err_type: Type[Exception], err_value: Exception,
                 err_traceback: TracebackType) -> None:
        remove_from_queue(self.queue, *self.items)


class _Ignore:

    def __init__(self, event: FileSystemEvent, start_time: float,
                 ttl: Optional[float], recursive: bool) -> None:
        self.event = event
        self.start_time = start_time
        self.ttl = ttl
        self.recursive = recursive


class FSEventHandler(FileSystemEventHandler):
    """
    Handles captured file events and adds them to :class:`SyncEngine`'s file event queue
    to be uploaded by :meth:`upload_worker`. This acts as a translation layer between
    :class:`watchdog.Observer` and :class:`SyncEngine`.

    :param syncing: Set when syncing is running.
    :param startup: Set when startup is running.

    :cvar float ignore_timeout: Timeout in seconds after which ignored paths will be
        discarded.
    """

    _ignored_events: List[_Ignore]
    local_file_event_queue: 'Queue[FileSystemEvent]'

    ignore_timeout = 2.0

    def __init__(self, syncing: Event, startup: Event) -> None:

        self.syncing = syncing
        self.startup = startup

        self._ignored_events = []
        self._ignored_events_mutex = RLock()

        self.local_file_event_queue = Queue()

    @contextmanager
    def ignore(self, *events: FileSystemEvent, recursive: bool = True) -> Iterator[None]:
        """
        A context manager to ignore file events. Once a matching event has been
        registered, further matching events will no longer be ignored unless ``recursive``
        is ``True``. If no matching event has occurred before leaving the context, the
        event will be ignored for ``ignore_timeout`` sec after leaving then context and
        then discarded. This accounts for possible delays in the emission of local file
        system events.

        This context manager is used to filter out file system events caused by maestral
        itself, for instance during a download or when moving a conflict.

        :param events: Local events to ignore.
        :param recursive: If ``True``, all child events of a directory event will be
            ignored as well. This parameter will be ignored for file events.
        """

        with self._ignored_events_mutex:
            now = time.time()
            new_ignores = []
            for e in events:
                new_ignores.append(
                    _Ignore(
                        event=e,
                        start_time=now,
                        ttl=None,
                        recursive=recursive and e.is_directory,
                    )
                )
            self._ignored_events.extend(new_ignores)

        try:
            yield
        finally:
            with self._ignored_events_mutex:
                for ignore in new_ignores:
                    ignore.ttl = time.time() + self.ignore_timeout

    def _expire_ignored_events(self) -> None:
        """Removes all expired ignore entries."""

        with self._ignored_events_mutex:

            now = time.time()
            for ignore in self._ignored_events.copy():
                ttl = ignore.ttl
                if ttl and ttl < now:
                    self._ignored_events.remove(ignore)

    def _is_ignored(self, event: FileSystemEvent) -> bool:
        """
        Checks if a file system event should been explicitly ignored because it was
        triggered by Maestral itself.

        :param event: Local file system event.
        :returns: Whether the event should be ignored.
        """

        with self._ignored_events_mutex:

            self._expire_ignored_events()

            for ignore in self._ignored_events:
                ignore_event = ignore.event
                recursive = ignore.recursive

                if event == ignore_event:

                    if not recursive:
                        self._ignored_events.remove(ignore)

                    return True

                elif recursive:

                    type_match = event.event_type == ignore_event.event_type
                    src_match = is_equal_or_child(event.src_path, ignore_event.src_path)
                    dest_match = is_equal_or_child(get_dest_path(event),
                                                   get_dest_path(ignore_event))

                    if type_match and src_match and dest_match:
                        return True

            return False

    def on_any_event(self, event: FileSystemEvent) -> None:
        """
        Callback on any event. Checks if the system file event should be ignored. If not,
        adds it to the queue for events to upload. If syncing is paused or stopped, all
        events will be ignored.

        :param event: Watchdog file event.
        """

        # ignore events if we are not during startup or sync
        if not (self.syncing.is_set() or self.startup.is_set()):
            return

        # ignore all DirMovedEvents
        if isinstance(event, DirModifiedEvent):
            return

        # check if event should be ignored
        if self._is_ignored(event):
            return

        self.local_file_event_queue.put(event)


class PersistentStateMutableSet(abc.MutableSet):
    """
    A wrapper for a list of Python types in the saved state that implements a MutableSet
    interface.

    :param config_name: Name of config (determines name of state file).
    :param section: Section name in state file.
    :param option: Option name in state file.
    """

    _lock = RLock()

    def __init__(self, config_name: str, section: str, option: str) -> None:
        super().__init__()
        self.config_name = config_name
        self.section = section
        self.option = option
        self._state = MaestralState(config_name)

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
        return f'<{self.__class__.__name__}(section=\'{self.section}\',' \
               f'option=\'{self.option}\', entries={list(self)})>'


class SyncEvent(Base):
    """
    Represents a file or folder change in the sync queue. This is used to abstract the
    :class:`watchdog.events.FileSystemEvent`s created for local changes and the
    :class:`dropbox.files.Metadata` created for remote changes. All arguments are used to
    construct instance attributes and some attributes may not be set for all event types.
    Note that some instance attributes depend on the state of the Maestral instance, e.g.,
    :attr:`local_path` will depend on the current path of the local Dropbox folder. They
    may therefore become invalid after sync sessions.

    The convenience methods :meth:`SyncEngine.sync_event_from_dbx_metadata` and
    :meth:`SyncEngine.sync_event_from_file_system_event` should be used to properly
    construct a SyncEvent from Dropbox Metadata or a local FileSystemEvent, respectively.

    :param direction: Direction of the sync: upload or download.
    :param item_type: The item type: file or folder.
    :param sync_time: The time the sync event was registered.
    :param dbx_path: Dropbox path of the item to sync. If the sync represents a move
        operation, this will be the destination path. The path does not need to be
        correctly cased apart from the basename.
    :param dbx_path_from: Dropbox path that this item was moved from. Will only be set if
        ``change_type`` is ``ChangeType.Moved``. The same rules for casing as for
        ``dbx_path`` apply.
    :param local_path: Local path of the item to sync. If the sync represents a move
        operation, this will be the destination path. This must always be correctly cased
        for all existing local ancestors of the path.
    :param local_path_from: Local path that this item was moved from. Will only be set if
        ``change_type`` is ``ChangeType.Moved``. The same rules for casing as for
        ``local_path`` apply.
    :param rev: The file revision. Will only be set for remote changes. Will be 'folder'
        for folders and None for deletions.
    :param content_hash: A hash representing the file content. Will be 'folder' for
        folders and None for deleted items. Set for both local and remote changes.
    :param change_type: The type of change: deleted, moved, added or changed.
    :param change_time: The time of the change: Local ctime or remote client_modified
        time for files. None for folders or for remote deletions. Note that the
        client_modified may not be reliable as it is set by other clients and not
        verified.
    :param change_dbid: The Dropbox ID of the account which performed the changes. This
        may not be set for added folders or deletions on the server.
    :param change_user_name: The user name of the account which performed the changes.
        This will only be set if the account given by ``change_dbid`` still exists.
    :param status: Field containing the sync status: queued, syncing, done, failed,
        skipped (item was already in sync) or aborted (by the user).
    :param size: Size of the item in bytes. Always zero for folders.

    :attr completed: File size in bytes which has already been uploaded or downloaded.
        Always zero for folders.
    """

    __tablename__ = 'history'

    id = Column(Integer, primary_key=True)
    direction = Column(Enum(SyncDirection))
    item_type = Column(Enum(ItemType))
    sync_time = Column(Float)
    dbx_path = Column(String)
    local_path = Column(String)
    dbx_path_from = Column(String)
    local_path_from = Column(String)
    rev = Column(String)
    content_hash = Column(String)
    change_type = Column(Enum(ChangeType))
    change_time = Column(Float)
    change_dbid = Column(String)
    change_user_name = Column(String)
    status = Column(Enum(SyncStatus))
    size = Column(Integer)
    completed = Column(Integer)

    def __init__(self,
                 direction: SyncDirection,
                 item_type: ItemType,
                 sync_time: float,
                 dbx_path: str,
                 local_path: str,
                 dbx_path_from: Optional[str],
                 local_path_from: Optional[str],
                 rev: Optional[str],
                 content_hash: Optional[str],
                 change_type: ChangeType,
                 change_time: Optional[float],
                 change_dbid: Optional[str],
                 change_user_name: Optional[str],
                 status: SyncStatus,
                 size: int,
                 orig: Union[FileSystemEvent, Metadata, None] = None) -> None:

        self.direction = direction
        self.item_type = item_type
        self.sync_time = sync_time
        self.dbx_path = dbx_path
        self.local_path = local_path
        self.dbx_path_from = dbx_path_from
        self.local_path_from = local_path_from
        self.rev = rev
        self.content_hash = content_hash
        self.change_type = change_type
        self.change_time = change_time
        self.change_dbid = change_dbid
        self.change_user_name = change_user_name
        self.status = status
        self.size = size
        self.completed = 0
        self.orig = orig

    @property
    def is_file(self) -> bool:
        """Returns True for file changes"""
        return self.item_type == ItemType.File

    @property
    def is_directory(self) -> bool:
        """Returns True for folder changes"""
        return self.item_type == ItemType.Folder

    @property
    def is_added(self) -> bool:
        """Returns True for added items"""
        return self.change_type == ChangeType.Added

    @property
    def is_moved(self) -> bool:
        """Returns True for moved items"""
        return self.change_type == ChangeType.Moved

    @property
    def is_changed(self) -> bool:
        """Returns True for changed file contents"""
        return self.change_type == ChangeType.Changed

    @property
    def is_deleted(self) -> bool:
        """Returns True for deleted items"""
        return self.change_type == ChangeType.Deleted

    @property
    def is_upload(self) -> bool:
        """Returns True for changes to upload"""
        return self.direction == SyncDirection.Up

    @property
    def is_download(self) -> bool:
        """Returns True for changes to download"""
        return self.direction == SyncDirection.Down

    def __repr__(self):
        return f"<{self.__class__.__name__}(direction={self.direction.name}, " \
               f"change_type={self.change_type.name}, dbx_path='{self.dbx_path}')>"


class SyncEngine:
    """
    Class that contains methods to sync local file events with Dropbox and vice versa.

    Notes on event processing:

    Remote events come in three types: DeletedMetadata, FolderMetadata and FileMetadata.
    The Dropbox API does not differentiate between created, moved or modified events.
    Maestral processes remote events as follows:

      1) :meth:`wait_for_remote_changes` blocks until remote changes are available.
      2) :meth:`get_remote_changes` lists all remote changes since the last sync.
      3) :meth:`_clean_remote_changes`: Combines multiple events per file path into one.
         This is rarely necessary, Dropbox typically already provides only a single event
         per path but this is not guaranteed and may change. One exception is sharing a
         folder: This is done by removing the folder from Dropbox and re-mounting it as a
         shared folder and produces at least one DeletedMetadata and one FolderMetadata
         event. If querying for changes *during* this process, multiple DeletedMetadata
         events may be returned. If a file / folder event implies a type changes, e.g.,
         replacing a folder with a file, we explicitly generate the necessary
         DeletedMetadata here to simplify conflict resolution.
      4) :meth:`_filter_excluded_changes_remote`: Filters out events that occurred for
         entries that are excluded by selective sync and hard-coded file names which are
         always excluded (e.g., '.DS_Store').
      5) :meth:`apply_remote_changes`: Sorts all events hierarchically, with top-level
         events coming first. Deleted and folder events are processed in order, file
         events in parallel with up to 6 worker threads. The actual download is carried
         out by :meth:`_create_local_entry`.
      6) :meth:`_create_local_entry`: Checks for sync conflicts by comparing the file
         "rev" with our locally saved rev. We assign folders a rev of ``'folder'`` and
         deleted / non-existent items a rev of ``None``. If revs are equal, the local item
         is the same or newer as on Dropbox and no download / deletion occurs. If revs are
         different, we compare content hashes. Folders are assigned a hash of 'folder'. If
         hashes are equal, no download occurs. If they are different, we check if the
         local item has been modified since the last download sync. In case of a folder,
         we take the newest change of any of its children. If the local entry has not been
         modified since the last sync, it will be replaced. Otherwise, we create a
         conflicting copy.
      7) :meth:`notify_user`: Shows a desktop notification for the remote changes.

    Local file events come in eight types: For both files and folders we collect created,
    moved, modified and deleted events. They are processed as follows:

      1) :meth:`wait_for_local_changes`: Blocks until local changes were registered by
         :class:`FSEventHandler` and returns those changes.
      2) :meth:`_filter_excluded_changes_local`: Filters out events ignored by a "mignore"
         pattern as well as hard-coded file names and changes in our cache path.
      3) :meth:`_clean_local_events`: Cleans up local events in two stages. First,
         multiple events per path are combined into a single event which reproduces the
         file changes. The only exception is when the entry type changes from file to
         folder or vice versa: in this case, both deleted and created events are kept.
         Second, when a whole folder is moved or deleted, we discard the moved and deleted
         events of its children.
      4) :meth:`apply_local_changes`: Sorts local changes hierarchically and applies
         events in the order of deleted, folders and files. Deletions and creations will
         be carried out in parallel with up to 6 threads. Conflict resolution and the
         actual upload will be handled by :meth:`_create_remote_entry` as follows:
      5) :meth:`_create_remote_entry`: For created and moved events, we check if the new
         path has been excluded by the user with selective sync but still exists on
         Dropbox. If yes, it will be renamed by appending "(selective sync conflict)". On
         case-sensitive file systems, we check if the new path differs only in casing from
         an existing path. If yes, it will be renamed by appending "(case conflict)". If a
         file has been replaced with a folder or vice versa, we check if any un-synced
         changes will be lost by replacing the remote item and create a conflicting copy
         if necessary. Dropbox does not handle conflict resolution for us in this case.
         For created or modified files, check if the local content hash equals the remote
         content hash. If yes, we don't upload but update our rev number. If no, we upload
         the changes and specify the rev which we want to replace or delete. If the remote
         item is newer (different rev), Dropbox will handle conflict resolution for us. We
         finally confirm the successful upload and check if Dropbox has renamed the item
         to a conflicting copy. In the latter case, we apply those changes locally.

    :param client: Dropbox API client instance.
    :param fs_events_handler: File system event handler to inform us of local events.

    """

    sync_errors: 'Queue[SyncError]'
    queued_for_upload: 'Queue[SyncEvent]'
    queued_for_download: 'Queue[SyncEvent]'
    uploading: 'Queue[SyncEvent]'
    downloading: 'Queue[SyncEvent]'
    history: 'Queue[SyncEvent]'
    _rev_dict_cache: Dict[str, str]
    _last_sync_for_path: Dict[str, float]

    _max_history = 30
    _num_threads = min(32, _cpu_count * 3)

    def __init__(self, client: DropboxClient, fs_events_handler: FSEventHandler):

        self.client = client
        self.config_name = self.client.config_name
        self.cancel_pending = Event()
        self.fs_events = fs_events_handler

        self.sync_lock = RLock()
        self._rev_lock = RLock()

        self._conf = MaestralConfig(self.config_name)
        self._state = MaestralState(self.config_name)
        self._notifier = MaestralDesktopNotifier.for_config(self.config_name)

        # upload_errors / download_errors: contains failed uploads / downloads
        # (from sync errors) to retry later
        self.upload_errors = PersistentStateMutableSet(
            self.config_name, section='sync', option='upload_errors'
        )
        self.download_errors = PersistentStateMutableSet(
            self.config_name, section='sync', option='download_errors'
        )
        # pending_uploads / pending_downloads: contains interrupted uploads / downloads
        # to retry later. Running uploads / downloads can be stored in these lists to be
        # resumed if Maestral quits unexpectedly. This used for downloads which are not
        # part of the regular sync cycle and are therefore not restarted automatically.
        self.pending_downloads = PersistentStateMutableSet(
            self.config_name, section='sync', option='pending_downloads'
        )
        self.pending_uploads = PersistentStateMutableSet(
            self.config_name, section='sync', option='pending_uploads'
        )

        # queues used for internal communication
        self.sync_errors = Queue()  # entries are `SyncIssue` instances

        # data structures for user information
        self.queued_for_upload = Queue()
        self.queued_for_download = Queue()
        self.uploading = Queue()
        self.downloading = Queue()

        # determine file paths
        self._dropbox_path = osp.realpath(self._conf.get('main', 'path'))
        self._mignore_path = osp.join(self._dropbox_path, MIGNORE_FILE)
        self._file_cache_path = osp.join(self._dropbox_path, FILE_CACHE)
        self._rev_file_path = get_data_path('maestral', f'{self.config_name}.index')
        self._db_path = get_data_path('maestral', f'{self.config_name}.db')

        # initialize history database
        self._db_engine = create_engine(
            f'sqlite:///file:{self._db_path}?check_same_thread=false&uri=true'
        )
        Base.metadata.create_all(self._db_engine)
        Session.configure(bind=self._db_engine)
        self._db_session = Session()
        self._keep_history = 60*60*24*7

        # load cached properties
        self._is_case_sensitive = is_fs_case_sensitive(get_home_dir())
        self._rev_dict_cache = dict()
        self._load_rev_dict_from_file(raise_exception=True)
        self._excluded_items = self._conf.get('main', 'excluded_items')
        self._mignore_rules = self._load_mignore_rules_form_file()
        self._last_sync_for_path = dict()

        self._max_cpu_percent = self._conf.get('sync', 'max_cpu_percent') * _cpu_count

        # clean our file cache
        self.clean_cache_dir()

    # ==== settings ======================================================================

    @property
    def dropbox_path(self) -> str:
        """
        Path to local Dropbox folder, as loaded from the config file. Before changing
        :attr:`dropbox_path`, make sure that syncing is paused. Move the dropbox folder to
        the new location before resuming the sync. Changes are saved to the config file.
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
            self._is_case_sensitive = is_fs_case_sensitive(self._dropbox_path)
            self._conf.set('main', 'path', path)

    @property
    def rev_file_path(self) -> str:
        """Path to sync index with rev numbers (read only)."""
        return self._rev_file_path

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
            self._conf.set('main', 'excluded_items', clean_list)

    @staticmethod
    def clean_excluded_items_list(folder_list: List[str]) -> List[str]:
        """
        Removes all duplicates and children of excluded items from the excluded items
        list.

        :param folder_list: Dropbox paths to exclude.
        :returns: Cleaned up items.
        """

        # remove duplicate entries by creating set, strip trailing '/'
        folder_set = set(f.lower().rstrip('/') for f in folder_list)

        # remove all children of excluded folders
        clean_list = list(folder_set)
        for folder in folder_set:
            clean_list = [f for f in clean_list if not is_child(f, folder)]

        return clean_list

    @property
    def max_cpu_percent(self) -> float:
        """Maximum CPU usage for parallel downloads or uploads in percent of the total
        available CPU time per core. Individual workers in a thread pool will pause until
        the usage drops below this value. Tasks in the main thread such as indexing file
        changes may still use more CPU time. Setting this to 100% means that no limits on
        CPU usage will be applied."""
        return self._max_cpu_percent

    @max_cpu_percent.setter
    def max_cpu_percent(self, percent: float) -> None:
        """Setter: max_cpu_percent."""
        self._max_cpu_percent = percent
        self._conf.set('app', 'max_cpu_percent', percent // _cpu_count)

    # ==== sync state ====================================================================

    @property
    def last_cursor(self) -> str:
        """Cursor from last sync with remote Dropbox. The value is updated and saved to
        the config file on every successful download of remote changes."""
        return self._state.get('sync', 'cursor')

    @last_cursor.setter
    def last_cursor(self, cursor: str) -> None:
        """Setter: last_cursor"""
        with self.sync_lock:
            self._state.set('sync', 'cursor', cursor)
            logger.debug('Remote cursor saved: %s', cursor)

    @property
    def last_sync(self) -> float:
        """Time stamp from last sync with remote Dropbox. The value is updated and saved
        to the config file on every successful upload of local changes."""
        return self._state.get('sync', 'lastsync')

    @last_sync.setter
    def last_sync(self, last_sync: float) -> None:
        """Setter: last_sync"""
        with self.sync_lock:
            logger.debug('Local cursor saved: %s', last_sync)
            self._state.set('sync', 'lastsync', last_sync)

    @property
    def last_reindex(self) -> float:
        """Time stamp of last indexing. This is used to determine when the next full
        indexing should take place."""
        return self._state.get('sync', 'last_reindex')

    @last_reindex.setter
    def last_reindex(self, time_stamp: float) -> None:
        """Setter: last_reindex."""
        self._state.set('sync', 'last_reindex', time_stamp)

    def get_last_sync_for_path(self, dbx_path: str) -> float:
        """
        Returns the timestamp of last sync for an individual path.

        :param dbx_path: Path relative to Dropbox folder.
        :returns: Time of last sync.
        """
        dbx_path = dbx_path.lower()
        return max(self._last_sync_for_path.get(dbx_path, 0.0), self.last_sync)

    def set_last_sync_for_path(self, dbx_path: str, last_sync: float) -> None:
        """
        Sets the timestamp of last sync for a path.

        :param dbx_path: Path relative to Dropbox folder.
        :param last_sync: Time of last sync.
        """
        dbx_path = dbx_path.lower()
        if last_sync == 0.0:
            try:
                del self._last_sync_for_path[dbx_path]
            except KeyError:
                pass
        else:
            self._last_sync_for_path[dbx_path] = last_sync

    @property
    def history(self):
        return self._db_session.query(SyncEvent).order_by(SyncEvent.change_time).all()

    # ==== rev file management ===========================================================

    def get_rev_index(self) -> Dict[str, str]:
        """
        Returns a copy of the revision index containing the revision numbers for all
        synced files and folders.

        :returns: Copy of revision index.
        """
        with self._rev_lock:
            return self._rev_dict_cache.copy()

    def get_local_rev(self, dbx_path: str) -> Optional[str]:
        """
        Gets revision number of local file.

        :param dbx_path: Path relative to Dropbox folder.
        :returns: Revision number as str or ``None`` if no local revision number has been
            saved.
        """
        with self._rev_lock:
            dbx_path = dbx_path.lower()
            rev = self._rev_dict_cache.get(dbx_path, None)

            return rev

    def set_local_rev(self, dbx_path: str, rev: Optional[str]) -> None:
        """
        Saves revision number ``rev`` for local file. If ``rev`` is ``None``, the entry
        for the file is removed.

        :param dbx_path: Path relative to Dropbox folder.
        :param rev: Revision number as string or ``None``.
        """
        with self._rev_lock:
            dbx_path = dbx_path.lower()

            if rev == self._rev_dict_cache.get(dbx_path, None):
                # rev is already set, nothing to do
                return

            if rev is None:
                # remove entry and all its children revs
                for path in dict(self._rev_dict_cache):
                    if is_equal_or_child(path, dbx_path):
                        self._rev_dict_cache.pop(path, None)
                        self._append_rev_to_file(path, None)
            else:
                # add entry
                self._rev_dict_cache[dbx_path] = rev
                self._append_rev_to_file(dbx_path, rev)
                # set all parent revs to 'folder'
                dirname = osp.dirname(dbx_path)
                while dirname != '/':
                    self._rev_dict_cache[dirname] = 'folder'
                    self._append_rev_to_file(dirname, 'folder')
                    dirname = osp.dirname(dirname)

    def _clean_and_save_rev_file(self) -> None:
        """Cleans the revision index from duplicate entries and keeps only the last entry
        for any individual path. Then saves the index to the drive."""
        with self._rev_lock:
            self._save_rev_dict_to_file()

    def clear_rev_index(self) -> None:
        """Clears the revision index."""
        with self._rev_lock:
            self._rev_dict_cache.clear()
            self._save_rev_dict_to_file()

    @contextmanager
    def _handle_rev_read_exceptions(self, raise_exception: bool = False) -> Iterator[None]:

        title = None
        new_err = None

        try:
            yield
        except (FileNotFoundError, IsADirectoryError):
            logger.info('Maestral index could not be found')
            # reset sync state
            self.last_sync = 0.0
            self._rev_dict_cache = dict()
            self.last_cursor = ''
        except PermissionError as err:
            title = 'Could not load index'
            msg = (f'Insufficient permissions for "{self.rev_file_path}". Please '
                   'make sure that you have read and write permissions.')
            new_err = RevFileError(title, msg).with_traceback(err.__traceback__)
        except OSError as err:
            title = 'Could not load index'
            msg = f'Errno {err.errno}. Please resync your Dropbox to rebuild the index.'
            new_err = RevFileError(title, msg).with_traceback(err.__traceback__)

        if new_err and raise_exception:
            raise new_err
        elif new_err:
            logger.error(title, exc_info=_exc_info(new_err))

    @contextmanager
    def _handle_rev_write_exceptions(self, raise_exception: bool = False) -> Iterator[None]:

        title = None
        new_err = None

        try:
            yield
        except PermissionError as err:
            title = 'Could not save index'
            msg = (f'Insufficient permissions for "{self.rev_file_path}". Please '
                   'make sure that you have read and write permissions.')
            new_err = RevFileError(title, msg).with_traceback(err.__traceback__)
        except OSError as err:
            title = 'Could not save index'
            msg = f'Errno {err.errno}. Please check the logs for more information.'
            new_err = RevFileError(title, msg).with_traceback(err.__traceback__)

        if new_err and raise_exception:
            raise new_err
        elif new_err:
            logger.error(title, exc_info=_exc_info(new_err))

    def _load_rev_dict_from_file(self, raise_exception: bool = False) -> None:
        """
        Loads Maestral's rev index from ``rev_file_path``. Every line contains the rev
        number for a single path, saved in a json format. Only the last entry for each
        path is kept, overriding possible (older) previous entries.

        :param raise_exception: If ``True``, raises an exception when loading fails.
            If ``False``, an error message is logged instead.
        :raises: :class:`errors.RevFileError`
        """
        with self._rev_lock:
            self._rev_dict_cache.clear()
            with self._handle_rev_read_exceptions(raise_exception):
                with open(self.rev_file_path) as f:
                    for line in f:
                        try:
                            entry = json.loads(line.strip('\n'))
                            self._rev_dict_cache.update(entry)
                        except json.decoder.JSONDecodeError as err:
                            if line.endswith('\n'):
                                raise err
                            else:
                                # last line of file, likely an interrupted write
                                pass

            # clean up empty revs
            for path, rev in self._rev_dict_cache.copy().items():
                if not rev:
                    del self._rev_dict_cache[path]

    def _save_rev_dict_to_file(self, raise_exception: bool = False) -> None:
        """
        Save Maestral's rev index to ``rev_file_path``.

        :param raise_exception: If ``True``, raises an exception when saving fails. If
            ``False``, an error message is logged instead.
        :raises: :class:`errors.RevFileError`
        """
        with self._rev_lock:
            with self._handle_rev_write_exceptions(raise_exception):
                with atomic_write(self.rev_file_path, mode='w', overwrite=True) as f:
                    for path, rev in self._rev_dict_cache.items():
                        f.write(json.dumps({path: rev}) + '\n')

    def _append_rev_to_file(self, path: str, rev: Optional[str],
                            raise_exception: bool = False) -> None:
        """
        Appends a new line with a rev entry to the rev file. This is quicker than saving
        the entire rev index. When loading the rev file, older entries will be overwritten
        with newer ones and all entries with ``rev == None`` will be discarded.

        :param path: Path for rev.
        :param rev: Dropbox rev or ``None``.
        :param raise_exception: If ``True``, raises an exception when saving fails.
            If ``False``, an error message is logged instead.
        :raises: :class:`errors.RevFileError`
        """

        with self._rev_lock:
            with self._handle_rev_write_exceptions(raise_exception):
                with open(self.rev_file_path, mode='a') as f:
                    f.write(json.dumps({path: rev}) + '\n')

    # ==== mignore management ============================================================

    @property
    def mignore_path(self) -> str:
        """Path to mignore file on local drive (read only)."""
        return self._mignore_path

    @property
    def mignore_rules(self) -> pathspec.PathSpec:
        """List of mignore rules following git wildmatch syntax (read only)."""
        if self._get_ctime(self.mignore_path) != self._mignore_ctime_loaded:
            self._mignore_rules = self._load_mignore_rules_form_file()
        return self._mignore_rules

    def _load_mignore_rules_form_file(self) -> pathspec.PathSpec:
        """Loads rules from mignore file. No rules are loaded if the file does
        not exist or cannot be read."""
        self._mignore_ctime_loaded = self._get_ctime(self.mignore_path)
        try:
            with open(self.mignore_path) as f:
                spec = f.read()
        except OSError as err:
            logger.debug(f'Could not load mignore rules from {self.mignore_path}: {err}')
            spec = ''
        return pathspec.PathSpec.from_lines('gitwildmatch', spec.splitlines())

    # ==== helper functions ==============================================================

    @property
    def is_case_sensitive(self) -> bool:
        return self._is_case_sensitive

    def ensure_dropbox_folder_present(self) -> None:
        """
        Checks if the Dropbox folder still exists where we expect it to be.

        :raises: :class:`errors.DropboxDeletedError`
        """

        if not osp.isdir(self.dropbox_path):
            title = 'Dropbox folder has been moved or deleted'
            msg = ('Please move the Dropbox folder back to its original location '
                   'or restart Maestral to set up a new folder.')
            raise NoDropboxDirError(title, msg)

    def _ensure_cache_dir_present(self) -> None:
        """Checks for or creates a directory at :attr:`file_cache_path`."""

        err_title = 'Cannot create cache directory (errno {})'
        err_msg = 'Please check if you have write permissions for "{}".'

        retries = 0
        max_retries = 3

        while not osp.isdir(self.file_cache_path):
            try:
                # this will still raise an exception if file_cache_path
                # exists but is a file instead of a directory
                os.makedirs(self.file_cache_path, exist_ok=True)
            except FileExistsError:
                err = delete(self._file_cache_path)
                if err and not isinstance(err, FileNotFoundError):
                    raise CacheDirError(err_title.format(err.errno),
                                        err_msg.format(err.filename))
            except OSError as err:
                raise CacheDirError(err_title.format(err.errno),
                                    err_msg.format(err.filename))

            if retries > max_retries:
                raise CacheDirError('Cannot create cache directory',
                                    'Exceeded maximum number of retries')

    def clean_cache_dir(self) -> None:
        """Removes all items in the cache directory."""

        with self.sync_lock:
            err = delete(self._file_cache_path)
            if err and not isinstance(err, FileNotFoundError):
                raise CacheDirError(f'Cannot create cache directory (errno {err.errno})',
                                    'Please check if you have write permissions for '
                                    f'{self._file_cache_path}.')

    def _new_tmp_file(self) -> str:
        """Returns a new temporary file name in our cache directory."""
        self._ensure_cache_dir_present()
        try:
            with tempfile.NamedTemporaryFile(dir=self.file_cache_path, delete=False) as f:
                return f.name
        except OSError as err:
            raise CacheDirError(f'Cannot create temporary file (errno {err.errno})',
                                'Please check if you have write permissions for '
                                f'{self._file_cache_path}.')

    def to_dbx_path(self, local_path: str) -> str:
        """
        Converts a local path to a path relative to the Dropbox folder. Casing of the
        given ``local_path`` will be preserved.

        :param local_path: Absolute path on local drive.
        :returns: Relative path with respect to Dropbox folder.
        :raises: :class:`ValueError` the path lies outside of the local Dropbox folder.
        """

        if is_equal_or_child(local_path, self.dropbox_path):
            dbx_path = osp.sep + local_path.replace(self.dropbox_path, '', 1).lstrip(osp.sep)
            return dbx_path.replace(osp.sep, '/')
        else:
            raise ValueError(f'Specified path "{local_path}" is outside of Dropbox '
                             f'directory "{self.dropbox_path}"')

    def to_local_path(self, dbx_path: str) -> str:
        """
        Converts a Dropbox path to the corresponding local path.

        The ``path_display`` attribute returned by the Dropbox API only guarantees correct
        casing of the basename and not of the full path. This is because Dropbox itself is
        not case sensitive and stores all paths in lowercase internally. To the extent
        where parent directories of ``dbx_path`` exist on the local drive, their casing
        will be used. Otherwise, the casing from ``dbx_path`` is used. This aims to
        preserve the correct casing of file and folder names and prevents the creation of
        duplicate folders with different casing on a local case-sensitive file system.

        :param dbx_path: Path relative to Dropbox folder.
        :returns: Corresponding local path on drive.
        """

        dbx_path = dbx_path.replace('/', osp.sep)
        dbx_path_parent, dbx_path_basename = osp.split(dbx_path)

        local_parent = to_cased_path(dbx_path_parent, root=self.dropbox_path,
                                     is_fs_case_sensitive=self.is_case_sensitive)

        return osp.join(local_parent, dbx_path_basename)

    def has_sync_errors(self) -> bool:
        """Returns ``True`` in case of sync errors, ``False`` otherwise."""
        return self.sync_errors.qsize() > 0

    def clear_sync_error(self, local_path: Optional[str] = None,
                         dbx_path: Optional[str] = None) -> None:
        """
        Clears all sync errors for ``local_path`` or ``dbx_path``.

        :param local_path: Absolute path on local drive.
        :param dbx_path: Path relative to Dropbox folder.
        """

        if not dbx_path:
            if local_path:
                dbx_path = self.to_dbx_path(local_path)
            else:
                return

        dbx_path = dbx_path.lower()

        if self.has_sync_errors():
            for error in list(self.sync_errors.queue):
                equal = error.dbx_path.lower() == dbx_path
                child = is_child(error.dbx_path.lower(), dbx_path)
                if equal or child:
                    remove_from_queue(self.sync_errors, error)

        self.upload_errors.discard(dbx_path)
        self.download_errors.discard(dbx_path)

    def clear_all_sync_errors(self) -> None:
        """Clears all sync errors."""
        with self.sync_errors.mutex:
            self.sync_errors.queue.clear()
        self.upload_errors.clear()
        self.download_errors.clear()

    @staticmethod
    def is_excluded(path) -> bool:
        """
        Checks if a file is excluded from sync. Certain file names are always excluded
        from syncing, following the Dropbox support article:

        https://help.dropbox.com/installs-integrations/sync-uploads/files-not-syncing

        This includes file system files such as 'desktop.ini' and '.DS_Store' and some
        temporary files as well as caches used by Dropbox or Maestral. `is_excluded`
        accepts both local and Dropbox paths.

        :param path: Path of item. Can be both a local or Dropbox paths.
        :returns: Whether the path is excluded from syncing.
        """
        path = path.lower().replace(osp.sep, '/')

        # is root folder?
        if path in ('/', ''):
            return True

        dirname, basename = osp.split(path)
        # in excluded files?
        if basename in EXCLUDED_FILE_NAMES:
            return True

        # in excluded dirs?
        dirnames = dirname.split('/')
        if any(name in dirnames for name in EXCLUDED_DIR_NAMES):
            return True

        # is temporary file?
        # 1) office temporary files
        if basename.startswith('~$'):
            return True
        if basename.startswith('.~'):
            return True
        # 2) other temporary files
        if basename.startswith('~') and basename.endswith('.tmp'):
            return True

        return False

    def is_excluded_by_user(self, dbx_path: str) -> bool:
        """
        Check if file has been excluded through "selective sync" by the user.

        :param dbx_path: Path relative to Dropbox folder.
        :returns: Whether the path is excluded from download syncing by the user.
        """
        dbx_path = dbx_path.lower()

        return any(is_equal_or_child(dbx_path, path) for path in self.excluded_items)

    def is_mignore(self, sync_event: SyncEvent) -> bool:
        """
        Check if local file change has been excluded by an mignore pattern.

        :param sync_event: SyncEvent for local file event.
        :returns: Whether the path is excluded from upload syncing by the user.
        """
        if len(self.mignore_rules.patterns) == 0:
            return False

        return (self._is_mignore_path(sync_event.dbx_path, is_dir=sync_event.is_directory)
                and not self.get_local_rev(sync_event.dbx_path))

    def _is_mignore_path(self, dbx_path: str, is_dir: bool = False) -> bool:

        relative_path = dbx_path.lstrip('/')

        if is_dir:
            relative_path += '/'

        return self.mignore_rules.match_file(relative_path)

    def _slow_down(self) -> None:

        if self._max_cpu_percent == 100:
            return

        if 'pool' in current_thread().name:
            cpu_usage = cpu_usage_percent()
            while cpu_usage > self._max_cpu_percent:
                cpu_usage = cpu_usage_percent(0.5 + 2 * random.random())

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

        # fill out missing dbx_path or local_path
        if err.dbx_path and not err.local_path:
            err.local_path = self.to_local_path(err.dbx_path)
        if err.local_path and not err.dbx_path:
            err.dbx_path = self.to_dbx_path(err.local_path)

        # fill out missing dbx_path_dst or local_path_dst
        if err.dbx_path_dst and not err.local_path_dst:
            err.local_path_dst = self.to_local_path(err.dbx_path_dst)
        if err.local_path_dst and not not err.dbx_path:
            err.dbx_path_dst = self.to_dbx_path(err.local_path_dst)

        if err.dbx_path:
            # we have a file / folder associated with the sync error
            file_name = osp.basename(err.dbx_path)
            logger.warning('Could not sync %s', file_name, exc_info=True)
            self.sync_errors.put(err)

            # save download errors to retry later
            if direction == SyncDirection.Down:
                self.download_errors.add(err.dbx_path.lower())
            elif direction == SyncDirection.Up:
                self.upload_errors.add(err.dbx_path.lower())

    def sync_event_from_dbx_metadata(self, md: Metadata) -> SyncEvent:
        """
        Initializes a SyncEvent from the given Dropbox metadata.

        :param md: Dropbox metadata.
        """
        if isinstance(md, DeletedMetadata):
            # there is currently on API call to determine who deleted a file or folder
            change_type = ChangeType.Deleted
            change_time = None
            size = 0
            rev = None
            content_hash = None

            try:
                old_md = self.client.list_revisions(md.path_lower, limit=1).entries[0]
                item_type = ItemType.File
                if not old_md.sharing_info:
                    # file is not in a shared folder, therefore
                    # the current user must have deleted it
                    change_dbid = self._conf.get('account', 'account_id')
                else:
                    # we cannot determine who deleted the item
                    change_dbid = None
            except IsAFolderError:
                item_type = ItemType.Folder
                change_dbid = None

        elif isinstance(md, FolderMetadata):
            # there is currently no API call to determine who added a folder
            change_type = ChangeType.Added
            item_type = ItemType.Folder
            size = 0
            rev = 'folder'
            content_hash = 'folder'
            change_time = None
            change_dbid = None

        elif isinstance(md, FileMetadata):
            item_type = ItemType.File
            rev = md.rev
            content_hash = md.content_hash
            size = md.size
            change_time = md.client_modified.replace(tzinfo=timezone.utc).timestamp()
            if self.get_local_rev(md.path_lower):
                change_type = ChangeType.Changed
            else:
                change_type = ChangeType.Added
            if md.sharing_info:
                change_dbid = md.sharing_info.modified_by
            else:
                # file is not a shared folder, therefore
                # the current user must have added or modified it
                change_dbid = cast(str, self._conf.get('account', 'account_id'))
        else:
            raise RuntimeError(f'Cannot convert {md} to SyncEvent')

        if change_dbid:
            try:
                account_info = self.client.get_account_info(change_dbid)
                change_user_name = account_info.name.display_name
            except InvalidDbidError:
                change_user_name = None
        else:
            change_user_name = None

        return SyncEvent(
            direction=SyncDirection.Down,
            item_type=item_type,
            sync_time=time.time(),
            dbx_path=md.path_display,
            local_path=self.to_local_path(md.path_display),
            dbx_path_from=None,
            local_path_from=None,
            rev=rev,
            content_hash=content_hash,
            change_type=change_type,
            change_time=change_time,
            change_dbid=change_dbid,
            change_user_name=change_user_name,
            status=SyncStatus.Queued,
            size=size,
            orig=md,
        )

    def sync_event_from_file_system_event(self, event: FileSystemEvent) -> SyncEvent:
        """
        Initializes a SyncEvent from the given local file system event.

        :param event: Local file system event.
        """

        change_dbid = self._conf.get('account', 'account_id')
        change_user_name = self._conf.get('account', 'display_name')
        to_path = get_dest_path(event)
        from_path = None

        if event.event_type == EVENT_TYPE_CREATED:
            change_type = ChangeType.Added
        elif event.event_type == EVENT_TYPE_DELETED:
            change_type = ChangeType.Deleted
        elif event.event_type == EVENT_TYPE_MOVED:
            change_type = ChangeType.Moved
            from_path = event.src_path
        elif event.event_type == EVENT_TYPE_MODIFIED:
            change_type = ChangeType.Changed
        else:
            raise RuntimeError(f'Cannot convert {event} to SyncEvent')

        change_time: Optional[float]
        stat: Optional[os.stat_result]

        try:
            stat = os.stat(to_path)
        except OSError:
            stat = None

        if event.is_directory:
            item_type = ItemType.Folder
            size = 0
            change_time = None
        else:
            item_type = ItemType.File
            change_time = stat.st_ctime if stat else None
            size = stat.st_size if stat else 0

        return SyncEvent(
            direction=SyncDirection.Up,
            item_type=item_type,
            sync_time=time.time(),
            dbx_path=self.to_dbx_path(to_path),
            local_path=to_path,
            dbx_path_from=self.to_dbx_path(from_path) if from_path else None,
            local_path_from=from_path,
            rev=None,
            content_hash=get_local_hash(to_path),
            change_type=change_type,
            change_time=change_time,
            change_dbid=change_dbid,
            change_user_name=change_user_name,
            status=SyncStatus.Queued,
            size=size,
            orig=event,
        )

    # ==== Upload sync ===================================================================

    def upload_local_changes_while_inactive(self) -> None:
        """
        Collects changes while sync has not been running and uploads them to Dropbox.
        Call this method when resuming sync.
        """

        with self.sync_lock:

            logger.info('Indexing local changes...')

            try:
                events, local_cursor = self._get_local_changes_while_inactive()
                logger.debug('Retrieved local changes:\n%s', pprint.pformat(events))
                events = self._clean_local_events(events)
                sync_events = [self.sync_event_from_file_system_event(e) for e in events]
            except FileNotFoundError:
                self.ensure_dropbox_folder_present()
                return

            if len(events) > 0:
                self.apply_local_changes(sync_events, local_cursor)
                logger.debug('Uploaded local changes while inactive')
            else:
                self.last_sync = local_cursor
                logger.debug('No local changes while inactive')

    def _get_local_changes_while_inactive(self) -> Tuple[List[FileSystemEvent], float]:

        changes = []
        now = time.time()
        snapshot = DirectorySnapshot(self.dropbox_path)

        # get lowercase paths
        lowercase_snapshot_paths = {x.lower() for x in snapshot.paths}

        # get modified or added items
        for path in snapshot.paths:
            if path != self.dropbox_path:
                stats = snapshot.stat_info(path)
                # check if item was created or modified since last sync
                # but before we started the FileEventHandler (~now)
                dbx_path = self.to_dbx_path(path).lower()
                ctime_check = now > stats.st_ctime > self.get_last_sync_for_path(dbx_path)

                # always upload untracked items, check ctime of tracked items
                rev = self.get_local_rev(dbx_path)
                is_new = not rev
                is_modified = rev and ctime_check

                if is_new:
                    if snapshot.isdir(path):
                        event = DirCreatedEvent(path)
                    else:
                        event = FileCreatedEvent(path)
                    changes.append(event)
                elif is_modified:
                    if snapshot.isdir(path) and rev == 'folder':
                        event = DirModifiedEvent(path)
                        changes.append(event)
                    elif not snapshot.isdir(path) and rev != 'folder':
                        event = FileModifiedEvent(path)
                        changes.append(event)
                    elif snapshot.isdir(path):
                        event0 = FileDeletedEvent(path)
                        event1 = DirCreatedEvent(path)
                        changes += [event0, event1]
                    elif not snapshot.isdir(path):
                        event0 = DirDeletedEvent(path)
                        event1 = FileCreatedEvent(path)
                        changes += [event0, event1]

        # get deleted items
        rev_dict_copy = self.get_rev_index()
        for path in rev_dict_copy:
            local_path_uncased = (self.dropbox_path + path).lower()
            if local_path_uncased not in lowercase_snapshot_paths:
                local_path = self.to_local_path(path)
                if rev_dict_copy[path] == 'folder':
                    event = DirDeletedEvent(local_path)
                else:
                    event = FileDeletedEvent(local_path)
                changes.append(event)

        del snapshot
        del lowercase_snapshot_paths

        return changes, now

    def wait_for_local_changes(self, timeout: float = 5,
                               delay: float = 1) -> Tuple[List[SyncEvent], float]:
        """
        Waits for local file changes. Returns a list of local changes with at most one
        entry per path.

        :param timeout: If no changes are detected within timeout (sec), an empty list is
            returned.
        :param delay: Delay in sec to wait for subsequent changes that may be duplicates.
        :returns: (list of sync times events, time_stamp)
        """

        self.ensure_dropbox_folder_present()

        try:
            events = [self.fs_events.local_file_event_queue.get(timeout=timeout)]
            local_cursor = time.time()
        except Empty:
            return [], time.time()

        # keep collecting events until idle for `delay`
        while True:
            try:
                event = self.fs_events.local_file_event_queue.get(timeout=delay)
                events.append(event)
                local_cursor = time.time()
            except Empty:
                break

        logger.debug('Retrieved local file events:\n%s', pprint.pformat(events))

        events = self._clean_local_events(events)
        sync_events = [self.sync_event_from_file_system_event(e) for e in events]

        return sync_events, local_cursor

    def apply_local_changes(self, sync_events: List[SyncEvent],
                            local_cursor: float) -> List[SyncEvent]:
        """
        Applies locally detected changes to the remote Dropbox.

        :param sync_events: List of local file system events.
        :param local_cursor: Time stamp of last event in ``events``.
        """

        with self.sync_lock:

            results = []

            if len(sync_events) > 0:

                sync_events, _ = self._filter_excluded_changes_local(sync_events)

                deleted: List[SyncEvent] = []
                dir_moved: List[SyncEvent] = []
                other: List[SyncEvent] = []  # file created + moved, dir created

                for sync_event in sync_events:
                    if sync_event.is_deleted:
                        deleted.append(sync_event)
                    elif sync_event.is_directory and sync_event.is_moved:
                        dir_moved.append(sync_event)
                    else:
                        other.append(sync_event)

                    # housekeeping
                    self.queued_for_upload.put(sync_event)

                # apply deleted events first, folder moved events second
                # neither event type requires an actual upload
                if deleted:
                    logger.info('Uploading deletions...')

                with ThreadPoolExecutor(max_workers=self._num_threads,
                                        thread_name_prefix='maestral-upload-pool') as executor:
                    fs = (executor.submit(self._create_remote_entry, e)
                          for e in deleted)

                    n_items = len(deleted)
                    for f, n in zip(as_completed(fs), range(1, n_items + 1)):
                        throttled_log(logger, f'Deleting {n}/{n_items}...')
                        results.append(f.result())

                if dir_moved:
                    logger.info('Moving folders...')

                for sync_event in dir_moved:
                    logger.info(f'Moving {sync_event.local_path_from}...')
                    res = self._create_remote_entry(sync_event)
                    results.append(res)

                # apply file created events in parallel since order does not matter
                with ThreadPoolExecutor(max_workers=self._num_threads,
                                        thread_name_prefix='maestral-upload-pool') as executor:
                    fs = (executor.submit(self._create_remote_entry, e) for e in other)

                    n_items = len(other)
                    for f, n in zip(as_completed(fs), range(1, n_items + 1)):
                        throttled_log(logger, f'Uploading {n}/{n_items}...')
                        results.append(f.result())

                self._clean_and_save_rev_file()
                self._save_to_history(sync_events)

            if not self.cancel_pending.is_set():
                # always save local cursor if not aborted by user,
                # failed uploads will be tracked and retried individually
                self.last_sync = local_cursor

            return results

    def _filter_excluded_changes_local(self, events: List[SyncEvent]) \
            -> Tuple[List[SyncEvent], List[SyncEvent]]:
        """
        Checks for and removes file events referring to items which are excluded from
        syncing.

        :param events: List of file events.
        :returns: (``events_filtered``, ``events_excluded``)
        """

        events_filtered = []
        events_excluded = []

        for si in events:

            if self.is_excluded(si.local_path):
                events_excluded.append(si)
            elif self.is_mignore(si):
                # moved events with an ignored path are
                # already split into deleted, created pairs
                events_excluded.append(si)
            else:
                events_filtered.append(si)

        logger.debug('Filtered local file events:\n%s', pprint.pformat(events_filtered))

        return events_filtered, events_excluded

    def _clean_local_events(self, events: List[FileSystemEvent]) -> List[FileSystemEvent]:
        """
        Takes local file events within and cleans them up so that there is only a single
        event per path. Collapses moved and deleted events of folders with those of their
        children.

        :param events: Iterable of :class:`watchdog.FileSystemEvent`.
        :returns: List of :class:`watchdog.FileSystemEvent`.
        """

        # COMBINE EVENTS TO ONE EVENT PER PATH

        # Move events are difficult to combine with other event types, we split them into
        # deleted and created events and recombine them later if none of the paths has
        # other events associated with it or is excluded from sync.

        histories: Dict[str, List[FileSystemEvent]] = dict()
        for i, event in enumerate(events):
            if isinstance(event, (FileMovedEvent, DirMovedEvent)):
                deleted, created = split_moved_event(event)
                deleted.id = i
                created.id = i
                try:
                    histories[deleted.src_path].append(deleted)
                except KeyError:
                    histories[deleted.src_path] = [deleted]
                try:
                    histories[created.src_path].append(created)
                except KeyError:
                    histories[created.src_path] = [created]
            else:
                try:
                    histories[event.src_path].append(event)
                except KeyError:
                    histories[event.src_path] = [event]

        unique_events = []

        for h in histories.values():
            if len(h) == 1:
                unique_events.append(h[0])
            else:
                path = h[0].src_path

                n_created = len([e for e in h if e.event_type == EVENT_TYPE_CREATED])
                n_deleted = len([e for e in h if e.event_type == EVENT_TYPE_DELETED])

                if n_created > n_deleted:  # item was created
                    if h[-1].is_directory:
                        unique_events.append(DirCreatedEvent(path))
                    else:
                        unique_events.append(FileCreatedEvent(path))
                if n_created < n_deleted:  # item was deleted
                    if h[0].is_directory:
                        unique_events.append(DirDeletedEvent(path))
                    else:
                        unique_events.append(FileDeletedEvent(path))
                else:

                    first_created_idx = next(iter(i for i, e in enumerate(h) if e.event_type == EVENT_TYPE_CREATED), -1)
                    first_deleted_idx = next(iter(i for i, e in enumerate(h) if e.event_type == EVENT_TYPE_DELETED), -1)

                    if n_created == 0 or first_deleted_idx < first_created_idx:
                        # item was modified
                        if h[0].is_directory and h[-1].is_directory:
                            unique_events.append(DirModifiedEvent(path))
                        elif not h[0].is_directory and not h[-1].is_directory:
                            unique_events.append(FileModifiedEvent(path))
                        elif h[0].is_directory:
                            unique_events.append(DirDeletedEvent(path))
                            unique_events.append(FileCreatedEvent(path))
                        elif h[-1].is_directory:
                            unique_events.append(FileDeletedEvent(path))
                            unique_events.append(DirCreatedEvent(path))
                    else:
                        # item was only temporary
                        pass

        # event order does not matter anymore from this point because we have already
        # consolidated events for every path

        cleaned_events = set(unique_events)

        # recombine moved events
        moved_events: Dict[str, List[FileSystemEvent]] = dict()
        for event in unique_events:
            if hasattr(event, 'id'):
                try:
                    moved_events[event.id].append(event)
                except KeyError:
                    moved_events[event.id] = [event]

        for event_list in moved_events.values():
            if len(event_list) == 2:
                src_path = next(e.src_path for e in event_list
                                if e.event_type == EVENT_TYPE_DELETED)
                dest_path = next(e.src_path for e in event_list
                                 if e.event_type == EVENT_TYPE_CREATED)
                if event_list[0].is_directory:
                    new_event = DirMovedEvent(src_path, dest_path)
                else:
                    new_event = FileMovedEvent(src_path, dest_path)

                if not self._should_split_excluded(new_event):
                    cleaned_events.difference_update(event_list)
                    cleaned_events.add(new_event)

        # COMBINE MOVED AND DELETED EVENTS OF FOLDERS AND THEIR CHILDREN INTO ONE EVENT

        # Avoid nested iterations over all events here, they are on the order of O(n^2)
        # which becomes costly then the user moves or deletes folder with a large number
        # of children. Benchmark: aim to stay below 1 sec for 20,000 nested events on
        # representative laptops.

        # 1) combine moved events of folders and their children into one event
        dir_moved_paths = set((e.src_path, e.dest_path) for e in cleaned_events
                              if isinstance(e, DirMovedEvent))

        if len(dir_moved_paths) > 0:
            child_moved_events: Dict[Tuple[str, str], List[FileSystemEvent]] = dict()
            for path in dir_moved_paths:
                child_moved_events[path] = []

            for event in cleaned_events:
                if event.event_type == EVENT_TYPE_MOVED:
                    try:
                        dirnames = (osp.dirname(event.src_path),
                                    osp.dirname(event.dest_path))
                        child_moved_events[dirnames].append(event)
                    except KeyError:
                        pass

            for event_list in child_moved_events.values():
                cleaned_events.difference_update(event_list)

        # 2) combine deleted events of folders and their children to one event
        dir_deleted_paths = set(e.src_path for e in cleaned_events
                                if isinstance(e, DirDeletedEvent))

        if len(dir_deleted_paths) > 0:
            child_deleted_events: Dict[str, List[FileSystemEvent]] = dict()
            for path in dir_deleted_paths:
                child_deleted_events[path] = []

            for event in cleaned_events:
                if event.event_type == EVENT_TYPE_DELETED:
                    try:
                        dirname = osp.dirname(event.src_path)
                        child_deleted_events[dirname].append(event)
                    except KeyError:
                        pass

            for event_list in child_deleted_events.values():
                cleaned_events.difference_update(event_list)

        logger.debug('Cleaned up local file events:\n%s', pprint.pformat(cleaned_events))

        del events
        del unique_events

        return list(cleaned_events)

    def _should_split_excluded(self, event: Union[FileMovedEvent, DirMovedEvent]):

        if event.event_type != EVENT_TYPE_MOVED:
            raise ValueError('Can only split moved events')

        dbx_src_path = self.to_dbx_path(event.src_path)
        dbx_dest_path = self.to_dbx_path(event.dest_path)

        if (self.is_excluded(event.src_path)
                or self.is_excluded(event.dest_path)
                or self.is_excluded_by_user(dbx_src_path)
                or self.is_excluded_by_user(dbx_dest_path)):
            return True
        else:
            return self._should_split_mignore(event)

    def _should_split_mignore(self, event: Union[FileMovedEvent, DirMovedEvent]):

        if len(self.mignore_rules.patterns) == 0:
            return False

        dbx_src_path = self.to_dbx_path(event.src_path)
        dbx_dest_path = self.to_dbx_path(event.dest_path)

        return (self._is_mignore_path(dbx_src_path, event.is_directory)
                or self._is_mignore_path(dbx_dest_path, event.is_directory))

    def _handle_case_conflict(self, si: SyncEvent) -> bool:
        """
        Checks for other items in the same directory with same name but a different
        case. Renames items if necessary. Only needed for case sensitive file systems.

        :param si: Sync item for local created or moved event.
        :returns: Whether a case conflict was detected and handled.
        """

        if not self.is_case_sensitive:
            return False

        if not (si.is_added or si.is_moved):
            return False

        dirname, basename = osp.split(si.local_path)

        # check number of paths with the same case
        if len(cased_path_candidates(basename, root=dirname)) > 1:

            local_path_cc = generate_cc_name(si.local_path, suffix='case conflict',
                                             is_fs_case_sensitive=self.is_case_sensitive)

            event_cls = DirMovedEvent if osp.isdir(si.local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(si.local_path, local_path_cc)):
                exc = move(si.local_path, local_path_cc)
                if exc:
                    raise os_to_maestral_error(exc, local_path=local_path_cc)

                self.rescan(local_path_cc)

            logger.info('Case conflict: renamed "%s" to "%s"', si.local_path, local_path_cc)

            return True
        else:
            return False

    def _handle_selective_sync_conflict(self, si: SyncEvent) -> bool:
        """
        Checks for items in the local directory with same path as an item which is
        excluded by selective sync. Renames items if necessary.

        :param si: Sync item for local created or moved event.
        :returns: Whether a selective sync conflict was detected and handled.
        """

        if not (si.is_added or si.is_moved):
            return False

        if self.is_excluded_by_user(si.dbx_path):
            local_path_cc = generate_cc_name(si.local_path,
                                             suffix='selective sync conflict',
                                             is_fs_case_sensitive=self.is_case_sensitive)

            event_cls = DirMovedEvent if osp.isdir(si.local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(si.local_path, local_path_cc)):
                exc = move(si.local_path, local_path_cc)
                if exc:
                    raise os_to_maestral_error(exc, local_path=local_path_cc)

                self.rescan(local_path_cc)

            logger.info('Selective sync conflict: renamed "%s" to "%s"',
                        si.local_path, local_path_cc)
            return True
        else:
            return False

    def _create_remote_entry(self, sync_event: SyncEvent) -> SyncEvent:
        """
        Applies a local file system event to the remote Dropbox and clears any existing
        sync errors belonging to that path. Any :class:`errors.SyncError` will be caught
        and logged as appropriate.

        :param sync_event: SyncEvent for local file event.
        :returns: SyncEvent with updated status.
        """

        # housekeeping
        remove_from_queue(self.queued_for_upload, sync_event)
        self.uploading.put(sync_event)

        if self.cancel_pending.is_set():
            sync_event.status = SyncStatus.Aborted
            remove_from_queue(self.uploading, sync_event)
            return sync_event

        self._slow_down()

        self.clear_sync_error(local_path=sync_event.local_path)
        self.clear_sync_error(local_path=sync_event.local_path_from)
        sync_event.status = SyncStatus.Syncing

        try:

            if sync_event.is_added:
                res = self._on_local_created(sync_event)
            elif sync_event.is_moved:
                res = self._on_local_moved(sync_event)
            elif sync_event.is_changed:
                res = self._on_local_modified(sync_event)
            elif sync_event.is_deleted:
                res = self._on_local_deleted(sync_event)
            else:
                res = None

            if res:
                sync_event.status = SyncStatus.Done
            else:
                sync_event.status = SyncStatus.Skipped

        except SyncError as err:
            self._handle_sync_error(err, direction=SyncDirection.Up)
            sync_event.status = SyncStatus.Failed
        finally:
            remove_from_queue(self.uploading, sync_event)

        return sync_event

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

    def _on_local_moved(self, sync_event: SyncEvent) -> Optional[SyncEvent]:
        """
        Call when a local item is moved.

        Keep in mind that we may be moving a whole tree of items. But its better deal with
        the complexity than to delete and re-uploading everything. Thankfully, in case of
        directories, we always process the top-level first. Trying to move the children
        will then be delegated to `on_create` (because the old item no longer lives on
        Dropbox) and that won't upload anything because file contents have remained the
        same.

        :param sync_event: SyncEvent for local moved event.
        :raises: :class:`errors.MaestralApiError`
        :returns: SyncEvent for created remote item at destination.
        """

        if self._handle_selective_sync_conflict(sync_event):
            return None
        if self._handle_case_conflict(sync_event):
            return None

        md_from_old = self.client.get_metadata(sync_event.dbx_path_from)

        # If not on Dropbox, e.g., because its old name was invalid,
        # create it instead of moving it.
        if not md_from_old:
            if sync_event.is_directory:
                new_event = DirCreatedEvent(sync_event.local_path)
            else:
                new_event = FileCreatedEvent(sync_event.local_path)

            new_sync_event = self.sync_event_from_file_system_event(new_event)

            return self._on_local_created(new_sync_event)

        md_to_new = self.client.move(sync_event.dbx_path_from, sync_event.dbx_path, autorename=True)

        self.set_local_rev(sync_event.dbx_path_from, None)  # type: ignore

        # handle remote conflicts
        if md_to_new.path_lower != sync_event.dbx_path.lower():
            logger.info('Upload conflict: renamed "%s" to "%s"',
                        sync_event.dbx_path, md_to_new.path_display)
        else:
            self._set_local_rev_recursive(md_to_new)
            logger.debug('Moved "%s" to "%s" on Dropbox', sync_event.dbx_path_from,
                         sync_event.dbx_path)

        return self.sync_event_from_dbx_metadata(md_to_new)

    def _set_local_rev_recursive(self, md):

        if isinstance(md, FileMetadata):
            self.set_local_rev(md.path_lower, md.rev)
        elif isinstance(md, FolderMetadata):
            self.set_local_rev(md.path_lower, 'folder')
            result = self.client.list_folder(md.path_lower, recursive=True)
            for md in result.entries:
                if isinstance(md, FileMetadata):
                    self.set_local_rev(md.path_lower, md.rev)
                elif isinstance(md, FolderMetadata):
                    self.set_local_rev(md.path_lower, 'folder')

    def _on_local_created(self, sync_event: SyncEvent) -> Optional[SyncEvent]:
        """
        Call when a local item is created.

        :param sync_event: SyncEvent corresponding to local created event.
        :raises: :class:`errors.MaestralApiError`
        :returns: SyncEvent for created remote item or None if no remote item is created.
        """

        if self._handle_selective_sync_conflict(sync_event):
            return None
        if self._handle_case_conflict(sync_event):
            return None

        self._wait_for_creation(sync_event.local_path)

        if sync_event.is_directory:
            try:
                md_new = self.client.make_dir(sync_event.dbx_path, autorename=False)
            except FolderConflictError:
                logger.debug('No conflict for "%s": the folder already exists',
                             sync_event.local_path)
                self.set_local_rev(sync_event.dbx_path, 'folder')
                return None
            except FileConflictError:
                md_new = self.client.make_dir(sync_event.dbx_path, autorename=True)

        else:
            # check if file already exists with identical content
            md_old = self.client.get_metadata(sync_event.dbx_path)
            if isinstance(md_old, FileMetadata):
                if sync_event.content_hash == md_old.content_hash:
                    # file hashes are identical, do not upload
                    self.set_local_rev(md_old.path_lower, md_old.rev)
                    return None

            rev = self.get_local_rev(sync_event.dbx_path)
            if not rev:
                # file is new to us, let Dropbox rename it if something is in the way
                mode = dropbox.files.WriteMode.add
            elif rev == 'folder':
                # try to overwrite the destination, this will fail...
                mode = dropbox.files.WriteMode.overwrite
            else:
                # file has been modified, update remote if matching rev,
                # create conflict otherwise
                logger.debug('"%s" appears to have been created but we are '
                             'already tracking it', sync_event.dbx_path)
                mode = dropbox.files.WriteMode.update(rev)
            try:
                md_new = self.client.upload(
                    sync_event.local_path, sync_event.dbx_path,
                    autorename=True, mode=mode,
                    sync_event=sync_event
                )
            except NotFoundError:
                logger.debug('Could not upload "%s": the item does not exist',
                             sync_event.local_path)
                return None

        if md_new.path_lower != sync_event.dbx_path.lower():
            # conflicting copy created during upload, mirror remote changes locally
            local_path_cc = self.to_local_path(md_new.path_display)
            event_cls = DirMovedEvent if osp.isdir(sync_event.local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(sync_event.local_path, local_path_cc)):
                exc = move(sync_event.local_path, local_path_cc)
                if exc:
                    raise os_to_maestral_error(exc, local_path=local_path_cc,
                                               dbx_path=md_new.path_display)

            # Delete revs of old path but don't set revs for new path here. This will
            # force conflict resolution on download in case of intermittent changes.
            self.set_local_rev(sync_event.dbx_path, None)
            logger.debug('Upload conflict: renamed "%s" to "%s"',
                         sync_event.dbx_path, md_new.path_lower)
        else:
            # everything went well, save new revs
            rev = getattr(md_new, 'rev', 'folder')
            self.set_local_rev(md_new.path_lower, rev)
            logger.debug('Created "%s" on Dropbox', sync_event.dbx_path)

        return self.sync_event_from_dbx_metadata(md_new)

    def _on_local_modified(self, sync_event: SyncEvent) -> Optional[SyncEvent]:
        """
        Call when local item is modified.

        :param sync_event: SyncEvent for local modified event.
        :raises: :class:`errors.MaestralApiError`
        :returns: SyncEvent corresponding to modified remote item or None if no remote item
            is modified.
        """

        if sync_event.is_directory:  # ignore directory modified events
            return None

        self._wait_for_creation(sync_event.local_path)

        # check if item already exists with identical content
        md_old = self.client.get_metadata(sync_event.dbx_path)
        if isinstance(md_old, FileMetadata):
            if sync_event.content_hash == md_old.content_hash:
                # file hashes are identical, do not upload
                self.set_local_rev(md_old.path_lower, md_old.rev)
                logger.debug('Modification of "%s" detected but file content is '
                             'the same as on Dropbox', sync_event.dbx_path)
                return None

        rev = self.get_local_rev(sync_event.dbx_path)
        if rev == 'folder':
            mode = dropbox.files.WriteMode.overwrite
        elif not rev:
            logger.debug('"%s" appears to have been modified but cannot '
                         'find old revision', sync_event.dbx_path)
            mode = dropbox.files.WriteMode.add
        else:
            mode = dropbox.files.WriteMode.update(rev)

        try:
            md_new = self.client.upload(
                sync_event.local_path, sync_event.dbx_path,
                autorename=True, mode=mode,
                sync_event=sync_event
            )
        except NotFoundError:
            logger.debug('Could not upload "%s": the item does not exist', sync_event.dbx_path)
            return None

        if md_new.path_lower != sync_event.dbx_path.lower():
            # conflicting copy created during upload, mirror remote changes locally
            local_path_cc = self.to_local_path(md_new.path_display)
            with self.fs_events.ignore(FileMovedEvent(sync_event.local_path, local_path_cc)):
                try:
                    os.rename(sync_event.local_path, local_path_cc)
                except OSError:
                    with self.fs_events.ignore(FileDeletedEvent(sync_event.local_path)):
                        delete(sync_event.local_path)

            # Delete revs of old path but don't set revs for new path here. This will
            # force conflict resolution on download in case of intermittent changes.
            self.set_local_rev(sync_event.dbx_path, None)
            logger.debug('Upload conflict: renamed "%s" to "%s"',
                         sync_event.dbx_path, md_new.path_lower)
        else:
            # everything went well, save new revs
            self.set_local_rev(md_new.path_lower, md_new.rev)
            logger.debug('Uploaded modified "%s" to Dropbox', md_new.path_lower)

        return self.sync_event_from_dbx_metadata(md_new)

    def _on_local_deleted(self, sync_event: SyncEvent) -> Optional[SyncEvent]:
        """
        Call when local item is deleted. We try not to delete remote items which have been
        modified since the last sync.

        :param sync_event: SyncEvent for local deletion.
        :raises: :class:`errors.MaestralApiError`
        :returns: SyncEvent for deleted remote item or None if no remote item is deleted.
        """

        if self.is_excluded_by_user(sync_event.dbx_path):
            logger.debug('Not deleting "%s": is excluded by selective sync', sync_event.dbx_path)
            return None

        local_rev = self.get_local_rev(sync_event.dbx_path)

        md = self.client.get_metadata(sync_event.dbx_path, include_deleted=True)

        if sync_event.is_directory and isinstance(md, FileMetadata):
            logger.debug('Expected folder at "%s" but found a file instead, checking '
                         'which one is newer', md.path_display)
            # don't delete a remote file if it was modified since last sync
            if md.server_modified.timestamp() >= self.get_last_sync_for_path(sync_event.dbx_path):
                logger.debug('Skipping deletion: remote item "%s" has been modified '
                             'since last sync', md.path_display)
                # mark local folder as untracked
                self.set_local_rev(sync_event.dbx_path, None)
                return None

        if sync_event.is_file and isinstance(md, FolderMetadata):
            # don't delete a remote folder if we were expecting a file
            # TODO: Delete the folder if its children did not change since last sync.
            #   Is there a way of achieving this without listing the folder or listing
            #   all changes and checking when they occurred?
            logger.debug('Skipping deletion: expected file at "%s" but found a '
                         'folder instead', md.path_display)
            # mark local file as untracked
            self.set_local_rev(sync_event.dbx_path, None)
            return None

        try:
            # will only perform delete if Dropbox remote rev matches `local_rev`
            md_deleted = self.client.remove(
                sync_event.dbx_path,
                parent_rev=local_rev if sync_event.is_file else None
            )
        except NotFoundError:
            logger.debug('Could not delete "%s": the item no longer exists on Dropbox',
                         sync_event.dbx_path)
            md_deleted = None
        except PathError:
            logger.debug('Could not delete "%s": the item has been changed '
                         'since last sync', sync_event.dbx_path)
            md_deleted = None

        # remove revision metadata
        self.set_local_rev(sync_event.dbx_path, None)

        if md_deleted:
            return self.sync_event_from_dbx_metadata(md_deleted)
        else:
            return None

    # ==== Download sync =================================================================

    def get_remote_folder(self, dbx_path: str = '/', ignore_excluded: bool = True) \
            -> List[SyncEvent]:
        """
        Gets all files/folders from Dropbox and writes them to the local folder
        :attr:`dropbox_path`. Call this method on first run of the Maestral. Indexing and
        downloading may take several minutes, depending on the size of the user's Dropbox
        folder.

        :param dbx_path: Path relative to Dropbox folder. Defaults to root ('/').
        :param ignore_excluded: If ``True``, do not index excluded folders.
        :returns: Whether download was successful.
        """

        with self.sync_lock:

            dbx_path = dbx_path or '/'
            is_dbx_root = dbx_path == '/'
            results = []

            if is_dbx_root:
                logger.info('Downloading your Dropbox')
            else:
                logger.info('Downloading %s', dbx_path)

            if not any(is_child(folder, dbx_path) for folder in self.excluded_items):
                # if there are no excluded subfolders, index and download all at once
                ignore_excluded = False

            # get a cursor and list the folder content
            try:
                cursor = self.client.get_latest_cursor(dbx_path)
                root_result = self.client.list_folder(
                    dbx_path,
                    recursive=(not ignore_excluded),
                    include_deleted=False
                )
            except SyncError as e:
                self._handle_sync_error(e, direction=SyncDirection.Down)
                # TODO: return failed SyncEvent?
                return []

            # download top-level folders / files first
            sync_events = [self.sync_event_from_dbx_metadata(md) for md in root_result.entries]
            res = self.apply_remote_changes(sync_events, cursor=None)
            results.extend(res)

            if ignore_excluded:
                # download sub-folders if not excluded
                for md in root_result.entries:
                    if isinstance(md, FolderMetadata) and not self.is_excluded_by_user(
                            md.path_display):
                        results.extend(self.get_remote_folder(md.path_display))

            if is_dbx_root:
                # always save remote cursor if this is the root folder,
                # failed downloads will be tracked and retried individually
                self.last_cursor = cursor
                self.last_reindex = time.time()

            return results

    def get_remote_item(self, dbx_path: str) -> List[SyncEvent]:
        """
        Downloads a remote file or folder and updates its local rev. If the remote item no
        longer exists, the corresponding local item will be deleted. Given paths will be
        added to the (persistent) pending_downloads list for the duration of the download
        so that they will be resumed in case Maestral is terminated during the download.
        If ``dbx_path`` refers to a folder, the download will be handled by
        :meth:`get_remote_folder`. If it refers to a single file, the download will be
        performed by :meth:`_create_local_entry`.

        This method can be used to fetch individual items outside of the regular sync
        cycle, for instance when including a previously excluded file or folder.

        :param dbx_path: Path relative to Dropbox folder.
        :returns: Whether download was successful.
        """

        with self.sync_lock:

            self.pending_downloads.add(dbx_path.lower())
            md = self.client.get_metadata(dbx_path, include_deleted=True)
            sync_event = self.sync_event_from_dbx_metadata(md)

            results = []

            if sync_event.is_directory:
                results = self.get_remote_folder(dbx_path)
            else:
                results = [self._create_local_entry(sync_event)]

            success = all(si.status in (SyncStatus.Done, SyncStatus.Skipped) for si in results)

            if success:
                self.pending_downloads.discard(dbx_path.lower())

            return results

    def wait_for_remote_changes(self, last_cursor: str, timeout: int = 40,
                                delay: float = 2) -> bool:
        """
        Blocks until changes to the remote Dropbox are available.

        :param last_cursor: Cursor form last sync.
        :param timeout: Timeout in seconds before returning even if there are no changes.
            Dropbox adds random jitter of up to 90 sec to this value.
        :param delay: Delay in sec to wait for subsequent changes that may be duplicates.
            This delay is typically only necessary folders are shared / un-shared with
            other Dropbox accounts.
        """
        logger.debug('Waiting for remote changes since cursor:\n%s', last_cursor)
        has_changes = self.client.wait_for_remote_changes(last_cursor, timeout=timeout)
        time.sleep(delay)
        logger.debug('Detected remote changes: %s', has_changes)
        return has_changes

    def list_remote_changes(self, last_cursor: str) -> Tuple[List[SyncEvent], str]:
        """
        Lists remote changes since the last download sync.

        :param last_cursor: Cursor from last download sync.
        :returns: Tuple with remote changes and corresponding cursor
        """
        changes = self.client.list_remote_changes(last_cursor)
        logger.debug('Listed remote changes:\n%s', entries_to_str(changes.entries))
        clean_changes = self._clean_remote_changes(changes)
        logger.debug('Cleaned remote changes:\n%s', entries_to_str(clean_changes.entries))

        sync_events = [self.sync_event_from_dbx_metadata(md) for md in changes.entries]
        return sync_events, changes.cursor

    def apply_remote_changes(self, sync_events: List[SyncEvent],
                             cursor: Optional[str]) -> List[SyncEvent]:
        """
        Applies remote changes to local folder. Call this on the result of
        :meth:`list_remote_changes`. The saved cursor is updated after a set of changes
        has been successfully applied. Entries in the local index are created after
        successful completion.

        :param sync_events: List of remote changes.
        :param cursor: Remote cursor corresponding to changes. Take care to only pass
            cursors which represent the state of the entire Dropbox. Pass None instead
            if you are only downloading a subset of changes.
        :returns: List of changes that were made to local files and bool indicating if all
            download syncs were successful.
        """

        # filter out excluded changes
        changes_included, changes_excluded = self._filter_excluded_changes_remote(sync_events)

        # remove deleted item and its children from the excluded list
        for si in changes_excluded:
            new_excluded = [path for path in self.excluded_items
                            if not is_equal_or_child(path, si.dbx_path.lower())]

            self.excluded_items = new_excluded

        # sort changes into folders, files and deleted
        folders: List[SyncEvent] = []
        files: List[SyncEvent] = []
        deleted: List[SyncEvent] = []

        for sync_event in changes_included:

            if sync_event.is_deleted:
                deleted.append(sync_event)
            elif sync_event.is_directory:
                folders.append(sync_event)
            elif sync_event.is_file:
                files.append(sync_event)

            # housekeeping
            self.queued_for_download.put(sync_event)

        # sort according to path hierarchy
        # do not create sub-folder / file before parent exists
        deleted.sort(key=lambda x: x.dbx_path.count('/'))
        folders.sort(key=lambda x: x.dbx_path.count('/'))
        files.sort(key=lambda x: x.dbx_path.count('/'))

        results = []  # local list of all changes

        # apply deleted items
        if deleted:
            logger.info('Applying deletions...')
        for item in deleted:
            res = self._create_local_entry(item)
            results.append(res)

        # create local folders, start with top-level and work your way down
        if folders:
            logger.info('Creating folders...')
        for folder in folders:
            res = self._create_local_entry(folder)
            results.append(res)

        # apply created files
        with ThreadPoolExecutor(max_workers=self._num_threads,
                                thread_name_prefix='maestral-download-pool') as executor:
            fs = (executor.submit(self._create_local_entry, file) for file in files)

            n_files = len(files)
            for f, n in zip(as_completed(fs), range(1, n_files + 1)):
                throttled_log(logger, f'Downloading {n}/{n_files}...')
                results.append(f.result())

        if cursor and not self.cancel_pending.is_set():
            # always save remote cursor if not aborted by user,
            # failed downloads will be tracked and retried individually
            self.last_cursor = cursor

        self._clean_and_save_rev_file()
        self._save_to_history(changes_included)

        return results

    def notify_user(self, sync_events: List[SyncEvent]) -> None:
        """
        Sends system notification for file changes.

        :param sync_events: List of sync items from download sync.
        """

        changes = [si for si in sync_events if si.status != SyncStatus.Skipped]

        # get number of remote changes
        n_changed = len(changes)

        if n_changed == 0:
            return

        # find out who changed the item(s), show the user name if its only a single user
        user_name: Optional[str]
        user_list = set(si.change_user_name for si in changes)
        if len(user_list) == 1:
            # all files have been modified by the same user
            user_name = user_list.pop()
            if user_name == self._conf.get('account', 'display_name'):
                user_name = 'You'
        else:
            user_name = None

        if n_changed == 1:
            # display user name, file name, and type of change
            si = changes[0]
            file_name = osp.basename(si.dbx_path)
            if si.change_type == ChangeType.Deleted:
                change_type = 'removed'
            elif si.change_type == ChangeType.Added:
                change_type = 'added'
            else:
                change_type = 'changed'
        else:

            if all(si.change_type == ChangeType.Deleted for si in sync_events):
                change_type = 'removed'
            elif all(si.change_type == ChangeType.Added for si in sync_events):
                change_type = 'added'
            else:
                change_type = 'changed'

            if all(si.item_type == ItemType.File for si in sync_events):
                file_name = f'{n_changed} files'
            elif all(si.item_type == ItemType.Folder for si in sync_events):
                file_name = f'{n_changed} folders'
            else:
                file_name = f'{n_changed} items'

        if user_name:
            msg = f'{user_name} {change_type} {file_name}'
        else:
            msg = f'{file_name} {change_type}'

        self._notifier.notify(msg)

    def _filter_excluded_changes_remote(self, changes: List[SyncEvent]) \
            -> Tuple[List[SyncEvent], List[SyncEvent]]:
        """
        Removes all excluded items from the given list of changes.

        :param changes: List of SyncEvents.
        :returns: Tuple with items to keep and items to discard.
        """
        items_to_keep = []
        items_to_discard = []

        for item in changes:
            if self.is_excluded_by_user(item.dbx_path) or self.is_excluded(item.dbx_path):
                items_to_discard.append(item)
            else:
                items_to_keep.append(item)

        return items_to_keep, items_to_discard

    def _check_download_conflict(self, sync_event: SyncEvent) -> Conflict:
        """
        Check if a local item is conflicting with remote change. The equivalent check when
        uploading and a change will be carried out by Dropbox itself.

        Checks are carried out against our index, reflecting the latest sync state.

        :param sync_event: Download SyncEvent.
        :returns: Conflict check result.
        :raises: :class:`errors.MaestralApiError`
        """

        local_rev = self.get_local_rev(sync_event.dbx_path)

        if sync_event.rev == local_rev:
            # Local change has the same rev. May be newer and
            # not yet synced or identical. Don't overwrite.
            logger.debug('Equal revs for "%s": local item is the same or newer '
                         'than on Dropbox', sync_event.dbx_path)
            return Conflict.LocalNewerOrIdentical

        else:
            # Dropbox server version has a different rev, likely is newer.
            # If the local version has been modified while sync was stopped,
            # those changes will be uploaded before any downloads can begin.
            # Conflict resolution will then be handled by Dropbox.
            # If the local version has been modified while sync was running
            # but changes were not uploaded before the remote version was
            # changed as well, the local ctime will be newer than last_sync:
            # (a) The upload of the changed file has already started. Upload thread
            #     will hold the lock and we won't be here checking for conflicts.
            # (b) The upload has not started yet. Manually check for conflict.

            local_hash = get_local_hash(sync_event.local_path)

            if sync_event.content_hash == local_hash:
                logger.debug('Equal content hashes for "%s": no conflict', sync_event.dbx_path)
                self.set_local_rev(sync_event.dbx_path, sync_event.rev)
                return Conflict.Identical
            elif any(is_equal_or_child(p, sync_event.dbx_path.lower()) for p in self.upload_errors):
                logger.debug('Unresolved upload error for "%s": conflict', sync_event.dbx_path)
                return Conflict.Conflict
            elif self._get_ctime(sync_event.local_path) <= self.get_last_sync_for_path(sync_event.dbx_path):
                logger.debug('Ctime is older than last sync for "%s": remote item '
                             'is newer', sync_event.dbx_path)
                return Conflict.RemoteNewer
            elif not sync_event.rev:
                logger.debug('No remote rev for "%s": Local item has been modified '
                             'since remote deletion', sync_event.dbx_path)
                return Conflict.LocalNewerOrIdentical
            else:
                logger.debug('Ctime is newer than last sync for "%s": conflict', sync_event.dbx_path)
                return Conflict.Conflict

    def _get_ctime(self, local_path: str, ignore_excluded: bool = True) -> float:
        """
        Returns the ctime of a local item or -1.0 if there is nothing at the path. If the
        item is a directory, return the largest ctime of it and its children.

        :param local_path: Absolute path on local drive.
        :param ignore_excluded: If ``True``, the ctimes of children for which
            :meth:`is_excluded` evaluates to ``True`` are disregarded. This is only
            relevant if ``local_path`` points to a directory and has no effect if it
            points to a path.
        :returns: Ctime or -1.0.
        """
        try:
            stat = os.stat(local_path)
            if S_ISDIR(stat.st_mode):
                ctime = stat.st_ctime
                with os.scandir(local_path) as it:
                    for entry in it:
                        ignore = ignore_excluded and self.is_excluded(entry.name)
                        if not ignore:
                            ctime = max(ctime, entry.stat().st_ctime)
                return ctime
            else:
                return os.stat(local_path).st_ctime
        except FileNotFoundError:
            return -1.0

    def _get_modified_by_dbid(self, md: Metadata) -> str:
        """
        Returns the Dropbox ID of the user who modified a shared item or our own ID if the
        item was not shared.

        :param md: Dropbox metadata.
        :return: Dropbox ID
        """

        try:
            return md.sharing_info.modified_by
        except AttributeError:
            return self._conf.get('account', 'account_id')

    def _clean_remote_changes(self, changes: dropbox.files.ListFolderResult) \
            -> dropbox.files.ListFolderResult:
        """
        Takes remote file events since last sync and cleans them up so that there is only
        a single event per path.

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

        histories: Dict[str, List[Metadata]] = dict()
        for entry in changes.entries:
            try:
                histories[entry.path_lower].append(entry)
            except KeyError:
                histories[entry.path_lower] = [entry]

        new_entries = []

        for h in histories.values():
            if len(h) == 1:
                new_entries.extend(h)
            else:
                last_event = h[-1]
                was_dir = self.get_local_rev(last_event.path_lower) == 'folder'

                # Dropbox guarantees that applying events in the provided order
                # will reproduce the state in the cloud. We therefore keep only
                # the last event, unless there is a change in item type.
                if (was_dir and isinstance(last_event, FileMetadata)
                        or not was_dir and isinstance(last_event, FolderMetadata)):
                    deleted_event = DeletedMetadata(
                        name=last_event.name,
                        path_lower=last_event.path_lower,
                        path_display=last_event.path_display,
                        parent_shared_folder_id=last_event.parent_shared_folder_id
                    )
                    new_entries.append(deleted_event)
                    new_entries.append(last_event)
                else:
                    new_entries.append(last_event)

        changes.entries = new_entries

        return changes

    def _create_local_entry(self, sync_event: SyncEvent) -> SyncEvent:
        """
        Applies a file / folder change from Dropbox servers to the local Dropbox folder.
        Any :class:`errors.MaestralApiError` will be caught and logged as appropriate.
        Entries in the local index are created after successful completion.

        :param sync_event: Dropbox metadata.
        :returns: Copy of the Dropbox metadata if the change was applied successfully,
            ``True`` if the change already existed, ``False`` in case of a
            :class:`errors.SyncError` and ``None`` if cancelled.
        """

        # housekeeping
        remove_from_queue(self.queued_for_download, sync_event)
        self.downloading.put(sync_event)

        if self.cancel_pending.is_set():
            sync_event.status = SyncStatus.Aborted
            remove_from_queue(self.downloading, sync_event)
            return sync_event

        self._slow_down()

        self.clear_sync_error(dbx_path=sync_event.dbx_path)
        sync_event.status = SyncStatus.Syncing

        try:
            if sync_event.is_deleted:
                res = self._on_remote_deleted(sync_event)
            elif sync_event.is_file:
                res = self._on_remote_file(sync_event)
            elif sync_event.is_directory:
                res = self._on_remote_folder(sync_event)
            else:
                res = None

            if res:
                sync_event.status = SyncStatus.Done
            else:
                sync_event.status = SyncStatus.Skipped

        except SyncError as e:
            self._handle_sync_error(e, direction=SyncDirection.Down)
            sync_event.status = SyncStatus.Failed
        finally:
            remove_from_queue(self.downloading, sync_event)

        return sync_event

    def _on_remote_file(self, sync_event: SyncEvent) -> Optional[SyncEvent]:
        """
        Applies a remote file change or creation locally.

        :param sync_event: SyncEvent for file download.
        :returns: SyncEvent corresponding to local item or None if no local changes
            are made.
        """

        # Store the new entry at the given path in your local state.
        # If the required parent folders don’t exist yet, create them.
        # If there’s already something else at the given path,
        # replace it and remove all its children.

        conflict_check = self._check_download_conflict(sync_event)

        if conflict_check in (Conflict.Identical, Conflict.LocalNewerOrIdentical):
            return None

        local_path = sync_event.local_path

        # we download to a temporary file first (this may take some time)
        tmp_fname = self._new_tmp_file()

        try:
            md = self.client.download(
                f'rev:{sync_event.rev}', tmp_fname,
                sync_event=sync_event
            )
            sync_event = self.sync_event_from_dbx_metadata(md)
        except SyncError as err:
            # replace rev number with path
            err.dbx_path = sync_event.dbx_path
            raise err

        # re-check for conflict and move the conflict
        # out of the way if anything has changed
        if self._check_download_conflict(sync_event) == Conflict.Conflict:
            new_local_path = generate_cc_name(
                local_path, is_fs_case_sensitive=self.is_case_sensitive
            )
            event_cls = DirMovedEvent if osp.isdir(local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(local_path, new_local_path)):
                exc = move(local_path, new_local_path)

            if exc:
                raise os_to_maestral_error(exc, local_path=new_local_path)

            logger.debug('Download conflict: renamed "%s" to "%s"', local_path,
                         new_local_path)
            self.rescan(new_local_path)

        if osp.isdir(local_path):
            event_cls = DirDeletedEvent if osp.isdir(local_path) else FileDeletedEvent
            with self.fs_events.ignore(event_cls(local_path)):
                delete(local_path)

        # move the downloaded file to its destination
        with self.fs_events.ignore(FileDeletedEvent(local_path),
                                   FileMovedEvent(tmp_fname, local_path)):
            exc = move(tmp_fname, local_path)

        if exc:
            raise os_to_maestral_error(exc, dbx_path=sync_event.dbx_path,
                                       local_path=local_path)

        self.set_last_sync_for_path(sync_event.dbx_path, self._get_ctime(local_path))
        self.set_local_rev(sync_event.dbx_path, sync_event.rev)

        logger.debug('Created local file "%s"', sync_event.dbx_path)

        return sync_event

    def _on_remote_folder(self, sync_event: SyncEvent) -> Optional[SyncEvent]:
        """
        Applies a remote folder creation locally.

        :param sync_event: SyncEvent for folder download.
        :returns: SyncEvent corresponding to local item or None if no local changes
            are made.
        """

        # Store the new entry at the given path in your local state.
        # If the required parent folders don’t exist yet, create them.
        # If there’s already something else at the given path,
        # replace it but leave the children as they are.

        conflict_check = self._check_download_conflict(sync_event)

        if conflict_check in (Conflict.Identical, Conflict.LocalNewerOrIdentical):
            return None

        if conflict_check == Conflict.Conflict:
            new_local_path = generate_cc_name(
                sync_event.local_path, is_fs_case_sensitive=self.is_case_sensitive
            )
            event_cls = DirMovedEvent if osp.isdir(sync_event.local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(sync_event.local_path, new_local_path)):
                exc = move(sync_event.local_path, new_local_path)
                if exc:
                    raise os_to_maestral_error(exc, local_path=new_local_path)

            logger.debug('Download conflict: renamed "%s" to "%s"', sync_event.local_path,
                         new_local_path)
            self.rescan(new_local_path)

        if osp.isfile(sync_event.local_path):
            event_cls = DirDeletedEvent if osp.isdir(sync_event.local_path) else FileDeletedEvent
            with self.fs_events.ignore(event_cls(sync_event.local_path)):
                delete(sync_event.local_path)

        try:
            with self.fs_events.ignore(DirCreatedEvent(sync_event.local_path), recursive=False):
                os.makedirs(sync_event.local_path)
        except FileExistsError:
            pass
        except OSError as err:
            raise os_to_maestral_error(err, dbx_path=sync_event.dbx_path,
                                       local_path=sync_event.local_path)

        self.set_last_sync_for_path(sync_event.dbx_path, self._get_ctime(sync_event.local_path))
        self.set_local_rev(sync_event.dbx_path, 'folder')

        logger.debug('Created local folder "%s"', sync_event.dbx_path)

        return sync_event

    def _on_remote_deleted(self, sync_event: SyncEvent) -> Optional[SyncEvent]:
        """
        Applies a remote deletion locally.

        :param sync_event: Dropbox deleted metadata.
        :returns: Dropbox metadata corresponding to local deletion or None if no local
            changes are made.
        """

        # If your local state has something at the given path,
        # remove it and all its children. If there’s nothing at the
        # given path, ignore this entry.

        conflict_check = self._check_download_conflict(sync_event)

        if conflict_check in (Conflict.Identical, Conflict.LocalNewerOrIdentical):
            return None

        event_cls = DirDeletedEvent if osp.isdir(sync_event.local_path) else FileDeletedEvent
        with self.fs_events.ignore(event_cls(sync_event.local_path)):
            exc = delete(sync_event.local_path)

        if not exc:
            self.set_local_rev(sync_event.dbx_path, None)
            self.set_last_sync_for_path(sync_event.dbx_path, time.time())
            logger.debug('Deleted local item "%s"', sync_event.dbx_path)
            return sync_event
        elif isinstance(exc, FileNotFoundError):
            self.set_local_rev(sync_event.dbx_path, None)
            self.set_last_sync_for_path(sync_event.dbx_path, time.time())
            logger.debug('Deletion failed: "%s" not found', sync_event.dbx_path)
            return None
        else:
            raise os_to_maestral_error(exc)

    def rescan(self, local_path: str) -> None:
        """
        Forces a rescan of a local path: schedules created events for every folder,
        modified events for every file and deleted events for every deleted item (compared
        to our index).

        :param local_path: Path to rescan.
        """

        logger.debug('Rescanning "%s"', local_path)

        if osp.isfile(local_path):
            self.fs_events.local_file_event_queue.put(FileModifiedEvent(local_path))
        elif osp.isdir(local_path):
            # add created and deleted events of children as appropriate
            snapshot = DirectorySnapshot(local_path)
            lowercase_snapshot_paths = {x.lower() for x in snapshot.paths}
            local_path_lower = local_path.lower()

            for path in snapshot.paths:
                if snapshot.isdir(path):
                    self.fs_events.local_file_event_queue.put(DirCreatedEvent(path))
                else:
                    self.fs_events.local_file_event_queue.put(FileModifiedEvent(path))

            # get deleted items
            rev_dict_copy = self.get_rev_index()
            for path in rev_dict_copy:
                child_path_uncased = osp.join(self.dropbox_path, path).lower()
                if (child_path_uncased.startswith(local_path_lower)
                        and child_path_uncased not in lowercase_snapshot_paths):
                    local_child_path = self.to_local_path(path)
                    if rev_dict_copy[path] == 'folder':
                        self.fs_events.local_file_event_queue.put(
                            DirDeletedEvent(local_child_path)
                        )
                    else:
                        self.fs_events.local_file_event_queue.put(
                            FileDeletedEvent(local_child_path)
                        )

        elif not osp.exists(local_path):
            dbx_path = self.to_dbx_path(local_path)
            if self.get_local_rev(dbx_path) == 'folder':
                self.fs_events.local_file_event_queue.put(DirDeletedEvent(local_path))
            elif self.get_local_rev(dbx_path):
                self.fs_events.local_file_event_queue.put(FileDeletedEvent(local_path))

    def _save_to_history(self, sync_events: List[SyncEvent]) -> None:
        """
        Saves remote changes to the history database.

        :param sync_events: Dropbox Metadata.
        """

        self._db_session.add_all(sync_events)

        # drop all entries older than self._keep_history
        now = time.time()
        query = self._db_session.query(SyncEvent)
        query.filter(SyncEvent.change_time < now - self._keep_history).delete()

        # commit to drive
        self._db_session.commit()


# ========================================================================================
# Workers for upload, download and connection monitoring threads
# ========================================================================================

def helper(mm: 'SyncMonitor') -> None:
    """
    A worker for periodic maintenance:

     1) Checks for a connection to Dropbox servers.
     2) Pauses syncing when the connection is lost and resumes syncing when reconnected
        and syncing has not been paused by the user.
     3) Triggers weekly reindexing.

    :param mm: MaestralMonitor instance.
    """

    while mm.running.is_set():

        if check_connection('www.dropbox.com'):
            if not mm.connected.is_set() and not mm.paused_by_user.is_set():
                mm.startup.set()
            # rebuild the index periodically
            elif (time.time() - mm.sync.last_reindex > mm.reindex_interval
                    and mm.idle_time > 20 * 60):
                mm.rebuild_index()
            mm.connected.set()
            time.sleep(mm.connection_check_interval)

        else:
            if mm.connected.is_set():
                logger.info(DISCONNECTED)
            mm.syncing.clear()
            mm.connected.clear()
            mm.startup.clear()
            time.sleep(mm.connection_check_interval)


def download_worker(sync: SyncEngine, syncing: Event,
                    running: Event, connected: Event) -> None:
    """
    Worker to sync changes of remote Dropbox with local folder.

    :param sync: Instance of :class:`SyncEngine`.
    :param syncing: Event that indicates if workers are running or paused.
    :param running: Event to shutdown local file event handler and worker threads.
    :param connected: Event that indicates if we can connect to Dropbox.
    """

    while running.is_set():

        syncing.wait()

        try:
            has_changes = sync.wait_for_remote_changes(sync.last_cursor)

            with sync.sync_lock:

                if not (running.is_set() and syncing.is_set()):
                    continue

                if has_changes:
                    logger.info(SYNCING)

                    changes, remote_cursor = sync.list_remote_changes(sync.last_cursor)
                    downloaded = sync.apply_remote_changes(changes, remote_cursor)
                    sync.notify_user(downloaded)

                    logger.info(IDLE)

                    sync.client.get_space_usage()

                    gc.collect()

        except ConnectionError:
            syncing.clear()
            connected.clear()
            logger.info(DISCONNECTED)
        except Exception as err:
            running.clear()
            syncing.clear()
            title = getattr(err, 'title', 'Unexpected error')
            logger.error(title, exc_info=True)


def download_worker_added_item(sync: SyncEngine, syncing: Event,
                               running: Event, connected: Event,
                               added_item_queue: 'Queue[str]') -> None:
    """
    Worker to download items which have been newly included in sync.

    :param sync: Instance of :class:`SyncEngine`.
    :param syncing: Event that indicates if workers are running or paused.
    :param running: Event to shutdown local file event handler and worker threads.
    :param connected: Event that indicates if we can connect to Dropbox.
    :param added_item_queue: Queue with newly added items to download. Entries are Dropbox
        paths.
    """

    while running.is_set():

        syncing.wait()

        try:
            dbx_path = added_item_queue.get()

            with sync.sync_lock:
                if not (running.is_set() and syncing.is_set()):
                    sync.pending_downloads.add(dbx_path.lower())
                    continue

                sync.get_remote_item(dbx_path)
                logger.info(IDLE)

                gc.collect()

        except ConnectionError:
            syncing.clear()
            connected.clear()
            logger.info(DISCONNECTED)
        except Exception as err:
            running.clear()
            syncing.clear()
            title = getattr(err, 'title', 'Unexpected error')
            logger.error(title, exc_info=True)


def upload_worker(sync: SyncEngine, syncing: Event,
                  running: Event, connected: Event) -> None:
    """
    Worker to sync local changes to remote Dropbox.

    :param sync: Instance of :class:`SyncEngine`.
    :param syncing: Event that indicates if workers are running or paused.
    :param running: Event to shutdown local file event handler and worker threads.
    :param connected: Event that indicates if we can connect to Dropbox.
    """

    while running.is_set():

        syncing.wait()

        try:
            changes, local_cursor = sync.wait_for_local_changes()

            with sync.sync_lock:
                if not (running.is_set() and syncing.is_set()):
                    continue

                if len(changes) > 0:
                    logger.info(SYNCING)

                sync.apply_local_changes(changes, local_cursor)

                if len(changes) > 0:
                    logger.info(IDLE)

                gc.collect()

        except ConnectionError:
            syncing.clear()
            connected.clear()
            logger.info(DISCONNECTED)
        except Exception as err:
            running.clear()
            syncing.clear()
            title = getattr(err, 'title', 'Unexpected error')
            logger.error(title, exc_info=True)


def startup_worker(sync: SyncEngine, syncing: Event, running: Event, connected: Event,
                   startup: Event, paused_by_user: Event) -> None:
    """
    Worker to sync local changes to remote Dropbox.

    :param sync: Instance of :class:`SyncEngine`.
    :param syncing: Event that indicates if workers are running or paused.
    :param running: Event to shutdown local file event handler and worker threads.
    :param connected: Event that indicates if we can connect to Dropbox.
    :param startup: Set when we should run startup routines.
    :param paused_by_user: Set when syncing has been paused by the user.
    """

    while running.is_set():

        startup.wait()

        try:
            with sync.sync_lock:
                # run / resume initial download
                # local changes during this download will be registered
                # by the local FileSystemObserver but only uploaded after
                # `syncing` has been set
                if sync.last_cursor == '':
                    sync.clear_all_sync_errors()
                    sync.get_remote_folder()
                    sync.last_sync = time.time()

                if not running.is_set():
                    continue

                # retry failed downloads
                if len(sync.download_errors) > 0:
                    logger.info('Retrying failed downloads...')

                for dbx_path in list(sync.download_errors):
                    logger.info(f'Downloading {dbx_path}...')
                    sync.get_remote_item(dbx_path)

                # resume interrupted downloads
                if len(sync.pending_downloads) > 0:
                    logger.info('Resuming interrupted downloads...')

                for dbx_path in list(sync.pending_downloads):
                    logger.info(f'Downloading {dbx_path}...')
                    sync.get_remote_item(dbx_path)

                # retry failed / interrupted uploads by scheduling additional events
                # if len(sync.upload_errors) > 0:
                #     logger.debug('Retrying failed uploads...')
                #
                # for dbx_path in list(sync.upload_errors):
                #     sync.rescan(sync.to_local_path(dbx_path))

                # upload changes while inactive
                sync.upload_local_changes_while_inactive()

                # enforce immediate check for remote changes
                changes, remote_cursor = sync.list_remote_changes(sync.last_cursor)
                downloaded = sync.apply_remote_changes(changes, remote_cursor)
                sync.notify_user(downloaded)

                if not running.is_set():
                    continue

                gc.collect()

                if not paused_by_user.is_set():
                    syncing.set()

                startup.clear()

                logger.info(IDLE)

        except ConnectionError:
            syncing.clear()
            connected.clear()
            startup.clear()
            logger.info(DISCONNECTED)
        except Exception as err:
            running.clear()
            syncing.clear()
            title = getattr(err, 'title', 'Unexpected error')
            logger.error(title, exc_info=True)

    startup.clear()


# ========================================================================================
# Main Monitor class to start, stop and coordinate threads
# ========================================================================================

class SyncMonitor:
    """
    Class to sync changes between Dropbox and a local folder. It creates five threads:
    `observer` to retrieve local file system events, `startup_thread` to carry out any
    startup jobs such as initial syncs, `upload_thread` to upload local changes to
    Dropbox, `download_thread` to query for and download remote changes, and
    `helper_thread` which periodically checks the connection to Dropbox servers.

    :param client: The Dropbox API client, a wrapper around the Dropbox Python SDK.
    """

    added_item_queue: 'Queue[str]'
    connection_check_interval: float = 2.0

    def __init__(self, client: DropboxClient):

        self.client = client
        self.config_name = self.client.config_name

        self._conf = MaestralConfig(self.config_name)

        self.startup = Event()
        self.connected = Event()
        self.syncing = Event()
        self.running = Event()
        self.paused_by_user = Event()
        self.paused_by_user.set()

        self.added_item_queue = Queue()  # entries are dbx_paths

        self._lock = RLock()

        self.fs_event_handler = FSEventHandler(self.syncing, self.startup)
        self.sync = SyncEngine(self.client, self.fs_event_handler)

        self._startup_time = -1.0

    @property
    def reindex_interval(self) -> float:
        return self._conf.get('sync', 'reindex_interval')

    @property
    def uploading(self) -> List[SyncEvent]:
        """Returns a list all items queued for upload or currently uploading."""
        return list(self.sync.queued_for_upload.queue) + list(self.sync.uploading.queue)

    @property
    def downloading(self) -> List[SyncEvent]:
        """Returns a list all items queued for download or currently downloading."""
        return list(self.sync.queued_for_download.queue) + list(self.sync.downloading.queue)

    def start(self) -> None:
        """Creates observer threads and starts syncing."""

        with self._lock:

            if self.running.is_set() or self.startup.is_set():
                # do nothing if already started
                return

            self.running = Event()  # create new event to let old threads shut down

            self.local_observer_thread = Observer(timeout=0.1)
            self.local_observer_thread.setName('maestral-fsobserver')
            self._watch = self.local_observer_thread.schedule(
                self.fs_event_handler, self.sync.dropbox_path, recursive=True
            )
            for emitter in self.local_observer_thread.emitters:
                emitter.setName('maestral-fsemitter')

            self.helper_thread = Thread(
                target=helper,
                daemon=True,
                args=(self,),
                name='maestral-helper'
            )

            self.startup_thread = Thread(
                target=startup_worker,
                daemon=True,
                args=(
                    self.sync, self.syncing, self.running, self.connected,
                    self.startup, self.paused_by_user
                ),
                name='maestral-sync-startup'
            )

            self.download_thread = Thread(
                target=download_worker,
                daemon=True,
                args=(
                    self.sync, self.syncing, self.running, self.connected,
                ),
                name='maestral-download'
            )

            self.download_thread_added_folder = Thread(
                target=download_worker_added_item,
                daemon=True,
                args=(
                    self.sync, self.syncing, self.running, self.connected,
                    self.added_item_queue
                ),
                name='maestral-folder-download'
            )

            self.upload_thread = Thread(
                target=upload_worker,
                daemon=True,
                args=(
                    self.sync, self.syncing, self.running, self.connected,
                ),
                name='maestral-upload'
            )

            try:
                self.local_observer_thread.start()
            except OSError as err:
                new_err = fswatch_to_maestral_error(err)
                title = getattr(new_err, 'title', 'Unexpected error')
                logger.error(title, exc_info=_exc_info(new_err))

            self.running.set()
            self.syncing.clear()
            self.connected.set()
            self.startup.set()

            self.helper_thread.start()
            self.startup_thread.start()
            self.upload_thread.start()
            self.download_thread.start()
            self.download_thread_added_folder.start()

            self.paused_by_user.clear()

            self._startup_time = time.time()

    def pause(self) -> None:
        """Pauses syncing."""

        with self._lock:
            self.paused_by_user.set()
            self.syncing.clear()

            self.sync.cancel_pending.set()
            self._wait_for_idle()
            self.sync.cancel_pending.clear()

            logger.info(PAUSED)

    def resume(self) -> None:
        """Checks for changes while idle and starts syncing."""

        with self._lock:
            if not self.paused_by_user.is_set():
                return

            self.startup.set()
            self.paused_by_user.clear()

    def stop(self) -> None:
        """Stops syncing and destroys worker threads."""

        with self._lock:

            if not self.running.is_set():
                return

            logger.info('Shutting down threads...')

            self.running.clear()
            self.syncing.clear()
            self.paused_by_user.clear()
            self.startup.clear()

            self.sync.cancel_pending.set()
            self._wait_for_idle()
            self.sync.cancel_pending.clear()

            self.local_observer_thread.stop()
            self.local_observer_thread.join()
            self.helper_thread.join()
            self.upload_thread.join()

            logger.info(STOPPED)

    @property
    def idle_time(self) -> float:
        """Returns the idle time in seconds since the last file change or zero if syncing
        is not running."""
        if len(self.sync._last_sync_for_path) > 0:
            return time.time() - max(self.sync._last_sync_for_path.values())
        elif self.syncing.is_set():
            return time.time() - self._startup_time
        else:
            return 0.0

    def reset_sync_state(self) -> None:
        """Resets all saved sync state."""

        if self.syncing.is_set() or self.startup.is_set() or self.sync.busy():
            raise RuntimeError('Cannot reset sync state while syncing.')

        self.sync.last_cursor = ''
        self.sync.last_sync = 0.0
        self.sync.clear_rev_index()

        logger.debug('Sync state reset')

    def rebuild_index(self) -> None:
        """
        Rebuilds the rev file by comparing remote with local files and updating rev
        numbers from the Dropbox server. Files are compared by their content hashes and
        conflicting copies are created if the contents differ. File changes during the
        rebuild process will be queued and uploaded once rebuilding has completed.

        Rebuilding will be performed asynchronously.
        """

        logger.info('Rebuilding index...')

        self.pause()

        self.sync.last_cursor = ''
        self.sync.clear_rev_index()

        if not self.running.is_set():
            self.start()
        else:
            self.resume()

    def _wait_for_idle(self) -> None:

        self.sync.sync_lock.acquire()
        self.sync.sync_lock.release()

    def _threads_alive(self) -> bool:
        """Returns ``True`` if all threads are alive, ``False`` otherwise."""

        with self._lock:

            try:
                threads: Tuple[Thread, ...] = (
                    self.local_observer_thread,
                    self.upload_thread, self.download_thread,
                    self.download_thread_added_folder,
                    self.helper_thread,
                    self.startup_thread
                )
            except AttributeError:
                return False

            base_threads_alive = (t.is_alive() for t in threads)
            watchdog_emitters_alive = (e.is_alive() for e
                                       in self.local_observer_thread.emitters)

            return all(base_threads_alive) and all(watchdog_emitters_alive)


# ========================================================================================
# Helper functions
# ========================================================================================

def _exc_info(exc: BaseException) -> ExecInfoType:
    return type(exc), exc, exc.__traceback__


def get_dest_path(event: FileSystemEvent) -> str:
    return getattr(event, 'dest_path', event.src_path)


def split_moved_event(event: Union[FileMovedEvent, DirMovedEvent]) \
        -> Tuple[FileSystemEvent, FileSystemEvent]:
    """
    Splits a given FileSystemEvent into Deleted and Created events of the same type.

    :param event: Original event.
    :returns: Tuple of deleted and created events.
    """

    if event.is_directory:
        created_event_cls = DirCreatedEvent
        deleted_event_cls = DirDeletedEvent
    else:
        created_event_cls = FileCreatedEvent
        deleted_event_cls = FileDeletedEvent

    return deleted_event_cls(event.src_path), created_event_cls(event.dest_path)


def get_local_hash(local_path: str, chunk_size: int = 1024) -> Optional[str]:
    """
    Computes content hash of a local file.

    :param local_path: Absolute path on local drive.
    :param chunk_size: Size of chunks to hash in bites.
    :returns: Content hash to compare with Dropbox's content hash, or 'folder' if the path
        points to a directory. ``None`` if there is nothing at the path.
    """

    hasher = DropboxContentHasher()

    try:
        with open(local_path, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if len(chunk) == 0:
                    break
                hasher.update(chunk)

        return str(hasher.hexdigest())
    except IsADirectoryError:
        return 'folder'
    except FileNotFoundError:
        return None
    except NotADirectoryError:
        # a parent directory in the path refers to a file instead of a folder
        return None
    except OSError as err:
        raise os_to_maestral_error(err, local_path=local_path)
    finally:
        del hasher


def remove_from_queue(queue: Queue, *items: Any) -> None:
    """
    Tries to remove an item from a queue.

    :param queue: Queue to remove item from.
    :param items: Items to remove
    """

    with queue.mutex:
        for item in items:
            try:
                queue.queue.remove(item)
            except ValueError:
                pass


def entries_to_str(entries: List[Metadata]) -> str:
    str_reps = [f'<{e.__class__.__name__}(path_display={e.path_display})>'
                for e in entries]
    return '[' + ',\n '.join(str_reps) + ']'


_last_emit = time.time()


def throttled_log(log: logging.Logger, msg: str, level: int = logging.INFO,
                  limit: int = 1) -> None:

    global _last_emit

    if time.time() - _last_emit > limit:
        log.log(level=level, msg=msg)
        _last_emit = time.time()


def cpu_usage_percent(interval: float = 0.1) -> float:
    """Returns a float representing the CPU utilization of the current process as a
    percentage. This duplicates the similar method from psutil to avoid the psutil
    dependency.

    Compares process times to system CPU times elapsed before and after the interval
    (blocking). It is recommended for accuracy that this function be called with an
    interval of at least 0.1 sec.

    A value > 100.0 can be returned in case of processes running multiple threads on
    different CPU cores. The returned value is explicitly NOT split evenly between all
    available logical CPUs. This means that a busy loop process running on a system with
    2 logical CPUs will be reported as having 100% CPU utilization instead of 50%.

    :param interval: Interval in sec between comparisons of CPU times.
    :returns: CPU usage during interval in percent.
    """

    if not interval > 0:
        raise ValueError(f'interval is not positive (got {interval!r})')

    def timer():
        return time.monotonic() * _cpu_count

    st1 = timer()
    rt1 = resource.getrusage(resource.RUSAGE_SELF)
    time.sleep(interval)
    st2 = timer()
    rt2 = resource.getrusage(resource.RUSAGE_SELF)

    delta_proc = (rt2.ru_utime - rt1.ru_utime) + (rt2.ru_stime - rt1.ru_stime)
    delta_time = st2 - st1

    try:
        overall_cpus_percent = ((delta_proc / delta_time) * 100)
    except ZeroDivisionError:
        return 0.0
    else:
        single_cpu_percent = overall_cpus_percent * _cpu_count
        return round(single_cpu_percent, 1)


def check_connection(hostname: str) -> bool:
    """
    A low latency check for an internet connection.

    :param hostname: Hostname to use for connection check.
    :returns: Connection availability.
    """
    try:
        host = socket.gethostbyname(hostname)
        s = socket.create_connection((host, 80), 2)
        s.close()
        return True
    except Exception:
        return False
