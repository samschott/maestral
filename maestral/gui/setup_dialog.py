# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import os
import os.path as osp
import shutil
import webbrowser
from PyQt5 import QtGui, QtCore, QtWidgets, uic

from dropbox import files

from maestral.main import Maestral
from maestral.oauth import OAuth2Session
from maestral.config.main import CONF
from maestral.config.base import get_home_dir
from maestral.gui.folders_dialog import FolderItem
from maestral.gui.resources import (APP_ICON_PATH, SETUP_DIALOG_PATH,
                                    get_native_item_icon, get_native_folder_icon)
from maestral.gui.utils import UserDialog, icon_to_pixmap


class SetupDialog(QtWidgets.QDialog):

    auth_session = ""
    auth_url = ""

    def __init__(self, pending_link=True, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        # load user interface layout from .ui file
        uic.loadUi(SETUP_DIALOG_PATH, self)

        self.app_icon = QtGui.QIcon(APP_ICON_PATH)

        self.labelIcon.setPixmap(icon_to_pixmap(self.app_icon, 170))
        self.labelIcon_1.setPixmap(icon_to_pixmap(self.app_icon, 70))
        self.labelIcon_2.setPixmap(icon_to_pixmap(self.app_icon, 70))
        self.labelIcon_3.setPixmap(icon_to_pixmap(self.app_icon, 100))

        self.mdbx = None
        self.folder_items = []

        # rename dialog buttons
        self.buttonBoxAuthCode.buttons()[0].setText("Link")
        self.buttonBoxAuthCode.buttons()[1].setText("Cancel")
        self.buttonBoxDropboxPath.buttons()[0].setText("Select")
        self.buttonBoxDropboxPath.buttons()[0].setDefault(True)
        self.buttonBoxDropboxPath.buttons()[1].setText("Cancel")
        self.buttonBoxDropboxPath.buttons()[2].setText("Unlink")
        self.buttonBoxFolderSelection.buttons()[0].setText("Select")
        self.buttonBoxFolderSelection.buttons()[1].setText("Back")

        # set up combobox
        self.dropbox_location = osp.dirname(CONF.get("main", "path")) or get_home_dir()
        relative_path = self.rel_path(self.dropbox_location)

        folder_icon = get_native_item_icon(self.dropbox_location)
        self.comboBoxDropboxPath.addItem(folder_icon, relative_path)

        self.comboBoxDropboxPath.insertSeparator(1)
        self.comboBoxDropboxPath.addItem(QtGui.QIcon(), "Other...")
        self.comboBoxDropboxPath.currentIndexChanged.connect(self.on_combobox)
        self.dropbox_folder_dialog = QtWidgets.QFileDialog(self)
        self.dropbox_folder_dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptOpen)
        self.dropbox_folder_dialog.setFileMode(QtWidgets.QFileDialog.Directory)
        self.dropbox_folder_dialog.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
        self.dropbox_folder_dialog.fileSelected.connect(self.on_new_dbx_folder)
        self.dropbox_folder_dialog.rejected.connect(
                lambda: self.comboBoxDropboxPath.setCurrentIndex(0))

        # connect buttons to callbacks
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.pushButtonLink.clicked.connect(self.on_link)
        self.buttonBoxAuthCode.rejected.connect(self.abort)
        self.buttonBoxAuthCode.accepted.connect(self.on_auth)
        self.buttonBoxDropboxPath.rejected.connect(self.abort)
        self.buttonBoxDropboxPath.accepted.connect(self.on_dropbox_location_selected)
        self.buttonBoxDropboxPath.clicked.connect(self.abort_and_unlink_if_reset)
        self.buttonBoxFolderSelection.rejected.connect(
                lambda: self.stackedWidget.setCurrentIndex(2))
        self.buttonBoxFolderSelection.accepted.connect(self.on_folders_selected)
        self.pushButtonClose.clicked.connect(self.accept)
        self.listWidgetFolders.itemChanged.connect(self.update_select_all_checkbox)
        self.selectAllCheckBox.clicked.connect(self.on_select_all_clicked)

        self.labelDropboxPath.setText(self.labelDropboxPath.text().format(CONF.get(
            "main", "default_dir_name")))

        # check if we are already authenticated, skip authentication if yes
        if not pending_link:
            self.labelDropboxPath.setText("""
            <html><head/><body>
            <p align="left">
            Your Dropbox folder has been moved or deleted from its original location.
            Maestral will not work properly until you move it back. It used to be located
            at: </p><p align="left">{0}</p>
            <p align="left">
            To move it back, click "Quit" below, move the Dropbox folder back to its 
            original location, and launch Maestral again.
            </p>
            <p align="left">
            To re-download your Dropbox, please select a location for your Dropbox 
            folder below. Maestral will create a new folder named "{1}" in the
            selected location.</p>          
            <p align="left">
            To unlink your Dropbox account from Maestral, click "Unlink" below.</p>
            </body></html>
            """.format(CONF.get("main", "path"), CONF.get("main", "default_dir_name")))
            self.buttonBoxDropboxPath.buttons()[1].setText("Quit")
            self.stackedWidget.setCurrentIndex(2)
            self.mdbx = Maestral(run=False)
            self.mdbx.client.get_account_info()

# =============================================================================
# Main callbacks
# =============================================================================

    def closeEvent(self, event):
        if self.stackedWidget.currentIndex == 4:
            self.accept()
        else:
            self.abort()

    def abort(self):
        self.mdbx = None
        self.reject()

    def abort_and_unlink_if_reset(self, b):
        if self.buttonBoxDropboxPath.buttonRole(b) == self.buttonBoxDropboxPath.ResetRole:
            self.mdbx.unlink()
            self.abort()

    def on_link(self):
        self.auth_session = OAuth2Session()
        self.auth_url = self.auth_session.get_auth_url()
        prompt = self.labelAuthLink.text().format(self.auth_url)
        self.labelAuthLink.setText(prompt)

        self.stackedWidget.setCurrentIndex(1)
        webbrowser.open_new(self.auth_url)

    def on_auth(self):
        token = self.lineEditAuthCode.text()
        res = self.auth_session.verify_auth_token(token)

        if res == OAuth2Session.Success:
            self.auth_session.save_creds()

            # switch to next page
            self.stackedWidget.setCurrentIndex(2)

            # start Maestral after linking to Dropbox account
            self.mdbx = Maestral(run=False)
            self.mdbx.client.get_account_info()
        elif res == OAuth2Session.InvalidToken:
            msg = "Please make sure that you entered the correct authentication token."
            msg_box = UserDialog("Authentication failed.", msg, parent=self)
            msg_box.open()
        elif res == OAuth2Session.ConnectionFailed:
            msg = "Please make sure that you are connected to the internet and try again."
            msg_box = UserDialog("Connection failed.", msg, parent=self)
            msg_box.open()

    def on_dropbox_location_selected(self):

        # reset sync status, we are starting fresh!
        self.mdbx.sync.last_cursor = ""
        self.mdbx.sync.last_sync = None
        self.mdbx.sync.dropbox_path = ""

        # apply dropbox path
        dropbox_path = osp.join(self.dropbox_location, CONF.get("main", "default_dir_name"))
        if osp.isdir(dropbox_path):
            msg = ('The folder "%s" already exists. Would '
                   'you like to keep using it?' % self.dropbox_location)
            msg_box = UserDialog("Folder already exists", msg, parent=self)
            msg_box.setAcceptButtonName("Keep")
            msg_box.addSecondAcceptButton("Replace")
            msg_box.addCancelButton()
            res = msg_box.exec_()

            if res == 1:
                pass
            elif res == 2:
                shutil.rmtree(dropbox_path, ignore_errors=True)
            else:
                return

        elif osp.isfile(dropbox_path):
            msg = ('There already is a file named "{0}" at this location. Would '
                   'you like to replace it?'.format(CONF.get("main", "default_dir_name")))
            msg_box = UserDialog("File conflict", msg, parent=self)
            msg_box.setAcceptButtonName("Replace")
            msg_box.addCancelButton()
            res = msg_box.exec_()

            if res == 0:
                return
            else:
                try:
                    os.unlink(dropbox_path)
                except OSError:
                    pass

        self.mdbx.create_dropbox_directory(path=dropbox_path, overwrite=False)

        # switch to next page
        self.stackedWidget.setCurrentIndex(3)

        # populate folder list
        if self.folder_items == []:
            self.populate_folders_list()

    def on_folders_selected(self):
        # switch to next page
        self.stackedWidget.setCurrentIndex(4)

        # exclude folders
        excluded_folders = []
        included_folders = []

        for item in self.folder_items:
            if not item.isIncluded():
                excluded_folders.append("/" + item.name.lower())
            elif item.isIncluded():
                included_folders.append("/" + item.name.lower())

        CONF.set("main", "excluded_folders", excluded_folders)

        self.mdbx.get_remote_dropbox_async("", callback=self.mdbx.start_sync)

# =============================================================================
# Helper functions
# =============================================================================

    def on_combobox(self, idx):
        if idx == 2:
            self.dropbox_folder_dialog.open()

    def on_new_dbx_folder(self, new_location):
        self.comboBoxDropboxPath.setCurrentIndex(0)
        if not new_location == '':
            self.comboBoxDropboxPath.setItemText(0, self.rel_path(new_location))
            self.comboBoxDropboxPath.setItemIcon(0, get_native_item_icon(new_location))

        self.dropbox_location = new_location

    def populate_folders_list(self):

        self.listWidgetFolders.addItem("Loading your folders...")

        # add new entries
        root_folders = self.mdbx.client.list_folder("", recursive=False)
        self.listWidgetFolders.clear()

        if root_folders is False:
            self.listWidgetFolders.addItem("Unable to connect. Please try again later.")
            self.self.buttonBoxFolderSelection.buttons()[0].setEnabled(False)
        else:
            self.buttonBoxFolderSelection.buttons()[0].setEnabled(True)

            for entry in root_folders.entries:
                if isinstance(entry, files.FolderMetadata):
                    inc = not self.mdbx.sync.is_excluded_by_user(entry.path_lower)
                    item = FolderItem(entry.name, inc)
                    self.folder_items.append(item)

            for item in self.folder_items:
                self.listWidgetFolders.addItem(item)

        self.update_select_all_checkbox()

    def update_select_all_checkbox(self):
        is_included_list = (i.isIncluded() for i in self.folder_items)
        self.selectAllCheckBox.setChecked(all(is_included_list))

    def on_select_all_clicked(self, checked):
        for item in self.folder_items:
            item.setIncluded(checked)

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
        # update folder icons: the system may provide different icons in dark mode
        for item in self.folder_items:
            item.setIcon(get_native_folder_icon())

    # static method to create the dialog and return Maestral instance on success
    @staticmethod
    def configureMaestral(parent=None):
        fsd = SetupDialog(parent)
        fsd.exec_()

        return fsd.mdbx
