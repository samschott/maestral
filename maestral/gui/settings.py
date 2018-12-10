# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

import os.path as osp
import time
from PyQt5 import QtGui, QtCore, QtWidgets, uic

from ..main import __version__, __author__, __url__
from ..utils.autostart import AutoStart
from ..config.main import CONF
from ..config.base import get_home_dir
from .folders_dialog import FoldersDialog

_root = QtCore.QFileInfo(__file__).absolutePath()


class UnlinkDialog(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        # load user interface layout from .ui file
        uic.loadUi(osp.join(_root, "unlink_dialog.ui"), self)
        self.setFixedSize(460, 145)
        self.buttonBox.buttons()[0].setText('Unlink')


class SettingsWindow(QtWidgets.QWidget):

    def __init__(self, mdbx, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        # load user interface layout from .ui file
        uic.loadUi(osp.join(_root, "settings.ui"), self)
        self.setFixedSize(580, 380)
        self.generic_folder_icon = QtGui.QIcon(_root + "/resources/GenericFolderIcon.icns")
        self.home_folder_icon = QtGui.QIcon(_root + "/resources/HomeFolderIcon.icns")

        self.mdbx = mdbx
        self.folders_dialog = FoldersDialog(self.mdbx, parent=self)
        self.unlink_dialog = UnlinkDialog(self)

        # populate app section
        self.autostart = AutoStart()
        self.checkBoxStartup.setChecked(self.autostart.enabled)
        self.checkBoxStartup.stateChanged.connect(self.on_startup_clicked)
        self.checkBoxNotifications.setChecked(self.mdbx.notify)
        self.checkBoxNotifications.stateChanged.connect(self.on_notify_clicked)

        # populate sync section
        self.setup_combobox()
        self.pushButtonExcludedFolders.clicked.connect(self.folders_dialog.open)
        self.pushButtonExcludedFolders.clicked.connect(self.folders_dialog.populate_folders_list)

        # populate account section
        self.labelAccountEmail.setText(CONF.get("account", "email"))
        usage_type = CONF.get("account", "usage_type")
        if usage_type == "team":
            self.labelSpaceUsage1.setText("Your team's space:")
        elif usage_type == "individual":
            self.labelSpaceUsage1.setText("Your space:")
        self.labelSpaceUsage2.setText(CONF.get("account", "usage"))
        self.pushButtonUnlink.clicked.connect(self.unlink_dialog.open)
        self.unlink_dialog.accepted.connect(self.mdbx.unlink)

        # populate about section
        placeholder = self.labelAbout.text()
        year = time.localtime().tm_year
        about_text = placeholder.format(__version__, __url__, year, __author__)
        self.labelAbout.setText(about_text)

    def setup_combobox(self):

        parent_dir = osp.split(self.mdbx.client.dropbox_path)[0]
        short_path = self.rel_path(parent_dir)

        if parent_dir == get_home_dir():
            self.comboBoxDropboxPath.addItem(self.home_folder_icon, short_path)
        else:
            self.comboBoxDropboxPath.addItem(self.generic_folder_icon, short_path)
        self.comboBoxDropboxPath.insertSeparator(1)
        self.comboBoxDropboxPath.addItem(QtGui.QIcon(), "Other...")
        self.comboBoxDropboxPath.currentIndexChanged.connect(self.on_combobox)
        msg = ('Choose a location for your Dropbox. A folder named "Dropbox"' +
               ' will be created inside the folder you select.')
        self.dropbox_folder_dialog = QtWidgets.QFileDialog(self, caption=msg)
        self.dropbox_folder_dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptOpen)
        self.dropbox_folder_dialog.setFileMode(QtWidgets.QFileDialog.Directory)
        self.dropbox_folder_dialog.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
        self.dropbox_folder_dialog.fileSelected.connect(self.on_new_dbx_folder)
        self.dropbox_folder_dialog.rejected.connect(
                lambda: self.comboBoxDropboxPath.setCurrentIndex(0))

    def on_combobox(self, idx):
        if idx == 2:
            self.dropbox_folder_dialog.open()

    def on_new_dbx_folder(self, new_location):

        self.comboBoxDropboxPath.setCurrentIndex(0)
        if not new_location == '':
            self.comboBoxDropboxPath.setItemText(0, self.rel_path(new_location))
            if new_location == get_home_dir():
                self.comboBoxDropboxPath.setItemIcon(0, self.home_folder_icon)
            else:
                self.comboBoxDropboxPath.setItemIcon(0, self.generic_folder_icon)

            new_path = osp.join(new_location, 'Dropbox')
            self.mdbx.set_dropbox_directory(new_path)

    def rel_path(self, path):
        """
        Returns the path relative to the users directory, or the absolute
        path if not in a user directory.
        """
        usr = osp.abspath(osp.join(get_home_dir(), osp.pardir))
        if osp.commonprefix([path, usr]) == usr:
            return osp.relpath(path, usr)
        else:
            return path

    def on_startup_clicked(self, state):
        if state == 0:
            self.autostart.disable()
        elif state == 2:
            self.autostart.enable()

    def on_notify_clicked(self, state):
        if state == 0:
            self.mdbx.notify = False
        elif state == 2:
            self.mdbx.notify = True
