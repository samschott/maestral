# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

__version__ = "0.2.1"
__author__ = "Sam Schott"
__url__ = "https://github.com/SamSchott/maestral"

import os
import os.path as osp
import time
import shutil
import functools
from blinker import signal
from threading import Thread
from dropbox import files

from maestral.client import MaestralApiClient
from maestral.monitor import (MaestralMonitor, CONNECTION_ERRORS, IDLE,
                              DISCONNECTED, path_exists_case_insensitive)
from maestral.config.main import CONF

import logging

logger = logging.getLogger(__name__)

for logger_name in ["maestral.main", "maestral.client", "maestral.monitor"]:
    mdbx_logger = logging.getLogger(logger_name)
    mdbx_logger.addHandler(logging.StreamHandler())
    mdbx_logger.setLevel(logging.INFO)


CONNECTION_ERROR = ("Cannot connect to Dropbox servers. Please  check " +
                    "your internet connection and try again later.")


def folder_download_worker(sync, dbx_path):
    """
    Worker to to download a whole Dropbox directory in the background.

    :param class sync: :class:`UpDownSync` instance.
    :param str dbx_path: Path to directory on Dropbox.
    """
    download_complete_signal = signal("download_complete_signal")

    time.sleep(2)  # wait for pausing to take effect

    with sync.lock:
        completed = False
        while not completed:
            try:
                sync.get_remote_dropbox(dbx_path)
                sync.last_sync = time.time()
                logger.info(IDLE)

                time.sleep(1)
                completed = True
                download_complete_signal.send()

            except CONNECTION_ERRORS as e:
                logger.warning("{0}: {1}".format(CONNECTION_ERROR, e))
                logger.info(DISCONNECTED)


def with_sync_paused(func):
    """
    Decorator which pauses syncing before a method call, resumes afterwards. This
    should only be used to decorate Maestral methods.
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


def if_connected(func):
    """
    Decorator which checks for connection to Dropbox API before a method call. This can be
    used to decorate any function.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # pause syncing
        try:
            res = func(*args, **kwargs)
            return res
        except CONNECTION_ERRORS as e:
            logger.warning("{0}: {1}".format(CONNECTION_ERROR, e))
            return False

    return wrapper


class Maestral(object):
    """
    An open source Dropbox client for macOS and Linux to syncing a local folder
    with your Dropbox account. It currently only supports excluding top-level
    folders from the sync.

    Maestral gracefully handles lost internet connections and will detect
    changes in between sessions or while Maestral has been idle.
    """

    FIRST_SYNC = (not CONF.get("internal", "lastsync") or
                  CONF.get("internal", "cursor") == "" or
                  not osp.isdir(CONF.get("main", "path")))

    download_complete_signal = signal("download_complete_signal")

    def __init__(self, run=True):

        self.client = MaestralApiClient()
        self.get_account_info()

        # monitor needs to be created before any decorators are called
        self.monitor = MaestralMonitor(self.client)
        self.sync = self.monitor.sync

        if self.FIRST_SYNC:
            self.set_dropbox_directory()
            self.select_excluded_folders()

            CONF.set("internal", "cursor", "")
            CONF.set("internal", "lastsync", None)

            success = self.sync.get_remote_dropbox()
            if success:
                CONF.set("internal", "lastsync", time.time())

        if run:
            self.start_sync()

    @property
    def syncing(self):
        """Bool indicating if syncing is running or paused."""
        return self.monitor.running.is_set()

    @property
    def connected(self):
        """Bool indicating if Dropbox servers can be reached."""
        return self.monitor.connected.is_set()

    @property
    def notify(self):
        """Bool indicating if notifications are enabled."""
        return self.sync.notify.enabled

    @notify.setter
    def notify(self, boolean):
        """Setter: Bool indicating if notifications are enabled."""
        self.sync.notify.enabled = boolean

    @if_connected
    def get_account_info(self):
        res = self.client.get_account_info()
        return res

    @if_connected
    def get_remote_dropbox_async(self, dbx_path):
        """
        Runs `sync.get_remote_dropbox` in the background, downloads the full
        Dropbox folder `dbx_path` to the local drive. The folder is temporarily
        excluded from the local observer to prevent duplicate uploads.

        :param str dbx_path: Path to folder on Dropbox.
        """
        if dbx_path is "":  # pause all syncing while downloading root folder
            self.monitor.pause()
        else:  # exclude only specific folder otherwise
            self.monitor.flagged.append(self.sync.to_local_path(dbx_path))

        self.download_thread = Thread(
                target=folder_download_worker,
                args=(self.sync, dbx_path),
                name="MaestralFolderDownloader")
        self.download_thread.start()

        def callback(*args):  # blinker signal will carry the sender as argument
            if dbx_path is "":
                self.monitor.resume()  # resume all syncing
            else:
                # remove folder from excluded list
                self.monitor.flagged.remove(self.sync.to_local_path(dbx_path))

        self.download_complete_signal.connect(callback)

    def start_sync(self, overload=None):
        """
        Creates syncing threads and starts syncing.
        """
        self.monitor.start()
        logger.info(IDLE)

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
        Unlink the configured Dropbox account but leave all downloaded files
        in place. All syncing metadata will be removed as well.
        """
        self.stop_sync()
        self.client.unlink()

        os.remove(self.sync.rev_file_path)

        CONF.reset_to_defaults()

        logger.info("Unlinked Dropbox account.")

    def exclude_folder(self, dbx_path):
        """
        Excludes folder from sync and deletes local files. It is safe to call
        this method with folders which have already been excluded.

        :param str dbx_path: Dropbox folder to exclude.
        """

        dbx_path = dbx_path.lower()

        # add folder's Dropbox path to excluded list
        folders = CONF.get("main", "excluded_folders")
        if dbx_path not in folders:
            folders.append(dbx_path)

        CONF.set("main", "excluded_folders", folders)
        self.sync.set_local_rev(dbx_path, None)

        # remove folder from local drive
        local_path = self.sync.to_local_path(dbx_path)
        local_path_cased = path_exists_case_insensitive(local_path)
        logger.debug("Deleting folder {0}.".format(local_path_cased))
        if osp.isdir(local_path_cased):
            shutil.rmtree(local_path_cased)

    @if_connected
    def include_folder(self, dbx_path):
        """
        Includes folder in sync and downloads in the background. It is safe to
        call this method with folders which have already been included, they
        will not be downloaded again.

        :param str dbx_path: Dropbox folder to include.
        :return: `True` or `False` on success or failure, respectively.
        :rtype: bool
        """

        dbx_path = dbx_path.lower()

        # remove folder's Dropbox path from excluded list
        folders = CONF.get("main", "excluded_folders")
        if dbx_path in folders:
            new_folders = [x for x in folders if osp.normpath(x) != dbx_path]
        else:
            logger.debug("Folder was already included, nothing to do.")
            return

        self.sync.excluded_folders = new_folders
        CONF.set("main", "excluded_folders", new_folders)

        # download folder contents from Dropbox
        logger.debug("Downloading added folder.")
        self.get_remote_dropbox_async(dbx_path)

    @if_connected
    def select_excluded_folders(self):
        """
        Gets all top level folder paths from Dropbox and asks user to include
        or exclude. On initial sync, this does not trigger any syncing. Call
        `get_remote_dropbox` or `get_remote_dropbox_async` instead.

        :return: List of excluded folders.
        :rtype: list
        """

        excluded_folders = []
        included_folders = []

        # get all top-level Dropbox folders
        # if this raises an error, we have a serious problem => crash
        result = self.client.list_folder("", recursive=False)

        # paginate through top-level folders, ask to exclude
        for entry in result.entries:
            if isinstance(entry, files.FolderMetadata):
                yes = yesno("Exclude '%s' from sync?" % entry.path_display, False)
                if yes:
                    excluded_folders.append(entry.path_lower)
                else:
                    included_folders.append(entry.path_lower)

        # detect and apply changes
        if not self.FIRST_SYNC:
            for path in excluded_folders:
                self.exclude_folder(path)
            for path in included_folders:
                self.include_folder(path)  # may raise ConnectionError

        CONF.set("main", "excluded_folders", excluded_folders)

        return excluded_folders

    @with_sync_paused
    def set_dropbox_directory(self, new_path=None):
        """
        Change or set local dropbox directory. This moves all local files to
        the new location. If a file or directory already exists at this location,
        it will be overwritten.

        :param str new_path: Path to local Dropbox folder. If not given, the
            user will be prompted to input the path.
        """

        # get old and new paths
        old_path = CONF.get("main", "path")
        if new_path is None:
            new_path = self._ask_for_path(default=old_path)

        if osp.exists(old_path) and osp.exists(new_path):
            if osp.samefile(old_path, new_path):
                # nothing to do
                return

        # move old directory or create new directory
        if osp.exists(new_path):
            shutil.rmtree(new_path)

        if osp.isdir(old_path):
            shutil.move(old_path, new_path)
        else:
            os.makedirs(new_path)

        # update config file and client
        self.sync.dropbox_path = new_path
        CONF.set("main", "path", new_path)

    def get_dropbox_directory(self):
        """
        Returns the path to the local Dropbox directory.
        """
        return self.sync.dropbox_path

    def _ask_for_path(self, default="~/Dropbox"):
        """
        Asks for Dropbox path.
        """
        default = osp.expanduser(default)
        msg = "Please give Dropbox folder location or press enter for default [%s]:" % default
        res = input(msg).strip().strip("'")

        if res == "":
            dropbox_path = default
        else:
            dropbox_path = osp.expanduser(res)

        if osp.exists(dropbox_path):
            msg = "Directory '%s' already exist. Do you want to overwrite it?" % dropbox_path
            yes = yesno(msg, True)
            if yes:
                return dropbox_path
            else:
                dropbox_path = self._ask_for_path()

        return dropbox_path

    def __repr__(self):
        if self.connected:
            email = CONF.get("account", "email")
            account_type = CONF.get("account", "type")
            inner = "{0}, {1})".format(email, account_type)
        else:
            inner = DISCONNECTED

        return "{0}({1})".format(self.__class__.__name__, inner)


def yesno(message, default):
    """Handy helper function to ask a yes/no question.

    A blank line returns the default, and answering
    y/yes or n/no returns True or False.
    Retry on unrecognized answer.
    Special answers:
    - q or quit exits the program
    - p or pdb invokes the debugger
    """
    if default:
        message += " [Y/n] "
    else:
        message += " [N/y] "
    while True:
        answer = input(message).strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        if answer in ("q", "quit"):
            print("Exit")
            raise SystemExit(0)
        if answer in ("p", "pdb"):
            import pdb
            pdb.set_trace()
        print("Please answer YES or NO.")


if __name__ == "__main__":
    mdbx = Maestral()
