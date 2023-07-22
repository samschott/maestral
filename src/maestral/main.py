"""This module defines the main API which is exposed to the CLI or GUI."""

from __future__ import annotations

# system imports
import os
import os.path as osp
import shutil
import sqlite3
import time
import asyncio
import random
import gc
import tempfile
import mimetypes
import difflib
import logging
from asyncio import AbstractEventLoop, Future
from typing import Iterator, Any, Sequence

# external imports
import requests
from watchdog.events import DirDeletedEvent, FileDeletedEvent
from packaging.version import Version
from datetime import datetime, timezone

try:
    from systemd import journal
except ImportError:
    journal = None

# local imports
from . import __version__
from .client import DropboxClient
from .keyring import CredentialStorage
from .core import (
    SharedLinkMetadata,
    FullAccount,
    SpaceUsage,
    Metadata,
    FileMetadata,
    LinkAudience,
    LinkAccessLevel,
    UpdateCheckResult,
)
from .sync import SyncDirection, SyncEngine
from .manager import SyncManager
from .models import SyncEvent, SyncErrorEntry, SyncStatus
from .notify import MaestralDesktopNotifier
from .exceptions import (
    MaestralApiError,
    NotLinkedError,
    NoDropboxDirError,
    NotFoundError,
    BusyError,
    KeyringAccessError,
    UnsupportedFileTypeForDiff,
    UpdateCheckError,
)
from .errorhandling import convert_api_errors, CONNECTION_ERRORS
from .config import MaestralConfig, MaestralState, validate_config_name
from .logging import (
    AwaitableHandler,
    CachedHandler,
    scoped_logger,
    setup_logging,
    LOG_FMT_SHORT,
)
from .utils import get_newer_version
from .utils.path import (
    isdir,
    is_child,
    is_equal_or_child,
    to_existing_unnormalized_path,
    normalize,
    delete,
)
from .utils.appdirs import get_cache_path, get_data_path
from .database.core import Database
from .constants import (
    IS_LINUX,
    IS_MACOS,
    IDLE,
    PAUSED,
    CONNECTING,
    DEFAULT_CONFIG_NAME,
    GITHUB_RELEASES_API,
    FileStatus,
)


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
    :exc:`maestral.exceptions.MaestralApiError`: they need to be registered explicitly
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
    :param event_loop: Event loop to use for any features that require an asyncio event
        loop. If not given, those features will be disabled. This currently only affects
        desktop notifications.
    :param shutdown_future: Feature to set a result when shutdown is complete. Used to
        inform the caller if an API client calls :method:`shutdown_daemon`. The event
        loop associated with the Future must be the same as ``event_loop``.
    """

    _external_log_handlers: Sequence[logging.Handler]
    _log_handler_status_longpoll: AwaitableHandler
    _log_handler_info_cache: CachedHandler
    _log_handler_error_cache: CachedHandler

    def __init__(
        self,
        config_name: str = DEFAULT_CONFIG_NAME,
        log_to_stderr: bool = False,
        event_loop: AbstractEventLoop | None = None,
        shutdown_future: Future[bool] | None = None,
    ) -> None:
        # Check system compatibility.
        self._check_system_compatibility()

        self._loop = event_loop
        self._config_name = validate_config_name(config_name)
        self._conf = MaestralConfig(self.config_name)
        self._state = MaestralState(self.config_name)
        self._logger = scoped_logger(__name__, self.config_name)
        self.cred_storage = CredentialStorage(self.config_name)

        # Set up logging.
        self._log_to_stderr = log_to_stderr
        self._root_logger = scoped_logger("maestral", self.config_name)
        self._root_logger.setLevel(min(self.log_level, logging.INFO))
        self._root_logger.handlers.clear()
        self._setup_logging_external()
        self._setup_logging_internal()

        # Run update scripts after init of loggers and config / state.
        self._check_and_run_post_update_scripts()

        # Set up desktop notifier using event loop.
        self._dn: MaestralDesktopNotifier | None = None
        if self._loop:
            self._dn = MaestralDesktopNotifier(self._config_name, self._loop)

        # Set up sync infrastructure.
        self.client = DropboxClient(
            self.config_name,
            self.cred_storage,
            bandwidth_limit_up=self.bandwidth_limit_up,
            bandwidth_limit_down=self.bandwidth_limit_down,
        )
        self.sync = SyncEngine(self.client, self._dn)
        self.manager = SyncManager(self.sync, self._dn)

        # Create a future which will return once `shutdown_daemon` is called.
        # This can be used by an event loop to wait until maestral has been stopped.
        if shutdown_future and not shutdown_future.get_loop() is self._loop:
            raise RuntimeError("'shutdown_future' must use the passed event loop.")

        self.shutdown_future = shutdown_future

    @staticmethod
    def _check_system_compatibility() -> None:
        if os.stat not in os.supports_follow_symlinks:
            raise RuntimeError("Maestral requires lstat support")

        if not (IS_MACOS or IS_LINUX):
            raise RuntimeError("Only macOS and Linux are supported")

    def _setup_logging_external(self) -> None:
        """
        Sets up logging to external channels:
          * Log files.
          * The systemd journal, if started by systemd.
          * The systemd notify status, if started by systemd.
          * Stderr, if requested.
        """
        self._external_log_handlers = setup_logging(
            self.config_name, stderr=self._log_to_stderr
        )

    def _setup_logging_internal(self) -> None:
        """Sets up logging to internal info and error caches."""
        # Log to cached handlers for status and error APIs.
        self._log_handler_info_cache = CachedHandler(maxlen=1)
        self._log_handler_info_cache.setFormatter(LOG_FMT_SHORT)
        self._log_handler_info_cache.setLevel(logging.INFO)
        self._root_logger.addHandler(self._log_handler_info_cache)

        self._log_handler_error_cache = CachedHandler()
        self._log_handler_error_cache.setFormatter(LOG_FMT_SHORT)
        self._log_handler_error_cache.setLevel(logging.ERROR)
        self._root_logger.addHandler(self._log_handler_error_cache)

        self._log_handler_status_longpoll = AwaitableHandler(max_unblock_per_second=1)
        self._log_handler_status_longpoll.setFormatter(LOG_FMT_SHORT)
        self._log_handler_status_longpoll.setLevel(logging.INFO)
        self._root_logger.addHandler(self._log_handler_status_longpoll)

    @property
    def version(self) -> str:
        """Returns the current Maestral version."""
        return __version__

    def get_auth_url(self) -> str:
        """
        Returns a URL to authorize access to a Dropbox account. To link a Dropbox
        account, retrieve an authorization code from the URL and link Maestral by
        calling :meth:`link` with the provided code.

        :returns: URL to retrieve an authorization code.
        """
        return self.client.get_auth_url()

    def link(
        self,
        code: str | None = None,
        refresh_token: str | None = None,
        access_token: str | None = None,
    ) -> int:
        """
        Links Maestral with a Dropbox account using the given authorization code. The
        code will be exchanged for an access token and a refresh token with Dropbox
        servers. The refresh token will be stored for future usage as documented in the
        :mod:`oauth` module. Supported keyring backends are, in order of preference:

            * macOS Keychain
            * Any keyring implementing the SecretService Dbus specification
            * KWallet
            * Gnome Keyring
            * Plain text storage

        For testing, it is also possible to directly provide a long-lived refresh token
        or a short-lived access token. Note that the tokens must be issued for Maestral,
        with the required scopes, and will be validated with Dropbox servers as part of
        this call.

        :param code: Authorization code.
        :param refresh_token: Optionally, instead of an authorization code, directly
            provide a refresh token.
        :param access_token: Optionally, instead of an authorization code or a refresh
            token, directly provide an access token. Note that access tokens are
            short-lived.
        :returns: 0 on success, 1 for an invalid token and 2 for connection errors.
        """
        return self.client.link(
            code=code, refresh_token=refresh_token, access_token=access_token
        )

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
            self.cred_storage.delete_creds()
        except KeyringAccessError:
            self._logger.debug("Could not remove token from keyring", exc_info=True)

        # Clean up config and state files.
        self._conf.cleanup()
        self._state.cleanup()
        self.sync.reset_sync_state()
        self.sync.reload_cached_config()

        self._logger.info("Unlinked Dropbox account.")

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
    def excluded_items(self) -> list[str]:
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
    def excluded_items(self, items: list[str]) -> None:
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
                            self.manager.download_queue.put(path)

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
    def log_level(self, level: int) -> None:
        """Setter: log_level."""
        self._root_logger.setLevel(min(level, logging.INFO))
        for handler in self._external_log_handlers:
            handler.setLevel(level)
        self._conf.set("app", "log_level", level)

    @property
    def notification_snooze(self) -> float:
        """Snooze time for desktop notifications in minutes. Defaults to 0 if
        notifications are not snoozed."""
        if not self._dn:
            raise RuntimeError("Desktop notifications require an event loop")
        return self._dn.snoozed

    @notification_snooze.setter
    def notification_snooze(self, minutes: float) -> None:
        """Setter: notification_snooze."""
        if not self._dn:
            raise RuntimeError("Desktop notifications require an event loop")
        self._dn.snoozed = minutes

    @property
    def notification_level(self) -> int:
        """Level for desktop notifications. See :mod:`notify` for level definitions."""
        if not self._dn:
            raise RuntimeError("Desktop notifications require an event loop")
        return self._dn.notify_level

    @notification_level.setter
    def notification_level(self, level: int) -> None:
        """Setter: notification_level."""
        if not self._dn:
            raise RuntimeError("Desktop notifications require an event loop")
        self._dn.notify_level = level

    @property
    def bandwidth_limit_down(self) -> float:
        """Maximum download bandwidth to use in bytes per second."""
        return self._conf.get("app", "bandwidth_limit_down")

    @bandwidth_limit_down.setter
    def bandwidth_limit_down(self, value: float) -> None:
        """Setter: bandwidth_limit_down."""
        self.client.bandwidth_limit_down = value
        self._conf.set("app", "bandwidth_limit_down", value)

    @property
    def bandwidth_limit_up(self) -> float:
        """Maximum download bandwidth to use in bytes per second."""
        return self._conf.get("app", "bandwidth_limit_up")

    @bandwidth_limit_up.setter
    def bandwidth_limit_up(self, value: float) -> None:
        """Setter: bandwidth_limit_up."""
        self.client.bandwidth_limit_up = value
        self._conf.set("app", "bandwidth_limit_up", value)

    # ==== State information  ==========================================================

    def status_change_longpoll(self, timeout: float | None = 60) -> bool:
        """
        Blocks until there is a change in status or until a timeout occurs.

        This method can be used by frontends to wait for status changes without constant
        polling. Status changes are for example transitions from syncing to idle or
        vice-versa, new errors, or connection status changes.

        Will unblock at most once per second.

        :param timeout: Maximum time to block before returning, even if there is no
            status change.
        :returns: Whether there was a status change within the timeout.

        .. versionadded:: 1.3.0
        """
        return self._log_handler_status_longpoll.wait_for_emit(timeout)

    @property
    def pending_link(self) -> bool:
        """Whether Maestral is linked to a Dropbox account (read only). This will block
        until the user's keyring is unlocked to load the saved auth token."""
        return not self.client.linked

    @property
    def pending_dropbox_folder(self) -> bool:
        """Whether a local Dropbox directory has been configured (read only). This will
        not check if the configured directory actually exists, starting the sync may
        still raise a :exc:`maestral.exceptions.NoDropboxDirError`."""
        return not self.sync.dropbox_path

    @property
    def pending_first_download(self) -> bool:
        """Whether the initial download has already started (read only)."""
        return self.sync.local_cursor == 0 or self.sync.remote_cursor == ""

    @property
    def paused(self) -> bool:
        """Whether syncing is paused by the user (read only). Use :meth:`start_sync` and
        :meth:`stop_sync` to start and stop syncing, respectively."""
        return not self.manager.autostart.is_set() and not self.sync.busy()

    @property
    def running(self) -> bool:
        """Whether sync threads are running (read only). This is similar to
        :attr:`paused` but also returns False if syncing is paused because we cannot
        connect to Dropbox servers.."""
        return self.manager.running.is_set() or self.sync.busy()

    @property
    def connected(self) -> bool:
        """Whether Dropbox servers can be reached (read only)."""
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
            return self._log_handler_info_cache.get_last_message()

    @property
    def sync_errors(self) -> list[SyncErrorEntry]:
        """
        A list of current sync errors as dicts (read only). This list is populated by
        the sync threads. The following keys will always be present but may contain
        empty values: "type", "inherits", "title", "traceback", "title", "message",
        "local_path", "dbx_path".

        :raises NotLinkedError: if no Dropbox account is linked.
        """
        return self.sync.sync_errors

    @property
    def fatal_errors(self) -> list[MaestralApiError]:
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
        errors: list[MaestralApiError] = []

        for r in self._log_handler_error_cache.cached_records:
            if r.exc_info:
                err = r.exc_info[1]
                if isinstance(err, MaestralApiError):
                    errors.append(err)

        return errors

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
        for folders.

        * "uploading" if any file inside the folder is being uploaded.
        * "downloading" if any file inside the folder is being downloaded.
        * "error" if any item inside the folder failed to sync and none are currently
          being uploaded or downloaded.
        * "up to date" if all items are successfully synced.
        * "unwatched" if syncing is paused or for items outside the Dropbox directory.

        .. versionadded:: 1.4.4
           Recursive behavior. Previous versions would return "up to date" for a folder,
           even if some contained files would be syncing.

        :param local_path: Path to file on the local drive. May be relative to the
            current working directory.
        :returns: String indicating the sync status. Can be 'uploading', 'downloading',
            'up to date', 'error', or 'unwatched'.
        """
        if not self.running:
            return FileStatus.Unwatched.value

        local_path = osp.realpath(local_path)

        try:
            dbx_path_cased = self.sync.to_dbx_path(local_path)
        except ValueError:
            return FileStatus.Unwatched.value

        # Find any sync activity for the local path.
        node = self.sync.activity.get_node(dbx_path_cased)

        if not node:
            # Always return synced for the root folder in the absense of sync activity.
            if dbx_path_cased == "/":
                return FileStatus.Synced.value

            # Check if the path is in our index. If yes, it is fully synced, otherwise
            # it is unwatched.
            if self.sync.get_index_entry_for_local_path(local_path):
                return FileStatus.Synced.value

            return FileStatus.Unwatched.value

        # Return effective status of item and its children. Syncing items take
        # precedence over Failed which take precedence over Synced. Note that Up and
        # Down are mutually exclusive because they are performed in alternating cycles.
        file_status = FileStatus.Synced

        for event in node.sync_events:
            if event.status is SyncStatus.Syncing:
                if event.direction is SyncDirection.Up:
                    return FileStatus.Uploading.value
                elif event.direction is SyncDirection.Down:
                    return FileStatus.Downloading.value
            elif event.status is SyncStatus.Failed:
                file_status = FileStatus.Error

        return file_status.value

    def get_activity(self, limit: int | None = 100) -> list[SyncEvent]:
        """
        Returns the current upload / download activity.

        :param limit: Maximum number of items to return. If None, all entries will be
            returned.
        :returns: A lists of all sync events currently queued for or being uploaded or
            downloaded with the events the furthest up in the queue coming first.
        :raises NotLinkedError: if no Dropbox account is linked.
        """
        self._check_linked()
        return list(self.sync.activity.sync_events)[:limit]

    def get_history(
        self, dbx_path: str | None = None, limit: int | None = 100
    ) -> list[SyncEvent]:
        """
        Returns the historic upload / download activity. Up to 1,000 sync events are
        kept in the database. Any events which occurred before the interval specified by
        the ``keep_history`` config value are discarded.

        :param dbx_path: If given, show sync history for the specified Dropbox path only.
        :param limit: Maximum number of items to return. If None, all entries will be
            returned.
        :returns: A lists of all sync events since ``keep_history`` sorted by time with
            the oldest event first.
        :raises NotLinkedError: if no Dropbox account is linked.
        """
        self._check_linked()
        events = self.sync.get_history(dbx_path=dbx_path)
        return events[-limit:] if limit else events

    def get_account_info(self) -> FullAccount:
        """
        Returns the account information from Dropbox and returns it as a dictionary.

        :returns: Dropbox account information.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        """
        self._check_linked()
        return self.client.get_account_info()

    def get_space_usage(self) -> SpaceUsage:
        """
        Gets the space usage from Dropbox and returns it as a dictionary.

        :returns: Dropbox space usage information.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        """
        self._check_linked()
        return self.client.get_space_usage()

    # ==== Control methods for front ends ==============================================

    def get_profile_pic(self) -> str | None:
        """
        Attempts to download the user's profile picture from Dropbox. The picture is
        saved in Maestral's cache directory for retrieval when there is no internet
        connection. Check :attr:`account_profile_pic_path` for cached profile pics.

        :returns: Path to saved profile picture or ``None`` if no profile picture was
            downloaded.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        """
        self._check_linked()

        account_info = self.client.get_account_info()

        if account_info.profile_photo_url:
            with convert_api_errors():
                res = requests.get(account_info.profile_photo_url)
                with open(self.account_profile_pic_path, "wb") as f:
                    f.write(res.content)
            return self.account_profile_pic_path
        else:
            self._delete_old_profile_pics()
            return None

    def get_metadata(self, dbx_path: str) -> Metadata | None:
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
        return self.client.get_metadata(dbx_path)

    def list_folder(
        self,
        dbx_path: str,
        recursive: bool = False,
        include_deleted: bool = False,
        include_mounted_folders: bool = True,
        include_non_downloadable_files: bool = False,
    ) -> list[Metadata]:
        """
        List all items inside the folder given by ``dbx_path``. Keyword arguments are
        passed on the Dropbox API call :meth:`client.DropboxClient.list_folder`.

        :param dbx_path: Path to folder on Dropbox.
        :param recursive: If true, the list folder operation will be applied recursively
            to all subfolders and the response will contain contents of all subfolders.
        :param include_deleted: If true, the results will include entries for files and
            folders that used to exist but were deleted.
        :param bool include_mounted_folders: If true, the results will include
            entries under mounted folders which includes app folder, shared
            folder and team folder.
        :param bool include_non_downloadable_files: If true, include files that
            are not downloadable, i.e. Google Docs.
        :raises NotFoundError: if there is nothing at the given path.
        :raises NotAFolderError: if the given path refers to a file.
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        :raises NotLinkedError: if no Dropbox account is linked.
        """
        self._check_linked()

        res = self.client.list_folder(
            dbx_path,
            recursive=recursive,
            include_deleted=include_deleted,
            include_mounted_folders=include_mounted_folders,
            include_non_downloadable_files=include_non_downloadable_files,
        )
        return res.entries

    def list_folder_iterator(
        self,
        dbx_path: str,
        recursive: bool = False,
        include_deleted: bool = False,
        include_mounted_folders: bool = True,
        limit: int | None = None,
        include_non_downloadable_files: bool = False,
    ) -> Iterator[list[Metadata]]:
        """
        Returns an iterator over items inside the folder given by ``dbx_path``. Keyword
        arguments are passed on the client call
        :meth:`client.DropboxClient.list_folder_iterator`. Each iteration will yield a
        list of approximately 500 entries, depending on the number of entries returned
        by an individual API call.

        :param dbx_path: Path to folder on Dropbox.
        :param recursive: If true, the list folder operation will be applied recursively
            to all subfolders and the response will contain contents of all subfolders.
        :param include_deleted: If true, the results will include entries for files and
            folders that used to exist but were deleted.
        :param bool include_mounted_folders: If true, the results will include
            entries under mounted folders which includes app folder, shared
            folder and team folder.
        :param Nullable[int] limit: The maximum number of results to return per
            request. Note: This is an approximate number and there can be
            slightly more entries returned in some cases.
        :param bool include_non_downloadable_files: If true, include files that
            are not downloadable, i.e. Google Docs.
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

        res_iter = self.client.list_folder_iterator(
            dbx_path,
            recursive=recursive,
            include_deleted=include_deleted,
            include_mounted_folders=include_mounted_folders,
            limit=limit,
            include_non_downloadable_files=include_non_downloadable_files,
        )

        for res in res_iter:
            yield res.entries
            del res
            gc.collect()

    def list_revisions(self, dbx_path: str, limit: int = 10) -> list[FileMetadata]:
        """
        List revisions of old files at the given path ``dbx_path``. This will also
        return revisions if the file has already been deleted.

        :param dbx_path: Path to file on Dropbox.
        :param limit: Maximum number of revisions to list.
        :returns: List of Dropbox file metadata as dicts. See
            :class:`dropbox.files.Metadata` for keys and values.
        :raises NotFoundError: if there never was a file at the given path.
        :raises IsAFolderError: if the given path refers to a folder
        :raises DropboxAuthError: in case of an invalid access token.
        :raises DropboxServerError: for internal Dropbox errors.
        :raises ConnectionError: if the connection to Dropbox fails.
        """
        self._check_linked()
        return self.client.list_revisions(dbx_path, limit=limit)

    def get_file_diff(self, old_rev: str, new_rev: str | None = None) -> list[str]:
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

        def download_rev(rev: str) -> tuple[list[str], FileMetadata]:
            """
            Download a rev to a tmp file, read it and return the content + metadata.
            """
            with tempfile.NamedTemporaryFile(mode="w+") as f:
                md = self.client.download(f"rev:{rev}", f.name)

                # Read from the file.
                try:
                    with convert_api_errors(md.path_display, f.name):
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

    def restore(self, dbx_path: str, rev: str) -> FileMetadata:
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
        self._logger.info(f"Restored '{dbx_path} to {rev}'")
        self._logger.info(IDLE)
        return res

    def _delete_old_profile_pics(self) -> None:
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

        event_cls = DirDeletedEvent if isdir(local_path) else FileDeletedEvent
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
            return

        # ---- update excluded items list ----------------------------------------------
        excluded_items = set(self.sync.excluded_items)

        # Remove dbx_path from list.
        try:
            excluded_items.remove(dbx_path_lower)
        except KeyError:
            pass

        excluded_parent: str | None = None

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
                    self.manager.download_queue.put(excluded_parent)
                else:
                    self._logger.info("Included '%s'", dbx_path_lower)
                    self.manager.download_queue.put(dbx_path_lower)
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
        visibility: LinkAudience = LinkAudience.Public,
        access_level: LinkAccessLevel = LinkAccessLevel.Viewer,
        allow_download: bool | None = None,
        password: str | None = None,
        expires: datetime | None = None,
    ) -> SharedLinkMetadata:
        """
        Creates a shared link for the given ``dbx_path``. Returns a dictionary with
        information regarding the link, including the URL, access permissions, expiry
        time, etc. The shared link will grant read / download access only. Note that
        basic accounts do not support password protection or expiry times.

        :param dbx_path: Dropbox path to file or folder to share.
        :param visibility: The visibility of the shared link. Can be public, team-only,
            or no-one. In case of the latter, the link merely points the user to the
            content and does not grant additional rights to the user. Users of this link
            can only access the content with their pre-existing access rights.
        :param access_level: The level of access granted with the link. Can be viewer,
            editor, or max for maximum possible access level.
        :param allow_download: Whether to allow download capabilities for the link.
        :param password: If given, enables password protection for the link.
        :param expires: Expiry time for shared link. If no timezone is given, assume
            UTC. May not be supported for all account types.
        :returns: Metadata for shared link.
        """
        self._check_linked()
        return self.client.create_shared_link(
            dbx_path=dbx_path,
            visibility=visibility,
            password=password,
            access_level=access_level,
            allow_download=allow_download,
            expires=expires,
        )

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
        self, dbx_path: str | None = None
    ) -> list[SharedLinkMetadata]:
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
        return self.client.list_shared_links(dbx_path)

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

    def check_for_updates(self) -> UpdateCheckResult:
        """
        Checks if an update is available.

        :returns: A dictionary with information about the latest release with the fields
            'update_available' (bool), 'latest_release' (str), 'release_notes' (str)
            and 'error' (str or None).
        :raises UpdateCheckError: if checking for an update fails.
        """
        current_version = __version__.lstrip("v")
        update_release_notes = ""

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
        except CONNECTION_ERRORS:
            raise UpdateCheckError(
                "Could not check for updates",
                "No internet connection. Please try again later.",
            )
        except Exception as e:
            raise UpdateCheckError(
                "Could not check for updates",
                f"Unable to retrieve information: {e}",
            )

        return UpdateCheckResult(
            update_available=bool(new_version),
            latest_release=new_version or current_version,
            release_notes=update_release_notes,
        )

    def shutdown_daemon(self) -> None:
        """
        Stop syncing and notify anyone monitoring ``shutdown_future`` that we are done.
        """
        self.stop_sync()

        if self.shutdown_future and self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self.shutdown_future.set_result, True)

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

        if Version(updated_from) < Version("1.2.1"):
            self._update_from_pre_v1_2_1()
        if Version(updated_from) < Version("1.3.2"):
            self._update_from_pre_v1_3_2()
        if Version(updated_from) < Version("1.4.8"):
            self._update_from_pre_v1_4_8()
        if Version(updated_from) < Version("1.6.0.dev0"):
            self._update_from_pre_v1_6_0()

        self._state.set("app", "updated_scripts_completed", __version__)

        self._conf.remove_deprecated_options()
        self._state.remove_deprecated_options()

    def _update_from_pre_v1_2_1(self) -> None:
        raise RuntimeError("Cannot upgrade from version before v1.2.1")

    def _update_from_pre_v1_3_2(self) -> None:
        if self._conf.get("app", "keyring") == "keyring.backends.OS_X.Keyring":
            self._logger.info("Migrating keyring after update from pre v1.3.2")
            self._conf.set("app", "keyring", "keyring.backends.macOS.Keyring")

    def _update_from_pre_v1_4_8(self) -> None:
        # Migrate config and state keys to new sections.
        self._logger.info("Migrating config after update from pre v1.4.8")

        mapping = {
            "path": {"old": "main", "new": "sync"},
            "excluded_items": {"old": "main", "new": "sync"},
            "keyring": {"old": "app", "new": "auth"},
            "account_id": {"old": "account", "new": "auth"},
        }

        for key, sections in mapping.items():
            if self._conf.has_option(sections["old"], key):
                value = self._conf.get(sections["old"], key)
                self._conf.set(sections["new"], key, value)

        self._logger.info("Migrating state after update from pre v1.4.8")

        mapping = {
            "token_access_type": {"old": "account", "new": "auth"},
        }

        for key, sections in mapping.items():
            if self._state.has_option(sections["old"], key):
                value = self._state.get(sections["old"], key)
                self._state.set(sections["new"], key, value)

    def _update_from_pre_v1_6_0(self) -> None:
        self._logger.info("Scheduling reindex after update from pre v1.6.0")

        db_path = get_data_path("maestral", f"{self.config_name}.db")
        connection = sqlite3.connect(db_path, check_same_thread=False)
        db = Database(connection)

        _sql_drop_table(db, "hash_cache")
        _sql_drop_table(db, "'index'")
        _sql_drop_table(db, "'history'")

        self._state.reset_to_defaults("sync")

        db.close()

    # ==== Periodic async jobs =========================================================

    def __repr__(self) -> str:
        email = self._state.get("account", "email")
        account_type = self._state.get("account", "type")

        return (
            f"<{self.__class__.__name__}(config={self._config_name!r}, "
            f"account=({email!r}, {account_type!r}))>"
        )


async def sleep_rand(target: float, jitter: float = 60) -> None:
    await asyncio.sleep(target + random.random() * jitter)
