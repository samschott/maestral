# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

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

# external packages
import click
import requests
from dropbox import files
from watchdog.events import EVENT_TYPE_DELETED
import bugsnag
from bugsnag.handlers import BugsnagHandler

try:
    from systemd import journal
except ImportError:
    journal = None

import sdnotify

# maestral modules
from maestral import __version__
from maestral.client import MaestralApiClient
from maestral.sync import MaestralMonitor
from maestral.errors import MaestralApiError, DropboxAuthError
from maestral.config import MaestralConfig, MaestralState
from maestral.utils.path import is_child, to_cased_path, delete
from maestral.utils.notify import MaestralDesktopNotifier
from maestral.utils.serializer import error_to_dict, dropbox_stone_to_dict
from maestral.utils.appdirs import get_log_path, get_cache_path, get_home_dir
from maestral.utils.updates import check_update_available
from maestral.utils.housekeeping import run_housekeeping
from maestral.constants import (
    INVOCATION_ID, NOTIFY_SOCKET, WATCHDOG_PID, WATCHDOG_USEC, IS_WATCHDOG,
    BUGSNAG_API_KEY, IDLE, DISCONNECTED, FileStatus,
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

run_housekeeping()


# custom logging handlers

class CachedHandler(logging.Handler):
    """Handler which stores past records.

    :param int maxlen: Maximum number of records to store.
    """

    def __init__(self, maxlen=None):
        logging.Handler.__init__(self)
        self.cached_records = deque([], maxlen)

    def emit(self, record):
        self.format(record)
        self.cached_records.append(record)

    def getLastMessage(self):
        if len(self.cached_records) > 0:
            return self.cached_records[-1].message
        else:
            return ''

    def getAllMessages(self):
        return [r.message for r in self.cached_records]

    def clear(self):
        self.cached_records.clear()


class SdNotificationHandler(logging.Handler):
    """Handler which emits messages as systemd notifications."""

    def emit(self, record):
        sd_notifier.notify(f'STATUS={record.message}')


# decorators

def handle_disconnect(func):
    """
    Decorator which handles connection and auth errors during a function call and returns
    ``False`` if an error occurred.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # pause syncing
        try:
            res = func(*args, **kwargs)
            return res
        except ConnectionError:
            logger.info(DISCONNECTED)
            return False
        except DropboxAuthError as e:
            logger.exception(e.title)
            return False

    return wrapper


def with_sync_paused(func):
    """
    Decorator which pauses syncing before a method call, resumes afterwards. This should
    only be used to decorate Maestral methods.
    """
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


# ========================================================================================
# Main API
# ========================================================================================

class Maestral(object):
    """An open source Dropbox client for macOS and Linux.

    All methods and properties return objects or raise exceptions which can safely be
    serialized, i.e., pure Python types. The only exception are MaestralApiErrors which
    need to be registered explicitly with the serpent serializer used by Pyro5 in order
    to be transmitted to a frontend.

    :param str config_name: Name of maestral configuration to run. This will create a new
        configuration file if none exists.
    :param bool run: If ``True``, Maestral will start syncing immediately. Defaults to
        ``True``.
    """

    def __init__(self, config_name='maestral', run=True, log_to_stdout=False):

        self._daemon_running = True
        self._log_to_stdout = log_to_stdout

        self._config_name = config_name
        self._conf = MaestralConfig(self._config_name)
        self._state = MaestralState(self._config_name)

        self._setup_logging()

        self.client = MaestralApiClient(self._config_name)
        self.monitor = MaestralMonitor(self.client)
        self.sync = self.monitor.sync

        # periodically check for updates and refresh account info
        self.update_thread = Thread(
            name='maestral-update-check',
            target=self._periodic_refresh,
            daemon=True,
        )
        self.update_thread.start()

        if run:
            self.run()

    def run(self):
        """
        Runs setup if necessary, starts syncing, and starts systemd notifications if run
        as a systemd notify service.
        """

        if self.pending_dropbox_folder:
            self.monitor.reset_sync_state()
            self.create_dropbox_directory()

        # start syncing
        self.start_sync()

        if NOTIFY_SOCKET:  # notify systemd that we have started
            logger.debug('Running as systemd notify service')
            logger.debug('NOTIFY_SOCKET = %s', NOTIFY_SOCKET)
            sd_notifier.notify('READY=1')

        if IS_WATCHDOG:  # notify systemd periodically if alive
            logger.debug('Running as systemd watchdog service')
            logger.debug('WATCHDOG_USEC = %s', WATCHDOG_USEC)
            logger.debug('WATCHDOG_PID = %s', WATCHDOG_PID)

            self.watchdog_thread = Thread(
                name='maestral-watchdog',
                target=self._periodic_watchdog,
                daemon=True,
            )
            self.watchdog_thread.start()

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
        if INVOCATION_ID and journal:
            self.log_handler_journal = journal.JournalHandler()
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
        """Sets a configuration option."""
        self._conf.set(section, name, value)

    def get_conf(self, section, name):
        """Gets a configuration option."""
        return self._conf.get(section, name)

    def set_state(self, section, name, value):
        """Sets a state value."""
        self._state.set(section, name, value)

    def get_state(self, section, name):
        """Gets a state value."""
        return self._state.get(section, name)

    # ==== getters / setters for config with side effects ================================

    @property
    def dropbox_path(self):
        """Returns the path to the local Dropbox directory. Read only. Use
        :meth:`create_dropbox_directory` or :meth:`move_dropbox_directory` to set or
        change the Dropbox directory location instead. """
        return self.sync.dropbox_path

    @property
    def excluded_items(self):
        """
        Returns a list of excluded folders (read only). Use :meth:`exclude_item`,
        :meth:`include_item` or :meth:`set_excluded_items` to change which items are
        excluded from syncing.
        """
        return self.sync.excluded_items

    @property
    def log_level(self):
        """Log level for log files, the stream handler and the systemd journal."""
        return self._conf.get('app', 'log_level')

    @log_level.setter
    def log_level(self, level_num):
        """Setter: Log level for log files, the stream handler and the systemd journal."""
        self.log_handler_file.setLevel(level_num)
        if self.log_handler_journal:
            self.log_handler_journal.setLevel(level_num)
        if self.log_to_stdout:
            self.log_handler_stream.setLevel(level_num)
        self._conf.set('app', 'log_level', level_num)

    @property
    def log_to_stdout(self):
        return self._log_to_stdout

    @log_to_stdout.setter
    def log_to_stdout(self, enabled=True):
        """Enables or disables logging to stdout."""
        self._log_to_stdout = enabled
        level = self.log_level if enabled else 100
        self.log_handler_stream.setLevel(level)

    @property
    def analytics(self):
        """Enables or disables logging of errors to bugsnag."""
        return self._conf.get('app', 'analytics')

    @analytics.setter
    def analytics(self, enabled):
        """Setter: Enables or disables logging of errors to bugsnag."""

        bugsnag.configuration.auto_notify = enabled
        bugsnag.configuration.auto_capture_sessions = enabled
        self._log_handler_bugsnag.setLevel(logging.ERROR if enabled else 100)

        self._conf.set('app', 'analytics', enabled)

    @property
    def notification_snooze(self):
        """Snoozed time for desktop notifications in minutes."""
        return self.desktop_notifier.snoozed

    @notification_snooze.setter
    def notification_snooze(self, minutes):
        """Setter: Snoozed time for desktop notifications in minutes."""
        self.desktop_notifier.snoozed = minutes

    @property
    def notification_level(self):
        """Level for desktop notifications."""
        return self.desktop_notifier.notify_level

    @notification_level.setter
    def notification_level(self, level):
        """Setter: Level for desktop notifications."""
        self.desktop_notifier.notify_level = level

    # ==== state information  ============================================================

    @property
    def pending_dropbox_folder(self):
        """Bool indicating if a local Dropbox directory has been created."""
        return not osp.isdir(self._conf.get('main', 'path'))

    @property
    def pending_first_download(self):
        """Bool indicating if the initial download has already occurred."""
        return (self._state.get('sync', 'lastsync') == 0
                or self._state.get('sync', 'cursor') == '')

    @property
    def syncing(self):
        """
        Bool indicating if Maestral is syncing. It will be ``True`` if syncing is not
        paused by the user *and* Maestral is connected to the internet.
        """
        return self.monitor.syncing.is_set() or self.monitor.startup.is_set()

    @property
    def paused(self):
        """Bool indicating if syncing is paused by the user. This is set by
        calling :meth:`pause`."""
        return self.monitor.paused_by_user.is_set() and not self.sync.lock.locked()

    @property
    def stopped(self):
        """Bool indicating if syncing is stopped, for instance because of an exception."""
        return not self.monitor.running.is_set() and not self.sync.lock.locked()

    @property
    def connected(self):
        """Bool indicating if Dropbox servers can be reached."""
        return self.monitor.connected.is_set()

    @property
    def status(self):
        """
        Returns a string with the last status message. This can be displayed as
        information to the user but should not be relied on otherwise.
        """
        return self._log_handler_info_cache.getLastMessage()

    @property
    def sync_errors(self):
        """Returns list containing the current sync errors as dicts."""
        sync_errors = list(self.sync.sync_errors.queue)
        sync_errors_dicts = [error_to_dict(e) for e in sync_errors]
        return sync_errors_dicts

    @property
    def maestral_errors(self):
        """
        Returns a list of Maestral's errors as dicts. This does not include lost internet
        connections or file sync errors which only emit warnings and are tracked and
        cleared separately. Errors listed here must be acted upon for Maestral to
        continue syncing.
        """

        maestral_errors = [r.exc_info[1] for r in self._log_handler_error_cache.cached_records]
        maestral_errors_dicts = [error_to_dict(e) for e in maestral_errors]
        return maestral_errors_dicts

    def clear_maestral_errors(self):
        """
        Manually clears all Maestral errors. This should be used after they have been
        resolved by the user through the GUI or CLI.
        """
        self._log_handler_error_cache.clear()

    @property
    def account_profile_pic_path(self):
        """
        Returns the path of the current account's profile picture. There may not be an
        actual file at that path, if the user did not set a profile picture or the
        picture has not yet been downloaded.
        """
        return get_cache_path('maestral', self._config_name + '_profile_pic.jpeg')

    def get_file_status(self, local_path):
        """
        Returns the sync status of an individual file.

        :param str local_path: Path to file on the local drive. May be relative to the
            current working directory.
        :returns: String indicating the sync status. Can be 'uploading', 'downloading',
            'up to date', 'error', or 'unwatched' (for files outside of the Dropbox
            directory).
        :rtype: str
        """
        if not self.syncing:
            return FileStatus.Unwatched.value

        local_path = osp.abspath(local_path)

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

    def get_activity(self):
        """
        Gets current upload / download activity.

        :returns: A dictionary with lists of all files currently queued for or being
            uploaded or downloaded. Paths are given relative to the Dropbox folder.
        :rtype: dict(list, list)
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

    @handle_disconnect
    def get_account_info(self):
        """
        Gets account information from Dropbox and returns it as a dictionary. The entries
        will either be of type ``str`` or ``bool``.

        :returns: Dropbox account information.
        :rtype: dict[str, bool]
        :raises: :class:`MaestralApiError`
        """
        res = self.client.get_account_info()
        return dropbox_stone_to_dict(res)

    @handle_disconnect
    def get_space_usage(self):
        """
        Gets the space usage stored by Dropbox and returns it as a dictionary. The
        entries will either be of type ``str`` or ``bool``.

        :returns: Dropbox account information.
        :rtype: dict[str, bool]
        :raises: :class:`MaestralApiError`
        """
        res = self.client.get_space_usage()
        return dropbox_stone_to_dict(res)

    # ==== control methods for front ends ================================================

    @handle_disconnect
    def get_profile_pic(self):
        """
        Attempts to download the user's profile picture from Dropbox. The picture saved
        in Maestral's cache directory for retrieval when there is no internet connection.
        This function will fail silently in case of :class:`MaestralApiError`s.

        :returns: Path to saved profile picture or None if no profile picture is set.
        """

        try:
            res = self.client.get_account_info()
        except MaestralApiError:
            pass
        else:
            if res.profile_photo_url:
                # download current profile pic
                res = requests.get(res.profile_photo_url)
                with open(self.account_profile_pic_path, 'wb') as f:
                    f.write(res.content)
                return self.account_profile_pic_path
            else:
                # delete current profile pic
                self._delete_old_profile_pics()

    @handle_disconnect
    def list_folder(self, dbx_path, **kwargs):
        """
        List all items inside the folder given by :param:`dbx_path`.

        :param dbx_path: Path to folder on Dropbox.
        :returns: List of Dropbox item metadata as dicts or ``False`` if listing failed
            due to connection issues.
        :rtype: list[dict]
        :raises: :class:`MaestralApiError`
        """
        res = self.client.list_folder(dbx_path, **kwargs)

        entries = [dropbox_stone_to_dict(e) for e in res.entries]

        return entries

    def _delete_old_profile_pics(self):
        # delete all old pictures
        for file in os.listdir(get_cache_path('maestral')):
            if file.startswith(self._config_name + '_profile_pic'):
                try:
                    os.unlink(osp.join(get_cache_path('maestral'), file))
                except OSError:
                    pass

    def rebuild_index(self):
        """
        Rebuilds the rev file by comparing remote with local files and updating rev
        numbers from the Dropbox server. Files are compared by their content hashes and
        conflicting copies are created if the contents differ. File changes during the
        rebuild process will be queued and uploaded once rebuilding has completed.

        Rebuilding will be performed asynchronously.

        :raises: :class:`MaestralApiError`
        """

        self.monitor.rebuild_index()

    def start_sync(self):
        """
        Creates syncing threads and starts syncing.
        """
        self.monitor.start()

    def resume_sync(self):
        """
        Resumes the syncing threads if paused.
        """
        self.monitor.resume()

    def pause_sync(self):
        """
        Pauses the syncing threads if running.
        """
        self.monitor.pause()

    def stop_sync(self):
        """
        Stops the syncing threads if running, destroys observer thread.
        """
        self.monitor.stop()

    def reset_sync_state(self):
        """
        Resets the sync index and state. Only call this to clean up leftover state
        information if a Dropbox was improperly unlinked (e.g., auth token has been
        manually deleted). Otherwise leave state management to Maestral.
        """

        self.monitor.reset_sync_state()

    def unlink(self):
        """
        Unlinks the configured Dropbox account but leaves all downloaded files in place.
        All syncing metadata will be removed as well. Connection and API errors will be
        handled silently but the Dropbox access key will always be removed from the
        user's PC.
        """
        self.stop_sync()
        try:
            self.client.unlink()
        except (ConnectionError, MaestralApiError):
            pass

        try:
            os.remove(self.sync.rev_file_path)
        except OSError:
            pass

        self.sync.clear_rev_index()
        delete(self.sync.rev_file_path)
        self._conf.cleanup()
        self._state.cleanup()

        logger.info('Unlinked Dropbox account.')

    def exclude_item(self, dbx_path):
        """
        Excludes file or folder from sync and deletes it locally. It is safe to call this
        method with items which have already been excluded.

        :param str dbx_path: Dropbox path of item to exclude.
        :raises: :class:`ValueError` if ``dbx_path`` is not on Dropbox.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        """

        # input validation
        md = self.client.get_metadata(dbx_path)

        if not md:
            raise ValueError(f'"{dbx_path}" does not exist on Dropbox')

        dbx_path = dbx_path.lower().rstrip(osp.sep)

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
            with self.monitor.fs_event_handler.ignore(local_path,
                                                      recursive=osp.isdir(local_path),
                                                      event_types=(EVENT_TYPE_DELETED,)):
                delete(local_path)

    def include_item(self, dbx_path):
        """
        Includes file or folder in sync and downloads in the background. It is safe to
        call this method with items which have already been included, they will not be
        downloaded again.

        :param str dbx_path: Dropbox path of item to include.
        :raises: :class:`ValueError` if ``dbx_path`` is not on Dropbox or lies inside
            another excluded folder.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        """

        # input validation
        md = self.client.get_metadata(dbx_path)

        if not md:
            raise ValueError(f'"{dbx_path}" does not exist on Dropbox')

        dbx_path = dbx_path.lower().rstrip(osp.sep)

        old_excluded_items = self.sync.excluded_items

        for folder in old_excluded_items:
            if is_child(dbx_path, folder):
                raise ValueError(f'"{dbx_path}" lies inside the excluded folder '
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

    @handle_disconnect
    def set_excluded_items(self, items=None):
        """
        Sets the list of excluded files or folders. If not given, gets all top level
        folder paths from Dropbox and asks user to include or exclude. Items which are
        no in ``items`` but were previously excluded will be downloaded.

        On initial sync, this does not trigger any downloads.

        :param list items: If given, list of excluded files or folders to set.
        :raises: :class:`MaestralApiError`
        """

        if items is None:

            excluded_items = []

            # get all top-level Dropbox folders
            result = self.client.list_folder('/', recursive=False)

            # paginate through top-level folders, ask to exclude
            for entry in result.entries:
                if isinstance(entry, files.FolderMetadata):
                    yes = click.confirm(f'Exclude "{entry.path_display}" from sync?',
                                        prompt_suffix='')
                    if yes:
                        excluded_items.append(entry.path_lower)
        else:
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

    def excluded_status(self, dbx_path):
        """
        Returns 'excluded', 'partially excluded' or 'included'. This function will not
        check if the item actually exists on Dropbox.

        :param str dbx_path: Path to item on Dropbox.
        :returns: Excluded status.
        :rtype: str
        """

        dbx_path = dbx_path.lower().rstrip(osp.sep)

        excluded_items = self._conf.get('main', 'excluded_items')

        if dbx_path in excluded_items:
            return 'excluded'
        elif any(is_child(f, dbx_path) for f in excluded_items):
            return 'partially excluded'
        else:
            return 'included'

    @with_sync_paused
    def move_dropbox_directory(self, new_path=None):
        """
        Sets the local Dropbox directory. This moves all local files to the new location
        and resumes syncing afterwards.

        :param str new_path: Full path to local Dropbox folder. If not given, the user
            will be prompted to input the path.
        :raises: ``OSError`` if moving the directory fails.
        """

        # get old and new paths
        old_path = self.sync.dropbox_path
        new_path = new_path or select_dbx_path_dialog(self._config_name)

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

    @with_sync_paused
    def create_dropbox_directory(self, path=None):
        """
        Creates a new Dropbox directory. Only call this during setup.

        :param str path: Full path to local Dropbox folder. If not given, the user will be
            prompted to input the path.
        :raises: ``OSError`` if creation fails
        """
        path = path or select_dbx_path_dialog(self._config_name, allow_merge=True)

        # create new folder
        os.makedirs(path, exist_ok=True)

        # update config file and client
        self.sync.dropbox_path = path

    # ==== utility methods for front ends ================================================

    def to_local_path(self, dbx_path):
        """
        Converts a path relative to the Dropbox folder to a correctly cased local file
        system path.

        :param str dbx_path: Path relative to Dropbox root.
        :returns: Corresponding path of a location in the local Dropbox folder.
        :rtype: str
        """
        return self.sync.to_local_path(dbx_path)

    @staticmethod
    def check_for_updates():
        """
        Checks if an update is available.

        :returns: A dictionary with information about the latest release with the fields
            ``update_available`` (bool), ``latest_release`` (str), ``release_notes`` (str)
            and ``error`` (str or None).
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
            # update account info
            self.get_account_info()
            self.get_space_usage()
            self.get_profile_pic()
            # check for maestral updates
            res = self.check_for_updates()
            if not res['error']:
                self._state.set('app', 'latest_release', res['latest_release'])
            time.sleep(60 * 60)  # 60 min

    def _periodic_watchdog(self):
        while self.monitor._threads_alive():
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

        return f'<{self.__class__.__name__}({email}, {account_type})>'


def select_dbx_path_dialog(config_name, allow_merge=False):
    """
    A CLI dialog to ask for a local dropbox directory path.

    :param str config_name: The configuration to use for the default folder name.
    :param bool allow_merge: If ``True``, allows for selecting an existing path without
        deleting it. Defaults to ``False``.
    :returns: Path given by user.
    :rtype: str
    """

    conf = MaestralConfig(config_name)

    default = osp.join(get_home_dir(), conf.get('main', 'default_dir_name'))

    while True:
        res = click.prompt(
            'Please give Dropbox folder location',
            default=default,
            type=click.Path(writable=True)
        )

        res = res.rstrip(osp.sep)

        dropbox_path = osp.expanduser(res or default)
        old_path = osp.expanduser(conf.get('main', 'path'))

        try:
            same_path = osp.samefile(old_path, dropbox_path)
        except FileNotFoundError:
            same_path = False

        if osp.exists(dropbox_path) and not same_path:
            msg = (f'Directory "{dropbox_path}" already exist. Do you want to '
                   'overwrite it? Its content will be lost!')
            if click.confirm(msg, prompt_suffix=''):
                err = delete(dropbox_path)
                if err:
                    click.echo(f'Could not write to location "{dropbox_path}". Please '
                               'make sure that you have sufficient permissions.')
                else:
                    return dropbox_path
            elif allow_merge:
                msg = 'Would you like to merge its content with your Dropbox?'
                if click.confirm(msg, prompt_suffix=''):
                    return dropbox_path
        else:
            return dropbox_path
