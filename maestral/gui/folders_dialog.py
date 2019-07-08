#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

import os.path as osp
from PyQt5 import QtGui, QtCore, QtWidgets, uic

from dropbox import files

from maestral.config.main import CONF


_root = QtCore.QFileInfo(__file__).absolutePath()


class FolderItem(QtWidgets.QListWidgetItem):

    def __init__(self, icon, name, is_included, parent=None):
        super(self.__class__, self).__init__(icon, name, parent=parent)

        self.name = name

        checked_state = 2 if is_included else 0
        self.setCheckState(checked_state)

    def setIncluded(self, is_included):
        checked_state = 2 if is_included else 0
        self.setCheckState(checked_state)

    def isIncluded(self):
        checked_state = self.checkState()
        return True if checked_state == 2 else False


class FoldersDialog(QtWidgets.QDialog):

    path_items = []

    def __init__(self, mdbx,  parent=None):
        super(self.__class__, self).__init__(parent=parent)
        # load user interface layout from .ui file
        uic.loadUi(osp.join(_root, "folders_dialog.ui"), self)
        self.folder_icon = QtGui.QIcon(_root + "/resources/GenericFolderIcon.icns")

        self.mdbx = mdbx
        self.accept_button = self.buttonBox.buttons()[0]
        self.accept_button.setText('Update')

        # connect callbacks
        self.buttonBox.accepted.connect(self.on_accepted)
        self.buttonBox.rejected.connect(self.on_rejected)

    def populate_folders_list(self):

        self.listWidgetFolders.addItem("Loading your folders...")

        # add new entries
        result = self.mdbx.client.list_folder("", recursive=False)
        self.listWidgetFolders.clear()
        self.path_items = []

        if result is False:
            self.listWidgetFolders.addItem("Unable to connect")
            self.accept_button.setEnabled(False)
        else:
            self.accept_button.setEnabled(True)

            for entry in result.entries:
                if isinstance(entry, files.FolderMetadata):
                    inc = not self.mdbx.dbx_sync.is_excluded_by_user(entry.path_lower)
                    item = FolderItem(self.folder_icon, entry.name, inc)
                    self.path_items.append(item)

            for item in self.path_items:
                self.listWidgetFolders.addItem(item)

    def on_accepted(self):
        """
        Apply changes to local Dropbox folder.
        """

        excluded_folders = []
        included_folders = []

        for item in self.path_items:
            if not item.isIncluded():
                excluded_folders.append("/" + item.name.lower())
            elif item.isIncluded():
                included_folders.append("/" + item.name.lower())

        for path in excluded_folders:
            self.mdbx.exclude_folder(path)
        for path in included_folders:
            self.mdbx.include_folder(path)

        CONF.set("main", "excluded_folders", excluded_folders)

    def on_rejected(self):
        pass
