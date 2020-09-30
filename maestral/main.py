# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

This module defines the main API which is exposed to the CLI or GUI.

"""

# system imports
import sys
import os
import os.path as osp
import platform
import shutil
import logging.handlers
from collections import deque
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Union, List, Dict, Optional, Deque, Any

# external imports
import requests
from watchdog.events import DirDeletedEvent, FileDeletedEvent  # type: ignore
import bugsnag  # type: ignore
from bugsnag.handlers import BugsnagHandler  # type: ignore
from packaging.version import Version
import sdnotify  # type: ignore

try:
    from systemd import journal  # type: ignore
except ImportError:
    journal = None

# local imports
from maestral import __version__
from maestral.client import DropboxClient, to_maestral_error
from maestral.sync import SyncMonitor, SyncDirection
from maestral.errors import (
    MaestralApiError,
    NotLinkedError,
    NoDropboxDirError,
    NotFoundError,
    PathError,
)
from maestral.config import MaestralConfig, MaestralState
from maestral.utils import get_newer_version
from maestral.utils.housekeeping import validate_config_name
from maestral.utils.path import is_child, to_existing_cased_path, delete
from maestral.utils.notify import MaestralDesktopNotifier
from maestral.utils.serializer import (
    error_to_dict,
    dropbox_stone_to_dict,
    sync_event_to_dict,
    StoneType,
    ErrorType,
)
from maestral.utils.appdirs import get_log_path, get_cache_path, get_data_path
from maestral.constants import (
    BUGSNAG_API_KEY,
    IDLE,
    FileStatus,
    GITHUB_RELEASES_API,
)


logger = logging.getLogger(__name__)
sd_notifier = sdnotify.SystemdNotifier()

CONNECTION_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ReadTimeout,
    requests.exceptions.RetryError,
    ConnectionError,
)


# set up error reporting but do not activate

bugsnag.configure(
    api_key=BUGSNAG_API_KEY,
    app_version=__version__,
    auto_notify=False,
    auto_capture_sessions=False,
)


def bugsnag_global_callback(notification):
    notification.add_tab(
        "system", {"platform": platform.platform(), "python": platform.python_version()}
    )
    cause = notification.exception.__cause__
    if cause:
        notification.add_tab("original exception", error_to_dict(cause))


bugsnag.before_notify(bugsnag_global_callback)


# custom logging handlers


class CachedHandler(logging.Handler):
    """Handler which stores past records. This is used to populate Maestral's status and
    error interfaces.

    :param level: Initial log level. Defaults to NOTSET.
    :param maxlen: Maximum number of records to store. If ``None``, all records will be
        stored. Defaults to ``None``.
    """

    cached_records: Deque[logging.LogRecord]

    def __init__(
        self, level: int = logging.NOTSET, maxlen: Optional[int] = None
    ) -> None:
        logging.Handler.__init__(self, level=level)
        self.cached_records = deque([], maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        """
        Logs the specified log record and saves it to the cache.

        :param record: Log record.
        """
        self.format(record)
        self.cached_records.append(record)

    def getLastMessage(self) -> str:
        """
        :returns: The log message of the last record or an empty string.
        """
        if len(self.cached_records) > 0:
            return self.cached_records[-1].message
        else:
            return ""

    def getAllMessages(self) -> List[str]:
        """
        :returns: A list of all record messages.
        """
        return [r.message for r in self.cached_records]

    def clear(self) -> None:
        """
        Clears all cached records.
        """
        self.cached_records.clear()


class SdNotificationHandler(logging.Handler):
    """Handler which emits messages as systemd notifications."""

    def emit(self, record: logging.LogRecord) -> None:
        """
        Sends the record massage to systemd as service status.

        :param record: Log record.
        """
        sd_notifier.notify(f"STATUS={record.message}")


# ========================================================================================
# Main API
# ========================================================================================


class Maestral:
    """The public API.

    All methods and properties return objects or raise exceptions which can safely be
    serialized, i.e., pure Python types. The only exception are instances of
    :class:`errors.MaestralApiError`: they need to be registered explicitly with the
    serpent serializer which is used for communication to frontends.

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
    :param log_to_stdout: If ``True``, Maestral will print log messages to stdout.
        When started as a systemd services, this can result in duplicate log messages.
        Defaults to ``False``.
    """

    log_handler_sd: Optional[SdNotificationHandler]
    log_handler_journal: Optional["journal.JournalHandler"]

    def __init__(
        self, config_name: str = "maestral", log_to_stdout: bool = False
    ) -> None:

        self._config_name = validate_config_name(config_name)
        self._conf = MaestralConfig(self._config_name)
        self._state = MaestralState(self._config_name)

        # enable / disable automatic reporting of errors
        bugsnag.configuration.auto_notify = self.analytics

        # set up logging
        self._log_to_stdout = log_to_stdout
        self._setup_logging()

        # set up sync infrastructure
        self.client = DropboxClient(
            config_name=self.config_name
        )  # interface to Dbx SDK
        self.monitor = SyncMonitor(self.client)  # coordinates sync threads
        self.sync = self.monitor.sync  # provides core sync functionality

        self._check_and_run_post_update_scripts()

        # schedule background tasks
        self._loop = asyncio.get_event_loop()
        self._thread_pool = ThreadPoolExecutor(
            thread_name_prefix="maestral-thread-pool",
            max_workers=2,
        )
        self._refresh_task = self._loop.create_task(
            self._periodic_refresh(),
        )

        # create a future which will return once `shutdown_daemon` is called
        # can be used by an event loop wait until maestral has been stopped
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
        Links Maestral with a Dropbox account using the given access token. The token will
        be stored for future usage as documented in the :mod:`oauth` module. Supported
        keyring backends are, in order of preference:

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

        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.KeyringAccessError` if deleting the auth key fails because
            the user's keyring is locked.
        """

        self._check_linked()
        self.stop_sync()

        try:
            self.client.unlink()
        except (ConnectionError, MaestralApiError):
            logger.debug("Could not invalidate token with Dropbox", exc_info=True)

        # clean up config + state
        self.sync.clear_index()
        self.sync.clear_sync_history()
        self._conf.cleanup()
        self._state.cleanup()
        delete(self.sync.database_path)

        logger.info("Unlinked Dropbox account.")

    def _setup_logging(self) -> None:
        """
        Sets up logging to log files, status and error properties, desktop notifications,
        the systemd journal if available, bugsnag if error reports are enabled, and to
        stdout if requested.
        """

        self._logger = logging.getLogger("maestral")
        self._logger.setLevel(logging.DEBUG)

        # clean up any previous handlers
        # TODO: use namespaced handlers for config
        while self._logger.hasHandlers():
            self._logger.removeHandler(self._logger.handlers[0])

        log_fmt_long = logging.Formatter(
            fmt="%(asctime)s %(name)s %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        log_fmt_short = logging.Formatter(fmt="%(message)s")

        # log to file
        rfh_log_file = get_log_path("maestral", self._config_name + ".log")
        self.log_handler_file = logging.handlers.RotatingFileHandler(
            rfh_log_file, maxBytes=10 ** 7, backupCount=1
        )
        self.log_handler_file.setFormatter(log_fmt_long)
        self.log_handler_file.setLevel(self.log_level)
        self._logger.addHandler(self.log_handler_file)

        # log to journal when launched from systemd
        if journal and os.getenv("INVOCATION_ID"):
            # noinspection PyUnresolvedReferences
            self.log_handler_journal = journal.JournalHandler(
                SYSLOG_IDENTIFIER="maestral"
            )
            self.log_handler_journal.setFormatter(log_fmt_short)
            self.log_handler_journal.setLevel(self.log_level)
            self._logger.addHandler(self.log_handler_journal)
        else:
            self.log_handler_journal = None

        # notify systemd of status updates
        if os.getenv("NOTIFY_SOCKET"):
            self.log_handler_sd = SdNotificationHandler()
            self.log_handler_sd.setFormatter(log_fmt_short)
            self.log_handler_sd.setLevel(logging.INFO)
            self._logger.addHandler(self.log_handler_sd)
        else:
            self.log_handler_sd = None

        # log to stdout (disabled by default)
        level = self.log_level if self._log_to_stdout else 100
        self.log_handler_stream = logging.StreamHandler(sys.stdout)
        self.log_handler_stream.setFormatter(log_fmt_long)
        self.log_handler_stream.setLevel(level)
        self._logger.addHandler(self.log_handler_stream)

        # log to cached handlers for GUI and CLI
        self._log_handler_info_cache = CachedHandler(maxlen=1)
        self._log_handler_info_cache.setFormatter(log_fmt_short)
        self._log_handler_info_cache.setLevel(logging.INFO)
        self._logger.addHandler(self._log_handler_info_cache)

        self._log_handler_error_cache = CachedHandler()
        self._log_handler_error_cache.setFormatter(log_fmt_short)
        self._log_handler_error_cache.setLevel(logging.ERROR)
        self._logger.addHandler(self._log_handler_error_cache)

        # log to desktop notifications
        # 'file changed' events will be collated and sent as desktop
        # notifications by the monitor directly, we don't handle them here
        self.desktop_notifier = MaestralDesktopNotifier.for_config(self.config_name)
        self.desktop_notifier.setLevel(logging.WARNING)
        self._logger.addHandler(self.desktop_notifier)

        # log to bugsnag (disabled by default)
        self._log_handler_bugsnag = BugsnagHandler()
        self._log_handler_bugsnag.setLevel(logging.ERROR if self.analytics else 100)
        self._logger.addHandler(self._log_handler_bugsnag)

    # ==== methods to access config and saved state ======================================

    @property
    def config_name(self) -> str:
        """The selected configuration."""
        return self._config_name

    def set_conf(self, section: str, name: str, value: Any) -> None:
        """
        Sets a configuration option.

        :param section: Name of section in config file.
        :param name: Name of config option.
        :param value: Config value. May be any type accepted by ``ast.literal_eval``.
        """
        self._conf.set(section, name, value)

    def get_conf(self, section: str, name: str) -> Any:
        """
        Gets a configuration option.

        :param section: Name of section in config file.
        :param name: Name of config option.
        :returns: Config value. May be any type accepted by ``ast.literal_eval``.
        """
        return self._conf.get(section, name)

    def set_state(self, section: str, name: str, value: Any) -> None:
        """
        Sets a state value.

        :param section: Name of section in state file.
        :param name: Name of state variable.
        :param value: State value. May be any type accepted by ``ast.literal_eval``.
        """
        self._state.set(section, name, value)

    def get_state(self, section: str, name: str) -> Any:
        """
        Gets a state value.

        :param section: Name of section in state file.
        :param name: Name of state variable.
        :returns: State value. May be any type accepted by ``ast.literal_eval``.
        """
        return self._state.get(section, name)

    # helper functions

    # ==== getters / setters for config with side effects ================================

    @property
    def dropbox_path(self) -> str:
        """
        Returns the path to the local Dropbox folder (read only). This will be an empty
        string if not Dropbox folder has been set up yet. Use
        :meth:`create_dropbox_directory` or :meth:`move_dropbox_directory` to set or
        change the Dropbox directory location instead.

        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        if self.pending_link:
            return ""
        else:
            return self.sync.dropbox_path

    @property
    def excluded_items(self) -> List[str]:
        """
        Returns a list of files and folders excluded by selective sync (read only). Use
        :meth:`exclude_item`, :meth:`include_item` or :meth:`set_excluded_items` to change
        which items are excluded from syncing.

        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """
        if self.pending_link:
            return []
        else:
            return self.sync.excluded_items

    @property
    def log_level(self) -> int:
        """Log level for log files, stdout and the systemd journal."""
        return self._conf.get("app", "log_level")

    @log_level.setter
    def log_level(self, level_num: int) -> None:
        """Setter: log_level."""
        self.log_handler_file.setLevel(level_num)
        if self.log_handler_journal:
            self.log_handler_journal.setLevel(level_num)
        if self.log_to_stdout:
            self.log_handler_stream.setLevel(level_num)
        self._conf.set("app", "log_level", level_num)

    @property
    def log_to_stdout(self) -> bool:
        """Enables or disables logging to stdout."""
        return self._log_to_stdout

    @log_to_stdout.setter
    def log_to_stdout(self, enabled: bool) -> None:
        """Setter: log_to_stdout."""
        self._log_to_stdout = enabled
        level = self.log_level if enabled else 100
        self.log_handler_stream.setLevel(level)

    @property
    def analytics(self) -> bool:
        """Enables or disables logging of errors to bugsnag."""
        return self._conf.get("app", "analytics")

    @analytics.setter
    def analytics(self, enabled: bool) -> None:
        """Setter: analytics."""

        bugsnag.configuration.auto_notify = enabled
        self._log_handler_bugsnag.setLevel(logging.ERROR if enabled else 100)

        self._conf.set("app", "analytics", enabled)

    @property
    def notification_snooze(self) -> float:
        """Snooze time for desktop notifications in minutes. Defaults to 0.0 if
        notifications are not snoozed."""
        return self.desktop_notifier.snoozed

    @notification_snooze.setter
    def notification_snooze(self, minutes: float) -> None:
        """Setter: notification_snooze."""
        self.desktop_notifier.snoozed = minutes

    @property
    def notification_level(self) -> int:
        """Level for desktop notifications. See :mod:`utils.notify` for level
        definitions."""
        return self.desktop_notifier.notify_level

    @notification_level.setter
    def notification_level(self, level: int) -> None:
        """Setter: notification_level."""
        self.desktop_notifier.notify_level = level

    # ==== state information  ============================================================

    @property
    def pending_link(self) -> bool:
        """Indicates if Maestral is linked to a Dropbox account (read only). This will
        block until the user's keyring is unlocked to load the saved auth token."""
        return not self.client.linked

    @property
    def pending_dropbox_folder(self) -> bool:
        """Indicates if a local Dropbox directory has been created (read only)."""
        return not osp.isdir(self.sync.dropbox_path)

    @property
    def pending_first_download(self) -> bool:
        """Indicates if the initial download has already occurred (read only)."""
        return self.sync.local_cursor == 0 or self.sync.remote_cursor == ""

    @property
    def syncing(self) -> bool:
        """Indicates if Maestral is syncing (read only). It will be ``True`` if syncing
        is not paused by the user *and* Maestral is connected to the internet."""
        return (
            self.monitor.syncing.is_set()
            or self.monitor.startup.is_set()
            or self.sync.busy()
        )

    @property
    def paused(self) -> bool:
        """Indicates if syncing is paused by the user (read only). This is set by calling
        :meth:`pause`."""
        return self.monitor.paused_by_user.is_set() and not self.sync.busy()

    @property
    def running(self) -> bool:
        """Indicates if sync threads are running (read only). They will be stopped before
        :meth:`start_sync` is called, when shutting down or because of an exception."""
        return self.monitor.running.is_set() or self.sync.busy()

    @property
    def connected(self) -> bool:
        """Indicates if Dropbox servers can be reached (read only)."""

        if self.pending_link:
            return False
        else:
            return self.monitor.connected.is_set()

    @property
    def status(self) -> str:
        """The last status message (read only). This can be displayed as information to
        the user but should not be relied on otherwise."""
        return self._log_handler_info_cache.getLastMessage()

    @property
    def sync_errors(self) -> List[ErrorType]:
        """
        A list of current sync errors as dicts (read only). This list is populated by the
        sync threads. The following keys will always be present but may contain emtpy
        values: "type", "inherits", "title", "traceback", "title", "message",
        "local_path", "dbx_path".

        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        return [error_to_dict(e) for e in self.sync.sync_errors]

    @property
    def fatal_errors(self) -> List[ErrorType]:
        """
        Returns a list of fatal errors as dicts (read only). This does not include lost
        internet connections or file sync errors which only emit warnings and are tracked
        and cleared separately. Errors listed here must be acted upon for Maestral to
        continue syncing.

        The following keys will always be present but may contain emtpy values: "type",
        "inherits", "title", "traceback", "title", and "message".

        This list is populated from all log messages with level ERROR or higher that have
        ``exc_info`` attached.
        """

        maestral_errors_dicts: List[ErrorType] = []

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
        The path of the current account's profile picture (read only). There may not be an
        actual file at that path if the user did not set a profile picture or the picture
        has not yet been downloaded.
        """
        return get_cache_path("maestral", self._config_name + "_profile_pic.jpeg")

    def get_file_status(self, local_path: str) -> str:
        """
        Returns the sync status of an individual file.

        :param local_path: Path to file on the local drive. May be relative to the
            current working directory.
        :returns: String indicating the sync status. Can be 'uploading', 'downloading',
            'up to date', 'error', or 'unwatched' (for files outside of the Dropbox
            directory). This will always be 'unwatched' if syncing is paused.
        """
        if not self.syncing:
            return FileStatus.Unwatched.value

        local_path = osp.realpath(local_path)

        try:
            dbx_path = self.sync.to_dbx_path(local_path)
        except ValueError:
            return FileStatus.Unwatched.value

        sync_event = next(
            iter(e for e in self.monitor.activity if e.local_path == local_path), None
        )

        if sync_event and sync_event.direction == SyncDirection.Up:
            return FileStatus.Uploading.value
        elif sync_event and sync_event.direction == SyncDirection.Down:
            return FileStatus.Downloading.value
        elif any(dbx_path == err["dbx_path"] for err in self.sync_errors):
            return FileStatus.Error.value
        elif self.sync.get_local_rev(dbx_path):
            return FileStatus.Synced.value
        else:
            return FileStatus.Unwatched.value

    def get_activity(self) -> List[StoneType]:
        """
        Returns the current upload / download activity.

        :returns: A lists of all sync events currently queued for or being uploaded or
            downloaded.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        self._check_linked()

        activity = [sync_event_to_dict(event) for event in self.monitor.activity]
        return activity

    def get_history(self) -> List[StoneType]:
        """
        Returns the historic upload / download activity.

        :returns: A lists of all sync events from the last week.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        self._check_linked()

        history = [sync_event_to_dict(event) for event in self.monitor.history]

        return history

    def get_account_info(self) -> StoneType:
        """
        Returns the account information from Dropbox and returns it as a dictionary.

        :returns: Dropbox account information.
        :raises: :class:`errors.DropboxAuthError` in case of an invalid access token.
        :raises: :class:`errors.DropboxServerError` for internal Dropbox errors.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        self._check_linked()

        res = self.client.get_account_info()
        return dropbox_stone_to_dict(res)

    def get_space_usage(self) -> StoneType:
        """
        Gets the space usage from Dropbox and returns it as a dictionary.

        :returns: Dropbox space usage information.
        :raises: :class:`errors.DropboxAuthError` in case of an invalid access token.
        :raises: :class:`errors.DropboxServerError` for internal Dropbox errors.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        self._check_linked()

        res = self.client.get_space_usage()
        return dropbox_stone_to_dict(res)

    # ==== control methods for front ends ================================================

    @to_maestral_error()  # to handle errors when downloading and saving profile pic
    def get_profile_pic(self) -> Optional[str]:
        """
        Attempts to download the user's profile picture from Dropbox. The picture is saved
        in Maestral's cache directory for retrieval when there is no internet connection.

        :returns: Path to saved profile picture or ``None`` if no profile picture was
            downloaded.
        :raises: :class:`errors.DropboxAuthError` in case of an invalid access token.
        :raises: :class:`errors.DropboxServerError` for internal Dropbox errors.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        self._check_linked()

        res = self.client.get_account_info()

        if res.profile_photo_url:
            res = requests.get(res.profile_photo_url)
            with open(self.account_profile_pic_path, "wb") as f:
                f.write(res.content)
            return self.account_profile_pic_path
        else:
            self._delete_old_profile_pics()
            return None

    def get_metadata(self, dbx_path: str) -> StoneType:
        """
        Returns metadata for a file or folder on Dropbox.

        :param dbx_path: Path to file or folder on Dropbox.
        :returns: Dropbox item metadata as dict. See :class:`dropbox.files.Metadata` for
            keys and values.
        :raises: :class:`errors.NotFoundError` if there is nothing at the given path.
        :raises: :class:`errors.DropboxAuthError` in case of an invalid access token.
        :raises: :class:`errors.DropboxServerError` for internal Dropbox errors.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        self._check_linked()

        res = self.client.get_metadata(dbx_path)
        return dropbox_stone_to_dict(res)

    def list_folder(self, dbx_path: str, **kwargs) -> List[StoneType]:
        """
        List all items inside the folder given by ``dbx_path``. Keyword arguments are
        passed on the the Dropbox API call :meth:`client.DropboxClient.list_folder`.

        :param dbx_path: Path to folder on Dropbox.
        :returns: List of Dropbox item metadata as dicts. See
            :class:`dropbox.files.Metadata` for keys and values.
        :raises: :class:`errors.NotFoundError` if there is nothing at the given path.
        :raises: :class:`errors.NotAFolderError` if the given path refers to a file.
        :raises: :class:`errors.DropboxAuthError` in case of an invalid access token.
        :raises: :class:`errors.DropboxServerError` for internal Dropbox errors.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        self._check_linked()

        res = self.client.list_folder(dbx_path, **kwargs)
        entries = [dropbox_stone_to_dict(e) for e in res.entries]

        return entries

    def list_revisions(self, dbx_path: str, limit: int = 10) -> List[StoneType]:
        """
        List revisions of old files at the given path ``dbx_path``. This will also return
        revisions if the file has already been deleted.

        :param dbx_path: Path to file on Dropbox.
        :param limit: Maximum number of revisions to list.
        :returns: List of Dropbox file metadata as dicts. See
            :class:`dropbox.files.Metadata` for keys and values.
        :raises: :class:`errors.NotFoundError` if there never was a file at the given path.
        :raises: :class:`errors.IsAFolderError` if the given path refers to a folder.
        :raises: :class:`errors.DropboxAuthError` in case of an invalid access token.
        :raises: :class:`errors.DropboxServerError` for internal Dropbox errors.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        """

        self._check_linked()

        res = self.client.list_revisions(dbx_path, limit=limit)
        entries = [dropbox_stone_to_dict(e) for e in res.entries]

        return entries

    def restore(self, dbx_path: str, rev: str) -> StoneType:
        """
        Restore an old revision of a file.

        :param dbx_path: The path to save the restored file.
        :param rev: The revision to restore. Old revisions can be listed with
            :meth:`list_revisions`.
        :returns: Metadata of the returned file. See
            :class:`dropbox.files.FileMetadata` for keys and values.
        :raises: :class:`errors.DropboxAuthError` in case of an invalid access token.
        :raises: :class:`errors.DropboxServerError` for internal Dropbox errors.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        """

        self._check_linked()

        res = self.client.restore(dbx_path, rev)
        return dropbox_stone_to_dict(res)

    def _delete_old_profile_pics(self):
        for file in os.listdir(get_cache_path("maestral")):
            if file.startswith(self._config_name + "_profile_pic"):
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

        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.NoDropboxDirError` if local Dropbox folder is not set up.
        """

        self._check_linked()
        self._check_dropbox_dir()

        self.monitor.rebuild_index()

    def start_sync(self) -> None:
        """
        Creates syncing threads and starts syncing.

        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.NoDropboxDirError` if local Dropbox folder is not set up.
        """

        self._check_linked()
        self._check_dropbox_dir()

        if not self.running:
            self.monitor.start()

    def resume_sync(self) -> None:
        """
        Resumes syncing if paused.

        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.NoDropboxDirError` if local Dropbox folder is not set up.
        """

        self._check_linked()
        self._check_dropbox_dir()

        self.monitor.resume()

    def pause_sync(self) -> None:
        """
        Pauses the syncing if running.
        """
        if not self.paused:
            self.monitor.pause()

    def stop_sync(self) -> None:
        """
        Stops all syncing threads if running. Call :meth:`start_sync` to restart syncing.
        """
        if self.running:
            self.monitor.stop()

    def reset_sync_state(self) -> None:
        """
        Resets the sync index and state. Only call this to clean up leftover state
        information if a Dropbox was improperly unlinked (e.g., auth token has been
        manually deleted). Otherwise leave state management to Maestral.

        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        self._check_linked()
        self.monitor.reset_sync_state()

    def exclude_item(self, dbx_path: str) -> None:
        """
        Excludes file or folder from sync and deletes it locally. It is safe to call this
        method with items which have already been excluded.

        :param dbx_path: Dropbox path of item to exclude.
        :raises: :class:`errors.NotFoundError` if there is nothing at the given path.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        :raises: :class:`errors.DropboxAuthError` in case of an invalid access token.
        :raises: :class:`errors.DropboxServerError` for internal Dropbox errors.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.NoDropboxDirError` if local Dropbox folder is not set up.
        """

        self._check_linked()
        self._check_dropbox_dir()

        # input validation
        md = self.client.get_metadata(dbx_path)

        if not md:
            raise NotFoundError(
                "Cannot exclude item", f'"{dbx_path}" does not exist on Dropbox'
            )

        dbx_path = dbx_path.lower().rstrip("/")

        # add the path to excluded list
        if self.sync.is_excluded_by_user(dbx_path):
            logger.info("%s was already excluded", dbx_path)
            logger.info(IDLE)
            return

        excluded_items = self.sync.excluded_items
        excluded_items.append(dbx_path)

        self.sync.excluded_items = excluded_items

        logger.info("Excluded %s", dbx_path)

        self._remove_after_excluded(dbx_path)

        logger.info(IDLE)

    def _remove_after_excluded(self, dbx_path: str) -> None:

        # book keeping
        self.sync.clear_sync_error(dbx_path=dbx_path)
        self.sync.remove_path_from_index(dbx_path)

        # remove folder from local drive
        local_path = self.sync.to_local_path_from_cased(dbx_path)
        # dbx_path will be lower-case, we there explicitly run `to_existing_cased_path`
        try:
            local_path = to_existing_cased_path(local_path)
        except FileNotFoundError:
            pass
        else:
            event_cls = DirDeletedEvent if osp.isdir(local_path) else FileDeletedEvent
            with self.monitor.fs_event_handler.ignore(event_cls(local_path)):
                delete(local_path)

    def include_item(self, dbx_path: str) -> None:
        """
        Includes a file or folder in sync and downloads it in the background. It is safe
        to call this method with items which have already been included, they will not be
        downloaded again.

        Any downloads will be carried out by the sync threads. Errors during the download
        can be accessed through :attr:`sync_errors` or :attr:`maestral_errors`.

        :param dbx_path: Dropbox path of item to include.
        :raises: :class:`errors.NotFoundError` if there is nothing at the given path.
        :raises: :class:`errors.PathError` if the path lies inside an excluded folder.
        :raises: :class:`errors.DropboxAuthError` in case of an invalid access token.
        :raises: :class:`errors.DropboxServerError` for internal Dropbox errors.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.NoDropboxDirError` if local Dropbox folder is not set up.
        """

        self._check_linked()
        self._check_dropbox_dir()

        # input validation
        md = self.client.get_metadata(dbx_path)

        if not md:
            raise NotFoundError(
                "Cannot include item", f'"{dbx_path}" does not exist on Dropbox'
            )

        dbx_path = dbx_path.lower().rstrip("/")

        old_excluded_items = self.sync.excluded_items

        for folder in old_excluded_items:
            if is_child(dbx_path, folder):
                raise PathError(
                    "Cannot include item",
                    f'"{dbx_path}" lies inside the excluded folder '
                    f'"{folder}". Please include "{folder}" first.',
                )

        # Get items which will need to be downloaded, do not attempt to download
        # children of `dbx_path` which were already included.
        # `new_included_items` will either be empty (`dbx_path` was already
        # included), just contain `dbx_path` itself (the item was fully excluded) or
        # only contain children of `dbx_path` (`dbx_path` was partially included).
        new_included_items = tuple(
            x for x in old_excluded_items if x == dbx_path or is_child(x, dbx_path)
        )

        if new_included_items:
            # remove `dbx_path` or all excluded children from the excluded list
            excluded_items = list(set(old_excluded_items) - set(new_included_items))
        else:
            logger.info("%s was already included", dbx_path)
            return

        self.sync.excluded_items = excluded_items

        logger.info("Included %s", dbx_path)

        # download items from Dropbox
        for folder in new_included_items:
            self.monitor.added_item_queue.put(folder)

    def set_excluded_items(self, items: List[str]) -> None:
        """
        Sets the list of excluded files or folders. Items which are not in ``items`` but
        were previously excluded will be downloaded.

        Any downloads will be carried out by the sync threads. Errors during the download
        can be accessed through :attr:`sync_errors` or :attr:`maestral_errors`.

        On initial sync, this does not trigger any downloads.

        :param items: List of excluded files or folders to set.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.NoDropboxDirError` if local Dropbox folder is not set up.
        """

        self._check_linked()
        self._check_dropbox_dir()

        excluded_items = self.sync.clean_excluded_items_list(items)
        old_excluded_items = self.sync.excluded_items

        added_excluded_items = set(excluded_items) - set(old_excluded_items)
        added_included_items = set(old_excluded_items) - set(excluded_items)

        self.sync.excluded_items = excluded_items

        if not self.pending_first_download:
            # apply changes
            for path in added_excluded_items:
                logger.info("Excluded %s", path)
                self._remove_after_excluded(path)
            for path in added_included_items:
                if not self.sync.is_excluded_by_user(path):
                    logger.info("Included %s", path)
                    self.monitor.added_item_queue.put(path)

        logger.info(IDLE)

    def excluded_status(self, dbx_path: str) -> str:
        """
        Returns 'excluded', 'partially excluded' or 'included'. This function will not
        check if the item actually exists on Dropbox.

        :param dbx_path: Path to item on Dropbox.
        :returns: Excluded status.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        self._check_linked()

        dbx_path = dbx_path.lower().rstrip("/")

        if dbx_path in self.sync.excluded_items:
            return "excluded"
        elif any(is_child(f, dbx_path) for f in self.sync.excluded_items):
            return "partially excluded"
        else:
            return "included"

    def move_dropbox_directory(self, new_path: str) -> None:
        """
        Sets the local Dropbox directory. This moves all local files to the new location
        and resumes syncing afterwards.

        :param new_path: Full path to local Dropbox folder. "~" will be expanded to
            the user's home directory.
        :raises: :class:`OSError` if moving the directory fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.NoDropboxDirError` if local Dropbox folder is not set up.
        """

        self._check_linked()
        self._check_dropbox_dir()

        # pause syncing
        was_syncing = self.running
        self.stop_sync()

        # input checks
        old_path = self.sync.dropbox_path
        new_path = osp.realpath(osp.expanduser(new_path))

        logger.info("Moving Dropbox folder...")

        try:
            if osp.samefile(old_path, new_path):
                logger.info(f'Dropbox folder moved to "{new_path}"')
                if was_syncing:
                    self.start_sync()
                return
        except FileNotFoundError:
            pass

        if osp.exists(new_path):
            raise FileExistsError(f'Path "{new_path}" already exists.')

        # move folder from old location or create a new one if no old folder exists
        if osp.isdir(old_path):
            shutil.move(old_path, new_path)
        else:
            os.makedirs(new_path)

        # update config file and client
        self.sync.dropbox_path = new_path

        logger.info(f'Dropbox folder moved to "{new_path}"')

        # resume syncing
        if was_syncing:
            self.start_sync()

    def create_dropbox_directory(self, path: str) -> None:
        """
        Creates a new Dropbox directory. Only call this during setup.

        :param path: Full path to local Dropbox folder. "~" will be expanded to the
            user's home directory.
        :raises: :class:`OSError` if creation fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        self._check_linked()

        # pause syncing
        resume = False
        if self.syncing:
            self.pause_sync()
            resume = True

        # housekeeping
        path = osp.realpath(osp.expanduser(path))
        self.monitor.reset_sync_state()

        # create new folder
        os.makedirs(path, exist_ok=True)

        # update config file and client
        self.sync.dropbox_path = path

        # resume syncing
        if resume:
            self.resume_sync()

    # ==== utility methods for front ends ================================================

    def to_local_path(self, dbx_path: str) -> str:
        """
        Converts a path relative to the Dropbox folder to a correctly cased local file
        system path.

        :param dbx_path: Path relative to Dropbox root.
        :returns: Corresponding path on local hard drive.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.NoDropboxDirError` if local Dropbox folder is not set up.
        """

        self._check_linked()
        self._check_dropbox_dir()

        return self.sync.to_local_path_from_cased(dbx_path)

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
            r = requests.get(GITHUB_RELEASES_API)
            data = r.json()

            releases = []
            release_notes = []

            # this should do nothing since the github API already returns sorted entries
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
        Stop the event loop. This will also shut down the pyro daemon if running.
        """

        self.stop_sync()

        self._refresh_task.cancel()
        self._thread_pool.shutdown(wait=False)

        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self.shutdown_complete.set_result, True)

    # ==== private methods ===============================================================

    def _check_linked(self) -> None:

        if not self.client.linked:
            raise NotLinkedError(
                "No Dropbox account linked", 'Please call "link" to link an account.'
            )

    def _check_dropbox_dir(self) -> None:

        if self.pending_dropbox_folder:
            raise NoDropboxDirError(
                "No local Dropbox directory",
                'Call "create_dropbox_directory" to set up.',
            )

    def _check_and_run_post_update_scripts(self) -> None:
        """
        Runs post-update scripts if necessary.
        """

        updated_from = self.get_state("app", "updated_scripts_completed")

        if Version(updated_from) < Version("1.2.0"):
            self._update_from_pre_v1_2_0()
        elif Version(updated_from) < Version("1.2.1"):
            self._update_from_pre_v1_2_1()

        self.set_state("app", "updated_scripts_completed", __version__)

    def _update_from_pre_v1_2_0(self) -> None:

        logger.info("Reindexing after update from pre v1.2.0")

        # remove old index to trigger resync
        old_rev_file = get_data_path("maestral", f"{self.config_name}.index")
        delete(old_rev_file)
        self.sync.remote_cursor = ""

    def _update_from_pre_v1_2_1(self) -> None:

        logger.info("Recreating autostart entries after update from pre v1.2.1")

        from maestral.utils.autostart import AutoStart

        autostart = AutoStart(self.config_name)

        if autostart.enabled:
            autostart.disable()
            autostart.enable()

        logger.info("Migrating index after update from pre v1.2.1")

        from alembic.migration import MigrationContext  # type: ignore
        from alembic.operations import Operations  # type: ignore
        from sqlalchemy.engine import reflection  # type: ignore
        from maestral.sync import db_naming_convention as nc
        from maestral.sync import IndexEntry

        table_name = IndexEntry.__tablename__

        with self.sync._database_access():
            insp = reflection.Inspector.from_engine(self.sync._db_engine)
            unique_constraints = insp.get_unique_constraints(table_name)

            with self.sync._db_engine.connect() as con:
                ctx = MigrationContext.configure(con)
                op = Operations(ctx)
                with op.batch_alter_table(table_name, naming_convention=nc) as batch_op:
                    for uq in unique_constraints:

                        name = uq["name"]
                        if name is None:
                            # generate name from naming convention
                            name = nc["uq"] % {
                                "table_name": table_name,
                                "column_0_name": uq["column_names"][0],
                            }

                        batch_op.drop_constraint(constraint_name=name, type_="unique")

    async def _periodic_refresh(self) -> None:

        while True:
            # update account info
            if self.client.auth.loaded:
                # only run if we have loaded the keyring, we don't
                # want to trigger any keyring access from here
                if self.client.linked:
                    await self._loop.run_in_executor(
                        self._thread_pool, self.get_account_info
                    )
                    await self._loop.run_in_executor(
                        self._thread_pool, self.get_profile_pic
                    )

            # check for maestral updates
            res = await self._loop.run_in_executor(
                self._thread_pool, self.check_for_updates
            )

            if not res["error"]:
                self._state.set("app", "latest_release", res["latest_release"])

            await asyncio.sleep(60 * 60)  # 60 min

    def __repr__(self) -> str:

        email = self._state.get("account", "email")
        account_type = self._state.get("account", "type")

        return (
            f"<{self.__class__.__name__}(config={self._config_name!r}, "
            f"account=({email!r}, {account_type!r}))>"
        )
