# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import os.path as osp
import time
from distutils.version import LooseVersion
from PyQt5 import QtGui, QtCore, QtWidgets, uic

from maestral.main import __version__, __author__, __url__
from maestral.errors import CONNECTION_ERRORS
from maestral.utils.autostart import AutoStart
from maestral.config.main import CONF
from maestral.config.base import get_home_dir
from maestral.gui.folders_dialog import FoldersDialog
from maestral.gui.resources import (get_native_item_icon, UNLINK_DIALOG_PATH,
                                    SETTINGS_WINDOW_PATH, APP_ICON_PATH, FACEHOLDER_PATH)
from maestral.gui.utils import (get_scaled_font, isDarkWindow, quit_and_restart_maestral,
                                LINE_COLOR_DARK, LINE_COLOR_LIGHT, icon_to_pixmap,
                                get_masked_image)


class UnlinkDialog(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        # load user interface layout from .ui file
        uic.loadUi(UNLINK_DIALOG_PATH, self)
        self.setModal(True)

        self.setWindowFlags(QtCore.Qt.Sheet)

        self.buttonBox.buttons()[0].setText("Unlink")
        self.titleLabel.setFont(get_scaled_font(bold=True))
        self.infoLabel.setFont(get_scaled_font(scaling=0.9))

        icon = QtGui.QIcon(APP_ICON_PATH)
        pixmap = icon_to_pixmap(icon, self.iconLabel.width(), self.iconLabel.height())
        self.iconLabel.setPixmap(pixmap)


class SettingsWindow(QtWidgets.QWidget):

    def __init__(self, mdbx, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        uic.loadUi(SETTINGS_WINDOW_PATH, self)
        self.update_dark_mode()
        self.setWindowFlags(QtCore.Qt.WindowStaysOnTopHint)
        self.adjustSize()

        self.mdbx = mdbx
        self.folders_dialog = FoldersDialog(self.mdbx, parent=self)
        self.unlink_dialog = UnlinkDialog(self)

        self.labelAccountName.setFont(get_scaled_font(1.5))
        self.labelAccountInfo.setFont(get_scaled_font(0.85))
        self.labelSpaceUsage.setFont(get_scaled_font(0.85))

        # populate account name
        account_display_name = CONF.get("account", "display_name")
        # if the display name is longer than 230 pixels, reduce font-size
        if LooseVersion(QtCore.QT_VERSION_STR) >= LooseVersion("5.11"):
            account_display_name_length = QtGui.QFontMetrics(
                self.labelAccountName.font()).horizontalAdvance(account_display_name)
        else:
            account_display_name_length = QtGui.QFontMetrics(
                self.labelAccountName.font()).width(account_display_name)
        if account_display_name_length > 220:
            font = get_scaled_font(scaling=1.5*230/account_display_name_length)
            self.labelAccountName.setFont(font)
        self.labelAccountName.setText(CONF.get("account", "display_name"))

        # populate account info
        acc_mail = CONF.get("account", "email")
        acc_type = CONF.get("account", "type")
        if acc_type is not "":
            acc_type_text = ", Dropbox {0}".format(acc_type.capitalize())
        else:
            acc_type_text = ""
        self.labelAccountInfo.setText(acc_mail + acc_type_text)
        self.labelSpaceUsage.setText(CONF.get("account", "usage"))
        self.set_profile_pic()
        self.pushButtonUnlink.clicked.connect(self.unlink_dialog.open)
        self.unlink_dialog.accepted.connect(self.on_unlink)

        # populate sync section
        self.setup_combobox()
        self.pushButtonExcludedFolders.clicked.connect(self.folders_dialog.open)
        self.pushButtonExcludedFolders.clicked.connect(self.folders_dialog.populate_folders_list)

        # populate app section
        self.autostart = AutoStart()
        self.checkBoxStartup.setChecked(self.autostart.enabled)
        self.checkBoxStartup.stateChanged.connect(self.on_start_on_login_clicked)
        self.checkBoxNotifications.setChecked(self.mdbx.notify)
        self.checkBoxNotifications.stateChanged.connect(self.on_notifications_clicked)

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
        msg = ('Choose a location for your Dropbox. A folder named "{0}" will be ' +
               'created inside the folder you select.'.format(
                   CONF.get("main", "default_dir_name")))
        self.dropbox_folder_dialog = QtWidgets.QFileDialog(self, caption=msg)
        self.dropbox_folder_dialog.setModal(True)
        self.dropbox_folder_dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptOpen)
        self.dropbox_folder_dialog.setFileMode(QtWidgets.QFileDialog.Directory)
        self.dropbox_folder_dialog.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
        self.dropbox_folder_dialog.fileSelected.connect(self.on_new_dbx_folder)
        self.dropbox_folder_dialog.rejected.connect(
                lambda: self.comboBoxDropboxPath.setCurrentIndex(0))

    def set_profile_pic(self):

        self.mdbx.get_profile_pic()

        height = round(self.labelUserProfilePic.height()*0.7)

        try:
            pixmap = get_masked_image(self.mdbx.account_profile_pic_path, size=height)
        except Exception:
            initials = CONF.get("account", "abbreviated_name")
            pixmap = get_masked_image(FACEHOLDER_PATH, size=height, overlay_text=initials)

        self.labelUserProfilePic.setPixmap(pixmap)
        self.labelUserProfilePic.setAlignment(QtCore.Qt.AlignTop)

    def on_combobox(self, idx):
        if idx == 2:
            self.dropbox_folder_dialog.open()

    def on_new_dbx_folder(self, new_location):

        self.comboBoxDropboxPath.setCurrentIndex(0)
        if not new_location == '':
            self.comboBoxDropboxPath.setItemText(0, self.rel_path(new_location))
            self.comboBoxDropboxPath.setItemIcon(0, get_native_item_icon(new_location))

            new_path = osp.join(new_location, CONF.get("main", "default_dir_name"))
            self.mdbx.move_dropbox_directory(new_path)

    def on_unlink(self):
        """Unlinks the user's account and restarts the setup dialog."""

        try:
            self.mdbx.unlink()  # unlink
        except CONNECTION_ERRORS:
            pass
        quit_and_restart_maestral()

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

    def changeEvent(self, QEvent):

        if QEvent.type() == QtCore.QEvent.PaletteChange:
            self.update_dark_mode()

    def update_dark_mode(self):
        rgb = LINE_COLOR_DARK if isDarkWindow() else LINE_COLOR_LIGHT
        line_style = "color: rgb({0}, {1}, {2})".format(*rgb)

        self.line0.setStyleSheet(line_style)
        self.line1.setStyleSheet(line_style)
        self.line2.setStyleSheet(line_style)
