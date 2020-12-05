# -*- coding: utf-8 -*-
"""
This module contains the definitions of our data base tables which store the index, sync
history and cache of content hashes.
"""

# system imports
import os
import time
import enum
from datetime import timezone
from typing import Optional, TYPE_CHECKING

# external imports
import sqlalchemy.types as sqltypes  # type: ignore
from sqlalchemy.ext.declarative import declarative_base  # type: ignore
from sqlalchemy.orm import sessionmaker  # type: ignore
from sqlalchemy.sql import case  # type: ignore
from sqlalchemy.sql.elements import Case  # type: ignore
from sqlalchemy.ext.hybrid import hybrid_property  # type: ignore
from sqlalchemy import Column, MetaData  # type: ignore
from dropbox.files import Metadata, DeletedMetadata, FileMetadata, FolderMetadata  # type: ignore
from watchdog.events import (
    FileSystemEvent,
    EVENT_TYPE_CREATED,
    EVENT_TYPE_DELETED,
    EVENT_TYPE_MOVED,
    EVENT_TYPE_MODIFIED,
)

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
    "Session",
    "Base",
    "db_naming_convention",
]


db_naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
Base = declarative_base(metadata=MetaData(naming_convention=db_naming_convention))
Session = sessionmaker(expire_on_commit=False)


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


class StringPath(sqltypes.TypeDecorator):
    """
    Database column type which accepts strings the Python surrogate escapes as input.
    Those may be passed when the local path contains bytes outside of the reported file
    system encoding.
    """

    # TODO: bijective conversion of surrogate escapes in our database

    impl = sqltypes.String

    def process_bind_param(self, value: Optional[str], dialect) -> Optional[str]:
        if value is None:
            return value
        else:
            return os.fsencode(value).decode(errors="replace")

    def process_result_value(self, value: Optional[str], dialect) -> Optional[str]:
        return value


class SyncEvent(Base):  # type: ignore
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

    id = Column(sqltypes.Integer, primary_key=True)
    """A unique identifier of the SyncEvent."""

    direction = Column(sqltypes.Enum(SyncDirection), nullable=False)
    """The :class:`SyncDirection`."""

    item_type = Column(sqltypes.Enum(ItemType), nullable=False)
    """
    The :class:`ItemType`. May be undetermined for remote deletions.
    """

    sync_time = Column(sqltypes.Float, nullable=False)
    """The time the SyncEvent was registered."""

    dbx_id = Column(sqltypes.String)
    """
    A unique dropbox ID for the file or folder. Will only be set for download events
    which are not deletions.
    """

    dbx_path = Column(StringPath, nullable=False)
    """
    Dropbox path of the item to sync. If the sync represents a move operation, this will
    be the destination path. Follows the casing from server.
    """

    local_path = Column(StringPath, nullable=False)
    """
    Local path of the item to sync. If the sync represents a move operation, this will
    be the destination path. This will be correctly cased.
    """

    dbx_path_from = Column(StringPath)
    """
    Dropbox path that this item was moved from. Will only be set if :attr:`change_type`
    is :attr:`ChangeType.Moved`. Follows the casing from server.
    """

    local_path_from = Column(StringPath)
    """
    Local path that this item was moved from. Will only be set if :attr:`change_type`
    is :attr:`ChangeType.Moved`. This will be correctly cased.
    """

    rev = Column(sqltypes.String)
    """
    The file revision. Will only be set for remote changes. Will be ``'folder'`` for
    folders and ``None`` for deletions.
    """

    content_hash = Column(sqltypes.String)
    """
    A hash representing the file content. Will be ``'folder'`` for folders and ``None``
    for deletions. Set for both local and remote changes.
    """

    change_type = Column(sqltypes.Enum(ChangeType), nullable=False)
    """
    The :class:`ChangeType`. Remote SyncEvents currently do not generate moved events
    but are reported as deleted and added at the new location.
    """

    change_time = Column(sqltypes.Float)
    """
    Local ctime or remote ``client_modified`` time for files. ``None`` for folders or
    for remote deletions. Note that ``client_modified`` may not be reliable as it is set
    by other clients and not verified.
    """

    change_dbid = Column(sqltypes.String)
    """
    The Dropbox ID of the account which performed the changes. This may not be set for
    added folders or deletions on the server.
    """

    change_user_name = Column(sqltypes.String)
    """
    The user name corresponding to :attr:`change_dbid`, if the account still exists.
    This field may not be set for performance reasons.
    """

    status = Column(sqltypes.Enum(SyncStatus), nullable=False)
    """The :class:`SyncStatus`."""

    size = Column(sqltypes.Integer, nullable=False)
    """Size of the item in bytes. Always zero for folders."""

    completed = Column(sqltypes.Integer, default=0)
    """
    File size in bytes which has already been uploaded or downloaded. Always zero for
    folders.
    """

    @hybrid_property
    def change_time_or_sync_time(self) -> float:
        """
        Change time when available, otherwise sync time. This can be used for sorting or
        user information purposes.
        """
        return self.change_time or self.sync_time

    @change_time_or_sync_time.expression  # type: ignore
    def change_time_or_sync_time(cls) -> Case:
        return case(
            [(cls.change_time != None, cls.change_time)],  # noqa: E711
            else_=cls.sync_time,
        )

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
            change_dbid = None

            local_rev = sync_engine.get_local_rev(md.path_lower)
            if local_rev == "folder":
                item_type = ItemType.Folder
            elif local_rev is not None:
                item_type = ItemType.File
            else:
                item_type = ItemType.Unknown

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
        to_path = getattr(event, "dest_path", event.src_path)
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

        # Note: We get the content hash here instead of later, even though the
        # calculation may be slow and :meth:`from_file_system_event` may be called
        # serially and not from a thread pool. This is because hashing is CPU bound
        # and parallelization would cause large multi-core CPU usage (or result in
        # throttling of our thread-pool).

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
    """Represents an entry in our local sync index"""

    __tablename__ = "index"

    dbx_path_lower = Column(StringPath, nullable=False, primary_key=True)
    """
    Dropbox path of the item in lower case. This acts as a primary key for the SQLites
    database since there can only be one entry per case-insensitive Dropbox path.
    """

    dbx_path_cased = Column(StringPath, nullable=False)
    """Dropbox path of the item, correctly cased."""

    dbx_id = Column(sqltypes.String, nullable=False)
    """The unique dropbox ID for the item."""

    item_type = Column(sqltypes.Enum(ItemType), nullable=False)
    """The :class:`ItemType`."""

    last_sync = Column(sqltypes.Float)
    """
    The last time a local change was uploaded. Should be the ctime of the local item.
    """

    rev = Column(sqltypes.String, nullable=False)
    """The file revision. Will be ``'folder'`` for folders."""

    content_hash = Column(sqltypes.String)
    """
    A hash representing the file content. Will be ``'folder'`` for folders. May be
    ``None`` if not yet calculated.
    """

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
    """Represents an entry in our cache of content hashes"""

    __tablename__ = "hash_cache"

    local_path = Column(StringPath, nullable=False, primary_key=True)
    """The local path of the item."""

    hash_str = Column(sqltypes.String)
    """The content hash of the item."""

    mtime = Column(sqltypes.Float)
    """
    The mtime of the item just before the hash was computed. When the current ctime is
    newer, the hash will need to be recalculated.
    """
