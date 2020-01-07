# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

# system imports
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
import bugsnag
from bugsnag.handlers import BugsnagHandler

try:
    from systemd import journal
except ImportError:
    journal = None
try:
    import sdnotify
    system_notifier = sdnotify.SystemdNotifier()
except ImportError:
    sdnotify = None
    system_notifier = None

# maestral modules
from maestral import __version__
from maestral.sync.monitor import MaestralMonitor
from maestral.sync.utils import handle_disconnect, with_sync_paused
from maestral.sync.utils.path import is_child, path_exists_case_insensitive
from maestral.sync.constants import (
    INVOCATION_ID, NOTIFY_SOCKET, WATCHDOG_PID, WATCHDOG_USEC, IS_WATCHDOG,
)
from maestral.sync.client import MaestralApiClient
from maestral.sync.utils.serializer import maestral_error_to_dict, dropbox_stone_to_dict
from maestral.sync.utils.appdirs import get_log_path, get_cache_path, get_home_dir
from maestral.sync.utils.updates import check_update_available
from maestral.sync.oauth import OAuth2Session
from maestral.sync.errors import MaestralApiError
from maestral.config.main import MaestralConfig


logger = logging.getLogger(__name__)

# set up error reporting but do not activate

bugsnag.configure(
    api_key="081c05e2bf9730d5f55bc35dea15c833",
    app_version=__version__,
    auto_notify=False,
    auto_capture_sessions=False,
)

def callback(notification):
    notification.add_tab(
        "system",
        {"platform": platform.platform(), "python": platform.python_version()}
    )

bugsnag.before_notify(callback)

# helper classes

class CachedHandler(logging.Handler):
    """
    Handler which stores past records.

    :param int maxlen: Maximum number of records to store.
    """

    def __init__(self, maxlen=None):
        logging.Handler.__init__(self)
        self.cached_records = deque([], maxlen)

    def emit(self, record):
        self.format(record)
        self.cached_records.append(record)
        if NOTIFY_SOCKET and system_notifier:
            system_notifier.notify(f"STATUS={record.message}")

    def getLastMessage(self):
        if len(self.cached_records) > 0:
            return self.cached_records[-1].message
        else:
            return ""

    def getAllMessages(self):
        return [r.message for r in self.cached_records]

    def clear(self):
        self.cached_records.clear()


# ========================================================================================
# Main API
# ========================================================================================

class Maestral(object):
    """
    An open source Dropbox client for macOS and Linux to syncing a local folder
    with your Dropbox account. All functions and properties return objects or
    raise exceptions which can safely serialized, i.e., pure Python types. The only
    exception are MaestralApiErrors which have been registered explicitly with the Pyro5
    serializer.
    """

    _daemon_running = True  # for integration with Pyro

    def __init__(self, config_name='maestral', run=True):

        self._config_name = config_name
        self._conf = MaestralConfig(self._config_name)

        self._setup_logging()
        self.set_share_error_reports(self._conf.get("app", "analytics"))

        self.client = MaestralApiClient(config_name=self._config_name)
        self.monitor = MaestralMonitor(self.client, config_name=self._config_name)
        self.sync = self.monitor.sync

        if run:

            if self.pending_dropbox_folder(config_name):
                self.create_dropbox_directory()
                self.set_excluded_folders()

                self.sync.last_cursor = ""
                self.sync.last_sync = 0

            # start syncing
            self.start_sync()

            if NOTIFY_SOCKET and system_notifier:  # notify systemd that we have started
                logger.debug("Running as systemd notify service")
                logger.debug(f"NOTIFY_SOCKET = {NOTIFY_SOCKET}")
                system_notifier.notify("READY=1")

            if IS_WATCHDOG and system_notifier:  # notify systemd periodically if alive
                logger.debug("Running as systemd watchdog service")
                logger.debug(f"WATCHDOG_USEC = {WATCHDOG_USEC}")
                logger.debug(f"WATCHDOG_PID = {WATCHDOG_PID}")

                self.watchdog_thread = Thread(
                    name="Maestral watchdog",
                    target=self._periodic_watchdog,
                    daemon=True,
                )
                self.watchdog_thread.start()

            # periodically check for updates and refresh account info
            self.update_thread = Thread(
                name="Maestral update check",
                target=self._periodic_refresh,
                daemon=True,
            )
            self.update_thread.start()

    def _setup_logging(self):

        log_level = self._conf.get("app", "log_level")
        mdbx_logger = logging.getLogger("maestral")
        mdbx_logger.setLevel(logging.DEBUG)

        log_fmt_long = logging.Formatter(
            fmt="%(asctime)s %(name)s %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        log_fmt_short = logging.Formatter(fmt="%(message)s")

        # log to file
        rfh_log_file = get_log_path("maestral", self._config_name + ".log")
        self._log_handler_file = logging.handlers.RotatingFileHandler(rfh_log_file, maxBytes=10 ** 7, backupCount=1)
        self._log_handler_file.setFormatter(log_fmt_long)
        self._log_handler_file.setLevel(log_level)
        mdbx_logger.addHandler(self._log_handler_file)

        # log to journal when launched from systemd
        if INVOCATION_ID and journal:
            self._log_handler_journal = journal.JournalHandler()
            self._log_handler_journal.setFormatter(log_fmt_short)
            mdbx_logger.addHandler(self._log_handler_journal)

        # log to stdout (disabled by default)
        self._log_handler_stream = logging.StreamHandler(sys.stdout)
        self._log_handler_stream.setFormatter(log_fmt_long)
        self._log_handler_stream.setLevel(100)
        mdbx_logger.addHandler(self._log_handler_stream)

        # log to cached handlers for GUI and CLI
        self._log_handler_info_cache = CachedHandler(maxlen=1)
        self._log_handler_info_cache.setLevel(logging.INFO)
        self._log_handler_info_cache.setFormatter(log_fmt_short)
        mdbx_logger.addHandler(self._log_handler_info_cache)

        self._log_handler_error_cache = CachedHandler()
        self._log_handler_error_cache.setLevel(logging.ERROR)
        self._log_handler_error_cache.setFormatter(log_fmt_short)
        mdbx_logger.addHandler(self._log_handler_error_cache)

        # log to bugsnag (disabled by default)
        self._log_handler_bugsnag = BugsnagHandler()
        self._log_handler_bugsnag.setLevel(100)
        mdbx_logger.addHandler(self._log_handler_bugsnag)

    @property
    def config_name(self):
        return self._config_name

    def set_conf(self, section, name, value):
        self._conf.set(section, name, value)

    def get_conf(self, section, name):
        return self._conf.get(section, name)

    def set_log_level(self, level_num):
        self._log_handler_file.setLevel(level_num)
        self._log_handler_stream.setLevel(level_num)
        self._conf.set("app", "log_level", level_num)

    def set_log_to_stdout(self, enabled=True):

        if enabled:
            log_level = self._conf.get("app", "log_level")
            self._log_handler_stream.setLevel(log_level)
        else:
            self._log_handler_stream.setLevel(100)

    def set_share_error_reports(self, enabled):

        bugsnag.configuration.auto_notify = enabled
        bugsnag.configuration.auto_capture_sessions = enabled
        self._log_handler_bugsnag.setLevel(logging.ERROR if enabled else 100)

        self._conf.set("app", "analytics", enabled)

    @staticmethod
    def pending_link(config_name):
        """
        Bool indicating if auth tokens are stored in the system's keychain. This may raise
        a KeyringLocked exception if the user's keychain cannot be accessed. This
        exception will not be deserialized by Pyro5. You should check if Maestral is
        linked before instantiating a daemon.

        :param str config_name: Name of user config to check.

        :raises: :class:`keyring.errors.KeyringLocked`
        """
        auth_session = OAuth2Session(config_name)
        return auth_session.load_token() is None

    @staticmethod
    def pending_dropbox_folder(config_name):
        """
        Bool indicating if a local Dropbox directory has been set.

        :param str config_name: Name of user config to check.
        """
        conf = MaestralConfig(config_name)
        return not osp.isdir(conf.get("main", "path"))

    def pending_first_download(self):
        """Bool indicating if the initial download has already occurred."""
        return (self._conf.get("internal", "lastsync") == 0 or
                self._conf.get("internal", "cursor") == "")

    @property
    def syncing(self):
        """Bool indicating if Maestral is syncing. It will be ``True`` if syncing is
        not paused by the user *and* Maestral is connected to the internet."""
        return self.monitor.syncing.is_set()

    @property
    def paused(self):
        """Bool indicating if syncing is paused by the user. This is set by calling
        :meth:`pause`."""
        return not self.monitor._auto_resume_on_connect

    @property
    def stopped(self):
        """Bool indicating if syncing is stopped, for instance because of an exception."""
        return not self.monitor.running.is_set()

    @property
    def connected(self):
        """Bool indicating if Dropbox servers can be reached."""
        return self.monitor.connected.is_set()

    @property
    def status(self):
        """Returns a string with the last status message. This can be displayed as
        information to the user but should not be relied on otherwise."""
        return self._log_handler_info_cache.getLastMessage()

    @property
    def notify(self):
        """Bool indicating if notifications are enabled or disabled."""
        return self.sync.notify.enabled

    @notify.setter
    def notify(self, boolean):
        """Setter: Bool indicating if notifications are enabled."""
        self.sync.notify.enabled = boolean

    @property
    def dropbox_path(self):
        """Returns the path to the local Dropbox directory. Read only. Use
        :meth:`create_dropbox_directory` or :meth:`move_dropbox_directory` to set or
        change the Dropbox directory location instead. """
        return self.sync.dropbox_path

    @property
    def excluded_folders(self):
        """Returns a list of excluded folders (read only). Use :meth:`exclude_folder`,
        :meth:`include_folder` or :meth:`set_excluded_folders` change which folders are
        excluded from syncing."""
        return self.sync.excluded_folders

    @property
    def sync_errors(self):
        """Returns list containing the current sync errors as dicts."""
        sync_errors = list(self.sync.sync_errors.queue)
        sync_errors_dicts = [maestral_error_to_dict(e) for e in sync_errors]
        return sync_errors_dicts

    @property
    def maestral_errors(self):
        """Returns a list of Maestral's errors as dicts. This does not include lost
        internet connections or file sync errors which only emit warnings and are tracked
        and cleared separately. Errors listed here must be acted upon for Maestral to
        continue syncing.
        """

        maestral_errors = [r.exc_info[1] for r in self._log_handler_error_cache.cached_records]
        maestral_errors_dicts = [maestral_error_to_dict(e) for e in maestral_errors]
        return maestral_errors_dicts

    def clear_maestral_errors(self):
        """Manually clears all Maestral errors. This should be used after they have been
        resolved by the user through the GUI or CLI.
        """
        self._log_handler_error_cache.clear()

    @property
    def account_profile_pic_path(self):
        """Returns the path of the current account's profile picture. There may not be
        an actual file at that path, if the user did not set a profile picture or the
        picture has not yet been downloaded."""
        return get_cache_path("maestral", self._config_name + "_profile_pic.jpeg")

    def get_file_status(self, local_path):
        """
        Returns the sync status of an individual file.

        :param local_path: Path to file on the local drive.
        :return: String indicating the sync status. Can be "uploading", "downloading",
            "up to date", "error", or "unwatched" (for files outside of the Dropbox
            directory).
        :rtype: str
        """
        if not self.syncing:
            return "unwatched"

        try:
            dbx_path = self.sync.to_dbx_path(local_path)
        except ValueError:
            return "unwatched"

        if local_path in self.monitor.queued_for_upload:
            return "uploading"
        elif local_path in self.monitor.queued_for_download:
            return "downloading"
        elif any(local_path == err["local_path"] for err in self.sync_errors):
            return "error"
        elif self.sync.get_local_rev(dbx_path):
            return "up to date"
        else:
            return "unwatched"

    def get_activity(self):
        """
        Returns a dictionary with lists of all file currently queued for or being synced.

        :rtype: dict(list, list)
        """
        PathItem = namedtuple("PathItem", "local_path status")
        uploading = []
        downloading = []

        for path in self.monitor.uploading:
            uploading.append(PathItem(path, "uploading"))

        for path in self.monitor.queued_for_upload:
            uploading.append(PathItem(path, "queued"))

        for path in self.monitor.downloading:
            downloading.append(PathItem(path, "downloading"))

        for path in self.monitor.queued_for_download:
            downloading.append(PathItem(path, "queued"))

        return dict(uploading=uploading, downloading=downloading)

    @handle_disconnect
    def get_account_info(self):
        """
        Gets account information from Dropbox and returns it as a dictionary.
        The entries will either be of type ``str`` or ``bool``.

        :returns: Dropbox account information.
        :rtype: dict[str, bool]
        :raises: :class:`MaestralApiError`
        """
        res = self.client.get_account_info()
        return dropbox_stone_to_dict(res)

    @handle_disconnect
    def get_space_usage(self):
        """
        Gets the space usage stored by Dropbox and returns it as a dictionary.
        The entries will either be of type ``str`` or ``bool``.

        :returns: Dropbox account information.
        :rtype: dict[str, bool]
        """
        res = self.client.get_space_usage()
        return dropbox_stone_to_dict(res)

    @handle_disconnect
    def get_profile_pic(self):
        """
        Attempts to download the user's profile picture from Dropbox. The picture saved in
        Maestral's cache directory for retrieval when there is no internet connection.
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
                with open(self.account_profile_pic_path, "wb") as f:
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
        :return: List of Dropbox item metadata as dicts or ``False`` if listing failed
            due to connection issues.
        :rtype: list[dict]
        """
        dbx_path = "" if dbx_path == "/" else dbx_path
        res = self.client.list_folder(dbx_path, **kwargs)

        entries = [dropbox_stone_to_dict(e) for e in res.entries]

        return entries

    def _delete_old_profile_pics(self):
        # delete all old pictures
        for file in os.listdir(get_cache_path("maestral")):
            if file.startswith(self._config_name + "_profile_pic"):
                try:
                    os.unlink(osp.join(get_cache_path("maestral"), file))
                except OSError:
                    pass

    def rebuild_index(self):
        """
        Rebuilds the Maestral index and resumes syncing afterwards if it has been
        running.

        :raises: :class:`MaestralApiError`
        """

        self.monitor.rebuild_rev_file()

    def start_sync(self, overload=None):
        """
        Creates syncing threads and starts syncing.
        """
        self.monitor.start()

    def resume_sync(self, overload=None):
        """
        Resumes the syncing threads if paused.
        """
        self.monitor.resume()

    def pause_sync(self, overload=None):
        """
        Pauses the syncing threads if running.
        """
        self.monitor.pause()

    def stop_sync(self, overload=None):
        """
        Stops the syncing threads if running, destroys observer thread.
        """
        self.monitor.stop()

    def unlink(self):
        """
        Unlinks the configured Dropbox account but leaves all downloaded files
        in place. All syncing metadata will be removed as well. Connection and API errors
        will be handled silently but the Dropbox access key will always be removed from
        the user's PC.
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

        self._conf.reset_to_defaults()
        self._conf.set("main", "default_dir_name", f"Dropbox ({self._config_name.capitalize()})")

        logger.info("Unlinked Dropbox account.")

    def exclude_folder(self, dbx_path):
        """
        Excludes folder from sync and deletes local files. It is safe to call
        this method with folders which have already been excluded.

        :param str dbx_path: Dropbox folder to exclude.
        :raises: :class:`ValueError` if ``dbx_path`` is not on Dropbox.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        """

        dbx_path = dbx_path.lower().rstrip(osp.sep)

        md = self.client.get_metadata(dbx_path)

        if not isinstance(md, files.FolderMetadata):
            raise ValueError("No such folder on Dropbox: '{0}'".format(dbx_path))

        # add the path to excluded list
        excluded_folders = self.sync.excluded_folders
        if dbx_path not in excluded_folders:
            excluded_folders.append(dbx_path)
        else:
            logger.info("Folder was already excluded, nothing to do.")
            return

        self.sync.excluded_folders = excluded_folders
        self.sync.set_local_rev(dbx_path, None)

        # remove folder from local drive
        local_path = self.sync.to_local_path(dbx_path)
        local_path_cased = path_exists_case_insensitive(local_path)
        logger.info(f"Deleting folder '{local_path_cased}'.")
        if osp.isdir(local_path_cased):
            shutil.rmtree(local_path_cased)

    def include_folder(self, dbx_path):
        """
        Includes folder in sync and downloads in the background. It is safe to
        call this method with folders which have already been included, they
        will not be downloaded again.

        :param str dbx_path: Dropbox folder to include.
        :raises: :class:`ValueError` if ``dbx_path`` is not on Dropbox or lies inside
            another excluded folder.
        :raises: :class:`ConnectionError` if connection to Dropbox fails.
        """

        dbx_path = dbx_path.lower().rstrip(osp.sep)
        md = self.client.get_metadata(dbx_path)

        old_excluded_folders = self.sync.excluded_folders

        if not isinstance(md, files.FolderMetadata):
            raise ValueError("No such folder on Dropbox: '{0}'".format(dbx_path))
        for folder in old_excluded_folders:
            if is_child(dbx_path, folder):
                raise ValueError("'{0}' lies inside the excluded folder '{1}'. "
                                 "Please include '{1}' first.".format(dbx_path, folder))

        # Get folders which will need to be downloaded, do not attempt to download
        # subfolders of `dbx_path` which were already included.
        # `new_included_folders` will either be empty (`dbx_path` was already
        # included), just contain `dbx_path` itself (the whole folder was excluded) or
        # only contain subfolders of `dbx_path` (`dbx_path` was partially included).
        new_included_folders = tuple(x for x in old_excluded_folders if
                                     x == dbx_path or is_child(x, dbx_path))

        if new_included_folders:
            # remove `dbx_path` or all excluded children from the excluded list
            excluded_folders = list(set(old_excluded_folders) - set(new_included_folders))
        else:
            logger.info("Folder was already included, nothing to do.")
            return

        self.sync.excluded_folders = excluded_folders

        # download folder contents from Dropbox
        logger.info(f"Downloading added folder '{dbx_path}'.")
        for folder in new_included_folders:
            self.sync.queued_folder_downloads.put(folder)

    @handle_disconnect
    def _include_folder_without_subfolders(self, dbx_path):
        """Sets a folder to included without explicitly including its subfolders. This
        is to be used internally, when a folder has been removed from the excluded list,
        but some of its subfolders may have been added."""

        dbx_path = dbx_path.lower().rstrip(osp.sep)
        excluded_folders = self.sync.excluded_folders

        if dbx_path not in excluded_folders:
            return

        excluded_folders.remove(dbx_path)

        self.sync.excluded_folders = excluded_folders
        self.sync.queued_folder_downloads.put(dbx_path)

    @handle_disconnect
    def set_excluded_folders(self, folder_list=None):
        """
        Sets the list of excluded folders to `folder_list`. If not given, gets all top
        level folder paths from Dropbox and asks user to include or exclude. Folders
        which are no in `folder_list` but exist on Dropbox will be downloaded.

        On initial sync, this does not trigger any downloads.

        :param list folder_list: If given, list of excluded folder to set.
        :return: List of excluded folders.
        :rtype: list
        :raises: :class:`MaestralApiError`
        """

        if folder_list is None:

            excluded_folders = []

            # get all top-level Dropbox folders
            result = self.client.list_folder("", recursive=False)

            # paginate through top-level folders, ask to exclude
            for entry in result.entries:
                if isinstance(entry, files.FolderMetadata):
                    yes = click.confirm(f"Exclude '{entry.path_display}' from sync?")
                    if yes:
                        excluded_folders.append(entry.path_lower)
        else:
            excluded_folders = self.sync.clean_excluded_folder_list(folder_list)

        old_excluded_folders = self.sync.excluded_folders

        added_excluded_folders = set(excluded_folders) - set(old_excluded_folders)
        added_included_folders = set(old_excluded_folders) - set(excluded_folders)

        if not self.pending_first_download():
            # apply changes
            for path in added_excluded_folders:
                self.exclude_folder(path)
            for path in added_included_folders:
                self._include_folder_without_subfolders(path)

        self.sync.excluded_folders = excluded_folders

        return excluded_folders

    def excluded_status(self, dbx_path):
        """
        Returns 'excluded', 'partially excluded' or 'included'. This function will not
        check if the item actually exists on Dropbox.

        :param str dbx_path: Path to item on Dropbox.
        :returns: Excluded status.
        :rtype: str
        """

        dbx_path = dbx_path.lower().rstrip(osp.sep)

        excluded_items = self._conf.get("main", "excluded_folders") + self._conf.get("main", "excluded_files")

        if dbx_path in excluded_items:
            return "excluded"
        elif any(is_child(f, dbx_path) for f in excluded_items):
            return "partially excluded"
        else:
            return "included"

    @with_sync_paused
    def move_dropbox_directory(self, new_path=None):
        """
        Change or set local dropbox directory. This moves all local files to
        the new location. If a file or folder already exists at this location,
        it will be overwritten.

        :param str new_path: Full path to local Dropbox folder. If not given, the
            user will be prompted to input the path.
        """

        # get old and new paths
        old_path = self.sync.dropbox_path
        if new_path is None:
            new_path = self._ask_for_path(self._config_name)

        try:
            if osp.samefile(old_path, new_path):
                return
        except FileNotFoundError:
            pass

        # remove existing items at current location
        try:
            os.unlink(new_path)
        except IsADirectoryError:
            shutil.rmtree(new_path, ignore_errors=True)
        except FileNotFoundError:
            pass

        # move folder from old location or create a new one if no old folder exists
        if osp.isdir(old_path):
            shutil.move(old_path, new_path)
        else:
            os.makedirs(new_path)

        # update config file and client
        self.sync.dropbox_path = new_path

    @with_sync_paused
    def create_dropbox_directory(self, path=None, overwrite=True):
        """
        Set a new local dropbox directory.

        :param str path: Full path to local Dropbox folder. If not given, the user will be
            prompted to input the path.
        :param bool overwrite: If ``True``, any existing file or folder at ``new_path``
            will be replaced.
        """
        # ask for new path
        if path is None:
            path = self._ask_for_path(self._config_name)

        if overwrite:
            # remove any old items at the location
            try:
                shutil.rmtree(path)
            except NotADirectoryError:
                os.unlink(path)
            except FileNotFoundError:
                pass

        # create new folder
        os.makedirs(path, exist_ok=True)

        # update config file and client
        self.sync.dropbox_path = path

    @staticmethod
    def _ask_for_path(config_name):
        """
        Asks for Dropbox path.
        """

        conf = MaestralConfig(config_name)

        default = osp.join(get_home_dir(), conf.get("main", "default_dir_name"))

        while True:
            msg = f"Please give Dropbox folder location or press enter for default ['{default}']:"
            res = input(msg).strip("'\" ")

            dropbox_path = osp.expanduser(res or default)
            old_path = osp.expanduser(conf.get("main", "path"))

            same_path = False
            try:
                if osp.samefile(old_path, dropbox_path):
                    same_path = True
            except FileNotFoundError:
                pass

            if osp.exists(dropbox_path) and not same_path:
                msg = f"Directory '{dropbox_path}' already exist. Do you want to overwrite it?"
                yes = click.confirm(msg)
                if yes:
                    return dropbox_path
                else:
                    pass
            else:
                return dropbox_path

    def to_local_path(self, dbx_path):
        return self.sync.to_local_path(dbx_path)

    @staticmethod
    def check_for_updates():
        return check_update_available()

    def _periodic_refresh(self):
        while True:
            # update account info
            self.get_account_info()
            self.get_space_usage()
            self.get_profile_pic()
            # check for maestral updates
            res = self.check_for_updates()
            if not res["error"]:
                self._conf.set("app", "latest_release", res["latest_release"])
            time.sleep(60*60)  # 60 min

    def _periodic_watchdog(self):
        while self.monitor._threads_alive():
            system_notifier.notify("WATCHDOG=1")
            time.sleep(int(WATCHDOG_USEC) / (2 * 10 ** 6))

    def shutdown_pyro_daemon(self):
        """Does nothing except for setting the _daemon_running flag to ``False``. This
        will be checked by Pyro periodically to shut down the daemon when requested."""
        self._daemon_running = False
        if NOTIFY_SOCKET and system_notifier:
            # notify systemd that we are shutting down
            system_notifier.notify("STOPPING=1")

    def _loop_condition(self):
        return self._daemon_running

    def __del__(self):
        try:
            self.monitor.stop()
        except:
            pass

    def __repr__(self):
        email = self._conf.get("account", "email")
        account_type = self._conf.get("account", "type")

        return f"<{self.__class__}({email}, {account_type})>"
