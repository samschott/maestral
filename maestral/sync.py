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
import itertools
from contextlib import contextmanager
import functools
from enum import IntEnum
import pprint
import socket

# external imports
import pathspec
import dropbox
from dropbox.files import Metadata, DeletedMetadata, FileMetadata, FolderMetadata
from watchdog.events import FileSystemEventHandler
from watchdog.events import (
    EVENT_TYPE_CREATED, EVENT_TYPE_DELETED, EVENT_TYPE_MODIFIED, EVENT_TYPE_MOVED
)
from watchdog.events import (
    DirModifiedEvent, FileModifiedEvent, DirCreatedEvent, FileCreatedEvent,
    DirDeletedEvent, FileDeletedEvent, DirMovedEvent, FileMovedEvent
)
from watchdog.utils.dirsnapshot import DirectorySnapshot
from atomicwrites import atomic_write

# local imports
from maestral.config import MaestralConfig, MaestralState
from maestral.fsevents import Observer
from maestral.constants import (
    IDLE, SYNCING, PAUSED, STOPPED, DISCONNECTED, EXCLUDED_FILE_NAMES,
    EXCLUDED_DIR_NAMES, MIGNORE_FILE, FILE_CACHE
)
from maestral.errors import (
    MaestralApiError, SyncError, RevFileError, NoDropboxDirError, CacheDirError,
    PathError, NotFoundError, FileConflictError, FolderConflictError,
    fswatch_to_maestral_error, os_to_maestral_error
)
from maestral.utils.content_hasher import DropboxContentHasher
from maestral.utils.notify import MaestralDesktopNotifier, FILECHANGE
from maestral.utils.path import (
    generate_cc_name, cased_path_candidates, to_cased_path, is_fs_case_sensitive,
    move, delete, is_child, is_equal_or_child
)
from maestral.utils.appdirs import get_data_path, get_home_dir

logger = logging.getLogger(__name__)

_cpu_count = os.cpu_count()


# ========================================================================================
# Syncing functionality
# ========================================================================================

class Conflict(IntEnum):
    """
    Enumeration of sync conflict types.

    :cvar int RemoteNewer: Remote item is newer.
    :cvar int Conflict: Conflict.
    :cvar int Identical: Items are identical.
    :cvar int LocalNewerOrIdentical: Local item is newer or identical.
    """
    RemoteNewer = 0
    Conflict = 1
    Identical = 2
    LocalNewerOrIdentical = 2


class InQueue:
    """
    A context manager that puts ``items`` into ``queue`` when entering the context and
    removes them when exiting. This is used by maestral to keep track of uploads and
    downloads.
    """

    def __init__(self, queue, *items):
        """
        :param queue: Instance of :class:`queue.Queue`.
        :param iterable items: Items to put in queue.
        """
        self.items = items
        self.queue = queue

    def __enter__(self):
        for item in self.items:
            self.queue.put(item)

    def __exit__(self, err_type, err_value, err_traceback):
        remove_from_queue(self.queue, *self.items)


class FSEventHandler(FileSystemEventHandler):
    """
    Handles captured file events and adds them to :class:`SyncEngine`'s file event queue
    to be uploaded by :meth:`upload_worker`. This acts as a translation layer between
    :class:`watchdog.Observer` and :class:`SyncEngine`.

    :param Event syncing: Set when syncing is running.
    :param Event startup: Set when startup is running.
    :param SyncEngine sync: SyncEngine instance.

    :cvar int ignore_timeout: Timeout in seconds after which ignored paths will be
        discarded.
    """

    ignore_timeout = 2

    def __init__(self, syncing, startup, sync):

        self.syncing = syncing
        self.startup = startup
        self.sync = sync
        self.sync.fs_events = self

        self._ignored_events = []
        self._ignored_events_mutex = RLock()

        self.local_file_event_queue = Queue()

    @contextmanager
    def ignore(self, *events, recursive=True):
        """
        A context manager to ignore file events. Once a matching event has been
        registered, further matching events will no longer be ignored unless ``recursive``
        is ``True``. If no matching event has occurred before leaving the context, the
        event will be ignored for ``ignore_timeout`` sec after leaving then context and
        then discarded. This accounts for possible delays in the emission of local file
        system events.

        This context manager is used to filter out file system events caused by maestral
        itself, for instance during a download or when moving a conflict.

        :param iterable events: Local events to ignore.
        :param bool recursive: If ``True``, all child events of a directory event will be
            ignored as well. This parameter will be ignored for file events.
        """

        with self._ignored_events_mutex:
            now = time.time()
            new_ignores = []
            for e in events:
                new_ignores.append(
                    dict(
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
                    ignore['ttl'] = time.time() + self.ignore_timeout

    def _expire_ignored_events(self):
        """Removes all expired ignore entries."""

        with self._ignored_events_mutex:

            now = time.time()
            for ignore in self._ignored_events.copy():
                ttl = ignore['ttl']
                if ttl and ttl < now:
                    self._ignored_events.remove(ignore)

    def _is_ignored(self, event):
        """
        Checks if a file system event should been explicitly ignored because it was
        triggered by Maestral itself.

        :param FileSystemEvent event: Local file system event.
        :returns: ``True`` if the event should be ignored, ``False`` otherwise.
        :rtype: bool
        """

        with self._ignored_events_mutex:

            self._expire_ignored_events()

            for ignore in self._ignored_events:
                ignore_event = ignore['event']
                recursive = ignore['recursive']

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

    def on_any_event(self, event):
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
    A wrapper for a list of strings in the saved state that implements a MutableSet
    interface. All strings are stored as lower-case, reflecting Dropbox's case-insensitive
    file system.

    :param str config_name: Name of config (determines name of state file).
    :param str section: Section name in state file.
    :param str option: Option name in state file.
    """

    _lock = RLock()

    def __init__(self, config_name, section, option):
        super().__init__()
        self.config_name = config_name
        self.section = section
        self.option = option
        self._state = MaestralState(config_name)

    def __iter__(self):
        with self._lock:
            return iter(self._state.get(self.section, self.option))

    def __contains__(self, dbx_path):
        with self._lock:
            return dbx_path in self._state.get(self.section, self.option)

    def __len__(self):
        with self._lock:
            return len(self._state.get(self.section, self.option))

    def discard(self, dbx_path):
        dbx_path = dbx_path.lower().rstrip('/')
        with self._lock:
            state_list = self._state.get(self.section, self.option)
            state_list = set(state_list)
            state_list.discard(dbx_path)
            self._state.set(self.section, self.option, list(state_list))

    def add(self, dbx_path):
        dbx_path = dbx_path.lower().rstrip('/')
        with self._lock:
            state_list = self._state.get(self.section, self.option)
            state_list = set(state_list)
            state_list.add(dbx_path)
            self._state.set(self.section, self.option, list(state_list))

    def clear(self):
        """Clears all elements."""
        with self._lock:
            self._state.set(self.section, self.option, [])

    def __repr__(self):
        return f'<{self.__class__.__name__}(section=\'{self.section}\',' \
               f'option=\'{self.option}\', entries={list(self)})>'


def catch_sync_issues(download=False):
    """
    Returns a decorator that catches all SyncErrors and logs them.
    Should only be used to decorate methods of :class:`SyncEngine`.

    :param bool download: If ``True``, sync issues will be added to the `download_errors`
        list to retry later.
    """

    def decorator(func):

        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                res = func(self, *args, **kwargs)
                if res is None:
                    res = True
            except SyncError as exc:
                # fill out missing dbx_path or local_path
                if exc.dbx_path or exc.local_path:
                    if not exc.local_path:
                        exc.local_path = self.to_local_path(exc.dbx_path)
                    if not exc.dbx_path:
                        exc.dbx_path = self.to_dbx_path(exc.local_path)

                if exc.dbx_path_dst or exc.local_path_dst:
                    if not exc.local_path_dst:
                        exc.local_path_dst = self.to_local_path(exc.dbx_path_dst)
                    if not exc.dbx_path:
                        exc.dbx_path_dst = self.to_dbx_path(exc.local_path_dst)

                if exc.dbx_path:
                    # we have a file / folder associated with the sync error
                    file_name = osp.basename(exc.dbx_path)
                    logger.warning('Could not sync %s', file_name, exc_info=True)
                    self.sync_errors.put(exc)

                    # save download errors to retry later
                    if download:
                        self.download_errors.add(exc.dbx_path)

                res = False

            return res

        return wrapper

    return decorator


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
      2) :meth:`_filter_excluded_changes_remote`: Filters out events that occurred for
         entries that are excluded by selective sync and hard-coded file names which are
         always excluded (e.g., '.DS_Store').
      3) :meth:`apply_remote_changes`: Sorts all events hierarchically, with top-level
         events coming first. Deleted and folder events are processed in order, file
         events in parallel with up to 6 worker threads. The actual download is carried
         out by :meth:`_create_local_entry`.
      4) :meth:`_create_local_entry`: Checks for sync conflicts by comparing the file
         "rev" with our locally saved rev. We assign folders a rev of ``'folder'`` and
         deleted / non-existent items a rev of ``None``. If revs are equal, the local item
         is the same or newer as on Dropbox and no download / deletion occurs. If revs are
         different, we compare content hashes. Folders are assigned a hash of 'folder'. If
         hashes are equal, no download occurs. If they are different, we check if the
         local item has been modified since the last download sync. In case of a folder,
         we take the newest change of any of its children. If the local entry has not been
         modified since the last sync, it will be replaced. Otherwise, we create a
         conflicting copy.
      5) :meth:`notify_user`: Shows a desktop notification for the remote changes.

    Local file events come in eight types: For both files and folders we collect created,
    moved, modified and deleted events. They are processed as follows:

      1) :meth:`wait_for_local_changes`: Blocks until local changes were registered by
         :class:`FSEventHandler` and returns those changes.
      2) :meth:`_filter_excluded_changes_local`: Filters out events ignored by a
         "mignore" pattern as well as hard-coded file names and changes in our cache path.
      3) :meth:`_clean_local_events`: Cleans up local events in two stages. First,
         multiple events per path are combined into a single event which reproduces the
         file changes. The only exception is when the entry type changes from file to
         folder or vice versa: in this case, both deleted and created events are kept.
         Second, when a whole folder is moved or deleted, we discard the moved and deleted
         events of its children.
      4) :meth:`apply_local_changes`: Sorts local changes hierarchically and applies
         events in the order of deleted, folders and files. Deletions and creations
         will be carried out in parallel with up to 6 threads. Conflict resolution and
         the actual upload will be handled by :meth:`_create_remote_entry` as follows:
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

    :param DropboxClient client: Dropbox API client instance.

    """

    _max_history = 30
    _num_threads = min(32, os.cpu_count() * 3)

    def __init__(self, client):

        self.client = client
        self.config_name = self.client.config_name
        self.cancel_pending = Event()
        self.fs_events = None

        self.sync_lock = RLock()
        self._rev_lock = RLock()

        self._conf = MaestralConfig(self.config_name)
        self._state = MaestralState(self.config_name)
        self._notifier = MaestralDesktopNotifier.for_config(self.config_name)

        self.download_errors = PersistentStateMutableSet(
            self.config_name, section='sync', option='download_errors'
        )
        self.pending_downloads = PersistentStateMutableSet(
            self.config_name, section='sync', option='pending_downloads'
        )

        # queues used for internal communication
        self.sync_errors = Queue()  # entries are `SyncIssue` instances
        self.queued_newly_included_downloads = Queue()  # entries are local_paths

        # the following queues are only for monitoring / user info
        # and are expected to contain correctly cased local paths or Dropbox paths
        self.queued_for_download = Queue()
        self.queued_for_upload = Queue()
        self.queue_uploading = Queue()
        self.queue_downloading = Queue()

        # load cached properties
        self._dropbox_path = osp.realpath(self._conf.get('main', 'path'))
        self._mignore_path = osp.join(self._dropbox_path, MIGNORE_FILE)
        self._file_cache_path = osp.join(self._dropbox_path, FILE_CACHE)
        self._rev_file_path = get_data_path('maestral', f'{self.config_name}.index')
        # check for home, update later
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
    def dropbox_path(self):
        """
        Path to local Dropbox folder, as loaded from the config file. Before changing
        :attr:`dropbox_path`, make sure that syncing is paused. Move the dropbox folder to
        the new location before resuming the sync. Changes are saved to the config file.
        """
        return self._dropbox_path

    @dropbox_path.setter
    def dropbox_path(self, path):
        """Setter: dropbox_path"""

        path = osp.realpath(path)

        with self.sync_lock:
            self._dropbox_path = path
            self._mignore_path = osp.join(self._dropbox_path, MIGNORE_FILE)
            self._file_cache_path = osp.join(self._dropbox_path, FILE_CACHE)
            self._is_case_sensitive = is_fs_case_sensitive(self._dropbox_path)
            self._conf.set('main', 'path', path)

    @property
    def rev_file_path(self):
        """Path to sync index with rev numbers (read only)."""
        return self._rev_file_path

    @property
    def file_cache_path(self):
        """Path to cache folder for temporary files (read only). The cache folder
        '.maestral.cache' is located inside the local Dropbox folder to prevent
        file transfer between different partitions or drives during sync."""
        return self._file_cache_path

    @property
    def excluded_items(self):
        """List of all files and folders excluded from sync. Changes are saved to the
        config file. If a parent folder is excluded, its children will automatically be
        removed from the list. If only children are given but not the parent folder,
        any new items added to the parent will be synced. Change this property *before*
        downloading newly included items or deleting excluded items."""
        return self._excluded_items

    @excluded_items.setter
    def excluded_items(self, folder_list):
        """Setter: excluded_items"""

        with self.sync_lock:
            clean_list = self.clean_excluded_items_list(folder_list)
            self._excluded_items = clean_list
            self._conf.set('main', 'excluded_items', clean_list)

    @staticmethod
    def clean_excluded_items_list(folder_list):
        """
        Removes all duplicates and children of excluded items from the excluded items
        list.

        :param list folder_list: Items to exclude.
        :returns: Cleaned up items.
        :rtype: list[str]
        """

        # remove duplicate entries by creating set, strip trailing '/'
        folder_list = set(f.lower().rstrip('/') for f in folder_list)

        # remove all children of excluded folders
        clean_list = list(folder_list)
        for folder in folder_list:
            clean_list = [f for f in clean_list if not is_child(f, folder)]

        return clean_list

    @property
    def max_cpu_percent(self):
        """Maximum CPU usage for parallel downloads or uploads in percent of the total
        available CPU time per core. Individual workers in a thread pool will pause until
        the usage drops below this value. Tasks in the main thread such as indexing file
        changes may still use more CPU time. Setting this to 100% means that no limits on
        CPU usage will be applied."""
        return self._max_cpu_percent

    @max_cpu_percent.setter
    def max_cpu_percent(self, percent):
        """Setter: max_cpu_percent."""
        self._max_cpu_percent = percent
        self._conf.set('app', 'max_cpu_percent', percent // _cpu_count)

    # ==== sync state ====================================================================

    @property
    def last_cursor(self):
        """Cursor from last sync with remote Dropbox. The value is updated and saved to
        the config file on every successful download of remote changes."""
        return self._state.get('sync', 'cursor')

    @last_cursor.setter
    def last_cursor(self, cursor):
        """Setter: last_cursor"""
        with self.sync_lock:
            self._state.set('sync', 'cursor', cursor)
            logger.debug('Remote cursor saved: %s', cursor)

    @property
    def last_sync(self):
        """Time stamp from last sync with remote Dropbox. The value is updated and
        saved to the config file on every successful upload of local changes."""
        return self._state.get('sync', 'lastsync')

    @last_sync.setter
    def last_sync(self, last_sync):
        """Setter: last_sync"""
        with self.sync_lock:
            logger.debug('Local cursor saved: %s', last_sync)
            self._state.set('sync', 'lastsync', last_sync)

    @property
    def last_reindex(self):
        """Time stamp of last indexing. This is used to determine when the next full
        indexing should take place."""
        return self._state.get('sync', 'last_reindex')

    @last_reindex.setter
    def last_reindex(self, time_stamp):
        """Setter: last_reindex."""
        self._state.set('sync', 'last_reindex', time_stamp)

    def get_last_sync_for_path(self, dbx_path):
        """
        Returns the timestamp of last sync for an individual path.

        :param str dbx_path: Path relative to Dropbox folder.
        :returns: Time of last sync.
        :rtype: float
        """
        dbx_path = dbx_path.lower()
        return max(self._last_sync_for_path.get(dbx_path, 0.0), self.last_sync)

    def set_last_sync_for_path(self, dbx_path, last_sync):
        """
        Sets the timestamp of last sync for a path.

        :param str dbx_path: Path relative to Dropbox folder.
        :param float last_sync: Time of last sync.
        """
        dbx_path = dbx_path.lower()
        if last_sync == 0.0:
            try:
                del self._last_sync_for_path[dbx_path]
            except KeyError:
                pass
        else:
            self._last_sync_for_path[dbx_path] = last_sync

    # ==== rev file management ===========================================================

    def get_rev_index(self):
        """
        Returns a copy of the revision index containing the revision
        numbers for all synced files and folders.

        :returns: Copy of revision index.
        :rtype: dict
        """
        with self._rev_lock:
            return self._rev_dict_cache.copy()

    def get_local_rev(self, dbx_path):
        """
        Gets revision number of local file.

        :param str dbx_path: Path relative to Dropbox folder.
        :returns: Revision number as str or ``None`` if no local revision number
            has been saved.
        :rtype: str
        """
        with self._rev_lock:
            dbx_path = dbx_path.lower()
            rev = self._rev_dict_cache.get(dbx_path, None)

            return rev

    def set_local_rev(self, dbx_path, rev):
        """
        Saves revision number ``rev`` for local file. If ``rev`` is ``None``, the
        entry for the file is removed.

        :param str dbx_path: Path relative to Dropbox folder.
        :param str rev: Revision number as string or ``None``.
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

    def _clean_and_save_rev_file(self):
        """Cleans the revision index from duplicate entries and keeps only the last entry
        for any individual path. Then saves the index to the drive."""
        with self._rev_lock:
            self._save_rev_dict_to_file()

    def clear_rev_index(self):
        """Clears the revision index."""
        with self._rev_lock:
            self._rev_dict_cache.clear()
            self._save_rev_dict_to_file()

    @contextmanager
    def _handle_rev_read_exceptions(self, raise_exception=False):

        title = None
        new_exc = None

        try:
            yield
        except (FileNotFoundError, IsADirectoryError):
            logger.info('Maestral index could not be found')
            # reset sync state
            self.last_sync = 0.0
            self._rev_dict_cache = dict()
            self.last_cursor = ''
        except PermissionError as exc:
            title = 'Could not load index'
            msg = (f'Insufficient permissions for "{self.rev_file_path}". Please '
                   'make sure that you have read and write permissions.')
            new_exc = RevFileError(title, msg).with_traceback(exc.__traceback__)
        except OSError as exc:
            title = 'Could not load index'
            msg = 'Please resync your Dropbox to rebuild the index.'
            new_exc = RevFileError(title, msg).with_traceback(exc.__traceback__)

        if new_exc and raise_exception:
            raise new_exc
        elif new_exc:
            logger.error(title, exc_info=_exc_info(new_exc))

    @contextmanager
    def _handle_rev_write_exceptions(self, raise_exception=False):

        title = None
        new_exc = None

        try:
            yield
        except PermissionError as exc:
            title = 'Could not save index'
            msg = (f'Insufficient permissions for "{self.rev_file_path}". Please '
                   'make sure that you have read and write permissions.')
            new_exc = RevFileError(title, msg).with_traceback(exc.__traceback__)
        except OSError as exc:
            title = 'Could not save index'
            msg = 'Please check the logs for more information'
            new_exc = RevFileError(title, msg).with_traceback(exc.__traceback__)

        if new_exc and raise_exception:
            raise new_exc
        elif new_exc:
            logger.error(title, exc_info=_exc_info(new_exc))

    def _load_rev_dict_from_file(self, raise_exception=False):
        """
        Loads Maestral's rev index from ``rev_file_path``. Every line contains the rev
        number for a single path, saved in a json format. Only the last entry for each
        path is kept, overriding possible (older) previous entries.

        :param bool raise_exception: If ``True``, raises an exception when loading fails.
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
                        except json.decoder.JSONDecodeError as exc:
                            if line.endswith('\n'):
                                raise exc
                            else:
                                # last line of file, likely an interrupted write
                                pass

            # clean up empty revs
            for path, rev in self._rev_dict_cache.copy().items():
                if not rev:
                    del self._rev_dict_cache[path]

    def _save_rev_dict_to_file(self, raise_exception=False):
        """
        Save Maestral's rev index to ``rev_file_path``.

        :param bool raise_exception: If ``True``, raises an exception when saving fails.
            If ``False``, an error message is logged instead.
        :raises: :class:`errors.RevFileError`
        """
        with self._rev_lock:
            with self._handle_rev_write_exceptions(raise_exception):
                with atomic_write(self.rev_file_path, mode='w', overwrite=True) as f:
                    for path, rev in self._rev_dict_cache.items():
                        f.write(json.dumps({path: rev}) + '\n')

    def _append_rev_to_file(self, path, rev, raise_exception=False):
        """
        Appends a new line with a rev entry to the rev file. This is quicker than
        saving the entire rev index. When loading the rev file, older entries will be
        overwritten with newer ones and all entries with ``rev == None`` will be
        discarded.

        :param str path: Path for rev.
        :param str rev: Dropbox rev or ``None``.
        :param bool raise_exception: If ``True``, raises an exception when saving fails.
            If ``False``, an error message is logged instead.
        :raises: :class:`errors.RevFileError`
        """

        with self._rev_lock:
            with self._handle_rev_write_exceptions(raise_exception):
                with open(self.rev_file_path, mode='a') as f:
                    f.write(json.dumps({path: rev}) + '\n')

    # ==== mignore management ============================================================

    @property
    def mignore_path(self):
        """Path to mignore file on local drive (read only)."""
        return self._mignore_path

    @property
    def mignore_rules(self):
        """List of mignore rules following git wildmatch syntax (read only)."""
        if self._get_ctime(self.mignore_path) != self._mignore_ctime_loaded:
            self._mignore_rules = self._load_mignore_rules_form_file()
        return self._mignore_rules

    def _load_mignore_rules_form_file(self):
        """Loads rules from mignore file. No rules are loaded if the file does
        not exist or cannot be read."""
        self._mignore_ctime_loaded = self._get_ctime(self.mignore_path)
        try:
            with open(self.mignore_path) as f:
                spec = f.read()
        except OSError as exc:
            logger.debug(f'Could not load mignore rules from {self.mignore_path}: {exc}')
            spec = ''
        return pathspec.PathSpec.from_lines('gitwildmatch', spec.splitlines())

    # ==== helper functions ==============================================================

    @property
    def is_case_sensitive(self):
        return self._is_case_sensitive

    def ensure_dropbox_folder_present(self):
        """
        Checks if the Dropbox folder still exists where we expect it to be.

        :raises: :class:`errors.DropboxDeletedError`
        """

        if not osp.isdir(self.dropbox_path):
            title = 'Dropbox folder has been moved or deleted'
            msg = ('Please move the Dropbox folder back to its original location '
                   'or restart Maestral to set up a new folder.')
            raise NoDropboxDirError(title, msg)

    def _ensure_cache_dir_present(self):
        """Checks for or creates a directory at :attr:`file_cache_path`."""
        while not osp.isdir(self.file_cache_path):
            err = delete(self._file_cache_path)
            if err and not isinstance(err, FileNotFoundError):
                raise CacheDirError(f'Cannot create cache directory (errno {err.errno})',
                                    'Please check if you have write permissions for '
                                    f'{self.file_cache_path}.')
            try:
                os.mkdir(self.file_cache_path)
            except FileExistsError:
                pass
            except OSError:
                raise CacheDirError(f'Cannot create cache directory (errno {err.errno})',
                                    'Please check if you have write permissions for '
                                    f'{self.file_cache_path}.')

    def clean_cache_dir(self):
        """Removes all items in the cache directory."""

        with self.sync_lock:
            err = delete(self._file_cache_path)
            if err and not isinstance(err, FileNotFoundError):
                raise CacheDirError(f'Cannot create cache directory (errno {err.errno})',
                                    'Please check if you have write permissions for '
                                    f'{self._file_cache_path}.')

    def _new_tmp_file(self):
        self._ensure_cache_dir_present()
        try:
            with tempfile.NamedTemporaryFile(dir=self.file_cache_path, delete=False) as f:
                return f.name
        except OSError as exc:
            raise CacheDirError(f'Cannot create temporary file (errno {exc.errno})',
                                'Please check if you have write permissions for '
                                f'{self._file_cache_path}.')

    def to_dbx_path(self, local_path):
        """
        Converts a local path to a path relative to the Dropbox folder. Casing of the
        given ``local_path`` will be preserved.

        :param str local_path: Absolute path on local drive.
        :returns: Relative path with respect to Dropbox folder.
        :rtype: str
        :raises: :class:`ValueError` the path lies outside of the local Dropbox folder.
        """

        if is_equal_or_child(local_path, self.dropbox_path):
            dbx_path = osp.sep + local_path.replace(self.dropbox_path, '', 1).lstrip(osp.sep)
            return dbx_path.replace(osp.sep, '/')
        else:
            raise ValueError(f'Specified path "{local_path}" is outside of Dropbox '
                             f'directory "{self.dropbox_path}"')

    def to_local_path(self, dbx_path):
        """
        Converts a Dropbox path to the corresponding local path.

        The ``path_display`` attribute returned by the Dropbox API only guarantees correct
        casing of the basename and not of the full path. This is because Dropbox itself is
        not case sensitive and stores all paths in lowercase internally. To the extent
        where parent directories of ``dbx_path`` exist on the local drive, their casing
        will be used. Otherwise, the casing from ``dbx_path`` is used. This aims to
        preserve the correct casing of file and folder names and prevents the creation of
        duplicate folders with different casing on a local case-sensitive file system.

        :param str dbx_path: Path relative to Dropbox folder.
        :returns: Corresponding local path on drive.
        :rtype: str
        """

        dbx_path = dbx_path.replace('/', osp.sep)
        dbx_path_parent, dbx_path_basename = osp.split(dbx_path)

        local_parent = to_cased_path(dbx_path_parent, root=self.dropbox_path)

        return osp.join(local_parent, dbx_path_basename)

    def get_local_path(self, md):
        """
        Gets the corresponding local path for a Dropbox MetaData instance by calling
        :meth:`to_local_path` on the ``path_display`` attribute. The concerted path is
        stored as a new ``to_local_path`` attribute to speed up future calls.

        :param MetaData md: Dropbox metadata.
        :returns: Corresponding local path on drive.
        :rtype: str
        """

        if not hasattr(md, 'local_path'):
            md.local_path = self.to_local_path(md.path_display)

        return md.local_path

    def has_sync_errors(self):
        """Returns ``True`` in case of sync errors, ``False`` otherwise."""
        return self.sync_errors.qsize() > 0

    def clear_sync_error(self, local_path=None, dbx_path=None):
        """
        Clears all sync errors for ``local_path`` or ``dbx_path``.

        :param Optional[str] local_path: Absolute path on local drive.
        :param Optional[str] dbx_path: Path relative to Dropbox folder.
        :raises: :class:`ValueError` if no path is given.
        """
        if not (local_path or dbx_path):
            raise ValueError('Either local_path or dbx_path must be given.')

        if not dbx_path:
            dbx_path = self.to_dbx_path(local_path)

        if self.has_sync_errors():
            for error in list(self.sync_errors.queue):
                equal = error.dbx_path.lower() == dbx_path.lower()
                child = is_child(error.dbx_path.lower(), dbx_path.lower())
                if equal or child:
                    remove_from_queue(self.sync_errors, error)

        self.download_errors.discard(dbx_path)

    def clear_all_sync_errors(self):
        """Clears all sync errors."""
        with self.sync_errors.mutex:
            self.sync_errors.queue.clear()
        self.download_errors.clear()

    @staticmethod
    def is_excluded(path):
        """
        Checks if a file is excluded from sync. Certain file names are always excluded
        from syncing, following the Dropbox support article:

        https://help.dropbox.com/installs-integrations/sync-uploads/files-not-syncing

        This includes file system files such as 'desktop.ini' and '.DS_Store' and some
        temporary files as well as caches used by Dropbox or Maestral. `is_excluded`
        accepts both local and Dropbox paths.

        :param str path: Path of item. Can be both a local or Dropbox paths.
        :returns: ``True`` if excluded, ``False`` otherwise.
        :rtype: bool
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

    def is_excluded_by_user(self, dbx_path):
        """
        Check if file has been excluded from sync by the user.

        :param str dbx_path: Path relative to Dropbox folder.
        :returns: ``True`` if excluded, ``False`` otherwise.
        :rtype: bool
        """
        dbx_path = dbx_path.lower()

        return any(is_equal_or_child(dbx_path, path) for path in self.excluded_items)

    def is_mignore(self, event):
        """
        Check if local file change has been excluded by an mignore pattern.

        :param FileSystemEvent event: Local file event.
        :returns: ``True`` if excluded, ``False`` otherwise.
        :rtype: bool
        """
        if len(self.mignore_rules.patterns) == 0:
            return False

        dbx_path = self.to_dbx_path(event.src_path)

        return (self._is_mignore_path(dbx_path, is_dir=event.is_directory)
                and not self.get_local_rev(dbx_path))

    def _is_mignore_path(self, dbx_path, is_dir=False):

        relative_path = dbx_path.lstrip('/')

        if is_dir:
            relative_path += '/'

        return self.mignore_rules.match_file(relative_path)

    def _slow_down(self):

        if self._max_cpu_percent == 100:
            return

        if 'pool' in current_thread().name:
            cpu_usage = cpu_usage_percent()
            while cpu_usage > self._max_cpu_percent:
                cpu_usage = cpu_usage_percent(0.5 + 2 * random.random())

    def busy(self):
        """
        Checks if we are currently syncing.

        :returns: ``True`` if :attr:`sync_lock` cannot be aquired, ``False`` otherwise.
        :rtype: bool
        """

        idle = self.sync_lock.acquire(blocking=False)
        if idle:
            self.sync_lock.release()

        return not idle

    # ==== Upload sync ===================================================================

    def upload_local_changes_while_inactive(self):
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
            except FileNotFoundError:
                self.ensure_dropbox_folder_present()
                return

            if len(events) > 0:
                self.apply_local_changes(events, local_cursor)
                logger.debug('Uploaded local changes while inactive')
            else:
                self.last_sync = local_cursor
                logger.debug('No local changes while inactive')

    def _get_local_changes_while_inactive(self):

        changes = []
        now = time.time()
        snapshot = DirectorySnapshot(self.dropbox_path)

        # remove root entry from snapshot
        try:
            del snapshot._inode_to_path[snapshot.inode(self.dropbox_path)]
            del snapshot._stat_info[self.dropbox_path]
        except KeyError:
            pass

        # get lowercase paths
        lowercase_snapshot_paths = {x.lower() for x in snapshot.paths}

        # get modified or added items
        for path in snapshot.paths:
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
            # warning: local_path may not be correctly cased
            local_path = self.to_local_path(path)
            if local_path.lower() not in lowercase_snapshot_paths:
                if rev_dict_copy[path] == 'folder':
                    event = DirDeletedEvent(local_path)
                else:
                    event = FileDeletedEvent(local_path)
                changes.append(event)

        del snapshot
        del lowercase_snapshot_paths

        return changes, now

    def wait_for_local_changes(self, timeout=5, delay=1):
        """
        Waits for local file changes. Returns a list of local changes with at most one
        entry per path.

        :param float timeout: If no changes are detected within timeout (sec), an empty
            list is returned.
        :param float delay: Delay in sec to wait for subsequent changes that may be
            duplicates.
        :returns: (list of file events, time_stamp)
        :rtype: (list, float)
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
                events.append(self.fs_events.local_file_event_queue.get(timeout=delay))
                local_cursor = time.time()
            except Empty:
                break

        logger.debug('Retrieved local file events:\n%s', pprint.pformat(events))

        return self._clean_local_events(events), local_cursor

    def apply_local_changes(self, events, local_cursor):
        """
        Applies locally detected changes to the remote Dropbox.

        :param iterable events: List of local file system events.
        :param float local_cursor: Time stamp of last event in ``events``.
        """

        with self.sync_lock:

            events, _ = self._filter_excluded_changes_local(events)

            sorted_events = dict(deleted=[], dir_created=[], dir_moved=[], file=[])
            for e in events:
                if e.event_type == EVENT_TYPE_DELETED:
                    sorted_events['deleted'].append(e)
                elif e.is_directory and e.event_type == EVENT_TYPE_CREATED:
                    sorted_events['dir_created'].append(e)
                elif e.is_directory and e.event_type == EVENT_TYPE_MOVED:
                    sorted_events['dir_moved'].append(e)
                elif not e.is_directory and e.event_type != EVENT_TYPE_DELETED:
                    sorted_events['file'].append(e)

            # update queues
            for e in itertools.chain(*sorted_events.values()):
                self.queued_for_upload.put(get_dest_path(e))

            success = []

            # apply deleted events first, folder moved events second
            # neither event type requires an actual upload
            if sorted_events['deleted']:
                logger.info('Uploading deletions...')

            last_emit = time.time()
            with ThreadPoolExecutor(max_workers=self._num_threads,
                                    thread_name_prefix='maestral-upload-pool') as executor:
                fs = (executor.submit(self._create_remote_entry, e)
                      for e in sorted_events['deleted'])
                n_files = len(sorted_events['deleted'])
                for f, n in zip(as_completed(fs), range(1, n_files + 1)):
                    if time.time() - last_emit > 1 or n in (1, n_files):
                        # emit message at maximum every second
                        logger.info(f'Deleting {n}/{n_files}...')
                        last_emit = time.time()
                    success.append(f.result())

            if sorted_events['dir_moved']:
                logger.info('Moving folders...')

            for event in sorted_events['dir_moved']:
                logger.info(f'Moving {event.src_path}...')
                res = self._create_remote_entry(event)
                success.append(res)

            # apply file created events in parallel since order does not matter
            last_emit = time.time()
            with ThreadPoolExecutor(max_workers=self._num_threads,
                                    thread_name_prefix='maestral-upload-pool') as executor:
                fs = (executor.submit(self._create_remote_entry, e) for e in
                      itertools.chain(sorted_events['dir_created'], sorted_events['file']))
                n_files = len(sorted_events['dir_created']) + len(sorted_events['file'])
                for f, n in zip(as_completed(fs), range(1, n_files + 1)):
                    if time.time() - last_emit > 1 or n in (1, n_files):
                        # emit message at maximum every second
                        logger.info(f'Uploading {n}/{n_files}...')
                        last_emit = time.time()
                    success.append(f.result())

            # bookkeeping

            if all(success):
                self.last_sync = local_cursor

            self._clean_and_save_rev_file()

    def _filter_excluded_changes_local(self, events):
        """
        Checks for and removes file events referring to items which are excluded from
        syncing.

        :param list events: List of file events.
        :returns: (``events_filtered``, ``events_excluded``)
        """

        events_filtered = []
        events_excluded = []

        for event in events:

            local_path = get_dest_path(event)

            if self.is_excluded(local_path):
                events_excluded.append(event)
            elif self.is_mignore(event):
                # moved events with an ignored path are
                # already split into deleted, created pairs
                events_excluded.append(event)
            else:
                events_filtered.append(event)

        logger.debug('Filtered local file events:\n%s', pprint.pformat(events_filtered))

        return events_filtered, events_excluded

    def _clean_local_events(self, events):
        """
        Takes local file events within the monitored period and cleans them up so that
        there is only a single event per path. Collapses moved and deleted events of
        folders with those of their children.

        :param events: Iterable of :class:`watchdog.FileSystemEvent`.
        :returns: List of :class:`watchdog.FileSystemEvent`.
        :rtype: list
        """

        # COMBINE EVENTS TO ONE EVENT PER PATH

        # Move events are difficult to combine with other event types, we split them into
        # deleted and created events and recombine them later if none of the paths has
        # other events associated with it or is excluded from sync.

        histories = dict()
        for i, event in enumerate(events):
            if event.event_type == EVENT_TYPE_MOVED:
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
        moved_events = dict()
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
            child_moved_events = dict()
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
            child_deleted_events = dict()
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

    def _should_split_excluded(self, event):

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

    def _should_split_mignore(self, event):
        if len(self.mignore_rules.patterns) == 0:
            return False

        dbx_src_path = self.to_dbx_path(event.src_path)
        dbx_dest_path = self.to_dbx_path(event.dest_path)

        return (self._is_mignore_path(dbx_src_path, event.is_directory)
                or self._is_mignore_path(dbx_dest_path, event.is_directory))

    def _handle_case_conflict(self, event):
        """
        Checks for other items in the same directory with same name but a different
        case. Renames items if necessary. Only needed for case sensitive file systems.

        :param FileSystemEvent event: Created or moved event.
        :returns: ``True`` or ``False``.
        :rtype: bool
        """

        if not self.is_case_sensitive:
            return False

        if event.event_type not in (EVENT_TYPE_CREATED, EVENT_TYPE_MOVED):
            return False

        # get the created path (src_path or dest_path)
        local_path = get_dest_path(event)
        dirname, basename = osp.split(local_path)

        # check number of paths with the same case
        if len(cased_path_candidates(basename, root=dirname)) > 1:

            local_path_cc = generate_cc_name(local_path, suffix='case conflict')

            event_cls = DirMovedEvent if osp.isdir(local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(local_path, local_path_cc)):
                exc = move(local_path, local_path_cc)
                if exc:
                    raise os_to_maestral_error(exc, local_path=local_path_cc)

                self._rescan(local_path_cc)

            logger.info('Case conflict: renamed "%s" to "%s"', local_path, local_path_cc)

            return True
        else:
            return False

    def _handle_selective_sync_conflict(self, event):
        """
        Checks for items in the local directory with same path as an item which is
        excluded by selective sync. Renames items if necessary.

        :param FileSystemEvent event: Created or moved event.
        :returns: ``True`` or ``False``.
        :rtype: bool
        """

        if event.event_type not in (EVENT_TYPE_CREATED, EVENT_TYPE_MOVED):
            return False

        local_path = get_dest_path(event)
        dbx_path = self.to_dbx_path(local_path)

        if self.is_excluded_by_user(dbx_path):
            local_path_cc = generate_cc_name(local_path,
                                             suffix='selective sync conflict')

            event_cls = DirMovedEvent if osp.isdir(local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(local_path, local_path_cc)):
                exc = move(local_path, local_path_cc)
                if exc:
                    raise os_to_maestral_error(exc, local_path=local_path_cc)

                self._rescan(local_path_cc)

            logger.info('Selective sync conflict: renamed "%s" to "%s"',
                        local_path, local_path_cc)
            return True
        else:
            return False

    @catch_sync_issues(download=False)
    def _create_remote_entry(self, event):
        """
        Applies a local file system event to the remote Dropbox and clears any existing
        sync errors belonging to that path. Any :class:`errors.MaestralApiError` will be
        caught and logged as appropriate.

        :param FileSystemEvent event: Watchdog file system event.
        """

        if self.cancel_pending.is_set():
            return False

        self._slow_down()

        # book keeping
        local_path_from = event.src_path
        local_path_to = get_dest_path(event)

        remove_from_queue(self.queued_for_upload, local_path_from, local_path_to)
        self.clear_sync_error(local_path=local_path_to)
        self.clear_sync_error(local_path=local_path_from)

        with InQueue(self.queue_uploading, local_path_to):
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

        :param str path: Absolute path to file
        """
        try:
            while True:
                size1 = osp.getsize(path)
                time.sleep(0.2)
                size2 = osp.getsize(path)
                if size1 == size2:
                    return
        except OSError:
            return

    def _on_moved(self, event):
        """
        Call when a local item is moved.

        Keep in mind that we may be moving a whole tree of items. But its better deal
        with the complexity than to delete and re-uploading everything. Thankfully, in
        case of directories, we always process the top-level first. Trying to move the
        children will then be delegated to `on_create` (because the old item no longer
        lives on Dropbox) and that won't upload anything because file contents have
        remained the same.

        :param FilSystemEvent event: Watchdog file system event.
        :raises: :class:`errors.MaestralApiError`
        """

        local_path_from = event.src_path
        local_path_to = event.dest_path

        dbx_path_from = self.to_dbx_path(local_path_from)
        dbx_path_to = self.to_dbx_path(local_path_to)

        if self._handle_selective_sync_conflict(event):
            return
        if self._handle_case_conflict(event):
            return

        md_from_old = self.client.get_metadata(dbx_path_from)

        # If not on Dropbox, e.g., because its old name was invalid,
        # create it instead of moving it.
        if not md_from_old:
            if isinstance(event, DirMovedEvent):
                new_event = DirCreatedEvent(local_path_to)
            else:
                new_event = FileCreatedEvent(local_path_to)
            return self._on_created(new_event)

        md_to_new = self.client.move(dbx_path_from, dbx_path_to, autorename=True)

        self.set_local_rev(dbx_path_from, None)

        # handle remote conflicts
        if md_to_new.path_lower != dbx_path_to.lower():
            logger.info('Upload conflict: renamed "%s" to "%s"',
                        dbx_path_to, md_to_new.path_display)
        else:
            self._set_local_rev_recursive(md_to_new)
            logger.debug('Moved "%s" to "%s" on Dropbox', dbx_path_from, dbx_path_to)

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

    def _on_created(self, event):
        """
        Call when a local item is created.

        :param FileSystemEvent event: Watchdog file system event.
        :raises: :class:`errors.MaestralApiError`
        """

        local_path = event.src_path

        if self._handle_selective_sync_conflict(event):
            return
        if self._handle_case_conflict(event):
            return

        dbx_path = self.to_dbx_path(local_path)

        self._wait_for_creation(local_path)

        if event.is_directory:
            try:
                md_new = self.client.make_dir(dbx_path, autorename=False)
            except FolderConflictError:
                logger.debug('No conflict for "%s": the folder already exists',
                             event.src_path)
                self.set_local_rev(dbx_path, 'folder')
                return
            except FileConflictError:
                md_new = self.client.make_dir(dbx_path, autorename=True)

        else:
            # check if file already exists with identical content
            md_old = self.client.get_metadata(dbx_path)
            if isinstance(md_old, FileMetadata):
                local_hash = get_local_hash(local_path)
                if local_hash == md_old.content_hash:
                    # file hashes are identical, do not upload
                    self.set_local_rev(md_old.path_lower, md_old.rev)
                    return

            rev = self.get_local_rev(dbx_path)
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
                             'already tracking it', dbx_path)
                mode = dropbox.files.WriteMode.update(rev)
            try:
                md_new = self.client.upload(local_path, dbx_path,
                                            autorename=True, mode=mode)
            except NotFoundError:
                logger.debug('Could not upload "%s": the item does not exist',
                             event.src_path)
                return

        if md_new.path_lower != dbx_path.lower():
            # conflicting copy created during upload, mirror remote changes locally
            local_path_cc = self.get_local_path(md_new)
            event_cls = DirMovedEvent if osp.isdir(local_path) else FileMovedEvent
            with self.fs_events.ignore(event_cls(local_path, local_path_cc)):
                exc = move(local_path, local_path_cc)
                if exc:
                    raise os_to_maestral_error(exc, local_path=local_path_cc,
                                               dbx_path=md_new.path_display)

            # Delete revs of old path but don't set revs for new path here. This will
            # force conflict resolution on download in case of intermittent changes.
            self.set_local_rev(dbx_path, None)
            logger.debug('Upload conflict: renamed "%s" to "%s"',
                         dbx_path, md_new.path_lower)
        else:
            # everything went well, save new revs
            rev = getattr(md_new, 'rev', 'folder')
            self.set_local_rev(md_new.path_lower, rev)
            logger.debug('Created "%s" on Dropbox', dbx_path)

    def _on_created_folders_batch(self, events):
        """
        Creates multiple folders on Dropbox in a batch request. We currently don't use
        this because folders are created in parallel anyways and we perform conflict
        checks before uploading each folder.

        :param list[DirCreatedEvent] events: List of directory creates events.
        :raises: :class:`errors.MaestralApiError`
        """
        local_paths = []
        dbx_paths = []

        for e in events:
            local_path = e.src_path
            dbx_path = self.to_dbx_path(local_path)
            if not e.is_directory:
                raise ValueError('All events must be of type '
                                 'watchdog.events.DirCreatedEvent')

            if (not self._handle_case_conflict(e)
                    and not self._handle_selective_sync_conflict(e)):

                md_old = self.client.get_metadata(dbx_path)
                if isinstance(md_old, FolderMetadata):
                    self.set_local_rev(dbx_path, 'folder')

                local_paths.append(local_path)
                dbx_paths.append(dbx_path)

        res_list = self.client.make_dir_batch(dbx_paths, autorename=True)

        for res, dbx_path, local_path in zip(res_list, dbx_paths, local_paths):
            if isinstance(res, Metadata):
                if res.path_lower != dbx_path.lower():
                    # conflicting copy created during upload, mirror remote changes
                    # locally
                    local_path_cc = self.get_local_path(res)
                    event_cls = DirMovedEvent if osp.isdir(local_path) else FileMovedEvent
                    with self.fs_events.ignore(event_cls(local_path, local_path_cc)):
                        exc = move(local_path, local_path_cc)
                        if exc:
                            raise os_to_maestral_error(exc, local_path=local_path_cc,
                                                       dbx_path=res.path_display)

                    # Delete revs of old path but don't set revs for new path here. This
                    # will force conflict resolution on download in case of intermittent
                    # changes.
                    self.set_local_rev(dbx_path, None)
                    logger.debug('Upload conflict: renamed "%s" to "%s"',
                                 dbx_path, res.path_lower)
                else:
                    # everything went well, save new revs
                    self.set_local_rev(res.path_lower, 'folder')
                    logger.debug('Created "%s" on Dropbox', dbx_path)

            elif isinstance(res, SyncError):
                res.local_path = local_path
                self.sync_errors.put(res)

    def _on_modified(self, event):
        """
        Call when local item is modified.

        :param FileSystemEvent event: Watchdog file system event.
        :raises: :class:`errors.MaestralApiError`
        """

        if not event.is_directory:  # ignore directory modified events

            local_path = event.src_path
            dbx_path = self.to_dbx_path(local_path)

            self._wait_for_creation(local_path)

            # check if item already exists with identical content
            md_old = self.client.get_metadata(dbx_path)
            if isinstance(md_old, FileMetadata):
                local_hash = get_local_hash(local_path)
                if local_hash == md_old.content_hash:
                    # file hashes are identical, do not upload
                    self.set_local_rev(md_old.path_lower, md_old.rev)
                    logger.debug('Modification of "%s" detected but file content is '
                                 'the same as on Dropbox', dbx_path)
                    return

            rev = self.get_local_rev(dbx_path)
            if rev == 'folder':
                mode = dropbox.files.WriteMode.overwrite
            elif not rev:
                logger.debug('"%s" appears to have been modified but cannot '
                             'find old revision', dbx_path)
                mode = dropbox.files.WriteMode.add
            else:
                mode = dropbox.files.WriteMode.update(rev)

            try:
                md_new = self.client.upload(local_path, dbx_path,
                                            autorename=True, mode=mode)
            except NotFoundError:
                logger.debug('Could not upload "%s": the item does not exist', dbx_path)
                return

            if md_new.path_lower != dbx_path.lower():
                # conflicting copy created during upload, mirror remote changes locally
                local_path_cc = self.get_local_path(md_new)
                with self.fs_events.ignore(FileMovedEvent(local_path, local_path_cc)):
                    try:
                        os.rename(local_path, local_path_cc)
                    except OSError:
                        with self.fs_events.ignore(FileDeletedEvent(local_path)):
                            delete(local_path)

                # Delete revs of old path but don't set revs for new path here. This will
                # force conflict resolution on download in case of intermittent changes.
                self.set_local_rev(dbx_path, None)
                logger.debug('Upload conflict: renamed "%s" to "%s"',
                             dbx_path, md_new.path_lower)
            else:
                # everything went well, save new revs
                self.set_local_rev(md_new.path_lower, md_new.rev)
                logger.debug('Uploaded modified "%s" to Dropbox', md_new.path_lower)

    def _on_deleted(self, event):
        """
        Call when local item is deleted. We try not to delete remote items which have been
        modified since the last sync.

        :param FileSystemEvent event: Watchdog file system event.
        :raises: :class:`errors.MaestralApiError`
        """

        path = event.src_path
        dbx_path = self.to_dbx_path(path)

        if self.is_excluded_by_user(dbx_path):
            logger.debug('Not deleting "%s": is excluded by selective sync', dbx_path)
            return

        local_rev = self.get_local_rev(dbx_path)
        is_file = not event.is_directory
        is_directory = event.is_directory

        md = self.client.get_metadata(dbx_path, include_deleted=True)

        if is_directory and isinstance(md, FileMetadata):
            logger.debug('Expected folder at "%s" but found a file instead, checking '
                         'which one is newer', md.path_display)
            # don't delete a remote file if it was modified since last sync
            if md.server_modified.timestamp() >= self.get_last_sync_for_path(dbx_path):
                logger.debug('Skipping deletion: remote item "%s" has been modified '
                             'since last sync', md.path_display)
                # mark local folder as untracked
                self.set_local_rev(dbx_path, None)
                return

        if is_file and isinstance(md, FolderMetadata):
            # don't delete a remote folder if we were expecting a file
            # TODO: Delete the folder if its children did not change since last sync.
            #   Is there a way of achieving this without listing the folder or listing
            #   all changes and checking when they occurred?
            logger.debug('Skipping deletion: expected file at "%s" but found a '
                         'folder instead', md.path_display)
            # mark local file as untracked
            self.set_local_rev(dbx_path, None)
            return

        try:
            # will only perform delete if Dropbox remote rev matches `local_rev`
            self.client.remove(dbx_path, parent_rev=local_rev if is_file else None)
        except NotFoundError:
            logger.debug('Could not delete "%s": the item no longer exists on Dropbox',
                         dbx_path)
        except PathError:
            logger.debug('Could not delete "%s": the item has been changed '
                         'since last sync', dbx_path)

        # remove revision metadata
        self.set_local_rev(dbx_path, None)

    # ==== Download sync =================================================================

    @catch_sync_issues(download=True)
    def get_remote_folder(self, dbx_path='/', ignore_excluded=True):
        """
        Gets all files/folders from Dropbox and writes them to the local folder
        :attr:`dropbox_path`. Call this method on first run of the Maestral. Indexing
        and downloading may take several minutes, depending on the size of the user's
        Dropbox folder.

        :param str dbx_path: Path relative to Dropbox folder. Defaults to root ('/').
        :param bool ignore_excluded: If ``True``, do not index excluded folders.
        :returns: ``True`` on success, ``False`` otherwise.
        :rtype: bool
        """

        with self.sync_lock:

            dbx_path = dbx_path or '/'
            is_dbx_root = dbx_path == '/'
            success = []

            if is_dbx_root:
                logger.info('Downloading your Dropbox')
            else:
                logger.info('Downloading %s', dbx_path)

            if not any(is_child(folder, dbx_path) for folder in self.excluded_items):
                # if there are no excluded subfolders, index and download all at once
                ignore_excluded = False

            # get a cursor for the folder
            cursor = self.client.get_latest_cursor(dbx_path)

            root_result = self.client.list_folder(dbx_path,
                                                  recursive=(not ignore_excluded),
                                                  include_deleted=False)

            # download top-level folders / files first
            logger.info(SYNCING)
            _, s = self.apply_remote_changes(root_result, save_cursor=False)
            success.append(s)

            if ignore_excluded:
                # download sub-folders if not excluded
                for entry in root_result.entries:
                    if isinstance(entry, FolderMetadata) and not self.is_excluded_by_user(
                            entry.path_display):
                        success.append(self.get_remote_folder(entry.path_display))

            if is_dbx_root:
                self.last_cursor = cursor
                self.last_reindex = time.time()

            return all(success)

    def get_remote_item(self, dbx_path):
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

        :param str dbx_path: Path relative to Dropbox folder.
        :returns: ``True`` on success, ``False`` otherwise.
        :rtype: bool
        """

        with self.sync_lock:

            self.pending_downloads.add(dbx_path)
            md = self.client.get_metadata(dbx_path, include_deleted=True)

            if isinstance(md, FolderMetadata):
                res = self.get_remote_folder(dbx_path)
            elif md:  # FileMetadata or DeletedMetadata
                with InQueue(self.queue_downloading, md.path_display):
                    res = self._create_local_entry(md)

            if res is True:
                self.pending_downloads.discard(dbx_path)

            return res

    def wait_for_remote_changes(self, last_cursor, timeout=40, delay=2):
        """
        Blocks until changes to the remote Dropbox are available.

        :param str last_cursor: Cursor form last sync.
        :param int timeout: Timeout in seconds before returning even if there are no
            changes. Dropbox adds random jitter of up to 90 sec to this value.
        :param float delay: Delay in sec to wait for subsequent changes that may be
            duplicates. This delay is typically only necessary folders are shared /
            un-shared with other Dropbox accounts.
        """
        logger.debug('Waiting for remote changes since cursor:\n%s', last_cursor)
        has_changes = self.client.wait_for_remote_changes(last_cursor, timeout=timeout)
        time.sleep(delay)
        logger.debug('Detected remote changes: %s', has_changes)
        return has_changes

    def list_remote_changes(self, last_cursor):
        """
        Lists remote changes since the last download sync.

        :param str last_cursor: Cursor from last download sync.
        :returns: Remote changes.
        :rtype: :class:`dropbox.files.ListFolderResult`
        """
        changes = self.client.list_remote_changes(last_cursor)
        logger.debug('Listed remote changes:\n%s', entries_to_str(changes.entries))
        clean_changes = self._clean_remote_changes(changes)
        logger.debug('Cleaned remote changes:\n%s', entries_to_str(clean_changes.entries))
        return clean_changes

    def apply_remote_changes(self, changes, save_cursor=True):
        """
        Applies remote changes to local folder. Call this on the result of
        :meth:`list_remote_changes`. The saved cursor is updated after a set of changes
        has been successfully applied. Entries in the local index are created after
        successful completion.

        :param changes: :class:`dropbox.files.ListFolderResult` instance.
        :param bool save_cursor: If ``True``, :attr:`last_cursor` will be updated from
            the last applied changes. Take care to only save a cursors which represent
            the state of the entire Dropbox.
        :returns: List of changes that were made to local files and bool indicating if all
            download syncs were successful.
        :rtype: (list, bool)
        """

        if not changes:
            return False

        # filter out excluded changes
        changes_included, changes_excluded = self._filter_excluded_changes_remote(changes)

        # remove all deleted items from the excluded list
        _, _, deleted_excluded = self._separate_remote_entry_types(changes_excluded)
        for d in deleted_excluded:
            new_excluded = [item for item in self.excluded_items
                            if not is_equal_or_child(item, d.path_lower)]
            self.excluded_items = new_excluded

        # sort changes into folders, files and deleted
        folders, files, deleted = self._separate_remote_entry_types(changes_included)

        # sort according to path hierarchy
        # do not create sub-folder / file before parent exists
        deleted.sort(key=lambda x: x.path_display.count('/'))
        folders.sort(key=lambda x: x.path_display.count('/'))
        files.sort(key=lambda x: x.path_display.count('/'))

        for md in deleted + folders + files:
            self.queued_for_download.put(md.path_display)

        downloaded = []  # local list of all changes

        # apply deleted items
        if deleted:
            logger.info('Applying deletions...')
        for item in deleted:
            res = self._create_local_entry(item)
            downloaded.append(res)

        # create local folders, start with top-level and work your way down
        if folders:
            logger.info('Creating folders...')
        for folder in folders:
            res = self._create_local_entry(folder)
            downloaded.append(res)

        # apply created files
        n_files = len(files)
        last_emit = time.time()
        with ThreadPoolExecutor(max_workers=self._num_threads,
                                thread_name_prefix='maestral-download-pool') as executor:
            fs = (executor.submit(self._create_local_entry, file) for file in files)
            for f, n in zip(as_completed(fs), range(1, n_files + 1)):
                if time.time() - last_emit > 1 or n in (1, n_files):
                    # emit messages at maximum every second
                    logger.info(f'Downloading {n}/{n_files}...')
                    last_emit = time.time()
                downloaded.append(f.result())

        success = all(downloaded)

        if save_cursor and not self.cancel_pending.is_set():
            self.last_cursor = changes.cursor

        self._clean_and_save_rev_file()

        return [entry for entry in downloaded if isinstance(entry, Metadata)], success

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
        change_type = 'changed'

        # find out who changed the item(s), get the user name if its only a single user
        dbid_list = set(self._get_modified_by_dbid(md) for md in changes)
        if len(dbid_list) == 1:
            # all files have been modified by the same user
            dbid = dbid_list.pop()
            if dbid == self._conf.get('account', 'account_id'):
                user_name = 'You'
            else:
                account_info = self.client.get_account_info(dbid)
                user_name = account_info.name.display_name

        if n_changed == 1:
            # display user name, file name, and type of change
            md = changes[0]
            file_name = osp.basename(md.path_display)

            if isinstance(md, DeletedMetadata):
                change_type = 'removed'
            elif isinstance(md, FileMetadata):
                revs = self.client.list_revisions(md.path_lower, limit=2)
                is_new_file = len(revs.entries) == 1
                change_type = 'added' if is_new_file else 'changed'
            elif isinstance(md, FolderMetadata):
                change_type = 'added'

        else:
            # display user name if unique, number of files, and type of change
            file_name = f'{n_changed} items'

            if all(isinstance(x, DeletedMetadata) for x in changes):
                change_type = 'removed'
            elif all(isinstance(x, FolderMetadata) for x in changes):
                change_type = 'added'
                file_name = f'{n_changed} folders'
            elif all(isinstance(x, FileMetadata) for x in changes):
                file_name = f'{n_changed} files'

        if user_name:
            msg = f'{user_name} {change_type} {file_name}'
        else:
            msg = f'{file_name} {change_type}'

        self._notifier.notify(msg, level=FILECHANGE)

    def _filter_excluded_changes_remote(self, changes):
        """Removes all excluded items from the given list of changes.

        :param changes: :class:`dropbox.files.ListFolderResult` instance.
        :returns: (``changes_filtered``, ``changes_discarded``)
        :rtype: tuple[:class:`dropbox.files.ListFolderResult`]
        """
        entries_filtered = []
        entries_discarded = []

        for e in changes.entries:
            if self.is_excluded_by_user(e.path_lower) or self.is_excluded(e.path_lower):
                entries_discarded.append(e)
            else:
                entries_filtered.append(e)

        changes_filtered = dropbox.files.ListFolderResult(
            entries=entries_filtered, cursor=changes.cursor, has_more=False)
        changes_discarded = dropbox.files.ListFolderResult(
            entries=entries_discarded, cursor=changes.cursor, has_more=False)

        return changes_filtered, changes_discarded

    def _check_download_conflict(self, md):
        """
        Check if a local item is conflicting with remote change. The equivalent check when
        uploading and a change will be carried out by Dropbox itself.

        Checks are carried out against our index, reflecting the latest sync state.

        :param Metadata md: Dropbox SDK metadata.
        :returns: Conflict check result.
        :rtype: :class:`Conflict`
        :raises: :class:`errors.MaestralApiError`
        """

        # get metadata of remote item
        if isinstance(md, FileMetadata):
            remote_rev = md.rev
            remote_hash = md.content_hash
        elif isinstance(md, FolderMetadata):
            remote_rev = 'folder'
            remote_hash = 'folder'
        else:  # DeletedMetadata
            remote_rev = None
            remote_hash = None

        dbx_path = md.path_lower
        local_rev = self.get_local_rev(dbx_path)

        if remote_rev == local_rev:
            # Local change has the same rev. May be newer and
            # not yet synced or identical. Don't overwrite.
            logger.debug('Equal revs for "%s": local item is the same or newer '
                         'than on Dropbox', dbx_path)
            return Conflict.LocalNewerOrIdentical

        elif remote_rev != local_rev:
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

            local_path = self.get_local_path(md)
            local_hash = get_local_hash(local_path)

            if remote_hash == local_hash:
                logger.debug('Equal content hashes for "%s": no conflict', dbx_path)
                self.set_local_rev(dbx_path, remote_rev)
                return Conflict.Identical
            elif self._get_ctime(local_path) <= self.get_last_sync_for_path(dbx_path):
                logger.debug('Ctime is older than last sync for "%s": remote item '
                             'is newer', dbx_path)
                return Conflict.RemoteNewer
            elif not remote_rev:
                logger.debug('No remote rev for "%s": Local item has been modified '
                             'since remote deletion', dbx_path)
                return Conflict.LocalNewerOrIdentical
            else:
                logger.debug('Ctime is newer than last sync for "%s": conflict', dbx_path)
                return Conflict.Conflict

    def _get_ctime(self, local_path, ignore_excluded=True):
        """
        Returns the ctime of a local item or -1.0 if there is nothing at the path. If
        the item is a directory, return the largest ctime of it and its children.

        :param str local_path: Absolute path on local drive.
        :param bool ignore_excluded: If ``True``, the ctimes of children for which
            :meth:`is_excluded` evaluates to ``True`` are disregarded. This is only
            relevant if ``local_path`` points to a directory and has no effect if it
            points to a path.
        :returns: Ctime or -1.0.
        :rtype: float
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

    def _get_modified_by_dbid(self, md):
        """
        Returns the Dropbox ID of the user who modified a shared item or our own ID if the
        item was not shared.

        :param Metadata md: Dropbox file, folder or deleted metadata
        :return: Dropbox ID
        :rtype: str
        """

        try:
            return md.sharing_info.modified_by
        except AttributeError:
            return self._conf.get('account', 'account_id')

    @staticmethod
    def _separate_remote_entry_types(result):
        """
        Sorts entries in :class:`dropbox.files.ListFolderResult` into
        FolderMetadata, FileMetadata and DeletedMetadata.

        :returns: Tuple of (folders, files, deleted) containing instances of
            :class:`dropbox.files.FolderMetadata`, :class:`dropbox.files.FileMetadata`,
            and :class:`dropbox.files.DeletedMetadata` respectively.
        :rtype: tuple
        """
        binned = dict(folders=[], files=[], deleted=[])

        for x in result.entries:
            if isinstance(x, FolderMetadata):
                binned['folders'].append(x)
            elif isinstance(x, FileMetadata):
                binned['files'].append(x)
            elif isinstance(x, DeletedMetadata):
                binned['deleted'].append(x)

        return binned['folders'], binned['files'], binned['deleted']

    def _clean_remote_changes(self, changes):
        """
        Takes remote file events since last sync and cleans them up so that there is only
        a single event per path.

        Dropbox will sometimes report multiple changes per path. Once such instance is
        when sharing a folder: ``files/list_folder/continue`` will report the shared
        folder and its children as deleted and then created because the folder *is*
        actually deleted from the user's Dropbox and recreated as a shared folder which
        then gets mounted to the user's Dropbox. Ideally, we want to deal with this
        without re-downloading all its contents.

        :param changes: :class:`dropbox.files.ListFolderResult`
        :returns: Cleaned up changes with a single Metadata entry per path.
        :rtype: :class:`dropbox.files.ListFolderResult`
        """

        # Note: we won't have to deal with modified or moved events,
        # Dropbox only reports DeletedMetadata or FileMetadata / FolderMetadata

        histories = dict()
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

    @catch_sync_issues(download=True)
    def _create_local_entry(self, entry):
        """
        Applies a file change from Dropbox servers to the local Dropbox folder.
        Any :class:`errors.MaestralApiError` will be caught and logged as appropriate.
        Entries in the local index are created after successful completion.

        :param Metadata entry: Dropbox entry metadata.
        :returns: Copy of the Dropbox metadata if the change was applied locally,
            ``True`` if the change already existed locally and ``False`` in case of a
            :class:`errors.MaestralApiError`, for instance caused by sync issues.
        :rtype: Metadata, bool
        """

        if self.cancel_pending.is_set():
            return False

        self._slow_down()

        # book keeping
        local_path = self.get_local_path(entry)

        self.clear_sync_error(dbx_path=entry.path_display)
        remove_from_queue(self.queued_for_download, entry.path_display)
        self._save_to_history(entry.path_display)

        with InQueue(self.queue_downloading, entry.path_display):

            conflict_check = self._check_download_conflict(entry)

            applied = None

            if conflict_check in (Conflict.Identical, Conflict.LocalNewerOrIdentical):
                return applied

            if isinstance(entry, FileMetadata):
                # Store the new entry at the given path in your local state.
                # If the required parent folders don’t exist yet, create them.
                # If there’s already something else at the given path,
                # replace it and remove all its children.

                # we download to a temporary file first (this may take some time)
                tmp_fname = self._new_tmp_file()

                try:
                    md = self.client.download(f'rev:{entry.rev}', tmp_fname)
                except MaestralApiError as err:
                    # replace rev number with path
                    err.dbx_path = entry.path_display
                    raise err

                # re-check for conflict and move the conflict
                # out of the way if anything has changed
                if self._check_download_conflict(entry) == Conflict.Conflict:
                    new_local_path = generate_cc_name(local_path)
                    event_cls = DirMovedEvent if osp.isdir(local_path) else FileMovedEvent
                    with self.fs_events.ignore(event_cls(local_path, new_local_path)):
                        exc = move(local_path, new_local_path)
                        if exc:
                            raise os_to_maestral_error(exc, local_path=new_local_path)

                    logger.debug('Download conflict: renamed "%s" to "%s"', local_path,
                                 new_local_path)
                    self._rescan(new_local_path)

                if osp.isdir(local_path):
                    event_cls = DirDeletedEvent if osp.isdir(local_path) else FileDeletedEvent
                    with self.fs_events.ignore(event_cls(local_path)):
                        delete(local_path)

                # move the downloaded file to its destination
                with self.fs_events.ignore(FileDeletedEvent(local_path),
                                           FileMovedEvent(tmp_fname, local_path)):
                    exc = move(tmp_fname, local_path)
                    if exc:
                        raise os_to_maestral_error(exc, dbx_path=entry.path_display,
                                                   local_path=local_path)

                self.set_last_sync_for_path(entry.path_lower, self._get_ctime(local_path))
                self.set_local_rev(entry.path_lower, md.rev)

                logger.debug('Created local file "%s"', entry.path_display)
                applied = entry

            elif isinstance(entry, FolderMetadata):
                # Store the new entry at the given path in your local state.
                # If the required parent folders don’t exist yet, create them.
                # If there’s already something else at the given path,
                # replace it but leave the children as they are.

                if conflict_check == Conflict.Conflict:
                    new_local_path = generate_cc_name(local_path)
                    event_cls = DirMovedEvent if osp.isdir(local_path) else FileMovedEvent
                    with self.fs_events.ignore(event_cls(local_path, new_local_path)):
                        exc = move(local_path, new_local_path)
                        if exc:
                            raise os_to_maestral_error(exc, local_path=new_local_path)

                    logger.debug('Download conflict: renamed "%s" to "%s"', local_path,
                                 new_local_path)
                    self._rescan(new_local_path)

                if osp.isfile(local_path):
                    event_cls = DirDeletedEvent if osp.isdir(local_path) else FileDeletedEvent
                    with self.fs_events.ignore(event_cls(local_path)):
                        delete(local_path)

                try:
                    with self.fs_events.ignore(DirCreatedEvent(local_path), recursive=False):
                        os.makedirs(local_path)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise os_to_maestral_error(exc, dbx_path=entry.path_display,
                                               local_path=local_path)

                self.set_last_sync_for_path(entry.path_lower, self._get_ctime(local_path))
                self.set_local_rev(entry.path_lower, 'folder')

                logger.debug('Created local folder "%s"', entry.path_display)
                applied = entry

            elif isinstance(entry, DeletedMetadata):
                # If your local state has something at the given path,
                # remove it and all its children. If there’s nothing at the
                # given path, ignore this entry.

                event_cls = DirDeletedEvent if osp.isdir(local_path) else FileDeletedEvent
                with self.fs_events.ignore(event_cls(local_path)):
                    err = delete(local_path)

                self.set_local_rev(entry.path_lower, None)
                self.set_last_sync_for_path(entry.path_lower, time.time())

                if not err:
                    logger.debug('Deleted local item "%s"', entry.path_display)
                    applied = entry
                else:
                    logger.debug('Deletion failed: %s', err)

            return applied

    def _rescan(self, local_path):

        logger.debug('Rescanning "%s"', local_path)

        if osp.isfile(local_path):
            self.fs_events.local_file_event_queue.put(FileCreatedEvent(local_path))
        elif osp.isdir(local_path):
            snapshot = DirectorySnapshot(local_path)

            for path in snapshot.paths:
                if snapshot.isdir(path):
                    self.fs_events.local_file_event_queue.put(DirCreatedEvent(path))
                else:
                    self.fs_events.local_file_event_queue.put(FileCreatedEvent(path))

    def _save_to_history(self, dbx_path):
        # add new file to recent_changes
        recent_changes = self._state.get('sync', 'recent_changes')
        recent_changes.append(dbx_path)
        # eliminate duplicates
        recent_changes = list(dict.fromkeys(recent_changes))
        self._state.set('sync', 'recent_changes', recent_changes[-self._max_history:])


# ========================================================================================
# Workers for upload, download and connection monitoring threads
# ========================================================================================

def helper(mm):
    """
    A worker for periodic maintenance:

     1) Checks for a connection to Dropbox servers.
     2) Pauses syncing when the connection is lost and resumes syncing when reconnected
        and syncing has not been paused by the user.
     3) Triggers weekly reindexing.

    :param SyncMonitor mm: MaestralMonitor instance.
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


def download_worker(sync, syncing, running, connected):
    """
    Worker to sync changes of remote Dropbox with local folder.

    :param SyncEngine sync: Instance of :class:`SyncEngine`.
    :param Event syncing: Event that indicates if workers are running or paused.
    :param Event running: Event to shutdown local file event handler and worker threads.
    :param Event connected: Event that indicates if we can connect to Dropbox.
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

                    changes = sync.list_remote_changes(sync.last_cursor)
                    downloaded, _ = sync.apply_remote_changes(changes)
                    sync.notify_user(downloaded)

                    logger.info(IDLE)

                    sync.client.get_space_usage()

                    gc.collect()

        except ConnectionError:
            syncing.clear()
            connected.clear()
            logger.info(DISCONNECTED)
        except Exception as e:
            running.clear()
            syncing.clear()
            title = getattr(e, 'title', 'Unexpected error')
            logger.error(title, exc_info=True)


def download_worker_added_item(sync, syncing, running, connected):
    """
    Worker to download items which have been newly included in sync.

    :param SyncEngine sync: Instance of :class:`SyncEngine`.
    :param Event syncing: Event that indicates if workers are running or paused.
    :param Event running: Event to shutdown local file event handler and worker threads.
    :param Event connected: Event that indicates if we can connect to Dropbox.
    """

    while running.is_set():

        syncing.wait()

        try:
            dbx_path = sync.queued_newly_included_downloads.get()

            with sync.sync_lock:
                if not (running.is_set() and syncing.is_set()):
                    sync.pending_downloads.add(dbx_path)
                    continue

                sync.get_remote_item(dbx_path)
                logger.info(IDLE)

                gc.collect()

        except ConnectionError:
            syncing.clear()
            connected.clear()
            logger.info(DISCONNECTED)
        except Exception as e:
            running.clear()
            syncing.clear()
            title = getattr(e, 'title', 'Unexpected error')
            logger.error(title, exc_info=True)


def upload_worker(sync, syncing, running, connected):
    """
    Worker to sync local changes to remote Dropbox.

    :param SyncEngine sync: Instance of :class:`SyncEngine`.
    :param Event syncing: Event that indicates if workers are running or paused.
    :param Event running: Event to shutdown local file event handler and worker threads.
    :param Event connected: Event that indicates if we can connect to Dropbox.
    """

    while running.is_set():

        syncing.wait()

        try:
            events, local_cursor = sync.wait_for_local_changes()

            with sync.sync_lock:
                if not (running.is_set() and syncing.is_set()):
                    continue

                if len(events) > 0:
                    logger.info(SYNCING)
                    sync.apply_local_changes(events, local_cursor)
                    logger.info(IDLE)

                    gc.collect()
                else:
                    sync.last_sync = local_cursor

        except ConnectionError:
            syncing.clear()
            connected.clear()
            logger.info(DISCONNECTED)
        except Exception as e:
            running.clear()
            syncing.clear()
            title = getattr(e, 'title', 'Unexpected error')
            logger.error(title, exc_info=True)


def startup_worker(sync, syncing, running, connected, startup, paused_by_user):
    """
    Worker to sync local changes to remote Dropbox.

    :param SyncEngine sync: Instance of :class:`SyncEngine`.
    :param Event syncing: Event that indicates if workers are running or paused.
    :param Event running: Event to shutdown local file event handler and worker threads.
    :param Event connected: Event that indicates if we can connect to Dropbox.
    :param Event startup: Set when we should run startup routines.
    :param Event paused_by_user: Set when syncing has been paused by the user.
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

                # retry failed / interrupted downloads
                if len(sync.download_errors) > 0:
                    logger.info('Retrying failed downloads...')

                for dbx_path in list(sync.download_errors):
                    logger.info(f'Downloading {dbx_path}...')
                    sync.get_remote_item(dbx_path)

                if len(sync.pending_downloads) > 0:
                    logger.info('Resuming interrupted downloads...')

                for dbx_path in list(sync.pending_downloads):
                    logger.info(f'Downloading {dbx_path}...')
                    sync.get_remote_item(dbx_path)

                # upload changes while inactive
                sync.upload_local_changes_while_inactive()

                # enforce immediate check for remote changes
                changes = sync.list_remote_changes(sync.last_cursor)
                downloaded, _ = sync.apply_remote_changes(changes)
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
        except Exception as e:
            running.clear()
            syncing.clear()
            title = getattr(e, 'title', 'Unexpected error')
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

    :param DropboxClient client: The Dropbox API client, a wrapper around the Dropbox
        Python SDK.
    """

    connection_check_interval = 2

    def __init__(self, client):

        self.client = client
        self.config_name = self.client.config_name
        self.sync = SyncEngine(self.client)

        self.connected = Event()
        self.syncing = Event()
        self.running = Event()
        self.paused_by_user = Event()
        self.paused_by_user.set()

        self.startup = Event()

        self.fs_event_handler = FSEventHandler(self.syncing, self.startup, self.sync)

        self._startup_time = None
        self.reindex_interval = self.sync._conf.get('sync', 'reindex_interval')

    @property
    def uploading(self):
        """Returns a list of all items currently uploading. Items are absolute paths (str)
        on local drive."""
        return list(self.sync.queue_uploading.queue)

    @property
    def downloading(self):
        """Returns a list of all items currently downloading. Items are paths (str)
        relative to the Dropbox folder."""
        return list(self.sync.queue_downloading.queue)

    @property
    def queued_for_upload(self):
        """Returns a list of all items queued for upload. Items are absolute paths (str)
        on local drive."""
        return list(self.sync.queued_for_upload.queue)

    @property
    def queued_for_download(self):
        """Returns a list of all items queued for download. Items are paths (str) relative
        to the Dropbox folder."""
        return list(self.sync.queued_for_download.queue)

    def start(self):
        """Creates observer threads and starts syncing."""

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
        except Exception as exc:
            new_exc = fswatch_to_maestral_error(exc)
            title = getattr(new_exc, 'title', 'Unexpected error')
            logger.error(title, exc_info=_exc_info(new_exc))

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

    def pause(self):
        """Pauses syncing."""

        self.paused_by_user.set()
        self.syncing.clear()

        self.sync.cancel_pending.set()
        self._wait_for_idle()
        self.sync.cancel_pending.clear()

        logger.info(PAUSED)

    def resume(self):
        """Checks for changes while idle and starts syncing."""

        if not self.paused_by_user.is_set():
            return

        self.startup.set()
        self.paused_by_user.clear()

    def stop(self):
        """Stops syncing and destroys worker threads."""

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
    def idle_time(self):
        """Returns the idle time in seconds since the last file change or zero if syncing
        is not running."""
        if len(self.sync._last_sync_for_path) > 0:
            return time.time() - max(self.sync._last_sync_for_path.values())
        elif self.syncing.is_set():
            return time.time() - self._startup_time
        else:
            return 0.0

    def reset_sync_state(self):
        """Resets all saved sync state."""

        if self.syncing.is_set() or self.startup.is_set() or self.sync.busy():
            raise RuntimeError('Cannot reset sync state while syncing.')

        self.sync.last_cursor = ''
        self.sync.last_sync = 0.0
        self.sync.clear_rev_index()

        logger.debug('Sync state reset')

    def rebuild_index(self):
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

    def _wait_for_idle(self):

        self.sync.sync_lock.acquire()
        self.sync.sync_lock.release()

    def _threads_alive(self):
        """Returns ``True`` if all threads are alive, ``False`` otherwise."""

        try:
            threads = (
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

def _exc_info(exc):
    return type(exc), exc, exc.__traceback__


def get_dest_path(event):
    return getattr(event, 'dest_path', event.src_path)


def split_moved_event(event):
    """
    Splits a given FileSystemEvent into Deleted and Created events of the same type.

    :param FileSystemEvent event: Original event.
    :returns: Tuple of deleted and created events.
    :rtype: tuple
    """

    if event.is_directory:
        CreatedEvent = DirCreatedEvent
        DeletedEvent = DirDeletedEvent
    else:
        CreatedEvent = FileCreatedEvent
        DeletedEvent = FileDeletedEvent

    return DeletedEvent(event.src_path), CreatedEvent(event.dest_path)


def get_local_hash(local_path):
    """
    Computes content hash of a local file.

    :param str local_path: Absolute path on local drive.
    :returns: Content hash to compare with Dropbox's content hash,
        or 'folder' if the path points to a directory. ``None`` if there
        is nothing at the path.
    :rtype: str
    """

    hasher = DropboxContentHasher()

    try:
        with open(local_path, 'rb') as f:
            while True:
                chunk = f.read(1024)
                if len(chunk) == 0:
                    break
                hasher.update(chunk)

        return str(hasher.hexdigest())
    except IsADirectoryError:
        return 'folder'
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise os_to_maestral_error(exc, local_path=local_path)
    finally:
        del hasher


def remove_from_queue(queue, *items):
    """
    Tries to remove an item from a queue.

    :param Queue queue: Queue to remove item from.
    :param items: Items to remove
    """

    with queue.mutex:
        for item in items:
            try:
                queue.queue.remove(item)
            except ValueError:
                pass


def entries_to_str(entries):
    str_reps = [f'<{e.__class__.__name__}(path_display={e.path_display})>'
                for e in entries]
    return '[' + ',\n '.join(str_reps) + ']'


def cpu_usage_percent(interval=0.1):
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

    :param float interval: Interval in sec between comparisons of CPU times.
    :returns: CPU usage during interval in percent.
    :rtype: float
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


def check_connection(hostname):
    """
    A low latency check for an internet connection.

    :param str hostname: Hostname to use for connection check.
    :returns: Connection availability.
    :rtype: bool
    """
    try:
        host = socket.gethostbyname(hostname)
        s = socket.create_connection((host, 80), 2)
        s.close()
        return True
    except Exception:
        return False
