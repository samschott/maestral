# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

# system imports
import os
import os.path as osp
import shutil
from PyQt5 import QtGui, QtCore, QtWidgets, uic
from PyQt5.QtCore import QModelIndex, Qt

# maestral modules
from maestral.sync.main import Maestral, handle_disconnect
from maestral.sync.utils import delete_file_or_folder
from maestral.sync.oauth import OAuth2Session
from maestral.config.base import get_home_dir
from maestral.gui.resources import APP_ICON_PATH, SETUP_DIALOG_PATH, get_native_item_icon
from maestral.gui.utils import UserDialog, icon_to_pixmap, BackgroundTask
from maestral.gui.folders_dialog import AsyncLoadFolders, TreeModel, DropboxPathModel


class SetupDialog(QtWidgets.QDialog):
    """A dialog to link and set up a new Dropbox account."""

    auth_session = ""
    auth_url = ""

    accepted = False

    def __init__(self, pending_link=True, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        # load user interface layout from .ui file
        uic.loadUi(SETUP_DIALOG_PATH, self)

        self.app_icon = QtGui.QIcon(APP_ICON_PATH)

        self.labelIcon_0.setPixmap(icon_to_pixmap(self.app_icon, 150))
        self.labelIcon_1.setPixmap(icon_to_pixmap(self.app_icon, 70))
        self.labelIcon_2.setPixmap(icon_to_pixmap(self.app_icon, 70))
        self.labelIcon_3.setPixmap(icon_to_pixmap(self.app_icon, 120))

        self.mdbx = None
        self.dbx_model = None
        self.excluded_folders = []

        # resize dialog buttons
        width = self.pushButtonAuthPageCancel.width()*1.1
        for b in (self.pushButtonAuthPageLink, self.pussButtonDropboxPathUnlink,
                  self.pussButtonDropboxPathSelect, self.pushButtonFolderSelectionBack,
                  self.pushButtonFolderSelectionSelect, self.pushButtonAuthPageCancel,
                  self.pussButtonDropboxPathCalcel, self.pushButtonClose):
            b.setMinimumWidth(width)
            b.setMaximumWidth(width)

        # set up combobox
        self.dropbox_location = osp.dirname(Maestral.get_conf("main", "path")) or get_home_dir()
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
        self.pushButtonAuthPageCancel.clicked.connect(self.on_reject_requested)
        self.pushButtonAuthPageLink.clicked.connect(self.on_auth_clicked)
        self.pussButtonDropboxPathCalcel.clicked.connect(self.on_reject_requested)
        self.pussButtonDropboxPathSelect.clicked.connect(self.on_dropbox_location_selected)
        self.pussButtonDropboxPathUnlink.clicked.connect(self.unlink_and_go_to_start)
        self.pushButtonFolderSelectionBack.clicked.connect(self.stackedWidget.slideInPrev)
        self.pushButtonFolderSelectionSelect.clicked.connect(self.on_folders_selected)
        self.pushButtonClose.clicked.connect(self.on_accept_requested)
        self.selectAllCheckBox.clicked.connect(self.on_select_all_clicked)

        default_dir_name = Maestral.get_conf("main", "default_dir_name")

        self.labelDropboxPath.setText(self.labelDropboxPath.text().format(default_dir_name))

        # check if we are already authenticated, skip authentication if yes
        if not pending_link:
            self.mdbx = Maestral(run=False)
            self.mdbx.get_account_info()
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
            """.format(Maestral.get_conf("main", "path"), default_dir_name))
            self.pussButtonDropboxPathCalcel.setText("Quit")
            self.stackedWidget.setCurrentIndex(2)
            self.stackedWidgetButtons.setCurrentIndex(2)
        else:
            self.stackedWidget.setCurrentIndex(0)
            self.stackedWidgetButtons.setCurrentIndex(0)

# =============================================================================
# Main callbacks
# =============================================================================

    def closeEvent(self, event):

        if self.stackedWidget.currentIndex == 4:
            self.on_accept_requested()
        else:
            self.on_reject_requested()

    def on_accept_requested(self):
        del self.mdbx

        self.accepted = True
        self.accept()

    def on_reject_requested(self):
        if self.mdbx:
            self.mdbx.set_conf("main", "path", "")

        del self.mdbx
        self.accepted = False
        self.reject()

    def unlink_and_go_to_start(self, b):
        self.mdbx.unlink()
        self.stackedWidget.slideInIdx(0)

    def on_link(self):
        self.auth_session = OAuth2Session()
        self.auth_url = self.auth_session.get_auth_url()
        prompt = self.labelAuthLink.text().format(self.auth_url)
        self.labelAuthLink.setText(prompt)

        self.stackedWidget.fadeInIdx(1)
        self.pushButtonAuthPageLink.setFocus()

    def on_auth_clicked(self):

        if self.lineEditAuthCode.text() == "":
            msg = "Please enter an authentication token."
            msg_box = UserDialog("Authentication failed.", msg, parent=self)
            msg_box.open()
        else:
            self.progressIndicator.startAnimation()
            self.pushButtonAuthPageLink.setEnabled(False)
            self.lineEditAuthCode.setEnabled(False)

            self.verify_token_async()

    def verify_token_async(self):

        token = self.lineEditAuthCode.text()

        self.auth_task = BackgroundTask(
            parent=self,
            target=self.auth_session.verify_auth_token,
            args=(token,)
        )
        self.auth_task.sig_done.connect(self.on_verify_token_finished)

    def on_verify_token_finished(self, res):

        if res == OAuth2Session.Success:
            self.auth_session.save_creds()

            # switch to next page
            self.stackedWidget.slideInIdx(2)
            self.pussButtonDropboxPathSelect.setFocus()
            self.lineEditAuthCode.clear()  # clear since we might come back on unlink

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

        self.progressIndicator.stopAnimation()
        self.pushButtonAuthPageLink.setEnabled(True)
        self.lineEditAuthCode.setEnabled(True)

    def on_dropbox_location_selected(self):

        # reset sync status, we are starting fresh!
        self.mdbx.sync.last_cursor = ""
        self.mdbx.sync.last_sync = 0
        self.mdbx.sync.dropbox_path = ""

        # apply dropbox path
        dropbox_path = osp.join(self.dropbox_location, self.mdbx.get_conf("main", "default_dir_name"))
        if osp.isdir(dropbox_path):
            msg = ('The folder "%s" already exists. Would '
                   'you like to keep using it?' % dropbox_path)
            msg_box = UserDialog("Folder already exists", msg, parent=self)
            msg_box.setAcceptButtonName("Keep")
            msg_box.addSecondAcceptButton("Replace", icon="edit-clear")
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
                   'you like to replace it?'.format(self.mdbx.get_conf("main", "default_dir_name")))
            msg_box = UserDialog("File conflict", msg, parent=self)
            msg_box.setAcceptButtonName("Replace")
            msg_box.addCancelButton()
            res = msg_box.exec_()

            if res == 0:
                return
            else:
                delete_file_or_folder(dropbox_path)

        self.mdbx.create_dropbox_directory(path=dropbox_path, overwrite=False)

        # switch to next page
        self.mdbx.set_conf("main", "excluded_folders", [])
        self.stackedWidget.slideInIdx(3)
        self.treeViewFolders.setFocus()

        # populate folder list
        if not self.excluded_folders:  # don't repopulate
            self.populate_folders_list()

    def on_folders_selected(self):

        self.apply_selection()
        self.mdbx.set_conf("main", "excluded_folders", self.excluded_folders)

        # if any excluded folders are currently on the drive, delete them
        for folder in self.excluded_folders:
            local_folder = self.mdbx.to_local_path(folder)
            delete_file_or_folder(local_folder)

        # switch to next page
        self.stackedWidget.slideInIdx(4)

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

    @handle_disconnect
    def populate_folders_list(self, overload=None):
        self.async_loader = AsyncLoadFolders(self.mdbx, self)
        self.dbx_root = DropboxPathModel(self.mdbx, self.async_loader, "/")
        self.dbx_model = TreeModel(self.dbx_root)
        self.dbx_model.dataChanged.connect(self.update_select_all_checkbox)
        self.treeViewFolders.setModel(self.dbx_model)

        self.dbx_model.loading_done.connect(
            lambda: self.pushButtonFolderSelectionSelect.setEnabled(True))
        self.dbx_model.loading_failed.connect(
            lambda: self.pushButtonFolderSelectionSelect.setEnabled(False))

        self.dbx_model.loading_done.connect(
            lambda: self.selectAllCheckBox.setEnabled(True))
        self.dbx_model.loading_failed.connect(
            lambda: self.selectAllCheckBox.setEnabled(False))

    def update_select_all_checkbox(self):
        check_states = []
        for irow in range(self.dbx_model._root_item.child_count_loaded()):
            index = self.dbx_model.index(irow, 0, QModelIndex())
            check_states.append(self.dbx_model.data(index, Qt.CheckStateRole))
        if all(cs == 2 for cs in check_states):
            self.selectAllCheckBox.setChecked(True)
        else:
            self.selectAllCheckBox.setChecked(False)

    def on_select_all_clicked(self, checked):
        checked_state = 2 if checked else 0
        for irow in range(self.dbx_model._root_item.child_count_loaded()):
            index = self.dbx_model.index(irow, 0, QModelIndex())
            self.dbx_model.setCheckState(index, checked_state)

    def apply_selection(self, index=QModelIndex()):

        if index.isValid():
            item = index.internalPointer()
            item_dbx_path = item._root.lower()

            # We have started with all folders included. Therefore just append excluded
            # folders here.
            if item.checkState == 0:
                self.excluded_folders.append(item_dbx_path)
        else:
            item = self.dbx_model._root_item

        for row in range(item.child_count_loaded()):
            index_child = self.dbx_model.index(row, 0, index)
            self.apply_selection(index=index_child)

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
        if self.dbx_model:
            self.dbx_model.reloadData([Qt.DecorationRole])  # reload folder icons

    # static method to create the dialog and return Maestral instance on success
    @staticmethod
    def configureMaestral(pending_link=True, parent=None):
        fsd = SetupDialog(pending_link, parent)
        fsd.exec_()

        return fsd.accepted
