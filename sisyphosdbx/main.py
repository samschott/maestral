# -*- coding: utf-8 -*-

import os.path as osp
import shutil
from sisyphosdbx.client import SisyphosClient
from sisyphosdbx.monitor import LocalMonitor, RemoteMonitor
from sisyphosdbx.configure import Configure
from sisyphosdbx.config.main import CONF

import logging

logger = logging.getLogger()
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


class SisyphosDBX(object):

    FIRST_SYNC = (not CONF.get('internal', 'lastsync') or
                  CONF.get('internal', 'cursor') == '' or
                  not osp.isdir(CONF.get('main', 'path')))

    def __init__(self):

        self.client = SisyphosClient()
        self.start_sync()

    def on_firstsync(self):

        self.configure = Configure(self.client)
        self.configure.set_dropbox_directory()
        self.configure.ask_for_excluded_folders()
        self.client.excluded_folders = CONF.get('main', 'excluded_folders')
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

    def stop_sync(self):

        self.remote.stop()
        self.local.stop()

    def exclude_folder(self, dbx_path):

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

        self.set_local_rev(dbx_path, None)

    def include_folder(self, dbx_path):

        # remove folder's Dropbox path from excluded list
        folders = CONF.get('main', 'excluded_folders')
        if dbx_path in folders:
            new_folders = [x for x in folders if osp.normpath(x) == dbx_path]
        else:
            new_folders = folders

        self.client.excluded_folders = new_folders
        CONF.set('main', 'excluded_folders', new_folders)

        # download folder and contents from Dropbox
        self.client.get_remote_dropbox(path=dbx_path)
