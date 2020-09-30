# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.
"""

# system imports
import os
import os.path as osp
from stat import S_ISDIR
import resource
import logging
import time
import tempfile
import random
from threading import Thread, Event, RLock, current_thread
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty
from collections import abc
from contextlib import contextmanager
import enum
import pprint
import socket
import gc
from datetime import timezone
from functools import wraps
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
    Type,
    cast,
    TypeVar,
)
from types import TracebackType

# external imports
import click
import sqlalchemy.exc  # type: ignore
import sqlalchemy.engine.url  # type: ignore
from sqlalchemy.sql import func  # type: ignore
from sqlalchemy.ext.declarative import declarative_base  # type: ignore
from sqlalchemy.orm import sessionmaker  # type: ignore
from sqlalchemy.sql import case  # type: ignore
from sqlalchemy.sql.elements import Case  # type: ignore
from sqlalchemy.ext.hybrid import hybrid_property  # type: ignore
from sqlalchemy import MetaData, Column, Integer, String, Enum, Float, create_engine  # type: ignore
import pathspec  # type: ignore
import dropbox  # type: ignore
from dropbox.files import Metadata, DeletedMetadata, FileMetadata, FolderMetadata  # type: ignore
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
from watchdog.utils.dirsnapshot import DirectorySnapshot  # type: ignore

# local imports
from maestral.config import MaestralConfig, MaestralState
from maestral.fsevents import Observer
from maestral.constants import (
    IDLE,
    SYNCING,
    PAUSED,
    STOPPED,
    DISCONNECTED,
    EXCLUDED_FILE_NAMES,
    EXCLUDED_DIR_NAMES,
    MIGNORE_FILE,
    FILE_CACHE,
)
from maestral.errors import (
    SyncError,
    NoDropboxDirError,
    CacheDirError,
    PathError,
    NotFoundError,
    DropboxServerError,
    FileConflictError,
    FolderConflictError,
    IsAFolderError,
    InvalidDbidError,
    DatabaseError,
)
from maestral.client import (
    DropboxClient,
    os_to_maestral_error,
    fswatch_to_maestral_error,
)
from maestral.utils.notify import MaestralDesktopNotifier
from maestral.utils.path import (
    generate_cc_name,
    cased_path_candidates,
    is_fs_case_sensitive,
    move,
    delete,
    is_child,
    is_equal_or_child,
    content_hash,
)
from maestral.utils.appdirs import get_data_path, get_home_dir


logger = logging.getLogger(__name__)
_cpu_count = os.cpu_count() or 1  # os.cpu_count can return None

db_naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
Base = declarative_base(metadata=MetaData(naming_convention=db_naming_convention))
Session = sessionmaker(expire_on_commit=False)

ExecInfoType = Tuple[Type[BaseException], BaseException, Optional[TracebackType]]
FT = TypeVar("FT", bound=Callable[..., Any])


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

    RemoteNewer = "remote newer"
    Conflict = "conflict"
    Identical = "identical"
    LocalNewerOrIdentical = "local newer or identical"


class SyncDirection(enum.Enum):
    """
    Enumeration of sync direction.

    :cvar Up: Upload.
    :cvar Down: Download.
    """

    Up = "up"
    Down = "down"


class SyncStatus(enum.Enum):
    """
    Enumeration of sync status values.

    :cvar Queued: Queued for syncing.
    :cvar Syncing: Sync in progress.
    :cvar Done: Sync successfully completed.
    :cvar Failed: Sync failed.
    :cvar Skipped: Item was already in sync.
    """

    Queued = "queued"
    Syncing = "syncing"
    Done = "done"
    Failed = "failed"
    Skipped = "skipped"
    Aborted = "aborted"


class ItemType(enum.Enum):
    """
    Enumeration of SyncEvent types.

    :cvar File: File type.
    :cvar Folder: Folder type.
    """

    File = "file"
    Folder = "folder"


class ChangeType(enum.Enum):
    """
    Enumeration of SyncEvent change types.

    :cvar Added: An added file or folder.
    :cvar Removed: A deleted file or folder.
    :cvar Moved: A moved file or folder.
    :cvar Modified: A modified file. Does not apply to folders.
    """

    Added = "added"
    Removed = "removed"
    Moved = "moved"
    Modified = "modified"


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
    local_file_event_queue: "Queue[FileSystemEvent]"

    ignore_timeout = 2.0

    def __init__(self, syncing: Event, startup: Event) -> None:

        self.syncing = syncing
        self.startup = startup

        self._ignored_events = []
        self.local_file_event_queue = Queue()

    @contextmanager
    def ignore(
        self, *events: FileSystemEvent, recursive: bool = True
    ) -> Iterator[None]:
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
        self._ignored_events.extend(new_ignores)  # this is atomic

        try:
            yield
        finally:
            for ignore in new_ignores:
                ignore.ttl = time.time() + self.ignore_timeout

    def _expire_ignored_events(self) -> None:
        """Removes all expired ignore entries."""

        now = time.time()
        for ignore in self._ignored_events.copy():
            ttl = ignore.ttl
            if ttl and ttl < now:
                try:
                    self._ignored_events.remove(ignore)
                except ValueError:
                    # someone else removed it in the meantime
                    pass

    def _is_ignored(self, event: FileSystemEvent) -> bool:
        """
        Checks if a file system event should been explicitly ignored because it was
        triggered by Maestral itself.

        :param event: Local file system event.
        :returns: Whether the event should be ignored.
        """

        self._expire_ignored_events()

        for ignore in self._ignored_events.copy():
            ignore_event = ignore.event
            recursive = ignore.recursive

            if event == ignore_event:

                if not recursive:
                    try:
                        self._ignored_events.remove(ignore)
                    except ValueError:
                        pass

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


class SyncEvent(Base):  # type: ignore
    """
    Represents a file or folder change in the sync queue. This is used to abstract the
    :class:`watchdog.events.FileSystemEvent` created for local changes and the
    :class:`dropbox.files.Metadata` created for remote changes. All arguments are used to
    construct instance attributes and some attributes may not be set for all event types.
    Note that some instance attributes depend on the state of the Maestral instance, e.g.,
    :attr:`local_path` will depend on the current path of the local Dropbox folder. They
    may therefore become invalid after sync sessions.

    The convenience methods :meth:`from_dbx_metadata` and :meth:`from_file_system_event`
    should be used to properly construct a SyncEvent from Dropbox Metadata or a local
    FileSystemEvent, respectively.

    :param direction: Direction of the sync: upload or download.
    :param item_type: The item type: file or folder.
    :param sync_time: The time the SyncEvent was registered.
    :param dbx_id: A unique dropbox ID for the file or folder. Will only be set for
        download events which are not deletions.
    :param dbx_path: Dropbox path of the item to sync. If the sync represents a move
        operation, this will be the destination path. Follows the casing from server.
    :param dbx_path_from: Dropbox path that this item was moved from. Will only be set if
        ``change_type`` is ``ChangeType.Moved``. Follows the casing from server.
    :param local_path: Local path of the item to sync. If the sync represents a move
        operation, this will be the destination path. Follows the casing from server.
    :param local_path_from: Local path that this item was moved from. Will only be set if
        ``change_type`` is ``ChangeType.Moved``. Follows the casing from server.
    :param rev: The file revision. Will only be set for remote changes. Will be
        'folder' for folders and None for deletions.
    :param content_hash: A hash representing the file content. Will be 'folder' for
        folders and None for deleted items. Set for both local and remote changes.
    :param change_type: The type of change: deleted, moved, added or changed. Remote
        SyncEvents currently do not generate moved events but are reported as deleted and
        added at the new location.
    :param change_time: The time of the change: Local ctime or remote client_modified
        time for files. None for folders or for remote deletions. Note that the
        client_modified may not be reliable as it is set by other clients and not
        verified.
    :param change_dbid: The Dropbox ID of the account which performed the changes. This
        may not be set for added folders or deletions on the server.
    :param change_user_name: The user name corresponding to ``change_dbid``, if the
        account still exists. This field may not be set for performance reasons.
    :param status: Field containing the sync status: queued, syncing, done, failed,
        skipped (item was already in sync) or aborted (by the user).
    :param size: Size of the item in bytes. Always zero for folders.
    :param completed: File size in bytes which has already been uploaded or downloaded.
        Always zero for folders.

    :ivar id: A unique identifier of the SyncEvent.
    :ivar change_time_or_sync_time: Change time when available, otherwise sync time. This
        can be used for sorting or user information purposes.
    """

    __tablename__ = "history"

    id = Column(Integer, primary_key=True)
    direction = Column(Enum(SyncDirection), nullable=False)
    item_type = Column(Enum(ItemType), nullable=False)
    sync_time = Column(Float, nullable=False)
    dbx_id = Column(String)
    dbx_path = Column(String, nullable=False)
    local_path = Column(String, nullable=False)
    dbx_path_from = Column(String)
    local_path_from = Column(String)
    rev = Column(String)
    content_hash = Column(String)
    change_type = Column(Enum(ChangeType), nullable=False)
    change_time = Column(Float)
    change_dbid = Column(String)
    change_user_name = Column(String)
    status = Column(Enum(SyncStatus), nullable=False)
    size = Column(Integer, nullable=False)
    completed = Column(Integer, default=0)

    @hybrid_property
    def change_time_or_sync_time(self) -> float:
        return self.change_time or self.sync_time

    @change_time_or_sync_time.expression  # type: ignore
    def change_time_or_sync_time(cls) -> Case:
        return case(
            [(cls.change_time != None, cls.change_time)], else_=cls.sync_time
        )  # noqa: E711

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
        return self.change_type == ChangeType.Modified

    @property
    def is_deleted(self) -> bool:
        """Returns True for deleted items"""
        return self.change_type == ChangeType.Removed

    @property
    def is_upload(self) -> bool:
        """Returns True for changes to upload"""
        return self.direction == SyncDirection.Up

    @property
    def is_download(self) -> bool:
        """Returns True for changes to download"""
        return self.direction == SyncDirection.Down

    def __repr__(self):
        return (
            f"<{self.__class__.__name__}(direction={self.direction.name}, "
            f"change_type={self.change_type.name}, dbx_path='{self.dbx_path}')>"
        )

    @classmethod
    def from_dbx_metadata(cls, md: Metadata, sync_engine: "SyncEngine") -> "SyncEvent":
        """
        Initializes a SyncEvent from the given Dropbox metadata.

        :param md: Dropbox Metadata.
        :param sync_engine: SyncEngine instance.
        :returns: An instance of this class with attributes populated from the given
            Dropbox Metadata.
        """
        if isinstance(md, DeletedMetadata):
            # there is currently no API call to determine who deleted a file or folder
            change_type = ChangeType.Removed
            change_time = None
            size = 0
            rev = None
            hash_str = None
            dbx_id = None

            try:
                old_md = sync_engine.client.list_revisions(
                    md.path_lower, limit=1
                ).entries[0]
                item_type = ItemType.File
                if not old_md.sharing_info:
                    # file is not in a shared folder, therefore
                    # the current user must have deleted it
                    change_dbid = sync_engine.client.account_id
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
            rev = "folder"
            hash_str = "folder"
            dbx_id = md.id
            change_time = None
            change_dbid = None

        elif isinstance(md, FileMetadata):
            item_type = ItemType.File
            rev = md.rev
            hash_str = md.content_hash
            dbx_id = md.id
            size = md.size
            change_time = md.client_modified.replace(tzinfo=timezone.utc).timestamp()
            if sync_engine.get_local_rev(md.path_lower):
                change_type = ChangeType.Modified
            else:
                change_type = ChangeType.Added
            if md.sharing_info:
                change_dbid = md.sharing_info.modified_by
            else:
                # file is not a shared folder, therefore
                # the current user must have added or modified it
                change_dbid = sync_engine.client.account_id
        else:
            raise RuntimeError(f"Cannot convert {md} to SyncEvent")

        dbx_path_cased = sync_engine.correct_case(md.path_display)

        return cls(
            direction=SyncDirection.Down,
            item_type=item_type,
            sync_time=time.time(),
            dbx_path=dbx_path_cased,
            dbx_id=dbx_id,
            local_path=sync_engine.to_local_path_from_cased(dbx_path_cased),
            rev=rev,
            content_hash=hash_str,
            change_type=change_type,
            change_time=change_time,
            change_dbid=change_dbid,
            status=SyncStatus.Queued,
            size=size,
            completed=0,
        )

    @classmethod
    def from_file_system_event(
        cls, event: FileSystemEvent, sync_engine: "SyncEngine"
    ) -> "SyncEvent":
        """
        Initializes a SyncEvent from the given local file system event.

        :param event: Local file system event.
        :param sync_engine: SyncEngine instance.
        :returns: An instance of this class with attributes populated from the given
            SyncEvent.
        """

        change_dbid = sync_engine.client.account_id
        to_path = get_dest_path(event)
        from_path = None

        if event.event_type == EVENT_TYPE_CREATED:
            change_type = ChangeType.Added
        elif event.event_type == EVENT_TYPE_DELETED:
            change_type = ChangeType.Removed
        elif event.event_type == EVENT_TYPE_MOVED:
            change_type = ChangeType.Moved
            from_path = event.src_path
        elif event.event_type == EVENT_TYPE_MODIFIED:
            change_type = ChangeType.Modified
        else:
            raise RuntimeError(f"Cannot convert {event} to SyncEvent")

        change_time: Optional[float]
        stat: Optional[os.stat_result]

        try:
            stat = os.stat(to_path)
        except OSError:
            stat = None

        if event.is_directory:
            item_type = ItemType.Folder
            size = 0
            try:
                change_time = stat.st_birthtime  # type: ignore
            except AttributeError:
                change_time = None
        else:
            item_type = ItemType.File
            change_time = stat.st_ctime if stat else None
            size = stat.st_size if stat else 0

        # Note: We get the content hash here instead of later, even though the calculation
        # may be slow and ``from_file_system_event`` may be called serially and not from
        # a thread pool. This is because hashing is CPU bound and parallelization would
        # cause large multi-core CPU usage (or result in throttling of our thread-pool).

        return cls(
            direction=SyncDirection.Up,
            item_type=item_type,
            sync_time=time.time(),
            dbx_path=sync_engine.to_dbx_path(to_path),
            local_path=to_path,
            dbx_path_from=sync_engine.to_dbx_path(from_path) if from_path else None,
            local_path_from=from_path,
            content_hash=sync_engine.get_local_hash(to_path),
            change_type=change_type,
            change_time=change_time,
            change_dbid=change_dbid,
            status=SyncStatus.Queued,
            size=size,
            completed=0,
        )


class IndexEntry(Base):  # type: ignore
    """
    Represents an entry in our local sync index. All arguments are used to construct
    instance attributes. All arguments apart from ```content_hash`` and
    ``content_hash_ctime`` are required.

    :param dbx_path_cased: Dropbox path of the item, correctly cased.
    :param dbx_path_lower: Dropbox path of the item in lower case. This acts as a primary
        key for the SQL database since there can only be one entry per case-insensitive
        Dropbox path.
    :param dbx_id: A unique dropbox ID for the file or folder.
    :param item_type: The item type: file or folder.
    :param last_sync: The last time a local change was uploaded. Should be the ctime of
        the local file or folder.
    :param rev: The file revision. Will be 'folder' for folders.
    :param content_hash: A hash representing the file content. Will be 'folder' for
        folders. May be None if not yet calculated.
    :param content_hash_ctime: The ctime for which the content_hash was calculated. If
        this is older than the current ctime, the content_hash will be invalid and has to
        recalculated.
    """

    __tablename__ = "index"

    dbx_path_lower = Column(String, nullable=False, primary_key=True)
    dbx_path_cased = Column(String, nullable=False)
    dbx_id = Column(String, nullable=False)
    item_type = Column(Enum(ItemType), nullable=False)
    last_sync = Column(Float)
    rev = Column(String, nullable=False)
    content_hash = Column(String)

    @property
    def is_file(self) -> bool:
        """Returns True for file changes"""
        return self.item_type == ItemType.File

    @property
    def is_directory(self) -> bool:
        """Returns True for folder changes"""
        return self.item_type == ItemType.Folder

    def __repr__(self):
        return (
            f"<{self.__class__.__name__}(item_type={self.item_type.name}, "
            f"dbx_path='{self.dbx_path_cased}')>"
        )


class HashCacheEntry(Base):  # type: ignore
    """
    An entry in our cache of content hashes.

    :param local_path: The local path for which the hash is stored.
    :param hash_str: The content hash. 'folder' for folders.
    :param mtime: The mtime of the item just before the hash was computed. When the
        current ctime is newer, the hash_str will need to be recalculated.
    """

    __tablename__ = "hash_cache"

    local_path = Column(String, nullable=False, primary_key=True)
    hash_str = Column(String)
    mtime = Column(Float)


class SyncEngine:
    """
    Class that contains methods to sync local file events with Dropbox and vice versa.

    :param client: Dropbox API client instance.
    :param fs_events_handler: File system event handler to inform us of local events.
    """

    sync_errors: Set[SyncError]
    syncing: List[SyncEvent]
    _case_conversion_cache: Dict[str, str]

    _max_history = 30
    _num_threads = min(32, _cpu_count * 3)

    def __init__(self, client: DropboxClient, fs_events_handler: FSEventHandler):

        self.client = client
        self.config_name = self.client.config_name
        self.cancel_pending = Event()
        self.fs_events = fs_events_handler

        self.sync_lock = RLock()
        self._db_lock = RLock()

        self._conf = MaestralConfig(self.config_name)
        self._state = MaestralState(self.config_name)
        self._notifier = MaestralDesktopNotifier.for_config(self.config_name)

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

        # data structures for user information
        self.syncing = []

        # determine file paths
        self._dropbox_path = self._conf.get("main", "path")
        self._mignore_path = osp.join(self._dropbox_path, MIGNORE_FILE)
        self._file_cache_path = osp.join(self._dropbox_path, FILE_CACHE)
        self._db_path = get_data_path("maestral", f"{self.config_name}.db")

        # reset sync state if DB is missing
        if not osp.exists(self._db_path):
            self.remote_cursor = ""

        # initialize SQLite database
        url = sqlalchemy.engine.url.URL(
            drivername="sqlite",
            database=f"file:{self._db_path}",
            query={"check_same_thread": "false", "uri": "true"},
        )
        self._db_engine = create_engine(url)
        with self._database_access(log_errors=True):
            Base.metadata.create_all(self._db_engine)
            Session.configure(bind=self._db_engine)
        self._db_session = Session()

        # load cached properties
        self._is_case_sensitive = is_fs_case_sensitive(get_home_dir())
        self._mignore_rules = self._load_mignore_rules_form_file()
        self._excluded_items = self._conf.get("main", "excluded_items")
        self._max_cpu_percent = self._conf.get("sync", "max_cpu_percent") * _cpu_count

        # caches
        self._case_conversion_cache = dict()

        # clean our file cache
        self.clean_cache_dir()

    # ==== config access =================================================================

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
            self._conf.set("main", "path", path)

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
            self._conf.set("main", "excluded_items", clean_list)

    @staticmethod
    def clean_excluded_items_list(folder_list: List[str]) -> List[str]:
        """
        Removes all duplicates and children of excluded items from the excluded items
        list.

        :param folder_list: Dropbox paths to exclude.
        :returns: Cleaned up items.
        """

        # remove duplicate entries by creating set, strip trailing '/'
        folder_set = set(f.lower().rstrip("/") for f in folder_list)

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
        self._conf.set("app", "max_cpu_percent", percent // _cpu_count)

    # ==== sync state ====================================================================

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
            logger.debug("Remote cursor saved: %s", cursor)

    @property
    def local_cursor(self) -> float:
        """Time stamp from last sync with remote Dropbox. The value is updated and saved
        to the config file on every successful upload of local changes."""
        return self._state.get("sync", "lastsync")

    @local_cursor.setter
    def local_cursor(self, last_sync: float) -> None:
        """Setter: local_cursor"""
        with self.sync_lock:
            logger.debug("Local cursor saved: %s", last_sync)
            self._state.set("sync", "lastsync", last_sync)

    @property
    def last_change(self) -> float:
        """The time stamp of the last file change or 0.0 if there are no file changes in
        our history."""

        with self._database_access():

            res = self._db_session.query(func.max(IndexEntry.last_sync)).first()

            if res:
                return res[0] or 0.0
            else:
                return 0.0

    @property
    def last_reindex(self) -> float:
        """Time stamp of last full indexing. This is used to determine when the next full
        indexing should take place."""
        return self._state.get("sync", "last_reindex")

    @property
    def history(self) -> List[SyncEvent]:
        """All list of all SyncEvents in our history."""
        with self._database_access():
            query = self._db_session.query(SyncEvent)
            return query.order_by(SyncEvent.change_time_or_sync_time).all()

    def clear_sync_history(self) -> None:
        """Clears the sync history."""
        with self._database_access():
            SyncEvent.metadata.drop_all(self._db_engine)
            Base.metadata.create_all(self._db_engine)
            self._db_session.expunge_all()

    # ==== index management ==============================================================

    def get_index(self) -> List[IndexEntry]:
        """
        Returns a copy of the revision index containing the revision numbers for all
        synced files and folders.

        :returns: Copy of revision index.
        """
        with self._database_access():
            return self._db_session.query(IndexEntry).all()

    def get_local_rev(self, dbx_path: str) -> Optional[str]:
        """
        Gets revision number of local file.

        :param dbx_path: Dropbox path.
        :returns: Revision number as str or ``None`` if no local revision number has been
            saved.
        """

        with self._database_access():
            res = (
                self._db_session.query(IndexEntry.rev)
                .filter(IndexEntry.dbx_path_lower == dbx_path.lower())
                .first()
            )

        if res:
            return res[0]
        else:
            return None

    def get_last_sync(self, dbx_path: str) -> float:
        """
        Returns the timestamp of last sync for an individual path.

        :param dbx_path: Dropbox path.
        :returns: Time of last sync.
        """

        with self._database_access():
            res = self._db_session.query(IndexEntry).get(dbx_path.lower())

        if res:
            last_sync = res.last_sync or 0.0
        else:
            last_sync = 0.0

        return max(last_sync, self.local_cursor)

    def get_index_entry(self, dbx_path: str) -> Optional[IndexEntry]:
        """
        Gets the index entry for the given Dropbox path.

        :param dbx_path: Dropbox path.
        :returns: Index entry or ``None`` if no entry exists for the given path.
        """

        with self._database_access():
            return self._db_session.query(IndexEntry).get(dbx_path.lower())

    def get_local_hash(self, local_path: str) -> Optional[str]:
        """
        Computes content hash of a local file.

        :param local_path: Absolute path on local drive.
        :returns: Content hash to compare with Dropbox's content hash, or 'folder' if the
            path points to a directory. ``None`` if there is nothing at the path.
        """

        try:
            stat = os.stat(local_path)
        except (FileNotFoundError, NotADirectoryError):
            # remove any existing cache entries for path
            with self._database_access():
                cache_entry = self._db_session.query(HashCacheEntry).get(local_path)
                if cache_entry:
                    self._db_session.delete(cache_entry)
            return None
        except OSError as err:
            raise os_to_maestral_error(err, local_path=local_path)

        if S_ISDIR(stat.st_mode):
            # take shortcut: return 'folder'
            return "folder"

        mtime: Optional[float] = stat.st_mtime

        with self._database_access():
            # check cache for an up-to-date content hash and return if it exists
            cache_entry = self._db_session.query(HashCacheEntry).get(local_path)

            if cache_entry and cache_entry.mtime == mtime:
                return cache_entry.hash_str

        try:
            hash_str, mtime = content_hash(local_path)
        except OSError as err:
            raise os_to_maestral_error(err, local_path=local_path)

        self.save_local_hash(local_path, hash_str, mtime)

        return hash_str

    def save_local_hash(
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

            cache_entry = self._db_session.query(HashCacheEntry).get(local_path)

            if hash_str:

                if cache_entry:
                    cache_entry.hash_str = hash_str
                    cache_entry.mtime = mtime

                else:
                    cache_entry = HashCacheEntry(
                        local_path=local_path, hash_str=hash_str, mtime=mtime
                    )
                    self._db_session.add(cache_entry)
            else:
                if cache_entry:
                    self._db_session.delete(cache_entry)
                else:
                    pass

            self._db_session.commit()

    def clear_hash_cache(self) -> None:
        """Clears the sync history."""
        with self._database_access():
            HashCacheEntry.metadata.drop_all(self._db_engine)
            Base.metadata.create_all(self._db_engine)
            self._db_session.expunge_all()

    def update_index_from_sync_event(self, event: SyncEvent) -> None:
        """
        Updates the local index from a SyncEvent.

        :param event: SyncEvent from download.
        """

        if event.change_type is not ChangeType.Removed and not event.rev:
            raise ValueError("Rev required to update index")

        dbx_path_lower = event.dbx_path.lower()

        with self._database_access():

            # remove any entries for deleted or moved items

            if event.change_type is ChangeType.Removed:
                self.remove_path_from_index(dbx_path_lower)
            elif event.change_type is ChangeType.Moved:
                self.remove_path_from_index(event.dbx_path_from.lower())

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

                    self._db_session.add(entry)

            self._db_session.commit()

    def update_index_from_dbx_metadata(self, md: Metadata) -> None:
        """
        Updates the local index from Dropbox metadata.

        :param md: Dropbox metadata.
        """

        with self._database_access():

            if isinstance(md, DeletedMetadata):
                self.remove_path_from_index(md.path_lower)

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

                entry = self.get_index_entry(md.path_lower)
                dbx_path_cased = self.correct_case(md.path_display)

                if entry:
                    entry.dbx_id = md.id
                    entry.dbx_path_cased = dbx_path_cased
                    entry.item_type = item_type
                    entry.last_sync = None
                    entry.rev = rev
                    entry.content_hash = hash_str

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

                    self._db_session.add(entry)

            self._db_session.commit()

    def remove_path_from_index(self, dbx_path: str) -> None:
        """
        Removes any local index entries for the given path and all its children.

        :param dbx_path: Dropbox path.
        """

        with self._database_access():

            dbx_path_lower = dbx_path.lower()

            for entry in self.get_index():
                # remove children from index
                if is_equal_or_child(entry.dbx_path_lower, dbx_path_lower):
                    self._db_session.delete(entry)

            self._db_session.commit()

    def clear_index(self) -> None:
        """Clears the revision index."""
        with self._database_access():

            IndexEntry.metadata.drop_all(self._db_engine)
            Base.metadata.create_all(self._db_engine)
            self._db_session.expunge_all()

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
            logger.debug(
                f"Could not load mignore rules from {self.mignore_path}: {err}"
            )
            spec = ""
        return pathspec.PathSpec.from_lines("gitwildmatch", spec.splitlines())

    # ==== helper functions ==============================================================

    @property
    def is_case_sensitive(self) -> bool:
        """Returns ``True`` if the local Dropbox folder is located on a partition with a
        case-sensitive file system, ``False`` otherwise."""
        return self._is_case_sensitive

    def ensure_dropbox_folder_present(self) -> None:
        """
        Checks if the Dropbox folder still exists where we expect it to be.

        :raises: :class:`errors.DropboxDeletedError`
        """

        if not osp.isdir(self.dropbox_path):
            title = "Dropbox folder has been moved or deleted"
            msg = (
                "Please move the Dropbox folder back to its original location "
                "or restart Maestral to set up a new folder."
            )
            raise NoDropboxDirError(title, msg)

    def _ensure_cache_dir_present(self) -> None:
        """
        Checks for or creates a directory at :attr:`file_cache_path`.

        :raises: :class:`errors.CacheDirError`
        """

        err_title = "Cannot create cache directory (errno {})"
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
                if err and not isinstance(err, (FileNotFoundError, IsADirectoryError)):
                    raise CacheDirError(
                        err_title.format(err.errno), err_msg.format(err.filename)
                    )
            except OSError as err:
                raise CacheDirError(
                    err_title.format(err.errno), err_msg.format(err.filename)
                )

            if retries > max_retries:
                raise CacheDirError(
                    "Cannot create cache directory",
                    "Exceeded maximum number of retries",
                )

    def clean_cache_dir(self) -> None:
        """Removes all items in the cache directory."""

        with self.sync_lock:
            err = delete(self._file_cache_path)
            if err and not isinstance(err, (FileNotFoundError, IsADirectoryError)):
                raise CacheDirError(
                    f"Cannot create cache directory (errno {err.errno})",
                    "Please check if you have write permissions for "
                    f"{self._file_cache_path}.",
                )

    def _new_tmp_file(self) -> str:
        """Returns a new temporary file name in our cache directory."""
        self._ensure_cache_dir_present()
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.file_cache_path, delete=False
            ) as f:
                umask = os.umask(0)
                os.umask(umask)
                os.chmod(f.name, 0o777 & ~umask)
                return f.name
        except OSError as err:
            raise CacheDirError(
                f"Cannot create temporary file (errno {err.errno})",
                "Please check if you have write permissions for "
                f"{self._file_cache_path}.",
            )

    def correct_case(self, dbx_path: str) -> str:
        """
        Converts a Dropbox path with correctly cased basename to a fully cased path.

        :param dbx_path: Dropbox path with correctly cased basename, as provided by
            ``Metadata.path_display`` or ``Metadata.name``.
        :returns: Correctly cased Dropbox path.
        """

        dirname, basename = osp.split(dbx_path)

        dbx_path_lower = dbx_path.lower()
        dirname_lower = dirname.lower()

        if dirname == "/":
            path_cased = dbx_path

        else:

            # check in our conversion cache
            parent_path_cased = self._case_conversion_cache.get(dirname_lower)

            if not parent_path_cased:
                # try to get dirname casing from our index
                with self._database_access():
                    parent_entry = self.get_index_entry(dirname_lower)

                if parent_entry:
                    parent_path_cased = parent_entry.dbx_path_cased

                else:
                    # fall back to querying from server
                    md_parent = self.client.get_metadata(dirname_lower)
                    if md_parent:
                        # recurse over parent directories
                        parent_path_cased = self.correct_case(md_parent.path_display)
                    else:
                        # give up
                        parent_path_cased = osp.dirname(dbx_path)

            path_cased = f"{parent_path_cased}/{basename}"

        self._case_conversion_cache[dbx_path_lower] = path_cased

        return path_cased

    def to_dbx_path(self, local_path: str) -> str:
        """
        Converts a local path to a path relative to the Dropbox folder. Casing of the
        given ``local_path`` will be preserved.

        :param local_path: Absolute path on local drive.
        :returns: Relative path with respect to Dropbox folder.
        :raises: :class:`ValueError` the path lies outside of the local Dropbox folder.
        """

        if is_equal_or_child(local_path, self.dropbox_path):
            dbx_path = osp.sep + local_path.replace(self.dropbox_path, "", 1).lstrip(
                osp.sep
            )
            return dbx_path.replace(osp.sep, "/")
        else:
            raise ValueError(
                f'Specified path "{local_path}" is outside of Dropbox '
                f'directory "{self.dropbox_path}"'
            )

    def to_local_path_from_cased(self, dbx_path_cased: str) -> str:
        """
        Converts a correctly cased Dropbox path to the corresponding local path. This is
        more efficient than :meth:`to_local_path` which accepts uncased paths.

        :param dbx_path_cased: Path relative to Dropbox folder, correctly cased.
        :returns: Corresponding local path on drive.
        """

        dbx_path_cased = dbx_path_cased.replace("/", osp.sep).lstrip(osp.sep)

        return osp.join(self.dropbox_path, dbx_path_cased)

    def to_local_path(self, dbx_path: str) -> str:
        """
        Converts a Dropbox path to the corresponding local path. Only the basename must be
        correctly cased. This is slower than :meth:`to_local_path_from_cased`.

        :param dbx_path: Path relative to Dropbox folder, must be correctly cased in its
            basename.
        :returns: Corresponding local path on drive.
        """
        dbx_path_cased = self.correct_case(dbx_path)
        dbx_path_cased = dbx_path_cased.replace("/", osp.sep).lstrip(osp.sep)

        return osp.join(self.dropbox_path, dbx_path_cased)

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
        dbx_path_lower = dbx_path.lower()

        if self.has_sync_errors():
            for error in self.sync_errors.copy():
                assert isinstance(error.dbx_path, str)
                if is_equal_or_child(error.dbx_path.lower(), dbx_path_lower):
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
        path = path.lower().replace(osp.sep, "/")

        # is root folder?
        if path in ("/", ""):
            return True

        dirname, basename = osp.split(path)
        # in excluded files?
        if basename in EXCLUDED_FILE_NAMES:
            return True

        # in excluded dirs?
        # TODO: check if this can be optimised
        dirnames = dirname.split("/")
        if any(excluded_dirname in dirnames for excluded_dirname in EXCLUDED_DIR_NAMES):
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

    def is_excluded_by_user(self, dbx_path: str) -> bool:
        """
        Check if file has been excluded through "selective sync" by the user.

        :param dbx_path: Path relative to Dropbox folder.
        :returns: Whether the path is excluded from download syncing by the user.
        """
        dbx_path = dbx_path.lower()

        return any(is_equal_or_child(dbx_path, path) for path in self.excluded_items)

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
        ) and not self.get_local_rev(event.dbx_path)

    def _is_mignore_path(self, dbx_path: str, is_dir: bool = False) -> bool:

        relative_path = dbx_path.lstrip("/")

        if is_dir:
            relative_path += "/"

        return self.mignore_rules.match_file(relative_path)

    def _slow_down(self) -> None:

        if self._max_cpu_percent == 100:
            return

        if "pool" in current_thread().name:
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
        """
        Handles a sync error. Fills out any missing path information and adds the error to
        the persistent state for later resync.

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
        if err.local_path_dst and not not err.dbx_path:
            err.dbx_path_dst = self.to_dbx_path(err.local_path_dst)

        if err.dbx_path:
            # we have a file / folder associated with the sync error
            file_name = osp.basename(err.dbx_path)
            logger.warning("Could not sync %s", file_name, exc_info=True)
            self.sync_errors.add(err)

            # save download errors to retry later
            if direction == SyncDirection.Down:
                self.download_errors.add(err.dbx_path.lower())
            elif direction == SyncDirection.Up:
                self.upload_errors.add(err.dbx_path.lower())

    @contextmanager
    def _database_access(self, log_errors: bool = False) -> Iterator[None]:
        """
        Synchronises access to the SQLite database. Catches exceptions raised by
        SQLAlchemy and converts them to a MaestralApiError if we know how to handle them.

        :param log_errors: If ``True``, any resulting MaestralApiError is not raised but
            only logged.
        """

        title = None
        new_exc = None

        try:
            with self._db_lock:
                yield
        except (
            sqlalchemy.exc.DatabaseError,
            sqlalchemy.exc.DataError,
            sqlalchemy.exc.IntegrityError,
        ) as exc:
            title = "Database integrity error"
            msg = "Please rebuild the index to continue syncing."
            new_exc = DatabaseError(title, msg).with_traceback(exc.__traceback__)
        except sqlalchemy.exc.OperationalError as exc:
            title = "Database transaction error"
            msg = (
                f'The index file at "{self._db_path}" cannot be read. '
                "Please check that you have sufficient permissions and "
                "rebuild the index if necessary."
            )
            new_exc = DatabaseError(title, msg).with_traceback(exc.__traceback__)
        except sqlalchemy.exc.InternalError as exc:
            title = "Database transaction error"
            msg = "Please restart Maestral to continue syncing."
            new_exc = DatabaseError(title, msg).with_traceback(exc.__traceback__)

        if new_exc:
            if log_errors:
                logger.error(title, exc_info=_exc_info(new_exc))
            else:
                raise new_exc

    # ==== Upload sync ===================================================================

    def upload_local_changes_while_inactive(self) -> None:
        """
        Collects changes while sync has not been running and uploads them to Dropbox.
        Call this method when resuming sync.
        """

        with self.sync_lock:

            logger.info("Indexing local changes...")

            try:
                events, local_cursor = self._get_local_changes_while_inactive()
                logger.debug("Retrieved local changes:\n%s", pprint.pformat(events))
                events = self._clean_local_events(events)
                sync_events = [
                    SyncEvent.from_file_system_event(e, self) for e in events
                ]
            except FileNotFoundError:
                self.ensure_dropbox_folder_present()
                return

            if len(events) > 0:
                self.apply_local_changes(sync_events, local_cursor)
                logger.debug("Uploaded local changes while inactive")
            else:
                self.local_cursor = local_cursor
                logger.debug("No local changes while inactive")

    def _get_local_changes_while_inactive(self) -> Tuple[List[FileSystemEvent], float]:

        # pre-load index for performance
        self.get_index()

        changes = []
        now = time.time()
        snapshot = DirectorySnapshot(self.dropbox_path)
        entries = self.get_index()

        # get lowercase paths
        lowercase_snapshot_paths = {x.lower() for x in snapshot.paths}

        # get modified or added items
        for path in snapshot.paths:
            if path != self.dropbox_path:
                stats = snapshot.stat_info(path)
                # check if item was created or modified since last sync
                # but before we started the FileEventHandler (~now)
                dbx_path_lower = self.to_dbx_path(path).lower()
                ctime_check = now > stats.st_ctime > self.get_last_sync(dbx_path_lower)

                # always upload untracked items, check ctime of tracked items
                local_entry = self.get_index_entry(dbx_path_lower)
                is_modified = local_entry and ctime_check

                if not local_entry:
                    if snapshot.isdir(path):
                        event = DirCreatedEvent(path)
                    else:
                        event = FileCreatedEvent(path)
                    changes.append(event)

                elif is_modified:
                    if snapshot.isdir(path) and local_entry.is_directory:
                        event = DirModifiedEvent(path)
                        changes.append(event)
                    elif not snapshot.isdir(path) and not local_entry.is_directory:
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
        for entry in entries:
            local_path_uncased = (self.dropbox_path + entry.dbx_path_lower).lower()
            if local_path_uncased not in lowercase_snapshot_paths:
                local_path = self.to_local_path_from_cased(entry.dbx_path_cased)
                if entry.is_directory:
                    event = DirDeletedEvent(local_path)
                else:
                    event = FileDeletedEvent(local_path)
                changes.append(event)

        del snapshot
        del lowercase_snapshot_paths

        return changes, now

    def wait_for_local_changes(
        self, timeout: float = 5, delay: float = 1
    ) -> Tuple[List[SyncEvent], float]:
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

        logger.debug("Retrieved local file events:\n%s", pprint.pformat(events))

        events = self._clean_local_events(events)
        sync_events = [SyncEvent.from_file_system_event(e, self) for e in events]

        return sync_events, local_cursor

    def apply_local_changes(
        self, sync_events: List[SyncEvent], local_cursor: float
    ) -> List[SyncEvent]:
        """
        Applies locally detected changes to the remote Dropbox. Changes which should be
        ignored (mignore or always ignored files) are skipped.

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

                for event in sync_events:
                    if event.is_deleted:
                        deleted.append(event)
                    elif event.is_directory and event.is_moved:
                        dir_moved.append(event)
                    else:
                        other.append(event)

                    # housekeeping
                    self.syncing.append(event)

                # apply deleted events first, folder moved events second
                # neither event type requires an actual upload
                if deleted:
                    logger.info("Uploading deletions...")

                with ThreadPoolExecutor(
                    max_workers=self._num_threads,
                    thread_name_prefix="maestral-upload-pool",
                ) as executor:
                    fs = (
                        executor.submit(self._create_remote_entry, e) for e in deleted
                    )

                    n_items = len(deleted)
                    for f, n in zip(as_completed(fs), range(1, n_items + 1)):
                        throttled_log(logger, f"Deleting {n}/{n_items}...")
                        results.append(f.result())

                if dir_moved:
                    logger.info("Moving folders...")

                for event in dir_moved:
                    logger.info(f"Moving {event.dbx_path_from}...")
                    res = self._create_remote_entry(event)
                    results.append(res)

                # apply file created events in parallel since order does not matter
                with ThreadPoolExecutor(
                    max_workers=self._num_threads,
                    thread_name_prefix="maestral-upload-pool",
                ) as executor:
                    fs = (executor.submit(self._create_remote_entry, e) for e in other)

                    n_items = len(other)
                    for f, n in zip(as_completed(fs), range(1, n_items + 1)):
                        throttled_log(logger, f"Uploading {n}/{n_items}...")
                        results.append(f.result())

                self._clean_history()

            if not self.cancel_pending.is_set():
                # always save local cursor if not aborted by user,
                # failed uploads will be tracked and retried individually
                self.local_cursor = local_cursor

            return results

    def _filter_excluded_changes_local(
        self, sync_events: List[SyncEvent]
    ) -> Tuple[List[SyncEvent], List[SyncEvent]]:
        """
        Checks for and removes file events referring to items which are excluded from
        syncing. Called by :meth:`apply_local_changes`.

        :param sync_events: List of file events.
        :returns: (``events_filtered``, ``events_excluded``)
        """

        events_filtered = []
        events_excluded = []

        for event in sync_events:

            if self.is_excluded(event.dbx_path):
                events_excluded.append(event)
            elif self.is_mignore(event):
                # moved events with an ignored path are
                # already split into deleted, created pairs
                events_excluded.append(event)
            else:
                events_filtered.append(event)

        logger.debug("Filtered local file events:\n%s", pprint.pformat(events_filtered))

        return events_filtered, events_excluded

    def _clean_local_events(
        self, events: List[FileSystemEvent]
    ) -> List[FileSystemEvent]:
        """
        Takes local file events within and cleans them up so that there is only a single
        event per path. Collapses moved and deleted events of folders with those of their
        children. Called by :meth:`wait_for_local_changes`.

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

        for path, events in histories.items():
            if len(events) == 1:
                unique_events.append(events[0])
            else:

                n_created = len(
                    [e for e in events if e.event_type == EVENT_TYPE_CREATED]
                )
                n_deleted = len(
                    [e for e in events if e.event_type == EVENT_TYPE_DELETED]
                )

                if n_created > n_deleted:  # item was created
                    if events[-1].is_directory:
                        unique_events.append(DirCreatedEvent(path))
                    else:
                        unique_events.append(FileCreatedEvent(path))
                elif n_created < n_deleted:  # item was deleted
                    if events[0].is_directory:
                        unique_events.append(DirDeletedEvent(path))
                    else:
                        unique_events.append(FileDeletedEvent(path))
                else:

                    first_created_idx = next(
                        iter(
                            i
                            for i, e in enumerate(events)
                            if e.event_type == EVENT_TYPE_CREATED
                        ),
                        -1,
                    )
                    first_deleted_idx = next(
                        iter(
                            i
                            for i, e in enumerate(events)
                            if e.event_type == EVENT_TYPE_DELETED
                        ),
                        -1,
                    )

                    if n_created == 0 or first_deleted_idx < first_created_idx:
                        # item was modified
                        if events[0].is_directory and events[-1].is_directory:
                            unique_events.append(DirModifiedEvent(path))
                        elif not events[0].is_directory and not events[-1].is_directory:
                            unique_events.append(FileModifiedEvent(path))
                        elif events[0].is_directory:
                            unique_events.append(DirDeletedEvent(path))
                            unique_events.append(FileCreatedEvent(path))
                        elif events[-1].is_directory:
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
        for event in cleaned_events:
            if hasattr(event, "id"):
                try:
                    moved_events[event.id].append(event)
                except KeyError:
                    moved_events[event.id] = [event]

        for event_list in moved_events.values():
            if len(event_list) == 2:
                src_path = next(
                    e.src_path for e in event_list if e.event_type == EVENT_TYPE_DELETED
                )
                dest_path = next(
                    e.src_path for e in event_list if e.event_type == EVENT_TYPE_CREATED
                )
                if event_list[0].is_directory:
                    new_event = DirMovedEvent(src_path, dest_path)
                else:
                    new_event = FileMovedEvent(src_path, dest_path)

                if not self._should_split_excluded(new_event):
                    cleaned_events.difference_update(event_list)
                    cleaned_events.add(new_event)

        # COMBINE MOVED AND DELETED EVENTS OF FOLDERS AND THEIR CHILDREN INTO ONE EVENT

        # Avoid nested iterations over all events here, they are on the order of O(n^2)
        # which becomes costly when the user moves or deletes folder with a large number
        # of children. Benchmark: aim to stay below 1 sec for 20,000 nested events on
        # representative laptops.

        # 1) combine moved events of folders and their children into one event
        dir_moved_paths = set(
            (e.src_path, e.dest_path)
            for e in cleaned_events
            if isinstance(e, DirMovedEvent)
        )

        if len(dir_moved_paths) > 0:
            child_moved_events: Dict[Tuple[str, str], List[FileSystemEvent]] = dict()
            for paths in dir_moved_paths:
                child_moved_events[paths] = []

            for event in cleaned_events:
                if event.event_type == EVENT_TYPE_MOVED:
                    try:
                        dirnames = (
                            osp.dirname(event.src_path),
                            osp.dirname(event.dest_path),
                        )
                        child_moved_events[dirnames].append(event)
                    except KeyError:
                        pass

            for event_list in child_moved_events.values():
                cleaned_events.difference_update(event_list)

        # 2) combine deleted events of folders and their children to one event
        dir_deleted_paths = set(
            e.src_path for e in cleaned_events if isinstance(e, DirDeletedEvent)
        )

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

        logger.debug(
            "Cleaned up local file events:\n%s", pprint.pformat(cleaned_events)
        )

        del events
        del unique_events

        return list(cleaned_events)

    def _should_split_excluded(self, event: Union[FileMovedEvent, DirMovedEvent]):

        if event.event_type != EVENT_TYPE_MOVED:
            raise ValueError("Can only split moved events")

        dbx_src_path = self.to_dbx_path(event.src_path)
        dbx_dest_path = self.to_dbx_path(event.dest_path)

        if (
            self.is_excluded(event.src_path)
            or self.is_excluded(event.dest_path)
            or self.is_excluded_by_user(dbx_src_path)
            or self.is_excluded_by_user(dbx_dest_path)
        ):
            return True

        elif len(self.mignore_rules.patterns) == 0:
            return False
        else:
            dbx_src_path = self.to_dbx_path(event.src_path)
            dbx_dest_path = self.to_dbx_path(event.dest_path)

            return self._is_mignore_path(
                dbx_src_path, event.is_directory
            ) or self._is_mignore_path(dbx_dest_path, event.is_directory)

    def _handle_case_conflict(self, event: SyncEvent) -> bool:
        """
        Checks for other items in the same directory with same name but a different
        case. Renames items if necessary. Only needed for case sensitive file systems.

        :param event: SyncEvent for local created or moved event.
        :returns: Whether a case conflict was detected and handled.
        """

        if not self.is_case_sensitive:
            return False

        if not (event.is_added or event.is_moved):
            return False

        dirname, basename = osp.split(event.local_path)

        # check number of paths with the same case
        if len(cased_path_candidates(basename, root=dirname)) > 1:

            local_path_cc = generate_cc_name(
                event.local_path,
                suffix="case conflict",
                is_fs_case_sensitive=self.is_case_sensitive,
            )

            event_cls = DirMovedEvent if osp.isdir(event.local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(event.local_path, local_path_cc)):
                exc = move(event.local_path, local_path_cc)
                if exc:
                    raise os_to_maestral_error(exc, local_path=local_path_cc)

                self.rescan(local_path_cc)

            logger.info(
                'Case conflict: renamed "%s" to "%s"', event.local_path, local_path_cc
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

        if self.is_excluded_by_user(event.dbx_path):
            local_path_cc = generate_cc_name(
                event.local_path,
                suffix="selective sync conflict",
                is_fs_case_sensitive=self.is_case_sensitive,
            )

            event_cls = DirMovedEvent if osp.isdir(event.local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(event.local_path, local_path_cc)):
                exc = move(event.local_path, local_path_cc)
                if exc:
                    raise os_to_maestral_error(exc, local_path=local_path_cc)

                self.rescan(local_path_cc)

            logger.info(
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
        sync errors belonging to that path. Any :class:`errors.SyncError` will be caught
        and logged as appropriate.

        :param event: SyncEvent for local file event.
        :returns: SyncEvent with updated status.
        """

        if self.cancel_pending.is_set():
            event.status = SyncStatus.Aborted
            self.syncing.remove(event)
            return event

        self._slow_down()

        self.clear_sync_error(local_path=event.local_path)
        self.clear_sync_error(local_path=event.local_path_from)
        event.status = SyncStatus.Syncing

        try:

            if event.is_added:
                res = self._on_local_created(event)
            elif event.is_moved:
                res = self._on_local_moved(event)
            elif event.is_changed:
                res = self._on_local_modified(event)
            elif event.is_deleted:
                res = self._on_local_deleted(event)
            else:
                res = None

            if res:
                event.status = SyncStatus.Done
            else:
                event.status = SyncStatus.Skipped

        except SyncError as err:
            self._handle_sync_error(err, direction=SyncDirection.Up)
            event.status = SyncStatus.Failed
        finally:
            self.syncing.remove(event)

        # add to history database
        if event.status == SyncStatus.Done:
            with self._database_access():
                self._db_session.add(event)

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

    def _on_local_moved(self, event: SyncEvent) -> Optional[SyncEvent]:
        """
        Call when a local item is moved.

        Keep in mind that we may be moving a whole tree of items. But its better deal with
        the complexity than to delete and re-uploading everything. Thankfully, in case of
        directories, we always process the top-level first. Trying to move the children
        will then be delegated to `on_create` (because the old item no longer lives on
        Dropbox) and that won't upload anything because file contents have remained the
        same.

        :param event: SyncEvent for local moved event.
        :raises: :class:`errors.MaestralApiError`
        :returns: SyncEvent for created remote item at destination.
        """

        if self._handle_selective_sync_conflict(event):
            return None
        if self._handle_case_conflict(event):
            return None

        dbx_path_from = cast(str, event.dbx_path_from)
        md_from_old = self.client.get_metadata(dbx_path_from)

        # If not on Dropbox, e.g., because its old name was invalid,
        # create it instead of moving it.
        if not md_from_old:
            if event.is_directory:
                new_event = DirCreatedEvent(event.local_path)
            else:
                new_event = FileCreatedEvent(event.local_path)

            new_sync_event = SyncEvent.from_file_system_event(new_event, self)

            return self._on_local_created(new_sync_event)

        md_to_new = self.client.move(dbx_path_from, event.dbx_path, autorename=True)

        self.remove_path_from_index(dbx_path_from)

        if md_to_new.path_lower != event.dbx_path.lower():
            # TODO: test this
            # conflicting copy created during upload, mirror remote changes locally
            local_path_cc = self.to_local_path(md_to_new.path_display)
            event_cls = DirMovedEvent if osp.isdir(event.local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(event.local_path, local_path_cc)):
                exc = move(event.local_path, local_path_cc)
                if exc:
                    raise os_to_maestral_error(
                        exc, local_path=local_path_cc, dbx_path=md_to_new.path_display
                    )

            # Delete entry of old path but don't update entry for new path here. This will
            # force conflict resolution on download in case of intermittent changes.
            self.remove_path_from_index(event.dbx_path)
            logger.info(
                'Upload conflict: renamed "%s" to "%s"',
                event.dbx_path,
                md_to_new.path_display,
            )

        else:
            self._update_index_recursive(md_to_new)
            logger.debug('Moved "%s" to "%s" on Dropbox', dbx_path_from, event.dbx_path)

        return SyncEvent.from_dbx_metadata(md_to_new, self)

    def _update_index_recursive(self, md):

        self.update_index_from_dbx_metadata(md)

        if isinstance(md, FolderMetadata):
            result = self.client.list_folder(md.path_lower, recursive=True)
            for md in result.entries:
                self.update_index_from_dbx_metadata(md)

    def _on_local_created(self, event: SyncEvent) -> Optional[SyncEvent]:
        """
        Call when a local item is created.

        :param event: SyncEvent corresponding to local created event.
        :raises: :class:`errors.MaestralApiError`
        :returns: SyncEvent for created remote item or None if no remote item is created.
        """

        if self._handle_selective_sync_conflict(event):
            return None
        if self._handle_case_conflict(event):
            return None

        self._wait_for_creation(event.local_path)

        if event.is_directory:
            try:
                md_new = self.client.make_dir(event.dbx_path, autorename=False)
            except FolderConflictError:
                logger.debug(
                    'No conflict for "%s": the folder already exists', event.local_path
                )
                try:
                    md = self.client.get_metadata(event.dbx_path)
                    if isinstance(md, FolderMetadata):
                        self.update_index_from_dbx_metadata(md)
                except NotFoundError:
                    pass

                return None
            except FileConflictError:
                md_new = self.client.make_dir(event.dbx_path, autorename=True)

        else:
            # check if file already exists with identical content
            md_old = self.client.get_metadata(event.dbx_path)
            if isinstance(md_old, FileMetadata):
                if event.content_hash == md_old.content_hash:
                    # file hashes are identical, do not upload
                    self.update_index_from_dbx_metadata(md_old)
                    return None

            local_entry = self.get_index_entry(event.dbx_path)

            if not local_entry:
                # file is new to us, let Dropbox rename it if something is in the way
                mode = dropbox.files.WriteMode.add
            elif local_entry.is_directory:
                # try to overwrite the destination, this will fail...
                mode = dropbox.files.WriteMode.overwrite
            else:
                # file has been modified, update remote if matching rev,
                # create conflict otherwise
                logger.debug(
                    '"%s" appears to have been created but we are '
                    "already tracking it",
                    event.dbx_path,
                )
                mode = dropbox.files.WriteMode.update(local_entry.rev)
            try:
                md_new = self.client.upload(
                    event.local_path,
                    event.dbx_path,
                    autorename=True,
                    mode=mode,
                    sync_event=event,
                )
            except NotFoundError:
                logger.debug(
                    'Could not upload "%s": the item does not exist', event.local_path
                )
                return None

        if md_new.path_lower != event.dbx_path.lower():
            # conflicting copy created during upload, mirror remote changes locally
            local_path_cc = self.to_local_path(md_new.path_display)
            event_cls = DirMovedEvent if osp.isdir(event.local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(event.local_path, local_path_cc)):
                exc = move(event.local_path, local_path_cc)
                if exc:
                    raise os_to_maestral_error(
                        exc, local_path=local_path_cc, dbx_path=md_new.path_display
                    )

            # Delete entry of old path but don't update entry for new path here. This will
            # force conflict resolution on download in case of intermittent changes.
            self.remove_path_from_index(event.dbx_path)
            logger.debug(
                'Upload conflict: renamed "%s" to "%s"',
                event.dbx_path,
                md_new.path_lower,
            )
        else:
            # everything went well, update index
            self.update_index_from_dbx_metadata(md_new)
            logger.debug('Created "%s" on Dropbox', event.dbx_path)

        return SyncEvent.from_dbx_metadata(md_new, self)

    def _on_local_modified(self, event: SyncEvent) -> Optional[SyncEvent]:
        """
        Call when local item is modified.

        :param event: SyncEvent for local modified event.
        :raises: :class:`errors.MaestralApiError`
        :returns: SyncEvent corresponding to modified remote item or None if no remote item
            is modified.
        """

        if event.is_directory:  # ignore directory modified events
            return None

        self._wait_for_creation(event.local_path)

        # check if item already exists with identical content
        md_old = self.client.get_metadata(event.dbx_path)
        if isinstance(md_old, FileMetadata):
            if event.content_hash == md_old.content_hash:
                # file hashes are identical, do not upload
                self.update_index_from_dbx_metadata(md_old)
                logger.debug(
                    'Modification of "%s" detected but file content is '
                    "the same as on Dropbox",
                    event.dbx_path,
                )
                return None

        local_entry = self.get_index_entry(event.dbx_path)

        if not local_entry:
            logger.debug(
                '"%s" appears to have been modified but cannot ' "find old revision",
                event.dbx_path,
            )
            mode = dropbox.files.WriteMode.add
        elif local_entry.is_directory:
            mode = dropbox.files.WriteMode.overwrite
        else:
            mode = dropbox.files.WriteMode.update(local_entry.rev)

        try:
            md_new = self.client.upload(
                event.local_path,
                event.dbx_path,
                autorename=True,
                mode=mode,
                sync_event=event,
            )
        except NotFoundError:
            logger.debug(
                'Could not upload "%s": the item does not exist', event.dbx_path
            )
            return None

        if md_new.path_lower != event.dbx_path.lower():
            # conflicting copy created during upload, mirror remote changes locally
            local_path_cc = self.to_local_path(md_new.path_display)
            with self.fs_events.ignore(FileMovedEvent(event.local_path, local_path_cc)):
                try:
                    os.rename(event.local_path, local_path_cc)
                except OSError:
                    with self.fs_events.ignore(FileDeletedEvent(event.local_path)):
                        delete(event.local_path)

            # Delete revs of old path but don't set revs for new path here. This will
            # force conflict resolution on download in case of intermittent changes.
            self.remove_path_from_index(event.dbx_path)
            logger.debug(
                'Upload conflict: renamed "%s" to "%s"',
                event.dbx_path,
                md_new.path_lower,
            )
        else:
            # everything went well, save new revs
            self.update_index_from_dbx_metadata(md_new)
            logger.debug('Uploaded modified "%s" to Dropbox', md_new.path_lower)

        return SyncEvent.from_dbx_metadata(md_new, self)

    def _on_local_deleted(self, event: SyncEvent) -> Optional[SyncEvent]:
        """
        Call when local item is deleted. We try not to delete remote items which have been
        modified since the last sync.

        :param event: SyncEvent for local deletion.
        :raises: :class:`errors.MaestralApiError`
        :returns: SyncEvent for deleted remote item or None if no remote item is deleted.
        """

        if self.is_excluded_by_user(event.dbx_path):
            logger.debug(
                'Not deleting "%s": is excluded by selective sync', event.dbx_path
            )
            return None

        local_rev = self.get_local_rev(event.dbx_path)

        md = self.client.get_metadata(event.dbx_path, include_deleted=True)

        if event.is_directory and isinstance(md, FileMetadata):
            logger.debug(
                'Expected folder at "%s" but found a file instead, checking '
                "which one is newer",
                md.path_display,
            )
            # don't delete a remote file if it was modified since last sync
            if md.server_modified.timestamp() >= self.get_last_sync(event.dbx_path):
                logger.debug(
                    'Skipping deletion: remote item "%s" has been modified '
                    "since last sync",
                    md.path_display,
                )
                # mark local folder as untracked
                self.remove_path_from_index(event.dbx_path)
                return None

        if event.is_file and isinstance(md, FolderMetadata):
            # don't delete a remote folder if we were expecting a file
            # TODO: Delete the folder if its children did not change since last sync.
            #   Is there a way of achieving this without listing the folder or listing
            #   all changes and checking when they occurred?
            logger.debug(
                'Skipping deletion: expected file at "%s" but found a '
                "folder instead",
                md.path_display,
            )
            # mark local file as untracked
            self.remove_path_from_index(event.dbx_path)
            return None

        try:
            # will only perform delete if Dropbox remote rev matches `local_rev`
            md_deleted = self.client.remove(
                event.dbx_path, parent_rev=local_rev if event.is_file else None
            )
        except NotFoundError:
            logger.debug(
                'Could not delete "%s": the item no longer exists on Dropbox',
                event.dbx_path,
            )
            md_deleted = None
        except PathError:
            logger.debug(
                'Could not delete "%s": the item has been changed ' "since last sync",
                event.dbx_path,
            )
            md_deleted = None

        # remove revision metadata
        self.remove_path_from_index(event.dbx_path)

        if md_deleted:
            return SyncEvent.from_dbx_metadata(md_deleted, self)
        else:
            return None

    # ==== Download sync =================================================================

    def get_remote_folder(self, dbx_path: str = "/") -> List[SyncEvent]:
        """
        Gets all files/folders from Dropbox and writes them to the local folder
        :attr:`dropbox_path`. Call this method on first run of the Maestral. Indexing and
        downloading may take several minutes, depending on the size of the user's Dropbox
        folder.

        :param dbx_path: Path relative to Dropbox folder. Defaults to root ('/').
        :returns: Whether download was successful.
        """

        with self.sync_lock:

            dbx_path = dbx_path or "/"
            is_dbx_root = dbx_path == "/"
            results = []

            if is_dbx_root:
                logger.info("Downloading your Dropbox")
            else:
                logger.info("Downloading %s", dbx_path)

            if any(is_child(folder, dbx_path) for folder in self.excluded_items):
                # if there are excluded subfolders, index and download only included
                recursive = False
            else:
                # else index all at once
                recursive = True

            # get a cursor and list the folder content
            try:
                cursor = self.client.get_latest_cursor(dbx_path)
                root_result = self.client.list_folder(
                    dbx_path, recursive=recursive, include_deleted=False
                )
            except SyncError as e:
                self._handle_sync_error(e, direction=SyncDirection.Down)
                # TODO: return failed SyncEvent?
                return []

            # convert metadata to sync_events
            root_result.entries.sort(key=lambda x: x.path_lower.count("/"))
            sync_events = [
                SyncEvent.from_dbx_metadata(md, self) for md in root_result.entries
            ]

            # download top-level folders / files first
            res = self.apply_remote_changes(sync_events, cursor=None)
            results.extend(res)

            if not recursive:
                # download sub-folders if not excluded
                for md in root_result.entries:
                    if isinstance(md, FolderMetadata) and not self.is_excluded_by_user(
                        md.path_display
                    ):
                        results.extend(self.get_remote_folder(md.path_display))

            if is_dbx_root:
                # always save remote cursor if this is the root folder,
                # failed downloads will be tracked and retried individually
                self.remote_cursor = cursor
                self._state.set("sync", "last_reindex", time.time())

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
            event = SyncEvent.from_dbx_metadata(md, self)

            if event.is_directory:
                results = self.get_remote_folder(dbx_path)
            else:
                self.syncing.append(event)
                results = [self._create_local_entry(event)]

            success = all(
                e.status in (SyncStatus.Done, SyncStatus.Skipped) for e in results
            )

            if success:
                self.pending_downloads.discard(dbx_path.lower())

            return results

    def wait_for_remote_changes(
        self, last_cursor: str, timeout: int = 40, delay: float = 2
    ) -> bool:
        """
        Blocks until changes to the remote Dropbox are available.

        :param last_cursor: Cursor form last sync.
        :param timeout: Timeout in seconds before returning even if there are no changes.
            Dropbox adds random jitter of up to 90 sec to this value.
        :param delay: Delay in sec to wait for subsequent changes that may be duplicates.
            This delay is typically only necessary folders are shared / un-shared with
            other Dropbox accounts.
        """
        logger.debug("Waiting for remote changes since cursor:\n%s", last_cursor)
        has_changes = self.client.wait_for_remote_changes(last_cursor, timeout=timeout)
        time.sleep(delay)
        logger.debug("Detected remote changes: %s", has_changes)
        return has_changes

    def list_remote_changes(self, last_cursor: str) -> Tuple[List[SyncEvent], str]:
        """
        Lists remote changes since the last download sync.

        :param last_cursor: Cursor from last download sync.
        :returns: Tuple with remote changes and corresponding cursor
        """
        changes = self.client.list_remote_changes(last_cursor)
        logger.debug("Listed remote changes:\n%s", entries_to_str(changes.entries))
        clean_changes = self._clean_remote_changes(changes)
        logger.debug(
            "Cleaned remote changes:\n%s", entries_to_str(clean_changes.entries)
        )

        sync_events = [SyncEvent.from_dbx_metadata(md, self) for md in changes.entries]
        return sync_events, changes.cursor

    def apply_remote_changes(
        self, sync_events: List[SyncEvent], cursor: Optional[str]
    ) -> List[SyncEvent]:
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
        changes_included, changes_excluded = self._filter_excluded_changes_remote(
            sync_events
        )

        # remove deleted item and its children from the excluded list
        for event in changes_excluded:
            if event.is_deleted:
                new_excluded = [
                    path
                    for path in self.excluded_items
                    if not is_equal_or_child(path, event.dbx_path.lower())
                ]

                self.excluded_items = new_excluded

        # sort changes into folders, files and deleted
        folders: List[SyncEvent] = []
        files: List[SyncEvent] = []
        deleted: List[SyncEvent] = []

        for event in changes_included:

            if event.is_deleted:
                deleted.append(event)
            elif event.is_directory:
                folders.append(event)
            elif event.is_file:
                files.append(event)

            # housekeeping
            self.syncing.append(event)

        # sort according to path hierarchy
        # do not create sub-folder / file before parent exists
        deleted.sort(key=lambda x: x.dbx_path.count("/"))
        folders.sort(key=lambda x: x.dbx_path.count("/"))
        files.sort(key=lambda x: x.dbx_path.count("/"))

        results = []  # local list of all changes

        # apply deleted items
        if deleted:
            logger.info("Applying deletions...")
        for item in deleted:
            res = self._create_local_entry(item)
            results.append(res)

        # create local folders, start with top-level and work your way down
        if folders:
            logger.info("Creating folders...")
        for folder in folders:
            res = self._create_local_entry(folder)
            results.append(res)

        # apply created files
        with ThreadPoolExecutor(
            max_workers=self._num_threads, thread_name_prefix="maestral-download-pool"
        ) as executor:
            fs = (executor.submit(self._create_local_entry, file) for file in files)

            n_files = len(files)
            for f, n in zip(as_completed(fs), range(1, n_files + 1)):
                throttled_log(logger, f"Downloading {n}/{n_files}...")
                results.append(f.result())

        if cursor and not self.cancel_pending.is_set():
            # always save remote cursor if not aborted by user,
            # failed downloads will be tracked and retried individually
            self.remote_cursor = cursor

        self._clean_history()

        return results

    def notify_user(self, sync_events: List[SyncEvent]) -> None:
        """
        Shows a desktop notification for the given file changes.

        :param sync_events: List of SyncEvents from download sync.
        """

        callback: Optional[Callable]

        changes = [e for e in sync_events if e.status != SyncStatus.Skipped]

        # get number of remote changes
        n_changed = len(changes)

        if n_changed == 0:
            return

        # find out who changed the item(s), show the user name if its only a single user
        user_name: Optional[str]
        dbid_list = set(e.change_dbid for e in changes)
        if len(dbid_list) == 1:
            # all files have been modified by the same user
            dbid = dbid_list.pop()
            if dbid == self.client.account_id:
                user_name = "You"
            else:
                try:
                    account_info = self.client.get_account_info(dbid)
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

        else:

            if all(e.change_type == sync_events[0].change_type for e in sync_events):
                change_type = sync_events[0].change_type.value
            else:
                change_type = "changed"

            if all(e.item_type == ItemType.File for e in sync_events):
                file_name = f"{n_changed} files"
            elif all(e.item_type == ItemType.Folder for e in sync_events):
                file_name = f"{n_changed} folders"
            else:
                file_name = f"{n_changed} items"

            callback = None

        if change_type == ChangeType.Removed.value:

            def callback():
                # show dropbox website with deleted files
                click.launch("https://www.dropbox.com/deleted_files")

        if user_name:
            msg = f"{user_name} {change_type} {file_name}"
        else:
            msg = f"{file_name} {change_type}"

        self._notifier.notify("Items synced", msg, on_click=callback)

    def _filter_excluded_changes_remote(
        self, changes: List[SyncEvent]
    ) -> Tuple[List[SyncEvent], List[SyncEvent]]:
        """
        Removes all excluded items from the given list of changes.

        :param changes: List of SyncEvents.
        :returns: Tuple with items to keep and items to discard.
        """
        items_to_keep = []
        items_to_discard = []

        for item in changes:
            if self.is_excluded_by_user(item.dbx_path) or self.is_excluded(
                item.dbx_path
            ):
                items_to_discard.append(item)
            else:
                items_to_keep.append(item)

        return items_to_keep, items_to_discard

    def _check_download_conflict(self, event: SyncEvent) -> Conflict:
        """
        Check if a local item is conflicting with remote change. The equivalent check when
        uploading and a change will be carried out by Dropbox itself.

        Checks are carried out against our index, reflecting the latest sync state.

        :param event: Download SyncEvent.
        :returns: Conflict check result.
        :raises: :class:`errors.MaestralApiError`
        """

        local_rev = self.get_local_rev(event.dbx_path)

        if event.rev == local_rev:
            # Local change has the same rev. The local item (or deletion) be newer and not
            # yet synced or identical to the remote state. Don't overwrite.
            logger.debug(
                'Equal revs for "%s": local item is the same or newer '
                "than on Dropbox",
                event.dbx_path,
            )
            return Conflict.LocalNewerOrIdentical

        else:
            # Dropbox server version has a different rev, likely is newer. If the local
            # version has been modified while sync was stopped, those changes will be
            # uploaded before any downloads can begin. Conflict resolution will then be
            # handled by Dropbox. If the local version has been modified while sync was
            # running but changes were not uploaded before the remote version was changed
            # as well, the local ctime will be newer than last_sync:
            # (a) The upload of the changed file has already started. Upload thread will
            #     hold the lock and we won't be here checking for conflicts.
            # (b) The upload has not started yet. Manually check for conflict.

            local_hash = self.get_local_hash(event.local_path)

            if event.content_hash == local_hash:
                logger.debug(
                    'Equal content hashes for "%s": no conflict', event.dbx_path
                )
                return Conflict.Identical
            elif any(
                is_equal_or_child(p, event.dbx_path.lower()) for p in self.upload_errors
            ):
                logger.debug(
                    'Unresolved upload error for "%s": conflict', event.dbx_path
                )
                return Conflict.Conflict
            elif self._get_ctime(event.local_path) <= self.get_last_sync(
                event.dbx_path
            ):
                logger.debug(
                    'Ctime is older than last sync for "%s": remote item ' "is newer",
                    event.dbx_path,
                )
                return Conflict.RemoteNewer
            elif not event.rev:
                logger.debug(
                    'No remote rev for "%s": Local item has been modified '
                    "since remote deletion",
                    event.dbx_path,
                )
                return Conflict.LocalNewerOrIdentical
            else:
                logger.debug(
                    'Ctime is newer than last sync for "%s": conflict', event.dbx_path
                )
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

    def _clean_remote_changes(
        self, changes: dropbox.files.ListFolderResult
    ) -> dropbox.files.ListFolderResult:
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
                local_entry = self.get_index_entry(last_event.path_lower)
                was_dir = local_entry and local_entry.is_directory

                # Dropbox guarantees that applying events in the provided order
                # will reproduce the state in the cloud. We therefore keep only
                # the last event, unless there is a change in item type.
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
        Any :class:`errors.MaestralApiError` will be caught and logged as appropriate.
        Entries in the local index are created after successful completion.

        :param event: Dropbox metadata.
        :returns: Copy of the Dropbox metadata if the change was applied successfully,
            ``True`` if the change already existed, ``False`` in case of a
            :class:`errors.SyncError` and ``None`` if cancelled.
        """

        if self.cancel_pending.is_set():
            event.status = SyncStatus.Aborted
            self.syncing.remove(event)
            return event

        self._slow_down()

        self.clear_sync_error(dbx_path=event.dbx_path)
        event.status = SyncStatus.Syncing

        try:
            if event.is_deleted:
                res = self._on_remote_deleted(event)
            elif event.is_file:
                res = self._on_remote_file(event)
            elif event.is_directory:
                res = self._on_remote_folder(event)
            else:
                res = None

            if res:
                event.status = SyncStatus.Done
            else:
                event.status = SyncStatus.Skipped

        except SyncError as e:
            self._handle_sync_error(e, direction=SyncDirection.Down)
            event.status = SyncStatus.Failed
        finally:
            self.syncing.remove(event)

        # add to history database
        if event.status == SyncStatus.Done:
            with self._database_access():
                self._db_session.add(event)

        return event

    def _on_remote_file(self, event: SyncEvent) -> Optional[SyncEvent]:
        """
        Applies a remote file change or creation locally.

        :param event: SyncEvent for file download.
        :returns: SyncEvent corresponding to local item or None if no local changes
            are made.
        """

        self._apply_case_change(event)

        # Store the new entry at the given path in your local state.
        # If the required parent folders don’t exist yet, create them.
        # If there’s already something else at the given path,
        # replace it and remove all its children.

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
            md = self.client.download(f"rev:{event.rev}", tmp_fname, sync_event=event)
            event = SyncEvent.from_dbx_metadata(md, self)
        except SyncError as err:
            # replace rev number with path
            err.dbx_path = event.dbx_path
            raise err

        # re-check for conflict and move the conflict
        # out of the way if anything has changed
        if self._check_download_conflict(event) == Conflict.Conflict:
            new_local_path = generate_cc_name(
                local_path, is_fs_case_sensitive=self.is_case_sensitive
            )
            event_cls = DirMovedEvent if osp.isdir(local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(local_path, new_local_path)):
                exc = move(local_path, new_local_path)

            if exc:
                raise os_to_maestral_error(exc, local_path=new_local_path)

            logger.debug(
                'Download conflict: renamed "%s" to "%s"', local_path, new_local_path
            )
            self.rescan(new_local_path)

        if osp.isdir(local_path):
            with self.fs_events.ignore(DirDeletedEvent(local_path)):
                delete(local_path)

        # check if we should preserve permissions of destination file
        old_entry = self.get_index_entry(event.dbx_path)

        if old_entry and event.dbx_id == old_entry.dbx_id:
            preserve_permissions = True
        else:
            preserve_permissions = False

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

            exc = move(
                tmp_fname, local_path, preserve_dest_permissions=preserve_permissions
            )

        if exc:
            raise os_to_maestral_error(
                exc, dbx_path=event.dbx_path, local_path=local_path
            )

        self.update_index_from_sync_event(event)
        self.save_local_hash(event.local_path, event.content_hash, mtime)

        logger.debug('Created local file "%s"', event.dbx_path)

        return event

    def _on_remote_folder(self, event: SyncEvent) -> Optional[SyncEvent]:
        """
        Applies a remote folder creation locally.

        :param event: SyncEvent for folder download.
        :returns: SyncEvent corresponding to local item or None if no local changes
            are made.
        """

        self._apply_case_change(event)

        # Store the new entry at the given path in your local state.
        # If the required parent folders don’t exist yet, create them.
        # If there’s already something else at the given path,
        # replace it but leave the children as they are.

        conflict_check = self._check_download_conflict(event)

        if conflict_check is Conflict.Identical:
            self.update_index_from_sync_event(event)
            return None
        elif conflict_check is Conflict.LocalNewerOrIdentical:
            return None

        if conflict_check == Conflict.Conflict:
            new_local_path = generate_cc_name(
                event.local_path, is_fs_case_sensitive=self.is_case_sensitive
            )
            event_cls = DirMovedEvent if osp.isdir(event.local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(event.local_path, new_local_path)):
                exc = move(event.local_path, new_local_path)
                if exc:
                    raise os_to_maestral_error(exc, local_path=new_local_path)

            logger.debug(
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
            raise os_to_maestral_error(
                err, dbx_path=event.dbx_path, local_path=event.local_path
            )

        self.update_index_from_sync_event(event)

        logger.debug('Created local folder "%s"', event.dbx_path)

        return event

    def _on_remote_deleted(self, event: SyncEvent) -> Optional[SyncEvent]:
        """
        Applies a remote deletion locally.

        :param event: Dropbox deleted metadata.
        :returns: Dropbox metadata corresponding to local deletion or None if no local
            changes are made.
        """

        self._apply_case_change(event)

        # If your local state has something at the given path,
        # remove it and all its children. If there’s nothing at the
        # given path, ignore this entry.

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
            logger.debug('Deleted local item "%s"', event.dbx_path)
            return event
        elif isinstance(exc, FileNotFoundError):
            self.update_index_from_sync_event(event)
            logger.debug('Deletion failed: "%s" not found', event.dbx_path)
            return None
        else:
            raise os_to_maestral_error(exc)

    def _apply_case_change(self, event: SyncEvent) -> None:
        """
        Applies any changes in casing of the remote item locally. This should be called
        before any system calls using ``local_path`` because the actual path on the file
        system may have a different casing on case-sensitive file systems. On case-
        insensitive file systems, this causes only a cosmetic change.

        :param event: Download SyncEvent.
        """

        with self._database_access():
            old_entry = self.get_index_entry(event.dbx_path.lower())

        if old_entry and old_entry.dbx_path_cased != event.dbx_path:

            local_path_old = self.to_local_path_from_cased(old_entry.dbx_path_cased)

            event_cls = DirMovedEvent if osp.isdir(local_path_old) else FileMovedEvent
            with self.fs_events.ignore(event_cls(local_path_old, event.local_path)):
                move(local_path_old, event.local_path)

            with self._database_access():
                old_entry.dbx_path_cased = event.dbx_path
                self._db_session.commit()

            logger.debug('Renamed "%s" to "%s"', local_path_old, event.local_path)

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
            entries = self.get_index()
            for entry in entries:
                child_path_uncased = (self.dropbox_path + entry.dbx_path_lower).lower()
                if (
                    child_path_uncased.startswith(local_path_lower)
                    and child_path_uncased not in lowercase_snapshot_paths
                ):
                    local_child_path = self.to_local_path_from_cased(
                        entry.dbx_path_cased
                    )
                    if entry.is_directory:
                        self.fs_events.local_file_event_queue.put(
                            DirDeletedEvent(local_child_path)
                        )
                    else:
                        self.fs_events.local_file_event_queue.put(
                            FileDeletedEvent(local_child_path)
                        )

        elif not osp.exists(local_path):
            dbx_path = self.to_dbx_path(local_path)

            local_entry = self.get_index_entry(dbx_path)

            if local_entry:
                if local_entry.is_directory:
                    self.fs_events.local_file_event_queue.put(
                        DirDeletedEvent(local_path)
                    )
                else:
                    self.fs_events.local_file_event_queue.put(
                        FileDeletedEvent(local_path)
                    )

    def _clean_history(self):
        """Commits new events and removes all events older than ``_keep_history`` from
        history."""

        with self._database_access():

            # commit previous
            self._db_session.commit()

            # drop all entries older than keep_history
            now = time.time()
            keep_history = self._conf.get("sync", "keep_history")
            query = self._db_session.query(SyncEvent)
            subquery = query.filter(
                SyncEvent.change_time_or_sync_time < now - keep_history
            )
            subquery.delete(synchronize_session="fetch")

            # commit to drive
            self._db_session.commit()


# ========================================================================================
# Workers for upload, download and connection monitoring threads
# ========================================================================================


def helper(mm: "SyncMonitor") -> None:
    """
    A worker for periodic maintenance:

     1) Checks for a connection to Dropbox servers.
     2) Pauses syncing when the connection is lost and resumes syncing when reconnected
        and syncing has not been paused by the user.
     3) Triggers weekly reindexing.

    :param mm: MaestralMonitor instance.
    """

    while mm.running.is_set():

        if check_connection("www.dropbox.com"):
            if not mm.connected.is_set() and not mm.paused_by_user.is_set():
                mm.startup.set()
            # rebuild the index periodically
            elif (
                time.time() - mm.sync.last_reindex > mm.reindex_interval
                and mm.idle_time > 20 * 60
            ):
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


def download_worker(
    sync: SyncEngine, syncing: Event, running: Event, connected: Event
) -> None:
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
            has_changes = sync.wait_for_remote_changes(sync.remote_cursor)

            with sync.sync_lock:

                if not (running.is_set() and syncing.is_set()):
                    continue

                if has_changes:
                    logger.info(SYNCING)

                    changes, remote_cursor = sync.list_remote_changes(
                        sync.remote_cursor
                    )

                    downloaded = sync.apply_remote_changes(changes, remote_cursor)
                    sync.notify_user(downloaded)

                    logger.info(IDLE)

                    sync.client.get_space_usage()

        except DropboxServerError:
            logger.info("Dropbox server error", exc_info=True)
        except ConnectionError:
            syncing.clear()
            connected.clear()
            logger.debug("Lost connection", exc_info=True)
            logger.info(DISCONNECTED)
        except Exception as err:
            running.clear()
            syncing.clear()
            title = getattr(err, "title", "Unexpected error")
            logger.error(title, exc_info=True)


def download_worker_added_item(
    sync: SyncEngine,
    syncing: Event,
    running: Event,
    connected: Event,
    added_item_queue: "Queue[str]",
) -> None:
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
        except DropboxServerError:
            logger.info("Dropbox server error", exc_info=True)
        except ConnectionError:
            syncing.clear()
            connected.clear()
            logger.debug("Lost connection", exc_info=True)
            logger.info(DISCONNECTED)
        except Exception as err:
            running.clear()
            syncing.clear()
            title = getattr(err, "title", "Unexpected error")
            logger.error(title, exc_info=True)


def upload_worker(
    sync: SyncEngine, syncing: Event, running: Event, connected: Event
) -> None:
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

        except DropboxServerError:
            logger.info("Dropbox server error", exc_info=True)
        except ConnectionError:
            syncing.clear()
            connected.clear()
            logger.debug("Lost connection", exc_info=True)
            logger.info(DISCONNECTED)
        except Exception as err:
            running.clear()
            syncing.clear()
            title = getattr(err, "title", "Unexpected error")
            logger.error(title, exc_info=True)


def startup_worker(
    sync: SyncEngine,
    syncing: Event,
    running: Event,
    connected: Event,
    startup: Event,
    paused_by_user: Event,
) -> None:
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
                if sync.remote_cursor == "":
                    sync.clear_sync_errors()
                    sync.get_remote_folder()
                    sync.local_cursor = time.time()

                if not running.is_set():
                    continue

                # retry failed downloads
                if len(sync.download_errors) > 0:
                    logger.info("Retrying failed downloads...")

                for dbx_path in list(sync.download_errors):
                    logger.info(f"Downloading {dbx_path}...")
                    sync.get_remote_item(dbx_path)

                # resume interrupted downloads
                if len(sync.pending_downloads) > 0:
                    logger.info("Resuming interrupted downloads...")

                for dbx_path in list(sync.pending_downloads):
                    logger.info(f"Downloading {dbx_path}...")
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
                changes, remote_cursor = sync.list_remote_changes(sync.remote_cursor)
                downloaded = sync.apply_remote_changes(changes, remote_cursor)
                sync.notify_user(downloaded)

                if not running.is_set():
                    continue

                if not paused_by_user.is_set():
                    syncing.set()

                startup.clear()

                gc.collect()

                logger.info(IDLE)

        except DropboxServerError:
            logger.info("Dropbox server error", exc_info=True)
        except ConnectionError:
            syncing.clear()
            connected.clear()
            startup.clear()
            logger.debug("Lost connection", exc_info=True)
            logger.info(DISCONNECTED)
        except Exception as err:
            running.clear()
            syncing.clear()
            title = getattr(err, "title", "Unexpected error")
            logger.error(title, exc_info=True)


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

    added_item_queue: "Queue[str]"
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

    def _with_lock(fn: FT) -> FT:  # type: ignore
        @wraps(fn)
        def wrapper(self, *args, **kwargs):
            with self._lock:
                return fn(self, *args, **kwargs)

        return cast(FT, wrapper)

    @property
    def reindex_interval(self) -> float:
        return self._conf.get("sync", "reindex_interval")

    @reindex_interval.setter
    def reindex_interval(self, interval: float) -> None:
        self._conf.set("sync", "reindex_interval", interval)

    @property
    def activity(self) -> List[SyncEvent]:
        """Returns a list all items queued for or currently syncing."""
        return list(self.sync.syncing)

    @property
    def history(self) -> List[SyncEvent]:
        """Returns a list all past SyncEvents."""
        return self.sync.history

    @_with_lock
    def start(self) -> None:
        """Creates observer threads and starts syncing."""

        if self.running.is_set() or self.startup.is_set():
            # do nothing if already started
            return

        self.running = Event()  # create new event to let old threads shut down

        self.local_observer_thread = Observer(timeout=0.3)
        self.local_observer_thread.setName("maestral-fsobserver")
        self._watch = self.local_observer_thread.schedule(
            self.fs_event_handler, self.sync.dropbox_path, recursive=True
        )
        for emitter in self.local_observer_thread.emitters:
            emitter.setName("maestral-fsemitter")

        self.helper_thread = Thread(
            target=helper, daemon=True, args=(self,), name="maestral-helper"
        )

        self.startup_thread = Thread(
            target=startup_worker,
            daemon=True,
            args=(
                self.sync,
                self.syncing,
                self.running,
                self.connected,
                self.startup,
                self.paused_by_user,
            ),
            name="maestral-sync-startup",
        )

        self.download_thread = Thread(
            target=download_worker,
            daemon=True,
            args=(
                self.sync,
                self.syncing,
                self.running,
                self.connected,
            ),
            name="maestral-download",
        )

        self.download_thread_added_folder = Thread(
            target=download_worker_added_item,
            daemon=True,
            args=(
                self.sync,
                self.syncing,
                self.running,
                self.connected,
                self.added_item_queue,
            ),
            name="maestral-folder-download",
        )

        self.upload_thread = Thread(
            target=upload_worker,
            daemon=True,
            args=(
                self.sync,
                self.syncing,
                self.running,
                self.connected,
            ),
            name="maestral-upload",
        )

        try:
            self.local_observer_thread.start()
        except OSError as err:
            new_err = fswatch_to_maestral_error(err)
            title = getattr(new_err, "title", "Unexpected error")
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

    @_with_lock
    def pause(self) -> None:
        """Pauses syncing."""

        self.paused_by_user.set()
        self.syncing.clear()

        self.sync.cancel_pending.set()
        self._wait_for_idle()
        self.sync.cancel_pending.clear()

        logger.info(PAUSED)

    @_with_lock
    def resume(self) -> None:
        """Checks for changes while idle and starts syncing."""

        if not self.paused_by_user.is_set():
            return

        self.startup.set()
        self.paused_by_user.clear()

    @_with_lock
    def stop(self) -> None:
        """Stops syncing and destroys worker threads."""

        if not self.running.is_set():
            return

        logger.info("Shutting down threads...")

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
        """Returns the idle time in seconds since the last file change or since startup if
        if there haven't been any changes in our current session."""

        now = time.time()
        time_since_startup = now - self._startup_time
        time_since_last_sync = now - self.sync.last_change

        return min(time_since_startup, time_since_last_sync)

    def reset_sync_state(self) -> None:
        """Resets all saved sync state."""

        if self.syncing.is_set() or self.startup.is_set() or self.sync.busy():
            raise RuntimeError("Cannot reset sync state while syncing.")

        self.sync.remote_cursor = ""
        self.sync.local_cursor = 0.0
        self.sync.clear_index()
        self.sync.clear_sync_history()

        logger.debug("Sync state reset")

    def rebuild_index(self) -> None:
        """
        Rebuilds the rev file by comparing remote with local files and updating rev
        numbers from the Dropbox server. Files are compared by their content hashes and
        conflicting copies are created if the contents differ. File changes during the
        rebuild process will be queued and uploaded once rebuilding has completed.

        Rebuilding will be performed asynchronously.
        """

        logger.info("Rebuilding index...")

        self.pause()

        self.sync.remote_cursor = ""
        self.sync.clear_index()

        if not self.running.is_set():
            self.start()
        else:
            self.resume()

    def _wait_for_idle(self) -> None:

        self.sync.sync_lock.acquire()
        self.sync.sync_lock.release()


# ========================================================================================
# Helper functions
# ========================================================================================


def _exc_info(exc: BaseException) -> ExecInfoType:
    return type(exc), exc, exc.__traceback__


def get_dest_path(event: FileSystemEvent) -> str:
    return getattr(event, "dest_path", event.src_path)


def split_moved_event(
    event: Union[FileMovedEvent, DirMovedEvent]
) -> Tuple[FileSystemEvent, FileSystemEvent]:
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


def entries_to_str(entries: List[Metadata]) -> str:
    str_reps = [
        f"<{e.__class__.__name__}(path_display={e.path_display})>" for e in entries
    ]
    return "[" + ",\n ".join(str_reps) + "]"


_last_emit = time.time()


def throttled_log(
    log: logging.Logger, msg: str, level: int = logging.INFO, limit: int = 2
) -> None:

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
        raise ValueError(f"interval is not positive (got {interval!r})")

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
        overall_cpus_percent = (delta_proc / delta_time) * 100
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
