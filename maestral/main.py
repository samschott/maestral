# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

This module defines the main API which is exposed to the CLI or GUI.

"""

# system imports
import functools
import sys
import os
import os.path as osp
import platform
import shutil
import time
from threading import Thread
import logging.handlers
from collections import namedtuple, deque

# external imports
import requests
import keyring.errors
from watchdog.events import DirDeletedEvent, FileDeletedEvent
import bugsnag
from bugsnag.handlers import BugsnagHandler

try:
    from systemd import journal
except ImportError:
    journal = None

import sdnotify

# local imports
from maestral import __version__
from maestral.oauth import OAuth2Session
from maestral.client import DropboxClient, to_maestral_error
from maestral.sync import SyncMonitor
from maestral.errors import (
    MaestralApiError, NotLinkedError, NoDropboxDirError,
    NotFoundError, PathError
)
from maestral.config import MaestralConfig, MaestralState
from maestral.utils.path import is_child, to_cased_path, delete
from maestral.utils.notify import MaestralDesktopNotifier
from maestral.utils.serializer import error_to_dict, dropbox_stone_to_dict
from maestral.utils.appdirs import get_log_path, get_cache_path
from maestral.utils.updates import check_update_available
from maestral.constants import (
    INVOCATION_ID, NOTIFY_SOCKET, WATCHDOG_PID, WATCHDOG_USEC, IS_WATCHDOG,
    BUGSNAG_API_KEY, IDLE, FileStatus,
)

logger = logging.getLogger(__name__)
sd_notifier = sdnotify.SystemdNotifier()

# set up error reporting but do not activate

bugsnag.configure(
    api_key=BUGSNAG_API_KEY,
    app_version=__version__,
    auto_notify=False,
    auto_capture_sessions=False,
)


def bugsnag_global_callback(notification):
    notification.add_tab(
        'system', {
            'platform': platform.platform(),
            'python': platform.python_version()
        }
    )
    cause = notification.exception.__cause__
    if cause:
        notification.add_tab('original exception', error_to_dict(cause))


bugsnag.before_notify(bugsnag_global_callback)


# custom logging handlers

class CachedHandler(logging.Handler):
    """Handler which stores past records. This is used to populate Maestral's status and
    error interfaces.

    :param int level: Initial log level. Defaults to NOTSET.
    :param Optional[int] maxlen: Maximum number of records to store. If ``None``, all
        records will be stored. Defaults to ``None``.
    """

    def __init__(self, level=logging.NOTSET, maxlen=None):
        logging.Handler.__init__(self, level=level)
        self.cached_records = deque([], maxlen)

    def emit(self, record):
        """
        Logs the specified log record and saves it to the cache.

        :param record: Log record.
        """
        self.format(record)
        self.cached_records.append(record)

    def getLastMessage(self):
        """
        :returns: The log message of the last record or an empty string.
        :rtype: str
        """
        if len(self.cached_records) > 0:
            return self.cached_records[-1].message
        else:
            return ''

    def getAllMessages(self):
        """
        :returns: A list of all record messages.
        :rtype: list[str]
        """
        return [r.message for r in self.cached_records]

    def clear(self):
        """
        Clears all cached records.
        """
        self.cached_records.clear()


class SdNotificationHandler(logging.Handler):
    """Handler which emits messages as systemd notifications."""

    def emit(self, record):
        """
        Sends the record massage to systemd as service status.

        :param record: Log record.
        """
        sd_notifier.notify(f'STATUS={record.message}')


# decorators

def with_sync_paused(func):
    """Decorator which pauses syncing before a method call, resumes afterwards. This
    should only be used to decorate Maestral methods."""

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        # pause syncing
        resume = False
        if self.syncing:
            self.pause_sync()
            resume = True
        ret = func(self, *args, **kwargs)
        # resume syncing if previously paused
        if resume:
            self.resume_sync()
        return ret
    return wrapper


def require_linked(func):
    """Decorator which raises a RuntimeError if Maestral is not linked to a Dropbox
    account."""

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.pending_link:
            raise NotLinkedError('No Dropbox account linked',
                                 'Please run "link" or "start" to link an account.')
        return func(self, *args, **kwargs)

    return wrapper


def require_dir(func):
    """Decorator which raises a RuntimeError if there is no local Dropbox folder."""

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.pending_dropbox_folder:
            raise NoDropboxDirError('No local Dropbox directory',
                                    'Run "create_dropbox_directory" to set up.')
        return func(self, *args, **kwargs)

    return wrapper


# ========================================================================================
# Main API
# ========================================================================================

class Maestral:
    """The public API.

    All methods and properties return objects or raise exceptions which can safely be
    serialized, i.e., pure Python types. The only exception are instances of
    :class:`errors.MaestralApiError` which need to be registered explicitly with the
    serpent serializer which is used for communication to frontends.

    Sync errors and fatal errors which occur in the syncing threads can be read with the
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

    :param str config_name: Name of maestral configuration to run. This will create a new
        configuration file if none exists.
    :param bool log_to_stdout: If ``True``, Maestral will print log messages to stdout.
        When started as a systemd services, this can result in duplicate log messages.
        Defaults to ``False``.
    """

    def __init__(self, config_name='maestral', log_to_stdout=False):

        self._daemon_running = True
        self._log_to_stdout = log_to_stdout
        self._config_name = config_name

        self._conf = MaestralConfig(config_name)
        self._state = MaestralState(config_name)

        self._setup_logging()

        self._auth = OAuth2Session(config_name)
        self.client = DropboxClient(config_name=self.config_name, access_token='none')
        self.monitor = SyncMonitor(self.client)
        self.sync = self.monitor.sync

        # periodically check for updates and refresh account info
        self.update_thread = Thread(
            name='maestral-update-check',
            target=self._periodic_refresh,
            daemon=True,
        )
        self.update_thread.start()

        # notify systemd that we have started
        if NOTIFY_SOCKET:
            logger.debug('Running as systemd notify service')
            logger.debug('NOTIFY_SOCKET = %s', NOTIFY_SOCKET)
            sd_notifier.notify('READY=1')

        # notify systemd periodically if alive
        if IS_WATCHDOG:
            logger.debug('Running as systemd watchdog service')
            logger.debug('WATCHDOG_USEC = %s', WATCHDOG_USEC)
            logger.debug('WATCHDOG_PID = %s', WATCHDOG_PID)

            self.watchdog_thread = Thread(
                name='maestral-watchdog',
                target=self._periodic_watchdog,
                daemon=True,
            )
            self.watchdog_thread.start()

    def get_auth_url(self):
        """
        Returns a URL to authorize access to a Dropbox account. To link a Dropbox
        account, retrieve an auth token from the URL and link Maestral by calling
        :meth:`link` with the provided token.

        :returns: URL to retrieve an OAuth token.
        :rtype: str
        """
        return self._auth.get_auth_url()

    def link(self, token):
        """
        Links Maestral with a Dropbox account using the given access token. The token will
        be stored for future usage as documented in the :mod:`oauth` module. Supported
        keyring backends are, in order of preference:

            * MacOS Keychain
            * Any keyring implementing the SecretService Dbus specification
            * KWallet
            * Gnome Keyring
            * Plain text storage

        :param str token: OAuth token for Dropbox access.
        :returns: OAuth2Session.Success (0), OAuth2Session.InvalidToken (1) or
            OAuth2Session.ConnectionFailed (2)
        :rtype: int
        """

        res = self._auth.verify_auth_token(token)

        if res == self._auth.Success:
            self._auth.save_creds()

            self.client.set_access_token(self._auth.access_token)

            try:
                self.get_account_info()
                self.get_space_usage()
            except ConnectionError:
                pass

        return res

    @require_linked
    def unlink(self):
        """
        Unlinks the configured Dropbox account but leaves all downloaded files in place.
        All syncing metadata will be removed as well. Connection and API errors will be
        handled silently but the Dropbox access key will always be removed from the
        user's PC.

        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.KeyringAccessError` if deleting the auth key fails because
            the user's keyring is locked.
        """

        self.stop_sync()

        # revoke token
        try:
            self.client.unlink()
        except (ConnectionError, MaestralApiError):
            pass

        # clean up config + state
        self.sync.clear_rev_index()
        delete(self.sync.rev_file_path)
        self._conf.cleanup()
        self._state.cleanup()

        # delete auth token
        try:
            self._auth.delete_creds()
        except keyring.errors.PasswordDeleteError:
            logger.warning('Could not delete auth token', exc_info=True)

        logger.info('Unlinked Dropbox account.')

    def _setup_logging(self):
        """
        Sets up logging to log files, status and error properties, desktop notifications,
        the systemd journal if available, bugsnag if error reports are enabled, and to
        stdout if requested.
        """

        log_level = self._conf.get('app', 'log_level')
        mdbx_logger = logging.getLogger('maestral')
        mdbx_logger.setLevel(logging.DEBUG)

        log_fmt_long = logging.Formatter(
            fmt='%(asctime)s %(name)s %(levelname)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        log_fmt_short = logging.Formatter(fmt='%(message)s')

        # log to file
        rfh_log_file = get_log_path('maestral', self._config_name + '.log')
        self.log_handler_file = logging.handlers.RotatingFileHandler(
            rfh_log_file, maxBytes=10 ** 7, backupCount=1
        )
        self.log_handler_file.setFormatter(log_fmt_long)
        self.log_handler_file.setLevel(log_level)
        mdbx_logger.addHandler(self.log_handler_file)

        # log to journal when launched from systemd
        if journal and INVOCATION_ID:
            # noinspection PyUnresolvedReferences
            self.log_handler_journal = journal.JournalHandler(
                SYSLOG_IDENTIFIER='maestral'
            )
            self.log_handler_journal.setFormatter(log_fmt_short)
            self.log_handler_journal.setLevel(log_level)
            mdbx_logger.addHandler(self.log_handler_journal)
        else:
            self.log_handler_journal = None

        # send systemd notifications when started as 'notify' daemon
        if NOTIFY_SOCKET:
            self.log_handler_sd = SdNotificationHandler()
            self.log_handler_sd.setFormatter(log_fmt_short)
            self.log_handler_sd.setLevel(logging.INFO)
            mdbx_logger.addHandler(self.log_handler_sd)
        else:
            self.log_handler_sd = None

        # log to stdout (disabled by default)
        level = log_level if self._log_to_stdout else 100
        self.log_handler_stream = logging.StreamHandler(sys.stdout)
        self.log_handler_stream.setFormatter(log_fmt_long)
        self.log_handler_stream.setLevel(level)
        mdbx_logger.addHandler(self.log_handler_stream)

        # log to cached handlers for GUI and CLI
        self._log_handler_info_cache = CachedHandler(maxlen=1)
        self._log_handler_info_cache.setFormatter(log_fmt_short)
        self._log_handler_info_cache.setLevel(logging.INFO)
        mdbx_logger.addHandler(self._log_handler_info_cache)

        self._log_handler_error_cache = CachedHandler()
        self._log_handler_error_cache.setFormatter(log_fmt_short)
        self._log_handler_error_cache.setLevel(logging.ERROR)
        mdbx_logger.addHandler(self._log_handler_error_cache)

        # log to desktop notifications
        # 'file changed' events will be collated and sent as desktop
        # notifications by the monitor directly, we don't handle them here
        self.desktop_notifier = MaestralDesktopNotifier.for_config(self.config_name)
        self.desktop_notifier.setLevel(logging.WARNING)
        mdbx_logger.addHandler(self.desktop_notifier)

        # log to bugsnag (disabled by default)
        self._log_handler_bugsnag = BugsnagHandler()
        self._log_handler_bugsnag.setLevel(100)
        mdbx_logger.addHandler(self._log_handler_bugsnag)

        self.analytics = self._conf.get('app', 'analytics')

    # ==== methods to access config and saved state ======================================

    @property
    def config_name(self):
        """The selected configuration."""
        return self._config_name

    def set_conf(self, section, name, value):
        """
        Sets a configuration option.

        :param str section: Name of section in config file.
        :param str name: Name of config option.
        :param value: Config option value. May be any native Python type.
        """
        self._conf.set(section, name, value)

    def get_conf(self, section, name):
        """
        Gets a configuration option.

        :param str section: Name of section in config file.
        :param str name: Name of config option.
        :returns: Config value.
        """
        return self._conf.get(section, name)

    def set_state(self, section, name, value):
        """
        Sets a state value.

        :param str section: Name of section in state file.
        :param str name: Name of state variable.
        :param value: State value. May be any native Python type.
        """
        self._state.set(section, name, value)

    def get_state(self, section, name):
        """
        Gets a state value.

        :param str section: Name of section in state file.
        :param str name: Name of state variable.
        :returns: State value.
        """
        return self._state.get(section, name)

    # ==== getters / setters for config with side effects ================================

    @property
    @require_linked
    def dropbox_path(self):
        """
        Returns the path to the local Dropbox folder (read only). This will be an empty
        string if not Dropbox folder has been set up yet. Use
        :meth:`create_dropbox_directory` or :meth:`move_dropbox_directory` to set or
        change the Dropbox directory location instead.

        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """
        return self.sync.dropbox_path

    @property
    @require_linked
    def excluded_items(self):
        """
        Returns a list of excluded folders (read only). Use :meth:`exclude_item`,
        :meth:`include_item` or :meth:`set_excluded_items` to change which items are
        excluded from syncing.

        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """
        return self.sync.excluded_items

    @property
    def log_level(self):
        """Log level for log files, stdout and the systemd journal."""
        return self._conf.get('app', 'log_level')

    @log_level.setter
    def log_level(self, level_num):
        """Setter: log_level."""
        self.log_handler_file.setLevel(level_num)
        if self.log_handler_journal:
            self.log_handler_journal.setLevel(level_num)
        if self.log_to_stdout:
            self.log_handler_stream.setLevel(level_num)
        self._conf.set('app', 'log_level', level_num)

    @property
    def log_to_stdout(self):
        """Enables or disables logging to stdout (bool)."""
        return self._log_to_stdout

    @log_to_stdout.setter
    def log_to_stdout(self, enabled):
        """Setter: log_to_stdout."""
        self._log_to_stdout = enabled
        level = self.log_level if enabled else 100
        self.log_handler_stream.setLevel(level)

    @property
    def analytics(self):
        """Enables or disables logging of errors to bugsnag."""
        return self._conf.get('app', 'analytics')

    @analytics.setter
    def analytics(self, enabled):
        """Setter: analytics."""

        bugsnag.configuration.auto_notify = enabled
        bugsnag.configuration.auto_capture_sessions = enabled
        self._log_handler_bugsnag.setLevel(logging.ERROR if enabled else 100)

        self._conf.set('app', 'analytics', enabled)

    @property
    def notification_snooze(self):
        """Snooze time for desktop notifications in minutes (float). Defaults to 0.0 if
        notifications are not snoozed."""
        return self.desktop_notifier.snoozed

    @notification_snooze.setter
    def notification_snooze(self, minutes):
        """Setter: notification_snooze."""
        self.desktop_notifier.snoozed = minutes

    @property
    def notification_level(self):
        """Level for desktop notifications (int). See :mod:`utils.notify` for level
        definitions."""
        return self.desktop_notifier.notify_level

    @notification_level.setter
    def notification_level(self, level):
        """Setter: notification_level."""
        self.desktop_notifier.notify_level = level

    # ==== state information  ============================================================

    @property
    def pending_link(self):
        """Bool indicating if Maestral is linked to a Dropbox account (read only). This
        will block until the user's keyring is unlocked to load the saved auth token."""

        token = self._auth.access_token  # triggers keyring access
        if token != '':
            self.client.set_access_token(token)

        return token == ''

    @property
    def pending_dropbox_folder(self):
        """Bool indicating if a local Dropbox directory has been created (read only)."""
        return not osp.isdir(self._conf.get('main', 'path'))

    @property
    def pending_first_download(self):
        """Bool indicating if the initial download has already occurred (read only)."""
        return (self._state.get('sync', 'lastsync') == 0
                or self._state.get('sync', 'cursor') == '')

    @property
    def syncing(self):
        """
        Bool indicating if Maestral is syncing (read only). It will be ``True`` if syncing
        is not paused by the user *and* Maestral is connected to the internet.
        """

        if self.pending_link:
            return False
        else:
            return (self.monitor.syncing.is_set()
                    or self.monitor.startup.is_set()
                    or self.sync.busy())

    @property
    def paused(self):
        """Bool indicating if syncing is paused by the user (read only). This is set by
        calling :meth:`pause`."""

        if self.pending_link:
            return False
        else:
            return self.monitor.paused_by_user.is_set() and not self.sync.busy()

    @property
    def running(self):
        """
        Bool indicating if sync threads are running (read only). They will be stopped
        before :meth:`start_sync` is called, when shutting down or because of an exception.
        """

        if self.pending_link:
            return False
        else:
            return self.monitor.running.is_set() or self.sync.busy()

    @property
    def connected(self):
        """Bool indicating if Dropbox servers can be reached (read only)."""

        if self.pending_link:
            return False
        else:
            return self.monitor.connected.is_set()

    @property
    def status(self):
        """
        Returns a string with the last status message (read only). This can be displayed
        as information to the user but should not be relied on otherwise.
        """
        return self._log_handler_info_cache.getLastMessage()

    @property
    @require_linked
    def sync_errors(self):
        """
        Returns list of current sync errors as dicts (read only). This list is populated
        by the sync threads.

        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        sync_errors = list(self.sync.sync_errors.queue)
        sync_errors_dicts = [error_to_dict(e) for e in sync_errors]
        return sync_errors_dicts

    @property
    def fatal_errors(self):
        """
        Returns a list of fatal errors as dicts (read only). This does not include lost
        internet connections or file sync errors which only emit warnings and are tracked
        and cleared separately. Errors listed here must be acted upon for Maestral to
        continue syncing. This list is populated by the sync threads.
        """

        maestral_errors = [
            r.exc_info[1] for r in self._log_handler_error_cache.cached_records
            if r.exc_info is not None
        ]
        maestral_errors_dicts = [error_to_dict(e) for e in maestral_errors]
        return maestral_errors_dicts

    def clear_fatal_errors(self):
        """
        Manually clears all fatal errors. This should be used after they have been
        resolved by the user through the GUI or CLI.
        """
        self._log_handler_error_cache.clear()

    @property
    def account_profile_pic_path(self):
        """
        Returns the path of the current account's profile picture (read only). There may
        not be an actual file at that path if the user did not set a profile picture or
        the picture has not yet been downloaded.
        """
        return get_cache_path('maestral', self._config_name + '_profile_pic.jpeg')

    def get_file_status(self, local_path):
        """
        Returns the sync status of an individual file.

        :param str local_path: Path to file on the local drive. May be relative to the
            current working directory.
        :returns: String indicating the sync status. Can be 'uploading', 'downloading',
            'up to date', 'error', or 'unwatched' (for files outside of the Dropbox
            directory). This will always be 'unwatched' if syncing is paused.
        :rtype: str
        """
        if not self.syncing:
            return FileStatus.Unwatched.value

        local_path = osp.realpath(local_path)

        try:
            dbx_path = self.sync.to_dbx_path(local_path)
        except ValueError:
            return FileStatus.Unwatched.value

        if local_path in self.monitor.queued_for_upload:
            return FileStatus.Uploading.value
        elif local_path in self.monitor.queued_for_download:
            return FileStatus.Downloading.value
        elif any(local_path == err['local_path'] for err in self.sync_errors):
            return FileStatus.Error.value
        elif self.sync.get_local_rev(dbx_path):
            return FileStatus.Synced.value
        else:
            return FileStatus.Unwatched.value

    @require_linked
    def get_activity(self):
        """
        Gets current upload / download activity.

        :returns: A dictionary with lists of all files currently queued for or being
            uploaded or downloaded. Paths are given relative to the Dropbox folder.
        :rtype: dict(list, list)
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        PathItem = namedtuple('PathItem', 'path status')
        uploading = []
        downloading = []

        for path in self.monitor.uploading:
            path.lstrip(self.dropbox_path)
            uploading.append(PathItem(path, 'uploading'))

        for path in self.monitor.queued_for_upload:
            path.lstrip(self.dropbox_path)
            uploading.append(PathItem(path, 'queued'))

        for path in self.monitor.downloading:
            path.lstrip(self.dropbox_path)
            downloading.append(PathItem(path, 'downloading'))

        for path in self.monitor.queued_for_download:
            path.lstrip(self.dropbox_path)
            downloading.append(PathItem(path, 'queued'))

        return dict(uploading=uploading, downloading=downloading)

    @require_linked
    def get_account_info(self):
        """
        Gets account information from Dropbox and returns it as a dictionary. The entries
        will either be of type ``str`` or ``bool``.

        :returns: Dropbox account information.
        :rtype: dict[str, bool]
        :raises: :class:`errors.DropboxAuthError` in case of invalid access token.
        :raises: :class:`errors.DropboxServerError` for internal Dropbox errors.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        res = self.client.get_account_info()
        return dropbox_stone_to_dict(res)

    @require_linked
    def get_space_usage(self):
        """
        Gets the space usage stored by Dropbox and returns it as a dictionary. The
        entries will either be of type ``str`` or ``bool``.

        :returns: Dropbox account information.
        :rtype: dict[str, bool]
        :raises: :class:`errors.DropboxAuthError` in case of invalid access token.
        :raises: :class:`errors.DropboxServerError` for internal Dropbox errors.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        res = self.client.get_space_usage()
        return dropbox_stone_to_dict(res)

    # ==== control methods for front ends ================================================

    @require_linked
    @to_maestral_error()  # to handle errors when downloading and saving profile pic
    def get_profile_pic(self):
        """
        Attempts to download the user's profile picture from Dropbox. The picture is saved
        in Maestral's cache directory for retrieval when there is no internet connection.

        :returns: Path to saved profile picture or ``None`` if no profile picture is set.
        :rtype: str
        :raises: :class:`errors.DropboxAuthError` in case of invalid access token.
        :raises: :class:`errors.DropboxServerError` for internal Dropbox errors.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        res = self.client.get_account_info()

        if res.profile_photo_url:
            res = requests.get(res.profile_photo_url)
            with open(self.account_profile_pic_path, 'wb') as f:
                f.write(res.content)
            return self.account_profile_pic_path
        else:
            self._delete_old_profile_pics()

    @require_linked
    def list_folder(self, dbx_path, **kwargs):
        """
        List all items inside the folder given by ``dbx_path``. Keyword arguments are
        passed on the the Dropbox API call :meth:`client.DropboxClient.list_folder`.

        :param dbx_path: Path to folder on Dropbox.
        :returns: List of Dropbox item metadata as dicts or ``False`` if listing failed
            due to connection issues.
        :rtype: list[dict]
        :raises: :class:`errors.NotFoundError` if there is nothing at the given path.
        :raises: :class:`errors.NotAFolderError` if the given path refers to a file.
        :raises: :class:`errors.DropboxAuthError` in case of invalid access token.
        :raises: :class:`errors.DropboxServerError` for internal Dropbox errors.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        res = self.client.list_folder(dbx_path, **kwargs)

        entries = [dropbox_stone_to_dict(e) for e in res.entries]

        return entries

    def _delete_old_profile_pics(self):
        for file in os.listdir(get_cache_path('maestral')):
            if file.startswith(self._config_name + '_profile_pic'):
                try:
                    os.unlink(osp.join(get_cache_path('maestral'), file))
                except OSError:
                    pass

    @require_linked
    @require_dir
    def rebuild_index(self):
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

        self.monitor.rebuild_index()

    @require_linked
    @require_dir
    def start_sync(self):
        """
        Creates syncing threads and starts syncing.

        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.NoDropboxDirError` if local Dropbox folder is not set up.
        """

        if not self.running:
            self.monitor.start()

    @require_linked
    @require_dir
    def resume_sync(self):
        """
        Resumes syncing if paused.

        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.NoDropboxDirError` if local Dropbox folder is not set up.
        """

        self.monitor.resume()

    def pause_sync(self):
        """
        Pauses the syncing if running.
        """
        if not self.paused:
            self.monitor.pause()

    def stop_sync(self):
        """
        Stops all syncing threads if running. Call :meth:`start_sync` to restart syncing.
        """
        if self.running:
            self.monitor.stop()

    @require_linked
    def reset_sync_state(self):
        """
        Resets the sync index and state. Only call this to clean up leftover state
        information if a Dropbox was improperly unlinked (e.g., auth token has been
        manually deleted). Otherwise leave state management to Maestral.

        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        self.monitor.reset_sync_state()

    @require_linked
    @require_dir
    def exclude_item(self, dbx_path):
        """
        Excludes file or folder from sync and deletes it locally. It is safe to call this
        method with items which have already been excluded.

        :param str dbx_path: Dropbox path of item to exclude.
        :raises: :class:`errors.NotFoundError` if there is nothing at the given path.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        :raises: :class:`errors.DropboxAuthError` in case of invalid access token.
        :raises: :class:`errors.DropboxServerError` for internal Dropbox errors.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.NoDropboxDirError` if local Dropbox folder is not set up.
        """

        # input validation
        md = self.client.get_metadata(dbx_path)

        if not md:
            raise NotFoundError('Cannot exlcude item',
                                f'"{dbx_path}" does not exist on Dropbox')

        dbx_path = dbx_path.lower().rstrip('/')

        # add the path to excluded list
        if self.sync.is_excluded_by_user(dbx_path):
            logger.info('%s was already excluded', dbx_path)
            logger.info(IDLE)
            return

        excluded_items = self.sync.excluded_items
        excluded_items.append(dbx_path)

        self.sync.excluded_items = excluded_items

        logger.info('Excluded %s', dbx_path)

        self._remove_after_excluded(dbx_path)

        logger.info(IDLE)

    def _remove_after_excluded(self, dbx_path):

        # book keeping
        self.sync.clear_sync_error(dbx_path=dbx_path)
        self.sync.set_local_rev(dbx_path, None)

        # remove folder from local drive
        local_path = self.sync.to_local_path(dbx_path)
        # dbx_path will be lower-case, we there explicitly run `to_cased_path`
        local_path = to_cased_path(local_path)
        if local_path:
            event_cls = DirDeletedEvent if osp.isdir(local_path) else FileDeletedEvent
            with self.monitor.fs_event_handler.ignore(event_cls(local_path)):
                delete(local_path)

    @require_linked
    @require_dir
    def include_item(self, dbx_path):
        """
        Includes a file or folder in sync and downloads it in the background. It is safe
        to call this method with items which have already been included, they will not be
        downloaded again.

        Any downloads will be carried out by the sync threads. Errors during the download
        can be accessed through :attr:`sync_errors` or :attr:`maestral_errors`.

        :param str dbx_path: Dropbox path of item to include.
        :raises: :class:`errors.NotFoundError` if there is nothing at the given path.
        :raises: :class:`errors.PathError` if the path lies inside an excluded folder.
        :raises: :class:`errors.DropboxAuthError` in case of invalid access token.
        :raises: :class:`errors.DropboxServerError` for internal Dropbox errors.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.NoDropboxDirError` if local Dropbox folder is not set up.
        """

        # input validation
        md = self.client.get_metadata(dbx_path)

        if not md:
            raise NotFoundError('Cannot include item',
                                f'"{dbx_path}" does not exist on Dropbox')

        dbx_path = dbx_path.lower().rstrip('/')

        old_excluded_items = self.sync.excluded_items

        for folder in old_excluded_items:
            if is_child(dbx_path, folder):
                raise PathError('Cannot include item',
                                f'"{dbx_path}" lies inside the excluded folder '
                                f'"{folder}". Please include "{folder}" first.')

        # Get items which will need to be downloaded, do not attempt to download
        # children of `dbx_path` which were already included.
        # `new_included_items` will either be empty (`dbx_path` was already
        # included), just contain `dbx_path` itself (the item was fully excluded) or
        # only contain children of `dbx_path` (`dbx_path` was partially included).
        new_included_items = tuple(x for x in old_excluded_items if
                                   x == dbx_path or is_child(x, dbx_path))

        if new_included_items:
            # remove `dbx_path` or all excluded children from the excluded list
            excluded_items = list(set(old_excluded_items) - set(new_included_items))
        else:
            logger.info('%s was already included', dbx_path)
            return

        self.sync.excluded_items = excluded_items

        logger.info('Included %s', dbx_path)

        # download items from Dropbox
        for folder in new_included_items:
            self.sync.queued_newly_included_downloads.put(folder)

    @require_linked
    @require_dir
    def set_excluded_items(self, items):
        """
        Sets the list of excluded files or folders. Items which are not in ``items`` but
        were previously excluded will be downloaded.

        Any downloads will be carried out by the sync threads. Errors during the download
        can be accessed through :attr:`sync_errors` or :attr:`maestral_errors`.

        On initial sync, this does not trigger any downloads.

        :param list[str] items: List of excluded files or folders to set.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.NoDropboxDirError` if local Dropbox folder is not set up.
        """

        excluded_items = self.sync.clean_excluded_items_list(items)
        old_excluded_items = self.sync.excluded_items

        added_excluded_items = set(excluded_items) - set(old_excluded_items)
        added_included_items = set(old_excluded_items) - set(excluded_items)

        self.sync.excluded_items = excluded_items

        if not self.pending_first_download:
            # apply changes
            for path in added_excluded_items:
                logger.info('Excluded %s', path)
                self._remove_after_excluded(path)
            for path in added_included_items:
                if not self.sync.is_excluded_by_user(path):
                    logger.info('Included %s', path)
                    self.sync.queued_newly_included_downloads.put(path)

        logger.info(IDLE)

    @require_linked
    def excluded_status(self, dbx_path):
        """
        Returns 'excluded', 'partially excluded' or 'included'. This function will not
        check if the item actually exists on Dropbox.

        :param str dbx_path: Path to item on Dropbox.
        :returns: Excluded status.
        :rtype: str
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        dbx_path = dbx_path.lower().rstrip('/')

        excluded_items = self._conf.get('main', 'excluded_items')

        if dbx_path in excluded_items:
            return 'excluded'
        elif any(is_child(f, dbx_path) for f in excluded_items):
            return 'partially excluded'
        else:
            return 'included'

    @require_linked
    @require_dir
    @with_sync_paused
    def move_dropbox_directory(self, new_path):
        """
        Sets the local Dropbox directory. This moves all local files to the new location
        and resumes syncing afterwards.

        :param str new_path: Full path to local Dropbox folder. "~" will be expanded to
            the user's home directory.
        :raises: :class:`OSError` if moving the directory fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.NoDropboxDirError` if local Dropbox folder is not set up.
        """

        old_path = self.sync.dropbox_path
        new_path = osp.realpath(osp.expanduser(new_path))

        try:
            if osp.samefile(old_path, new_path):
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

    @require_linked
    @with_sync_paused
    def create_dropbox_directory(self, path):
        """
        Creates a new Dropbox directory. Only call this during setup.

        :param str path: Full path to local Dropbox folder. "~" will be expanded to the
            user's home directory.
        :raises: :class:`OSError` if creation fails.
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        """

        path = osp.realpath(osp.expanduser(path))

        self.monitor.reset_sync_state()

        # create new folder
        os.makedirs(path, exist_ok=True)

        # update config file and client
        self.sync.dropbox_path = path

    # ==== utility methods for front ends ================================================

    @require_linked
    @require_dir
    def to_local_path(self, dbx_path):
        """
        Converts a path relative to the Dropbox folder to a correctly cased local file
        system path.

        :param str dbx_path: Path relative to Dropbox root.
        :returns: Corresponding path on local hard drive.
        :rtype: str
        :raises: :class:`errors.NotLinkedError` if no Dropbox account is linked.
        :raises: :class:`errors.NoDropboxDirError` if local Dropbox folder is not set up.
        """

        return self.sync.to_local_path(dbx_path)

    @staticmethod
    def check_for_updates():
        """
        Checks if an update is available.

        :returns: A dictionary with information about the latest release with the fields
            'update_available' (bool), 'latest_release' (str), 'release_notes' (str)
            and 'error' (str or None).
        :rtype: dict
        """
        return check_update_available()

    def shutdown_pyro_daemon(self):
        """
        Sets the ``_daemon_running`` flag to ``False``. This will be checked by Pyro5
        periodically to shut down the daemon when requested.
        """
        self._daemon_running = False
        if NOTIFY_SOCKET:
            # notify systemd that we are shutting down
            sd_notifier.notify('STOPPING=1')

    # ==== private methods ===============================================================

    def _loop_condition(self):
        return self._daemon_running

    def _periodic_refresh(self):
        while True:
            if not self.pending_link:
                self.get_account_info()
                self.get_profile_pic()
            # check for maestral updates
            res = self.check_for_updates()
            if not res['error']:
                self._state.set('app', 'latest_release', res['latest_release'])
            time.sleep(60 * 60)  # 60 min

    @staticmethod
    def _periodic_watchdog():
        while True:
            sd_notifier.notify('WATCHDOG=1')
            time.sleep(int(WATCHDOG_USEC) / (2 * 10 ** 6))

    def __del__(self):
        try:
            self.monitor.stop()
        except Exception:
            pass

    def __repr__(self):
        email = self._state.get('account', 'email')
        account_type = self._state.get('account', 'type')

        return (f'<{self.__class__.__name__}(config={self._config_name!r}, '
                f'account=({email!r}, {account_type!r}))>')
