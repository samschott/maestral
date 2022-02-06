"""This module defines the main API which is exposed to the CLI or GUI."""

# system imports
import os
import os.path as osp
import shutil
import sqlite3
import time
import warnings
import logging.handlers
import asyncio
import random
import gc
import tempfile
import mimetypes
import difflib
from concurrent.futures import ThreadPoolExecutor
from typing import (
    Union,
    List,
    Iterator,
    Dict,
    Set,
    Tuple,
    Awaitable,
    Optional,
    Any,
)

# external imports
import requests
from watchdog.events import DirDeletedEvent, FileDeletedEvent
from packaging.version import Version
from datetime import datetime, timezone
from dropbox.files import FileMetadata
from dropbox.sharing import RequestedVisibility

# local imports
from . import __version__
from .client import CONNECTION_ERRORS, DropboxClient, convert_api_errors
from .sync import SyncDirection
from .manager import SyncManager
from .errors import (
    MaestralApiError,
    NotLinkedError,
    NoDropboxDirError,
    NotFoundError,
    BusyError,
    KeyringAccessError,
    UnsupportedFileTypeForDiff,
)
from .config import MaestralConfig, MaestralState, validate_config_name
from .logging import CachedHandler, setup_logging, scoped_logger
from .utils import get_newer_version
from .utils.path import (
    is_child,
    is_equal_or_child,
    normalize,
    to_existing_unnormalized_path,
    delete,
)
from .utils.serializer import (
    error_to_dict,
    dropbox_stone_to_dict,
    orm_type_to_dict,
    SerializedObjectType,
)
from .utils.appdirs import get_cache_path, get_data_path
from .utils.integration import get_ac_state, ACState
from .utils.orm import Database
from .constants import IDLE, PAUSED, CONNECTING, FileStatus, GITHUB_RELEASES_API


__all__ = ["Maestral"]


def _sql_add_column(db: Database, table: str, column: str, affinity: str) -> None:
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {affinity};")
    except sqlite3.OperationalError:
        # column already exists
        pass


def _sql_drop_table(db: Database, table: str) -> None:
    try:
        db.execute(f"DROP TABLE {table};")
    except sqlite3.OperationalError:
        # table does not exist
        pass


# ======================================================================================
# Main API
# ======================================================================================


class Maestral:
    """The public API

    All methods and properties return objects or raise exceptions which can safely be
    serialized, i.e., pure Python types. The only exception are instances of
    :class:`maestral.errors.MaestralApiError`: they need to be registered explicitly
    with the serpent serializer which is used for communication to frontends.

    Sync errors and fatal errors which occur in the sync threads can be read with the
    properties :attr:`sync_errors` and :attr:`fatal_errors`, respectively.

    :Example:

        First create an instance with a new config_name. In this example, we choose
        "private" to sync a private Dropbox account. Then link the created config to an
        existing Dropbox account and set up the local Dropbox folder. If successful,
        invoke :meth:`start_sync` to start syncing.

        >>> from maestral.main import Maestral
        >>> m = Maestral(config_name='private')
        >>> url = m.get_auth_url()  # get token from Dropbox website
        >>> print(f'Please go to {url} to retrieve a Dropbox authorization token.')
        >>> token = input('Enter auth token: ')
        >>> res = m.link(token)
        >>> if res == 0:
        ...     m.create_dropbox_directory('~/Dropbox (Private)')
        ...     m.start_sync()

    :param config_name: Name of maestral configuration to run. Must not contain any
        whitespace. If the given config file does exist, it will be created.
    :param log_to_stderr: If ``True``, Maestral will print log messages to stderr.
        When started as a systemd services, this can result in duplicate log messages
        in the systemd journal. Defaults to ``False``.
    """

    def __init__(
        self, config_name: str = "maestral", log_to_stderr: bool = False
    ) -> None:

        self._config_name = validate_config_name(config_name)
        self._conf = MaestralConfig(self._config_name)
        self._state = MaestralState(self._config_name)

        # Set up logging.
        self._logger = scoped_logger(__name__, config_name)
        self._log_to_stderr = log_to_stderr
        self._setup_logging()

        # Run update scripts after init of loggers and config / state.
        self._check_and_run_post_update_scripts()

        # Set up sync infrastructure.
        self.client = DropboxClient(config_name=self.config_name)
        self.manager = SyncManager(self.client)
        self.sync = self.manager.sync

        # Schedule background tasks.
        self._loop = asyncio.get_event_loop_policy().get_event_loop()
        self._tasks: Set[asyncio.Task] = set()
        self._pool = ThreadPoolExecutor(
            thread_name_prefix="maestral-thread-pool",
            max_workers=2,
        )

        self._schedule_task(self._periodic_refresh_profile())
        self._schedule_task(self._periodic_reindexing())

        # Create a future which will return once `shutdown_daemon` is called.
        # This can be used by an event loop to wait until maestral has been stopped.
        self.shutdown_complete = self._loop.create_future()

    @property
    def version(self) -> str:
        """Returns the current Maestral version."""
        return __version__

    def get_auth_url(self) -> str:
        """
        Returns a URL to authorize access to a Dropbox account. To link a Dropbox
        account, retrieve an auth token from the URL and link Maestral by calling
        :meth:`link` with the provided token.

        :returns: URL to retrieve an OAuth token.
        """
        return self.client.get_auth_url()

    def link(self, token: str) -> int:
        """
        Links Maestral with a Dropbox account using the given access token. The token
        will be stored for future usage as documented in the :mod:`oauth` module.
        Supported keyring backends are, in order of preference:

            * MacOS Keychain
            * Any keyring implementing the SecretService Dbus specification
            * KWallet
            * Gnome Keyring
            * Plain text storage

        :param token: OAuth token for Dropbox access.
        :returns: 0 on success, 1 for an invalid token and 2 for connection errors.
        """

        return self.client.link(token)

    def unlink(self) -> None:
        """
        Unlinks the configured Dropbox account but leaves all downloaded files in place.
        All syncing metadata will be removed as well. Connection and API errors will be
        handled silently but the Dropbox access key will always be removed from the
        user's PC.

        :raises NotLinkedError: if no Dropbox account is linked.
        """

        self._check_linked()
        self.stop_sync()

        try:
            self.client.unlink()
        except (ConnectionError, MaestralApiError):
            self._logger.debug("Could not invalidate token with Dropbox", exc_info=True)
        except KeyringAccessError:
            self._logger.debug("Could not remove token from keyring", exc_info=True)

        try:
            self.client.auth.delete_creds()
        except KeyringAccessError:
            self._logger.debug("Could not remove token from keyring", exc_info=True)

        # Clean up config and state files.
        self._conf.cleanup()
        self._state.cleanup()
        self.sync.reset_sync_state()
        self.sync.reload_cached_config()

        self._logger.info("Unlinked Dropbox account.")

    def _setup_logging(self) -> None:
        """
        Sets up logging to log files, status and error properties, desktop notifications,
        the systemd journal if available, and to stderr if requested.
        """
        self._root_logger = scoped_logger("maestral", self.config_name)
        (
            self._log_handler_file,
            self._log_handler_stream,
            self._log_handler_sd,
            self._log_handler_journal,
        ) = setup_logging(self.config_name, self._log_to_stderr)

        log_fmt_short = logging.Formatter(fmt="%(message)s")

        # Log to cached handlers for status and error APIs.
        self._log_handler_info_cache = CachedHandler(maxlen=1)
        self._log_handler_info_cache.setFormatter(log_fmt_short)
        self._log_handler_info_cache.setLevel(logging.INFO)
        self._root_logger.addHandler(self._log_handler_info_cache)

        self._log_handler_error_cache = CachedHandler()
        self._log_handler_error_cache.setFormatter(log_fmt_short)
        self._log_handler_error_cache.setLevel(logging.ERROR)
        self._root_logger.addHandler(self._log_handler_error_cache)

    # ==== Methods to access config and saved state ====================================

    @property
    def config_name(self) -> str:
        """The selected configuration."""
        return self._config_name

    def set_conf(self, section: str, name: str, value: Any) -> None:
        """
        Sets a configuration option.

        :param section: Name of section in config file.
        :param name: Name of config option.
        :param value: Config value. May be any type accepted by :obj:`ast.literal_eval`.
        """
        self._conf.set(section, name, value)

    def get_conf(self, section: str, name: str) -> Any:
        """
        Gets a configuration option.

        :param section: Name of section in config file.
        :param name: Name of config option.
        :returns: Config value. May be any type accepted by :obj:`ast.literal_eval`.
        """
        return self._conf.get(section, name)

    def set_state(self, section: str, name: str, value: Any) -> None:
        """
        Sets a state value.

        :param section: Name of section in state file.
        :param name: Name of state variable.
        :param value: State value. May be any type accepted by :obj:`ast.literal_eval`.
        """
        self._state.set(section, name, value)

    def get_state(self, section: str, name: str) -> Any:
        """
        Gets a state value.

        :param section: Name of section in state file.
        :param name: Name of state variable.
        :returns: State value. May be any type accepted by :obj:`ast.literal_eval`.
        """
        return self._state.get(section, name)

    # ==== Getters / setters for config with side effects ==============================

    @property
    def dropbox_path(self) -> str:
        """
        Returns the path to the local Dropbox folder (read only). This will be an empty
        string if not Dropbox folder has been set up yet. Use
        :meth:`create_dropbox_directory` or :meth:`move_dropbox_directory` to set or
        change the Dropbox directory location instead.

        :raises NotLinkedError: if no Dropbox account is linked.
        """

        if self.pending_link:
            return ""
        else:
            return self.sync.dropbox_path

    @property
    def excluded_items(self) -> List[str]:
        """
        The list of files and folders excluded by selective sync. Any changes to this
        list will be applied immediately if we have already performed the initial sync.
        I.e., paths which have been added to the list will be deleted from the local
        drive and paths which have been removed will be downloaded.

        Use :meth:`exclude_item` and :meth:`include_item` to add or remove individual
        items from selective sync.
        """
        if self.pending_link:
            return []
        else:
            return self.sync.excluded_items

    @excluded_items.setter
    def excluded_items(self, items: List[str]) -> None:
        """Setter: excluded_items"""

        excluded_items = self.sync.clean_excluded_items_list(items)
        old_excluded_items = self.excluded_items

        added_excluded_items = set(excluded_items) - set(old_excluded_items)
        added_included_items = set(old_excluded_items) - set(excluded_items)

        has_changes = len(added_excluded_items) > 0 or len(added_included_items) > 0

        if has_changes:

            if self.sync.sync_lock.acquire(blocking=False):

                try:

                    self.sync.excluded_items = excluded_items

                    if self.pending_first_download:
                        return

                    # Apply changes.
                    for path in added_excluded_items:
                        self._logger.info("Excluded %s", path)
                        self._remove_after_excluded(path)

                    for path in added_included_items:
                        if not self.sync.is_excluded_by_user(path):
                            self._logger.info("Included %s", path)
                            self.manager.added_item_queue.put(path)

                    self._logger.info(IDLE)

                finally:
                    self.sync.sync_lock.release()

            else:
                raise BusyError(
                    "Cannot set excluded items", "Please try again when idle."
                )

    @property
    def log_level(self) -> int:
        """Log level for log files, stderr and the systemd journal."""
        return self._conf.get("app", "log_level")

    @log_level.setter
    def log_level(self, level_num: int) -> None:
        """Setter: log_level."""
        self._root_logger.setLevel(min(level_num, logging.INFO))
        self._log_handler_file.setLevel(level_num)
        self._log_handler_stream.setLevel(level_num)
        self._log_handler_journal.setLevel(level_num)
        self._conf.set("app", "log_level", level_num)

    @property
    def notification_snooze(self) -> float:
        """Snooze time for desktop notifications in minutes. Defaults to 0 if
        notifications are not snoozed."""
        return self.sync.desktop_notifier.snoozed

    @notification_snooze.setter
    def notification_snooze(self, minutes: float) -> None:
        """Setter: notification_snooze."""
        self.sync.desktop_notifier.snoozed = minutes

    @property
    def notification_level(self) -> int:
        """Level for desktop notifications. See :mod:`utils.notify` for level
        definitions."""
        return self.sync.desktop_notifier.notify_level

    @notification_level.setter
    def notification_level(self, level: int) -> None:
        """Setter: notification_level."""
        self.sync.desktop_notifier.notify_level = level

    # ==== State information  ==========================================================

    def status_change_longpoll(self, timeout: Optional[float] = 60) -> bool:
        """
        Blocks until there is a change in status or until a timeout occurs. This method
        can be used by frontends to wait for status changes without constant polling.

        :param timeout: Maximum time to block before returning, even if there is no
            status change.
        :returns: ``True``if there was a status change, ``False`` in case of a timeout.

        .. versionadded:: 1.3.0
        """
        return self._log_handler_info_cache.wait_for_emit(timeout)

    @property
    def pending_link(self) -> bool:
        """Indicates if Maestral is linked to a Dropbox account (read only). This will
        block until the user's keyring is unlocked to load the saved auth token."""
        return not self.client.linked

    @property
    def pending_dropbox_folder(self) -> bool:
        """Indicates if a local Dropbox directory has been configured (read only). This
        will not check if the configured directory actually exists, starting the sync
        may still raise a :class:`NoDropboxDirError`."""
        return not self.sync.dropbox_path

    @property
    def pending_first_download(self) -> bool:
        """Indicates if the initial download has already occurred (read only)."""
        return self.sync.local_cursor == 0 or self.sync.remote_cursor == ""

    @property
    def paused(self) -> bool:
        """Indicates if syncing is paused by the user (read only). This is set by
        calling :meth:`pause`."""
        return not self.manager.autostart.is_set() and not self.sync.busy()

    @property
    def running(self) -> bool:
        """Indicates if sync threads are running (read only). They will be stopped
        before :meth:`start_sync` is called, when shutting down or because of an
        exception."""
        return self.manager.running.is_set() or self.sync.busy()

    @property
    def connected(self) -> bool:
        """Indicates if Dropbox servers can be reached (read only)."""

        if self.pending_link:
            return False
        else:
            return self.manager.connected

    @property
    def status(self) -> str:
        """The last status message (read only). This can be displayed as information to
        the user but should not be relied on otherwise."""
        if self.paused:
            return PAUSED
        elif not self.connected:
            return CONNECTING
        else:
            return self._log_handler_info_cache.getLastMessage()

    @property
    def sync_errors(self) -> List[SerializedObjectType]:
        """
        A list of current sync errors as dicts (read only). This list is populated by
        the sync threads. The following keys will always be present but may contain
        empty values: "type", "inherits", "title", "traceback", "title", "message",
        "local_path", "dbx_path".

        :raises NotLinkedError: if no Dropbox account is linked.
        """

        return [orm_type_to_dict(e) for e in self.sync.sync_errors]

    @property
    def fatal_errors(self) -> List[SerializedObjectType]:
        """
        Returns a list of fatal errors as dicts (read only). This does not include lost
        internet connections or file sync errors which only emit warnings and are
        tracked and cleared separately. Errors listed here must be acted upon for
        Maestral to continue syncing.

        The following keys will always be present but may contain empty values: "type",
        "inherits", "title", "traceback", "title", and "message".s

        This list is populated from all log messages with level ERROR or higher that
        have ``exc_info`` attached.
        """

        maestral_errors_dicts: List[SerializedObjectType] = []

        for r in self._log_handler_error_cache.cached_records:
            if r.exc_info:
                err = r.exc_info[1]
                if isinstance(err, Exception):
                    serialized_error = error_to_dict(err)
                    maestral_errors_dicts.append(serialized_error)

        return maestral_errors_dicts

    def clear_fatal_errors(self) -> None:
        """
        Manually clears all fatal errors. This should be used after they have been
        resolved by the user through the GUI or CLI.
        """
        self._log_handler_error_cache.clear()

    @property
    def account_profile_pic_path(self) -> str:
        """
        The path of the current account's profile picture (read only). There may not be
        an actual file at that path if the user did not set a profile picture or the
        picture has not yet been downloaded.
        """
        return get_cache_path("maestral", f"{self._config_name}_profile_pic.jpeg")

    def get_file_status(self, local_path: str) -> str:
        """
        Returns the sync status of a file or folder. The returned status is recursive
        for folders, e.g., the file status will be "uploading" for a a folder if any
        file inside that folder is being uploaded.

        .. versionadded:: 1.4.4
           Recursive behavior. Previous versions would return "up to date" even if in
           case of syncing children.

        :param local_path: Path to file on the local drive. May be relative to the
            current working directory.
        :returns: String indicating the sync status. Can be 'uploading', 'downloading',
            'up to date', 'error', or 'unwatched' (for files outside the Dropbox
            directory). This will always be 'unwatched' if syncing is paused.
        """
        if not self.running:
            return FileStatus.Unwatched.value

        local_path = osp.realpath(local_path)

        try:
            dbx_path_lower = self.sync.to_dbx_path_lower(local_path)
        except ValueError:
            return FileStatus.Unwatched.value

        sync_event = self.manager.activity.get(local_path)

        if sync_event is None:
            # Check if there is any sync event for a child path.
            # TODO: Improve performance
            path = next(
                iter(p for p in self.manager.activity if p.startswith(local_path)), ""
            )
            sync_event = self.manager.activity.get(path)

        if sync_event and sync_event.direction == SyncDirection.Up:
            return FileStatus.Uploading.value
        elif sync_event and sync_event.direction == SyncDirection.Down:
            return FileStatus.Downloading.value
        elif len(self.sync.sync_errors_for_path(dbx_path_lower)) > 0:
            return FileStatus.Error.value
        elif dbx_path_lower == "/" or self.sync.get_local_rev(dbx_path_lower):
            return FileStatus.Synced.value
        else:
            return FileStatus.Unwatched.value

    def get_activity(self, limit: Optional[int] = 100) -> List[SerializedObjectType]:
        """
        Returns the current upload / download activity.

        :param limit: Maximum number of items to return. If None, all entries will be
            returned.
        :returns: A lists of all sync events currently queued for or being uploaded or
            downloaded with the events furthest up in the queue coming first.
        :raises NotLinkedError: if no Dropbox account is linked.
        """

        self._check_linked()

        activity = list(self.manager.activity.values())[:limit]
        serialized_activity = [orm_type_to_dict(event) for event in activity]

        return serialized_activity

    def get_history(self, limit: Optional[int] = 100) -> List[SerializedObjectType]:
        """
        Returns the historic upload / download activity. Up to 1,000 sync events are
        kept in the database. Any events which occurred before the interval specified by
        the ``keep_history`` config value are discarded.

        :param limit: Maximum number of items to return. If None, all entries will be
            returned.
        :returns: A lists of all sync events since ``keep_history`` sorted by time with
            the oldest event first.
        :raises NotLinkedError: if no Dropbox account is linked.
        """

        self._check_linked()
        if limit:
            history = [orm_type_to_dict(e) for e in self.manager.history[-limit:]]
        else:
            history = [orm_type_to_dict(e) for e in self.manager.history]

        return history

    def get_account_info(self) -> SerializedObjectType:
        """
        Returns the account information from Dropbox and returns it as a dictionary.

        :returns: Dropbox account information.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        """

        self._check_linked()

        res = self.client.get_account_info()
        return dropbox_stone_to_dict(res)

    def get_space_usage(self) -> SerializedObjectType:
        """
        Gets the space usage from Dropbox and returns it as a dictionary.

        :returns: Dropbox space usage information.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        """

        self._check_linked()

        res = self.client.get_space_usage()
        return dropbox_stone_to_dict(res)

    # ==== Control methods for front ends ==============================================

    def get_profile_pic(self) -> Optional[str]:
        """
        Attempts to download the user's profile picture from Dropbox. The picture is
        saved in Maestral's cache directory for retrieval when there is no internet
        connection.

        :returns: Path to saved profile picture or ``None`` if no profile picture was
            downloaded.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        """

        self._check_linked()

        res = self.client.get_account_info()

        if res.profile_photo_url:
            with convert_api_errors():
                res = requests.get(res.profile_photo_url)
                with open(self.account_profile_pic_path, "wb") as f:
                    f.write(res.content)
            return self.account_profile_pic_path
        else:
            self._delete_old_profile_pics()
            return None

    def get_metadata(self, dbx_path: str) -> Optional[SerializedObjectType]:
        """
        Returns metadata for a file or folder on Dropbox.

        :param dbx_path: Path to file or folder on Dropbox.
        :returns: Dropbox item metadata as dict. See :class:`dropbox.files.Metadata` for
            keys and values.
        :raises NotFoundError: if there is nothing at the given path.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        """

        self._check_linked()

        res = self.client.get_metadata(dbx_path)

        if res is None:
            return None
        else:
            return dropbox_stone_to_dict(res)

    def list_folder(self, dbx_path: str, **kwargs) -> List[SerializedObjectType]:
        """
        List all items inside the folder given by ``dbx_path``. Keyword arguments are
        passed on the Dropbox API call :meth:`client.DropboxClient.list_folder`.

        :param dbx_path: Path to folder on Dropbox.
        :returns: List of Dropbox item metadata as dicts. See
            :class:`dropbox.files.Metadata` for keys and values.
        :raises NotFoundError: if there is nothing at the given path.
        :raises NotAFolderError: if the given path refers to a file.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        """

        self._check_linked()

        with self.client.clone_with_new_session() as client:
            res = client.list_folder(dbx_path, **kwargs)
            entries = [dropbox_stone_to_dict(e) for e in res.entries]

        return entries

    def list_folder_iterator(
        self, dbx_path: str, **kwargs
    ) -> Iterator[List[SerializedObjectType]]:
        """
        Returns an iterator over items inside the folder given by ``dbx_path``. Keyword
        arguments are passed on the client call
        :meth:`client.DropboxClient.list_folder_iterator`. Each iteration will yield a
        list of approximately 500 entries, depending on the number of entries returned
        by an individual API call.

        :param dbx_path: Path to folder on Dropbox.
        :returns: Iterator over list of Dropbox item metadata as dicts. See
            :class:`dropbox.files.Metadata` for keys and values.
        :raises NotFoundError: if there is nothing at the given path.
        :raises NotAFolderError: if the given path refers to a file.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        """

        self._check_linked()

        with self.client.clone_with_new_session() as client:

            res_iter = client.list_folder_iterator(dbx_path, **kwargs)

            for res in res_iter:
                entries = [dropbox_stone_to_dict(e) for e in res.entries]
                yield entries
                del entries
                gc.collect()

    def list_revisions(
        self, dbx_path: str, limit: int = 10
    ) -> List[SerializedObjectType]:
        """
        List revisions of old files at the given path ``dbx_path``. This will also
        return revisions if the file has already been deleted.

        :param dbx_path: Path to file on Dropbox.
        :param limit: Maximum number of revisions to list.
        :returns: List of Dropbox file metadata as dicts. See
            :class:`dropbox.files.Metadata` for keys and values.
        :raises NotFoundError:if there never was a file at the given path.
        :raises IsAFolderError: if the given path refers to a folder
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        """

        self._check_linked()

        with self.client.clone_with_new_session() as client:
            res = client.list_revisions(dbx_path, limit=limit)
            entries = [dropbox_stone_to_dict(e) for e in res.entries]

        return entries

    def get_file_diff(self, old_rev: str, new_rev: Optional[str] = None) -> List[str]:
        """
        Compare to revisions of a text file using Python's difflib. The versions will be
        downloaded to temporary files. If new_rev is None, the old revision will be
        compared to the corresponding local file, if any.

        :param old_rev: Identifier of old revision.
        :param new_rev: Identifier of new revision.
        :returns: Diff as a list of strings (lines).
        :raises UnsupportedFileTypeForDiff: if file type is not supported.
        :raises UnsupportedFileTypeForDiff: if file content could not be decoded.
        :raises MaestralApiError: if file could not be read for any other reason.
        """

        def str_from_date(d: datetime) -> str:
            """Convert 'client_modified' metadata to string in local timezone"""
            tz_date = d.replace(tzinfo=timezone.utc).astimezone()
            return tz_date.strftime("%d %b %Y at %H:%M")

        def download_rev(rev: str) -> Tuple[List[str], FileMetadata]:
            """
            Download a rev to a tmp file, read it and return the content + metadata.
            """

            with tempfile.NamedTemporaryFile(mode="w+") as f:
                md = self.client.download(dbx_path, f.name, rev=rev)

                # Read from the file.
                try:
                    with convert_api_errors(dbx_path=dbx_path, local_path=f.name):
                        content = f.readlines()
                except UnicodeDecodeError:
                    raise UnsupportedFileTypeForDiff(
                        "Failed to decode the file",
                        "Only UTF-8 plain text files are currently supported.",
                    )

            return content, md

        # Get the metadata for old_rev before attempting to download. This is used
        # to guess the file type and fail early for unsupported files.

        md_old = self.client.get_metadata(f"rev:{old_rev}", include_deleted=True)

        if md_old is None:
            raise NotFoundError(
                f"Could not a file with revision {old_rev}",
                "Use 'list_revisions' to list past revisions of a file.",
            )

        dbx_path = self.sync.correct_case(md_old.path_display)
        local_path = self.sync.to_local_path(md_old.path_display)

        # Check if a diff is possible.
        # If mime is None, proceed because most files without
        # an extension are just text files.
        mime, _ = mimetypes.guess_type(dbx_path)
        if mime is not None and not mime.startswith("text/"):
            raise UnsupportedFileTypeForDiff(
                f"Bad file type: '{mime}'", "Only files of type 'text/*' are supported."
            )

        if new_rev:
            content_new, md_new = download_rev(new_rev)
            date_str_new = str_from_date(md_new.client_modified)
        else:
            # Use the local file if new_rev is None.
            new_rev = "local version"
            try:
                with convert_api_errors(dbx_path=dbx_path, local_path=local_path):
                    mtime = time.localtime(osp.getmtime(local_path))
                    date_str_new = time.strftime("%d %b %Y at %H:%M", mtime)

                    with open(local_path) as f:
                        content_new = f.readlines()

            except UnicodeDecodeError:
                raise UnsupportedFileTypeForDiff(
                    "Failed to decode the file",
                    "Only UTF-8 plain text files are currently supported.",
                )

        content_old, md_old = download_rev(old_rev)
        date_str_old = str_from_date(md_old.client_modified)

        return list(
            difflib.unified_diff(
                content_old,
                content_new,
                fromfile=f"{dbx_path} ({old_rev})",
                tofile=f"{dbx_path} ({new_rev})",
                fromfiledate=date_str_old,
                tofiledate=date_str_new,
            )
        )

    def restore(self, dbx_path: str, rev: str) -> SerializedObjectType:
        """
        Restore an old revision of a file.

        :param dbx_path: The path to save the restored file.
        :param rev: The revision to restore. Old revisions can be listed with
            :meth:`list_revisions`.
        :returns: Metadata of the returned file. See :class:`dropbox.files.FileMetadata`
            for keys and values.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        """

        self._check_linked()

        self._logger.info(f"Restoring '{dbx_path} to {rev}'")

        res = self.client.restore(dbx_path, rev)
        return dropbox_stone_to_dict(res)

    def _delete_old_profile_pics(self):
        for file in os.listdir(get_cache_path("maestral")):
            if file.startswith(f"{self._config_name}_profile_pic"):
                try:
                    os.unlink(osp.join(get_cache_path("maestral"), file))
                except OSError:
                    pass

    def rebuild_index(self) -> None:
        """
        Rebuilds the rev file by comparing remote with local files and updating rev
        numbers from the Dropbox server. Files are compared by their content hashes and
        conflicting copies are created if the contents differ. File changes during the
        rebuild process will be queued and uploaded once rebuilding has completed.

        Rebuilding will be performed asynchronously and errors can be accessed through
        :attr:`sync_errors` or :attr:`maestral_errors`.

        :raises NotLinkedError: if no Dropbox account is linked.
        :raises NoDropboxDirError: if local Dropbox folder is not set up.
        """

        self._check_linked()
        self._check_dropbox_dir()

        self.manager.rebuild_index()

    def start_sync(self) -> None:
        """
        Creates syncing threads and starts syncing.

        :raises NotLinkedError: if no Dropbox account is linked.
        :raises NoDropboxDirError: if local Dropbox folder is not set up.
        """

        self._check_linked()
        self._check_dropbox_dir()

        self.manager.start()

    def stop_sync(self) -> None:
        """
        Stops all syncing threads if running. Call :meth:`start_sync` to restart
        syncing.
        """
        self.manager.stop()

    def reset_sync_state(self) -> None:
        """
        Resets the sync index and state. Only call this to clean up leftover state
        information if a Dropbox was improperly unlinked (e.g., auth token has been
        manually deleted). Otherwise leave state management to Maestral.

        :raises NotLinkedError: if no Dropbox account is linked.
        """

        self._check_linked()
        self.manager.reset_sync_state()

    def set_excluded_items(self, items: List[str]) -> None:
        warnings.warn(
            "'set_excluded_items' is deprecated, please set 'excluded_items' directly",
            DeprecationWarning,
        )
        self.excluded_items = items

    def exclude_item(self, dbx_path: str) -> None:
        """
        Excludes file or folder from sync and deletes it locally. It is safe to call
        this method with items which have already been excluded.

        :param dbx_path: Dropbox path of item to exclude.
        :raises NotFoundError: if there is nothing at the given path.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        :raises NoDropboxDirError: if local Dropbox folder is not set up.
        """

        self._check_linked()
        self._check_dropbox_dir()

        dbx_path_lower = normalize(dbx_path.rstrip("/"))

        # ---- input validation --------------------------------------------------------

        md = self.client.get_metadata(dbx_path_lower)

        if not md:
            raise NotFoundError(
                "Cannot exclude item", f'"{dbx_path_lower}" does not exist on Dropbox'
            )

        if self.sync.is_excluded_by_user(dbx_path_lower):
            self._logger.info("%s was already excluded", dbx_path_lower)
            self._logger.info(IDLE)
            return

        if self.sync.sync_lock.acquire(blocking=False):

            try:

                # ---- update excluded items list --------------------------------------

                excluded_items = self.sync.excluded_items
                excluded_items.append(dbx_path_lower)

                self.sync.excluded_items = excluded_items

                # ---- remove item from local Dropbox ----------------------------------

                self._remove_after_excluded(dbx_path_lower)

                self._logger.info("Excluded %s", dbx_path_lower)
                self._logger.info(IDLE)
            finally:
                self.sync.sync_lock.release()

        else:
            raise BusyError("Cannot exclude item", "Please try again when idle.")

    def _remove_after_excluded(self, dbx_path_lower: str) -> None:

        # Perform housekeeping.
        self.sync.remove_node_from_index(dbx_path_lower)
        self.sync.clear_sync_errors_for_path(dbx_path_lower, recursive=True)

        # Remove folder from local drive.
        local_path_uncased = f"{self.dropbox_path}{dbx_path_lower}"

        try:
            local_path = to_existing_unnormalized_path(local_path_uncased)
        except FileNotFoundError:
            return

        event_cls = DirDeletedEvent if osp.isdir(local_path) else FileDeletedEvent
        with self.manager.sync.fs_events.ignore(event_cls(local_path)):
            delete(local_path)

    def include_item(self, dbx_path: str) -> None:
        """
        Includes a file or folder in sync and downloads it in the background. It is safe
        to call this method with items which have already been included, they will not
        be downloaded again.

        If the path lies inside an excluded folder, all its immediate parents will be
        included. Other children of the excluded folder will remain excluded.

        If any children of dbx_path were excluded, they will now be included.

        Any downloads will be carried out by the sync threads. Errors during the
        download can be accessed through :attr:`sync_errors` or :attr:`maestral_errors`.

        :param dbx_path: Dropbox path of item to include.
        :raises NotFoundError: if there is nothing at the given path.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        :raises NoDropboxDirError: if local Dropbox folder is not set up.
        """

        self._check_linked()
        self._check_dropbox_dir()

        dbx_path_lower = normalize(dbx_path.rstrip("/"))

        # ---- input validation --------------------------------------------------------

        md = self.client.get_metadata(dbx_path_lower)

        if not md:
            raise NotFoundError(
                "Cannot include item",
                f"'{dbx_path_lower}' does not exist on Dropbox",
            )

        if not self.sync.is_excluded_by_user(dbx_path_lower):
            self._logger.info("'%s' is already included, nothing to do", dbx_path_lower)
            self._logger.info(IDLE)
            return

        # ---- update excluded items list ----------------------------------------------

        excluded_items = set(self.sync.excluded_items)

        # Remove dbx_path from list.
        try:
            excluded_items.remove(dbx_path_lower)
        except KeyError:
            pass

        excluded_parent: Optional[str] = None

        for folder in excluded_items.copy():

            # Include all parents which are required to download dbx_path.
            if is_child(dbx_path_lower, folder):
                # Remove parent folders from excluded list.
                excluded_items.remove(folder)
                # Re-add their children (except parents of dbx_path).
                for res in self.client.list_folder_iterator(folder):
                    for entry in res.entries:
                        if not is_equal_or_child(dbx_path_lower, entry.path_lower):
                            excluded_items.add(entry.path_lower)

                excluded_parent = folder

            # Include all children of dbx_path.
            if is_child(folder, dbx_path_lower):
                excluded_items.remove(folder)

        if self.sync.sync_lock.acquire(blocking=False):

            try:

                self.sync.excluded_items = list(excluded_items)

                # ---- download item from Dropbox --------------------------------------

                if excluded_parent:
                    self._logger.info(
                        "Included '%s' and parent directories", dbx_path_lower
                    )
                    self.manager.added_item_queue.put(excluded_parent)
                else:
                    self._logger.info("Included '%s'", dbx_path_lower)
                    self.manager.added_item_queue.put(dbx_path_lower)
            finally:
                self.sync.sync_lock.release()

        else:
            raise BusyError("Cannot include item", "Please try again when idle.")

    def excluded_status(self, dbx_path: str) -> str:
        """
        Returns 'excluded', 'partially excluded' or 'included'. This function will not
        check if the item actually exists on Dropbox.

        :param dbx_path: Path to item on Dropbox.
        :returns: Excluded status.
        :raises NotLinkedError: if no Dropbox account is linked.
        """

        self._check_linked()

        dbx_path_lower = normalize(dbx_path.rstrip("/"))

        if any(is_equal_or_child(dbx_path_lower, f) for f in self.sync.excluded_items):
            return "excluded"
        elif any(is_child(f, dbx_path_lower) for f in self.sync.excluded_items):
            return "partially excluded"
        else:
            return "included"

    def move_dropbox_directory(self, new_path: str) -> None:
        """
        Sets the local Dropbox directory. This moves all local files to the new location
        and resumes syncing afterwards.

        :param new_path: Full path to local Dropbox folder. "~" will be expanded to the
            user's home directory.
        :raises OSError: if moving the directory fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        :raises NoDropboxDirError: if local Dropbox folder is not set up.
        """

        self._check_linked()
        self._check_dropbox_dir()

        self._logger.info("Moving Dropbox folder...")

        old_path = self.sync.dropbox_path
        new_path = osp.realpath(osp.expanduser(new_path))

        try:
            if osp.samefile(old_path, new_path):
                self._logger.info(f'Dropbox folder moved to "{new_path}"')
                return
        except FileNotFoundError:
            pass

        if osp.exists(new_path):
            raise FileExistsError(f'Path "{new_path}" already exists.')

        # Pause syncing.
        was_syncing = self.running
        self.stop_sync()

        if osp.isdir(old_path):
            # Will also create ancestors of new_path if required.
            shutil.move(old_path, new_path)
        else:
            os.makedirs(new_path)

        # Update config file and client.
        self.sync.dropbox_path = new_path

        self._logger.info(f'Dropbox folder moved to "{new_path}"')

        # Resume syncing.
        if was_syncing:
            self.start_sync()

    def create_dropbox_directory(self, path: str) -> None:
        """
        Creates a new Dropbox directory. Only call this during setup.

        :param path: Full path to local Dropbox folder. "~" will be expanded to the
            user's home directory.
        :raises OSError: if creation fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        """

        self._check_linked()

        # Pause syncing.
        resume = False
        if self.running:
            self.stop_sync()
            resume = True

        # Perform housekeeping.
        path = osp.realpath(osp.expanduser(path))
        self.manager.reset_sync_state()

        # Create new folder.
        os.makedirs(path, exist_ok=True)

        # Update config file and client.
        self.sync.dropbox_path = path

        # Resume syncing.
        if resume:
            self.start_sync()

    def create_shared_link(
        self,
        dbx_path: str,
        visibility: str = "public",
        password: Optional[str] = None,
        expires: Optional[float] = None,
    ) -> SerializedObjectType:
        """
        Creates a shared link for the given ``dbx_path``. Returns a dictionary with
        information regarding the link, including the URL, access permissions, expiry
        time, etc. The shared link will grant read / download access only. Note that
        basic accounts do not support password protection or expiry times.

        :param dbx_path: Path to item on Dropbox.
        :param visibility: Requested visibility of the shared link. Must be "public",
            "team_only" or "password". The actual visibility may be different, depending
            on the team and folder settings. Inspect the "link_permissions" entry of the
            returned dictionary.
        :param password: An optional password required to access the link. Will be
            ignored if the visibility is not "password".
        :param expires: An optional expiry time for the link as POSIX timestamp.
        :returns: Shared link information as dict. See
            :class:`dropbox.sharing.SharedLinkMetadata` for keys and values.
        :raises ValueError: if visibility is 'password' but no password is provided.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        """

        self._check_linked()

        if visibility not in ("public", "team_only", "password"):
            raise ValueError("Visibility must be 'public', 'team_only', or 'password'")

        if visibility == "password" and not password:
            raise ValueError("Please specify a password")

        link_info = self.client.create_shared_link(
            dbx_path=dbx_path,
            visibility=RequestedVisibility(visibility),
            password=password,
            expires=datetime.utcfromtimestamp(expires) if expires else None,
        )

        return dropbox_stone_to_dict(link_info)

    def revoke_shared_link(self, url: str) -> None:
        """
        Revokes the given shared link. Note that any other links to the same file or
        folder will remain valid.

        :param url: URL of shared link to revoke.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        """

        self._check_linked()
        self.client.revoke_shared_link(url)

    def list_shared_links(
        self, dbx_path: Optional[str] = None
    ) -> List[SerializedObjectType]:
        """
        Returns a list of all shared links for the given Dropbox path. If no path is
        given, return all shared links for the account, up to a maximum of 1,000 links.

        :param dbx_path: Path to item on Dropbox.
        :returns: List of shared link information as dictionaries. See
            :class:`dropbox.sharing.SharedLinkMetadata` for keys and values.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        """

        self._check_linked()
        res = self.client.list_shared_links(dbx_path)

        return [dropbox_stone_to_dict(link) for link in res.links]

    # ==== Utility methods for front ends ==============================================

    def to_local_path(self, dbx_path: str) -> str:
        """
        Converts a path relative to the Dropbox folder to a correctly cased local file
        system path.

        :param dbx_path: Path relative to Dropbox root.
        :returns: Corresponding path on local hard drive.
        :raises NotLinkedError: if no Dropbox account is linked.
        :raises NoDropboxDirError: if local Dropbox folder is not set up.
        """

        self._check_linked()
        self._check_dropbox_dir()

        return self.sync.to_local_path(dbx_path)

    def check_for_updates(self) -> Dict[str, Union[str, bool, None]]:
        """
        Checks if an update is available.

        :returns: A dictionary with information about the latest release with the fields
            'update_available' (bool), 'latest_release' (str), 'release_notes' (str)
            and 'error' (str or None).
        """
        current_version = __version__.lstrip("v")
        new_version = None
        update_release_notes = ""
        error_msg = None

        try:
            resp = requests.get(GITHUB_RELEASES_API)
            resp.raise_for_status()

            data = resp.json()

            releases = []
            release_notes = []

            # Remove? The GitHub API already returns sorted entries.
            data.sort(key=lambda x: Version(x["tag_name"]), reverse=True)

            for item in data:
                v = item["tag_name"].lstrip("v")
                if not Version(v).is_prerelease:
                    releases.append(v)
                    release_notes.append("### {tag_name}\n\n{body}".format(**item))

            new_version = get_newer_version(current_version, releases)

            if new_version:
                # closest_release == current_version if current_version appears in the
                # release list. Otherwise closest_release < current_version
                closest_release = next(
                    v for v in releases if Version(v) <= Version(current_version)
                )
                closest_release_idx = releases.index(closest_release)

                update_release_notes_list = release_notes[0:closest_release_idx]
                update_release_notes = "\n".join(update_release_notes_list)

        except requests.exceptions.HTTPError:
            error_msg = "Unable to retrieve information. Please try again later."
        except CONNECTION_ERRORS:
            error_msg = "No internet connection. Please try again later."
        except Exception:
            error_msg = "Something when wrong. Please try again later."

        return {
            "update_available": bool(new_version),
            "latest_release": new_version or current_version,
            "release_notes": update_release_notes,
            "error": error_msg,
        }

    def shutdown_daemon(self) -> None:
        """
        Stop syncing and clean up our asyncio tasks. Set a result for the
        :attr:`shutdown_complete` future.
        """

        self.stop_sync()

        for task in self._tasks:
            task.cancel()

        self._pool.shutdown()

        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self.shutdown_complete.set_result, True)

    # ==== Verifiers ===================================================================

    def _check_linked(self) -> None:

        if not self.client.linked:
            raise NotLinkedError(
                "No Dropbox account linked",
                "Please link an account using the GUI or CLI.",
            )

    def _check_dropbox_dir(self) -> None:

        if self.pending_dropbox_folder:
            raise NoDropboxDirError(
                "No local Dropbox directory",
                "Please set up a local Dropbox directory using the GUI or CLI.",
            )

    # ==== Housekeeping on update  =====================================================

    def _check_and_run_post_update_scripts(self) -> None:
        """
        Runs post-update scripts if necessary.
        """

        updated_from = self._state.get("app", "updated_scripts_completed")

        if Version(updated_from) < Version("1.4.8"):
            self._update_from_pre_v1_4_8()
        if Version(updated_from) < Version("1.5.3"):
            self._update_from_pre_v1_5_3()
        if Version(updated_from) < Version("1.6.0.dev0"):
            self._update_from_pre_v1_6_0()

        self._state.set("app", "updated_scripts_completed", __version__)

        self._conf.remove_deprecated_options()
        self._state.remove_deprecated_options()

    def _update_from_pre_v1_4_8(self) -> None:
        raise RuntimeError("Cannot upgrade from version before v1.4.8")

    def _update_from_pre_v1_5_3(self) -> None:

        self._logger.info("Migrating database after update from pre v1.5.3")

        db_path = get_data_path("maestral", f"{self.config_name}.db")
        db = Database(db_path, check_same_thread=False)

        _sql_drop_table(db, "hash_cache")
        _sql_add_column(db, "history", "symlink_target", "TEXT")
        _sql_add_column(db, "'index'", "symlink_target", "TEXT")

        db.close()

    def _update_from_pre_v1_6_0(self) -> None:

        self._logger.info("Scheduling reindex after update from pre v1.6.0")

        db_path = get_data_path("maestral", f"{self.config_name}.db")
        db = Database(db_path, check_same_thread=False)

        _sql_drop_table(db, "hash_cache")
        _sql_drop_table(db, "'index'")
        _sql_drop_table(db, "'history'")

        self._state.reset_to_defaults("sync")

        db.close()

    # ==== Periodic async jobs =========================================================

    def _schedule_task(self, coro: Awaitable) -> None:
        """Schedules a task in our asyncio loop."""

        task = self._loop.create_task(coro)
        self._tasks.add(task)

    async def _periodic_refresh_profile(self) -> None:
        """Periodically refresh some infos."""

        await asyncio.sleep(60 * 5)

        while True:

            if self.client.auth.loaded:

                # Only run if we have loaded the keyring, we don't
                # want to trigger any keyring access from here.

                try:
                    await self._loop.run_in_executor(self._pool, self.get_profile_pic)
                    await self._loop.run_in_executor(self._pool, self.get_account_info)
                except (ConnectionError, MaestralApiError):
                    await sleep_rand(60 * 10)
                else:
                    await sleep_rand(60 * 45)

    async def _periodic_reindexing(self) -> None:
        """
        Trigger periodic reindexing, determined by the 'reindex_interval' setting. Don't
        reindex if we are running on battery power.
        """

        while True:

            if self.manager.running.is_set():
                elapsed = time.time() - self.sync.last_reindex
                ac_state = get_ac_state()

                reindexing_due = elapsed > self.manager.reindex_interval
                is_idle = self.manager.idle_time > 20 * 60
                has_ac_power = ac_state in (ACState.Connected, ACState.Undetermined)

                if reindexing_due and is_idle and has_ac_power:
                    self.manager.rebuild_index()

            await sleep_rand(60 * 5)

    def __repr__(self) -> str:

        email = self._state.get("account", "email")
        account_type = self._state.get("account", "type")

        return (
            f"<{self.__class__.__name__}(config={self._config_name!r}, "
            f"account=({email!r}, {account_type!r}))>"
        )


async def sleep_rand(target: float, jitter: float = 60):
    await asyncio.sleep(target + random.random() * jitter)
