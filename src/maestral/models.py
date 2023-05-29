"""
This module contains the definitions of our database tables which store the index, sync
history and cache of content hashes. Each table is defined by a subclass of
:class:`maestral.database.orm.Model` with properties representing database columns.
Class instances then represent table rows.
"""

from __future__ import annotations

# system imports
import os
import time
import enum
from typing import TYPE_CHECKING

# external imports
from watchdog.events import (
    FileSystemEvent,
    FileMovedEvent,
    DirMovedEvent,
    EVENT_TYPE_CREATED,
    EVENT_TYPE_DELETED,
    EVENT_TYPE_MOVED,
    EVENT_TYPE_MODIFIED,
)

# local imports
from .core import Metadata, DeletedMetadata, FileMetadata, FolderMetadata
from .database.orm import Model, Column, NonNullColumn
from .database.types import SqlInt, SqlLargeInt, SqlFloat, SqlString, SqlPath, SqlEnum
from .utils.path import normalize
from .exceptions import SyncError, NotLinkedError

if TYPE_CHECKING:
    from .sync import SyncEngine


__all__ = [
    "SyncDirection",
    "SyncStatus",
    "ItemType",
    "ChangeType",
    "SyncEvent",
    "IndexEntry",
    "HashCacheEntry",
    "SyncErrorEntry",
]


def get_dest_path(event: FileSystemEvent) -> str:
    """
    Returns the dest_path of a file system event if present (moved events only)
    otherwise returns the src_path (which is also the "destination").

    :param event: Watchdog file system event.
    :returns: Destination path for moved event, source path otherwise.
    """
    if isinstance(event, (FileMovedEvent, DirMovedEvent)):
        return event.dest_path
    return event.src_path


class SyncDirection(enum.Enum):
    """Enumeration of sync directions"""

    Up = "up"
    Down = "down"


class SyncStatus(enum.Enum):
    """Enumeration of sync status"""

    Queued = "queued"
    Syncing = "syncing"
    Done = "done"
    Failed = "failed"
    Skipped = "skipped"
    Aborted = "aborted"
    Conflict = "conflict"


class ItemType(enum.Enum):
    """Enumeration of SyncEvent types"""

    File = "file"
    Folder = "folder"
    Unknown = "unknown"  # This can occur for remote deleted events, see issue #198


class ChangeType(enum.Enum):
    """Enumeration of SyncEvent change types"""

    Added = "added"
    Removed = "removed"
    Moved = "moved"
    Modified = "modified"


class SyncEvent(Model):
    """Represents a file or folder change in the sync queue

    This class is used to represent both local and remote file system changes and track
    their sync progress. Some instance attributes will depend on the state of the sync
    session, e.g., :attr:`local_path` will depend on the current path of the local
    Dropbox folder. They may therefore become invalid between sync sessions.

    The class methods :meth:`from_dbx_metadata` and :meth:`from_file_system_event`
    should be used to properly construct a :class:`SyncEvent` from a
    :class:`dropbox.files.Metadata` instance or a
    :class:`watchdog.events.FileSystemEvent` instance, respectively.
    """

    __tablename__ = "history"

    id = Column(SqlInt(), primary_key=True)
    """A unique identifier of the SyncEvent."""

    direction = NonNullColumn(SqlEnum(SyncDirection))
    """The :class:`SyncDirection`."""

    item_type = NonNullColumn(SqlEnum(ItemType))
    """
    The :class:`ItemType`. May be undetermined for remote deletions.
    """

    sync_time = NonNullColumn(SqlFloat())
    """The time the SyncEvent was registered."""

    dbx_id = Column(SqlString())
    """
    A unique dropbox ID for the file or folder. Will only be set for download events
    which are not deletions.
    """

    dbx_path = NonNullColumn(SqlPath())
    """
    Correctly cased Dropbox path of the item to sync. If the sync represents a move
    operation, this will be the destination path. Follows the casing from the
    path_display attribute of Dropbox metadata.
    """

    dbx_path_lower = NonNullColumn(SqlPath())
    """
    Dropbox path of the item to sync. If the sync represents a move operation, this will
    be the destination path. This is normalised as the path_lower attribute of Dropbox
    metadata.
    """

    local_path = NonNullColumn(SqlPath())
    """
    Local path of the item to sync. If the sync represents a move operation, this will
    be the destination path. This will be correctly cased.
    """

    dbx_path_from = Column(SqlPath())
    """
    Dropbox path that this item was moved from. Will only be set if :attr:`change_type`
    is :attr:`ChangeType.Moved`. Follows the casing from the path_display attribute of
    Dropbox metadata.
    """

    dbx_path_from_lower = Column(SqlPath())
    """
    Dropbox path that this item was moved from. Will only be set if :attr:`change_type`
    is :attr:`ChangeType.Moved`. This is normalised as the path_lower attribute of
    Dropbox metadata.
    """

    local_path_from = Column(SqlPath())
    """
    Local path that this item was moved from. Will only be set if :attr:`change_type`
    is :attr:`ChangeType.Moved`. This will be correctly cased.
    """

    rev = Column(SqlString())
    """
    The file revision. Will only be set for remote changes. Will be ``'folder'`` for
    folders and ``None`` for deletions.
    """

    content_hash = Column(SqlString())
    """
    A hash representing the file content. Will be ``'folder'`` for folders and ``None``
    for deletions. Set for both local and remote changes.
    """

    symlink_target = Column(SqlPath())
    """
    If the file is a symlink, its target path. This should only be set for files.
    """

    change_type = NonNullColumn(SqlEnum(ChangeType))
    """
    The :class:`ChangeType`. Remote SyncEvents currently do not generate moved events
    but are reported as deleted and added at the new location.
    """

    change_time = Column(SqlFloat())
    """
    Local ctime or remote ``client_modified`` time for files. ``None`` for folders or
    for remote deletions. Note that ``client_modified`` may not be reliable as it is set
    by other clients and not verified.
    """

    change_dbid = Column(SqlString())
    """
    The Dropbox ID of the account which performed the changes. This may not be set for
    added folders or deletions on the server.
    """

    change_user_name = Column(SqlString())
    """
    The user name corresponding to :attr:`change_dbid`, if the account still exists.
    This field may not be set for performance reasons.
    """

    status = NonNullColumn(SqlEnum(SyncStatus))
    """The :class:`SyncStatus`."""

    size = NonNullColumn(SqlInt())
    """Size of the item in bytes. Always zero for folders."""

    completed = NonNullColumn(SqlInt(), default=0)
    """
    File size in bytes which has already been uploaded or downloaded. Always zero for
    folders.
    """

    @property
    def change_time_or_sync_time(self) -> float:
        """
        Change time when available, otherwise sync time. This can be used for sorting or
        user information purposes.
        """
        return self.change_time or self.sync_time

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

    def __repr__(self) -> str:
        attrs = ["direction", "change_type", "item_type", "dbx_path"]
        if self.change_type is ChangeType.Moved:
            attrs.append("dbx_path_from")
        if self.symlink_target is not None:
            attrs.append("symlink_target")

        attr_str = ", ".join(f"{attr}={getattr(self, attr)}" for attr in attrs)
        return f"<{self.__class__.__name__}({attr_str})>"

    @classmethod
    def from_metadata(cls, md: Metadata, sync_engine: SyncEngine) -> SyncEvent:
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
            symlink_target = None
            dbx_id = None
            change_dbid = None

            entry = sync_engine.get_index_entry(md.path_lower)
            item_type = ItemType.Unknown if entry is None else entry.item_type

        elif isinstance(md, FolderMetadata):
            # there is currently no API call to determine who added a folder
            change_type = ChangeType.Added
            item_type = ItemType.Folder
            size = 0
            rev = "folder"
            hash_str = "folder"
            symlink_target = None
            dbx_id = md.id
            change_time = None
            change_dbid = None

        elif isinstance(md, FileMetadata):
            item_type = ItemType.File
            rev = md.rev
            hash_str = md.content_hash
            symlink_target = md.symlink_target
            dbx_id = md.id
            size = md.size
            change_time = md.client_modified.timestamp()
            if sync_engine.get_local_rev(md.path_lower):
                change_type = ChangeType.Modified
            else:
                change_type = ChangeType.Added
            if md.shared:
                change_dbid = md.modified_by
            else:
                # File is not a shared folder, therefore
                # the current user must have added or modified it.
                change_dbid = sync_engine.client.account_info.account_id
        else:
            raise RuntimeError(f"Cannot convert {md} to SyncEvent")

        dbx_path_cased = sync_engine.correct_case(md.path_display)

        return cls(
            direction=SyncDirection.Down,
            item_type=item_type,
            sync_time=time.time(),
            dbx_path=dbx_path_cased,
            dbx_path_lower=md.path_lower,
            dbx_id=dbx_id,
            local_path=sync_engine.to_local_path_from_cased(dbx_path_cased),
            rev=rev,
            content_hash=hash_str,
            symlink_target=symlink_target,
            change_type=change_type,
            change_time=change_time,
            change_dbid=change_dbid,
            status=SyncStatus.Queued,
            size=size,
            completed=0,
        )

    @classmethod
    def from_file_system_event(
        cls, event: FileSystemEvent, sync_engine: SyncEngine
    ) -> SyncEvent:
        """
        Initializes a SyncEvent from the given local file system event.

        :param event: Local file system event.
        :param sync_engine: SyncEngine instance.
        :returns: An instance of this class with attributes populated from the given
            SyncEvent.
        """
        try:
            change_dbid = sync_engine.client.account_info.account_id
        except NotLinkedError:
            # Allow converting events when we are not linked.
            # This is useful for testing.
            change_dbid = ""

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

        change_time: float | None
        stat: os.stat_result | None

        try:
            stat = os.stat(to_path, follow_symlinks=False)
        except OSError:
            stat = None

        try:
            content_hash = sync_engine.get_local_hash(to_path)
        except SyncError:
            content_hash = None

        if event.is_directory:
            item_type = ItemType.Folder
            size = 0
            try:
                change_time = stat.st_birthtime  # type: ignore
            except AttributeError:
                change_time = None
            symlink_target = None
        else:
            item_type = ItemType.File
            change_time = stat.st_ctime if stat else None
            size = stat.st_size if stat else 0
            try:
                symlink_target = os.readlink(event.src_path)
            except OSError:
                symlink_target = None

        dbx_path = sync_engine.to_dbx_path(to_path)
        dbx_path_lower = normalize(dbx_path)

        dbx_path_from = sync_engine.to_dbx_path(from_path) if from_path else None
        dbx_path_from_lower = normalize(dbx_path_from) if dbx_path_from else None

        # For file changes, update the change type based on our index. Local file system
        # events of created vs modified can be misleading for some safe save mechanisms.
        # See https://github.com/samschott/maestral/issues/850.
        if item_type is ItemType.File and change_type in {
            ChangeType.Added,
            ChangeType.Modified,
        }:
            entry = sync_engine.get_index_entry(dbx_path_lower)
            if entry is None:
                # Item is new to us.
                change_type = ChangeType.Added
            elif entry.item_type is ItemType.File:
                # Item already existed.
                change_type = ChangeType.Modified

        return cls(
            direction=SyncDirection.Up,
            item_type=item_type,
            sync_time=time.time(),
            dbx_path=dbx_path,
            dbx_path_lower=dbx_path_lower,
            local_path=to_path,
            dbx_path_from=dbx_path_from,
            dbx_path_from_lower=dbx_path_from_lower,
            local_path_from=from_path,
            content_hash=content_hash,
            symlink_target=symlink_target,
            change_type=change_type,
            change_time=change_time,
            change_dbid=change_dbid,
            status=SyncStatus.Queued,
            size=size,
            completed=0,
        )


class IndexEntry(Model):
    """Represents an entry in our local sync index"""

    __tablename__ = "'index'"

    dbx_path_lower = NonNullColumn(SqlPath(), primary_key=True)
    """
    Dropbox path of the item in lower case. This acts as a primary key for the SQLites
    database since there can only be one entry per case-insensitive Dropbox path.
    Corresponds to the path_lower field of Dropbox metadata.
    """

    dbx_path_cased = NonNullColumn(SqlPath(), index=True)
    """
    Dropbox path of the item, correctly cased. Corresponds to the path_display field of
    Dropbox metadata.
    """

    dbx_id = NonNullColumn(SqlString())
    """The unique dropbox ID for the item."""

    item_type = NonNullColumn(SqlEnum(ItemType))
    """The :class:`ItemType`."""

    last_sync = Column(SqlFloat())
    """
    The last time a local change was uploaded. Should be the ctime of the local item.
    """

    rev = NonNullColumn(SqlString())
    """The file revision. Will be ``'folder'`` for folders."""

    content_hash = Column(SqlString())
    """
    A hash representing the file content. Will be ``'folder'`` for folders. May be
    ``None`` if not yet calculated.
    """

    symlink_target = Column(SqlPath())
    """
    If the file is a symlink, its target path. This should only be set for files.
    """

    @property
    def is_file(self) -> bool:
        """Returns True for files"""
        return self.item_type == ItemType.File

    @property
    def is_directory(self) -> bool:
        """Returns True for folders"""
        return self.item_type == ItemType.Folder

    @property
    def is_symlink(self) -> bool:
        """Returns True for symlinks"""
        return self.symlink_target is not None

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}(item_type={self.item_type.name}, "
            f"dbx_path='{self.dbx_path_cased}')>"
        )


class HashCacheEntry(Model):
    """Represents an entry in our cache of content hashes"""

    __tablename__ = "hash_cache"

    inode = NonNullColumn(SqlLargeInt(), primary_key=True)
    """The inode of the item."""

    local_path = NonNullColumn(SqlPath())
    """The local path of the item."""

    hash_str = Column(SqlString())
    """The content hash of the item."""

    mtime = Column(SqlFloat())
    """
    The mtime of the item just before the hash was computed. When the current mtime is
    newer, the hash will need to be recalculated.
    """


class SyncErrorEntry(Model):
    """Table of sync errors"""

    __tablename__ = "sync_errors"

    dbx_path = NonNullColumn(SqlPath())
    dbx_path_lower = NonNullColumn(SqlPath(), primary_key=True)
    dbx_path_from = Column(SqlPath())
    dbx_path_from_lower = Column(SqlPath())
    local_path = NonNullColumn(SqlPath())
    local_path_from = Column(SqlPath())
    direction = NonNullColumn(SqlEnum(SyncDirection))
    title = Column(SqlString())
    message = Column(SqlString())
    type = Column(SqlString())
