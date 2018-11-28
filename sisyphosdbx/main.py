# -*- coding: utf-8 -*-

__version__ = "0.1.0"
__author__ = "Sam Schott"

import os
import os.path as osp
import shutil
import functools
from dropbox import files

from sisyphosdbx.client import SisyphosClient
from sisyphosdbx.monitor import LocalMonitor, RemoteMonitor
from sisyphosdbx.config.main import CONF
from sisyphosdbx.config.base import get_home_dir

import logging

logger = logging.getLogger()
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


def pause_syncing(f):
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


class SisyphosDBX(object):

    FIRST_SYNC = (not CONF.get('internal', 'lastsync') or
                  CONF.get('internal', 'cursor') == '' or
                  not osp.isdir(CONF.get('main', 'path')))
    syncing = False

    def __init__(self):

        self.client = SisyphosClient()
        self.start_sync()

    def on_firstsync(self):

        self.set_dropbox_directory()
        self.select_excluded_folders()
        CONF.set('internal', 'cursor', '')
        CONF.set('internal', 'lastsync', None)

        result = False
        while not result:
            result = self.client.get_remote_dropbox()

    def start_sync(self):

        if self.FIRST_SYNC:
            self.on_firstsync()

        self.remote = RemoteMonitor(self.client)
        self.local = LocalMonitor(self.client)

        if not self.FIRST_SYNC:
            self.local.upload_local_changes_after_inactive()

        self.remote.start()
        self.local.start()

        self.syncing = True

    def pause_sync(self):

        if not self.syncing:
            return

        self.remote.stop()
        self.local.stop()

        self.syncing = False

    def resume_sync(self):

        if self.syncing:
            return

        self.local.upload_local_changes_after_inactive()

        self.remote.start()
        self.local.start()

        self.syncing = True

    @pause_syncing
    def exclude_folder(self, dbx_path):

        dbx_path = dbx_path.lower()

        # add folder's Dropbox path to excluded list
        folders = CONF.get('main', 'excluded_folders')
        if dbx_path not in folders:
            folders.append(dbx_path)

        self.client.excluded_folders = folders
        CONF.set('main', 'excluded_folders', folders)

        # remove folder from local drive
        local_path = self.client.to_local_path(dbx_path)
        if osp.isdir(local_path):
            shutil.rmtree(local_path)

        self.client.set_local_rev(dbx_path, None)

    @pause_syncing
    def include_folder(self, dbx_path):

        dbx_path = dbx_path.lower()

        # remove folder's Dropbox path from excluded list
        folders = CONF.get('main', 'excluded_folders')
        if dbx_path in folders:
            new_folders = [x for x in folders if osp.normpath(x) != dbx_path]
        else:
            new_folders = folders  # no change in folders to sync
            return

        self.client.excluded_folders = new_folders
        CONF.set('main', 'excluded_folders', new_folders)

        # download folder and contents from Dropbox
        self.client.get_remote_dropbox(path=dbx_path)

    def select_excluded_folders(self):
        """
        Gets all top level folder paths from Dropbox and asks user to inlcude
        or exclude.

        :return: List of excluded folders.
        :rtype: list
        """

        old_folders = CONF.get('main', 'excluded_folders')
        new_folders = []

        result = self.client.dbx.files_list_folder("", recursive=False)

        for entry in result.entries:
            if isinstance(entry, files.FolderMetadata):
                yes = yesno("Exclude '%s' from sync?" % entry.path_display, False)
                if yes:
                    new_folders.append(entry.path_lower)

        added_folders = set(new_folders) - set(old_folders)
        removed_folders = set(old_folders) - set(new_folders)

        if not self.FIRST_SYNC:
            for folder in added_folders:
                self.exclude_folder(folder)

            for folder in removed_folders:
                self.include_folder(folder)

        self.client.excluded_folders = new_folders
        CONF.set('main', 'excluded_folders', new_folders)

    @pause_syncing
    def set_dropbox_directory(self, new_path=None):
        """
        Change or set local dropbox directory. This moves all local files to
        the new location. If a file or directory alreay exists at this location,
        it will be overwritten.

        :param str new_path: Path to local Dropbox folder. If not given, the
            user will be prompted to input the path.
        """

        # get old and new paths
        old_path = CONF.get('main', 'path')
        if new_path is None:
            new_path = self._ask_for_path()

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
        CONF.set('main', 'path', new_path)

    def _ask_for_path(self):
        """
        Asks for Dropbox path.
        """
        msg = "Please give Dropbox folder location or press enter for default [~/Dropbox]:"
        dropbox_path = input(msg).strip().strip("'")
        dropbox_path = osp.abspath(osp.expanduser(dropbox_path))

        if dropbox_path == "":
            dropbox_path = osp.join(get_home_dir(), 'Dropbox')
        elif osp.exists(dropbox_path):
            msg = "Directory '%s' alredy exist. Should we overwrite?" % dropbox_path
            yes = yesno(msg, True)
            if yes:
                return dropbox_path
            else:
                dropbox_path = self._ask_for_path()
        else:
            return dropbox_path


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
        message += ' [Y/n] '
    else:
        message += ' [N/y] '
    while True:
        answer = input(message).strip().lower()
        if not answer:
            return default
        if answer in ('y', 'yes'):
            return True
        if answer in ('n', 'no'):
            return False
        if answer in ('q', 'quit'):
            print('Exit')
            raise SystemExit(0)
        if answer in ('p', 'pdb'):
            import pdb
            pdb.set_trace()
        print('Please answer YES or NO.')


def main():
    sdbx = SisyphosDBX()
    sdbx.start_sync()


if __name__ == '__main__':
    main()
