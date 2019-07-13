# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import os
import os.path as osp
from subprocess import Popen
import platform
import time
from PyQt5 import QtGui, QtCore, QtWidgets, uic

from maestral.main import __version__, __author__, __url__
from maestral.utils.autostart import AutoStart
from maestral.config.main import CONF
from maestral.config.base import get_home_dir
from maestral.gui.folders_dialog import FoldersDialog
from maestral.gui.resources import (get_native_item_icon, UNLINK_DIALOG_PATH,
                                    SETTINGS_WINDOW_PATH)


class UnlinkDialog(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        # load user interface layout from .ui file
        uic.loadUi(UNLINK_DIALOG_PATH, self)
        self.buttonBox.buttons()[0].setText('Unlink')


class SettingsWindow(QtWidgets.QWidget):

    def __init__(self, mdbx, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        # load user interface layout from .ui file
        uic.loadUi(SETTINGS_WINDOW_PATH, self)
        # self.setFixedSize(560, 320)

        self.mdbx = mdbx
        self.folders_dialog = FoldersDialog(self.mdbx, parent=self)
        self.unlink_dialog = UnlinkDialog(self)

        # populate app section
        self.autostart = AutoStart()
        self.checkBoxStartup.setChecked(self.autostart.enabled)
        self.checkBoxStartup.stateChanged.connect(self.on_start_on_login_clicked)
        self.checkBoxNotifications.setChecked(self.mdbx.notify)
        self.checkBoxNotifications.stateChanged.connect(self.on_notifications_clicked)

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
        self.unlink_dialog.accepted.connect(self.on_unlink)

        # populate about section
        year = time.localtime().tm_year
        self.labelVersion.setText(self.labelVersion.text().format(__version__))
        self.labelUrl.setText(self.labelUrl.text().format(__url__))
        self.labelCopyright.setText(self.labelCopyright.text().format(year, __author__))

    def setup_combobox(self):

        parent_dir = osp.split(self.mdbx.sync.dropbox_path)[0]
        relative_path = self.rel_path(parent_dir)

        folder_icon = get_native_item_icon(parent_dir)
        self.comboBoxDropboxPath.addItem(folder_icon, relative_path)

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
            self.comboBoxDropboxPath.setItemIcon(0, get_native_item_icon(new_location))

            new_path = osp.join(new_location, 'Dropbox')
            self.mdbx.set_dropbox_directory(new_path)

    def on_unlink(self):
        """Unlinks the user's account and restarts the setup dialog."""

        self.mdbx.unlink()  # unlink
        pid = os.getpid()  # get ID of current process

        # wait for current process to quit and then restart Maestral
        if platform.system() == "Darwin":
            Popen("lsof -p {0} +r 1 &>/dev/null; maestral-gui".format(pid), shell=True)
        elif platform.system() == "Linux":
            Popen("tail --pid={0} -f /dev/null; maestral-gui".format(pid), shell=True)

        QtCore.QCoreApplication.quit()

    def on_start_on_login_clicked(self, state):
        if state == 0:
            self.autostart.disable()
        elif state == 2:
            self.autostart.enable()

    def on_notifications_clicked(self, state):
        if state == 0:
            self.mdbx.notify = False
        elif state == 2:
            self.mdbx.notify = True

    @staticmethod
    def rel_path(path):
        """
        Returns the path relative to the users directory, or the absolute
        path if not in a user directory.
        """
        usr = osp.abspath(osp.join(get_home_dir(), osp.pardir))
        if osp.commonprefix([path, usr]) == usr:
            return osp.relpath(path, usr)
        else:
            return path
