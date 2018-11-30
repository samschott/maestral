# -*- coding: utf-8 -*-

__version__ = "0.1.0"
__author__ = "Sam Schott"

import os
import os.path as osp
import time
import requests
import shutil
import functools
from dropbox import files

from sisyphosdbx.client import SisyphosClient
from sisyphosdbx.monitor import Monitor
from sisyphosdbx.config.main import CONF
from sisyphosdbx.config.base import get_home_dir

import logging

logger = logging.getLogger(__name__)

for logger_name in ("sisyphosdbx.monitor"):
    sdbx_logger = logging.getLogger(logger_name)
    sdbx_logger.addHandler(logging.StreamHandler())
    sdbx_logger.setLevel(logging.INFO)


class SDBXConnectionError(Exception):
    """Raised on requests.exceptions.RequestException."""
    pass


def with_sync_paused(f):
    """
    Decorator which pauses syncing before a method call, resumes afterwards.
    """
    @functools.wraps(f)
    def wrapper(self, *args, **kwargs):
        # pause syncing
        resume = False
        if self.syncing:
            self.pause_sync()
            resume = True
        ret = f(self, *args, **kwargs)
        # resume syncing if previously paused
        if resume:
            self.resume_sync()
        return ret
    return wrapper


def if_connected(f):
    """
    Decorator which checks for connection to Dropbox API before a method call.
    """

    error_msg = ("Cannot connect to Dropbox servers. Please  check" +
                 "your internet connection and try again later.")

    @functools.wraps(f)
    def wrapper(self, *args, **kwargs):
        # pause syncing
        if not self.connected:
            raise SDBXConnectionError(error_msg)
        try:
            ret = f(self, *args, **kwargs)
            return ret
        except requests.exceptions.RequestException:
            raise SDBXConnectionError(error_msg)
        return
    return wrapper


class SisyphosDBX(object):

    FIRST_SYNC = (not CONF.get("internal", "lastsync") or
                  CONF.get("internal", "cursor") == "" or
                  not osp.isdir(CONF.get("main", "path")))
    paused_by_user = False

    def __init__(self, run=True):

        self.client = SisyphosClient()
        # monitor needs to be created before any decorators are called
        self.monitor = Monitor(self.client)
        self.monitor.stopped_by_user = True  # hold off on syncing anything

        if self.FIRST_SYNC:
            self.set_dropbox_directory()
            self.select_excluded_folders()

            CONF.set("internal", "cursor", "")
            CONF.set("internal", "lastsync", None)

            self.get_remote_dropbox()
            CONF.set("internal", "lastsync", time.time())

        if run:
            self.resume_sync()

    @property
    def syncing(self):
        return self.monitor.running.is_set()

    @property
    def connected(self):
        return self.monitor.connected.is_set()

    @if_connected
    def get_remote_dropbox(self):
        self.client.get_remote_dropbox()

    def pause_sync(self):
        self.monitor.stopped_by_user = True
        self.monitor.stop()

    def resume_sync(self):
        self.monitor.stopped_by_user = False
        self.monitor.start()

    @with_sync_paused
    def exclude_folder(self, dbx_path):
        """
        Excludes folder from sync and deletes local files. It is safe to call
        this method with folders which have alerady been excluded.

        :param str dbx_path: Dropbox folder to exclude.
        """

        dbx_path = dbx_path.lower()

        # add folder's Dropbox path to excluded list
        folders = CONF.get("main", "excluded_folders")
        if dbx_path not in folders:
            folders.append(dbx_path)

        self.client.excluded_folders = folders
        CONF.set("main", "excluded_folders", folders)

        # remove folder from local drive
        local_path = self.client.to_local_path(dbx_path)
        if osp.isdir(local_path):
            shutil.rmtree(local_path)

        self.client.set_local_rev(dbx_path, None)

    @if_connected
    @with_sync_paused
    def include_folder(self, dbx_path):
        """
        Includes folder in sync and downloads it. It is safe to call
        this method with folders which have alerady been included, they will
        not be downloaded again.

        :param str dbx_path: Dropbox folder to include.
        """

        dbx_path = dbx_path.lower()

        # remove folder's Dropbox path from excluded list
        folders = CONF.get("main", "excluded_folders")
        if dbx_path in folders:
            new_folders = [x for x in folders if osp.normpath(x) != dbx_path]
        else:
            logger.debug("Folder was already inlcuded, nothing to do.")
            return

        self.client.excluded_folders = new_folders
        CONF.set("main", "excluded_folders", new_folders)

        # download folder and contents from Dropbox
        logger.debug("Downloading folder.")
        self.client.get_remote_dropbox(path=dbx_path)  # may raise ConnectionError

    @if_connected
    def select_excluded_folders(self):
        """
        Gets all top level folder paths from Dropbox and asks user to inlcude
        or exclude.

        :return: List of excluded folders.
        :rtype: list
        """

        old_folders = CONF.get("main", "excluded_folders")
        new_folders = []

        # get all top-level Dropbox folders
        results = self.client.list_folder("", recursive=False)
        results_dict = self.client.flatten_results_list(results)

        # paginate through top-level folders, ask to exclude
        for entry in results_dict.values():
            if isinstance(entry, files.FolderMetadata):
                yes = yesno("Exclude '%s' from sync?" % entry.path_display, False)
                if yes:
                    new_folders.append(entry.path_lower)

        # detect and apply changes
        removed_folders = set(old_folders) - set(new_folders)

        if not self.FIRST_SYNC:
            for folder in new_folders:
                self.exclude_folder(folder)

            for folder in removed_folders:
                self.include_folder(folder)  # may raise ConnectionError

        self.client.excluded_folders = new_folders
        CONF.set("main", "excluded_folders", new_folders)

    @with_sync_paused
    def set_dropbox_directory(self, new_path=None):
        """
        Change or set local dropbox directory. This moves all local files to
        the new location. If a file or directory alreay exists at this location,
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
        if osp.isdir(old_path):
            if osp.exists(new_path):
                shutil.rmtree(new_path)
            shutil.move(old_path, new_path)
        else:
            os.makedirs(new_path)

        # update config file and client
        self.client.dropbox_path = new_path
        self.client.rev_file = osp.join(new_path, ".dropbox")
        CONF.set("main", "path", new_path)

    def _ask_for_path(self, default="~/Dropbox"):
        """
        Asks for Dropbox path.
        """
        default = os.path.expanduser(default)
        msg = "Please give Dropbox folder location or press enter for default [%s]:" % default
        dropbox_path = input(msg).strip().strip("'")
        dropbox_path = osp.abspath(osp.expanduser(dropbox_path))

        if dropbox_path == "":
            dropbox_path = default
        elif osp.exists(dropbox_path):
            msg = "Directory '%s' alredy exist. Should we overwrite?" % dropbox_path
            yes = yesno(msg, True)
            if yes:
                return dropbox_path
            else:
                dropbox_path = self._ask_for_path()
        else:
            return dropbox_path

    def __repr__(self):
        return "SisyphosDBX(account_id={0}, user_id={1})".format(
                self.client.auth.account_id, self.client.auth.user_id)

    def __str__(self):
        if self.connected:
            email = CONF.get("account", "mail")
            account_type = CONF.get("account", "type")
            inner = "{0}, {1})".format(email, account_type)
        else:
            inner = "Connecting..."

        return "SisyphosDBX({0})".format(inner)


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


def main():
    sdbx = SisyphosDBX()
    sdbx.start_sync()


if __name__ == "__main__":
    main()
