#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

import os.path as osp
import shutil
from qtpy import QtGui, QtCore, QtWidgets, uic

from sisyphosdbx.config.main import CONF


_root = QtCore.QFileInfo(__file__).absolutePath()


class TestClient(object):

    dropbox_path = osp.expanduser('~/Dropbox')

    def is_excluded(self, *args, **kwargs):
        return False

    def list_folder(self, *args, **kwargs):
        return None

    def flatten_result_list(self, *args, **kwargs):
        return {'Test Folder 1': None, 'Test Folder 2': None}


class FolderItem(QtWidgets.QListWidgetItem):

    def __init__(self, icon, text, is_included, parent=None):
        super(self.__class__, self).__init__(icon, text, parent=parent)

        self.path = text

        checked_state = 2 if is_included else 0
        self.setCheckState(checked_state)

    def setIncluded(self, is_included):
        checked_state = 2 if is_included else 0
        self.setCheckState(checked_state)

    def isIncluded(self):
        checked_state = self.checkState()
        return (True if checked_state == 2 else False)


class FoldersDialog(QtWidgets.QDialog):

    def __init__(self, parent=None, client=TestClient()):
        super(self.__class__, self).__init__(parent=parent)
        # load user interface layout from .ui file
        uic.loadUi(osp.join(_root, "folders_dialog.ui"), self)

        self.client = client
        self.accept_button = self.buttonBox.buttons()[0]
        self.accept_button.setText('Update')

        # populate UI
        self.folder_icon = QtGui.QIcon(_root + "/resources/GenericFolderIcon.icns")
        if self.client is not None:
            self.populate_folders_list()

        # connect callbacks
        self.buttonBox.accepted.connect(self.on_accepted)

    def populate_folders_list(self):

        # remove old entries
        self.listWidgetFolders.clear()

        # add new entries
        root_folders = self.client.list_folder("")
        if root_folders is False:
            self.listWidgetFolders.addItem("Unable to connect")
            self.accept_button.setEnabled(False)
        else:
            self.accept_button.setEnabled(True)

            self.folder_dict = self.client.flatten_result_list(root_folders)

            self.folder_items = []
            for path in self.folder_dict:
                is_included = not self.client.is_excluded(path)
                item = FolderItem(self.folder_icon, path, is_included)
                self.folder_items.append(item)

            for item in self.folder_items:
                self.listWidgetFolders.addItem(item)

    def on_accepted(self):
        """
        Apply changes to local Dropbox folder. Delete exlcuded folders,
        download newly included folders.
        """

        old_excluded = CONF.get("main", "excluded_folders")
        new_excluded = []

        for item in self.folder_items:
            if not item.isIncluded():
                new_excluded.append(item.path.lower())

        if isinstance(self.client, TestClient):
            print(new_excluded)
            return

        # detect and apply changes
        new_included = set(old_excluded) - set(new_excluded)

        self.client.excluded_folders = new_excluded
        CONF.set("main", "excluded_folders", new_excluded)

        for path in new_excluded:
            local_path = self.client.to_local_path(path)
            if osp.isdir(local_path):
                shutil.rmtree(local_path)

            self.client.set_local_rev(path, None)

        for path in new_included:
            self.client.get_remote_dropbox(path=path)
