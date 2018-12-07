# -*- coding: utf-8 -*-

# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import sys
import os.path as osp
import requests
from dropbox.oauth import BadStateException, NotApprovedException
from qtpy import QtGui, QtCore, QtWidgets, uic

from birdbox.main import BirdBox
from birdbox.client import OAuth2Session
from birdbox.monitor import CONNECTION_ERRORS
from birdbox.config.main import CONF
from birdbox.config.base import get_home_dir
from birdbox.gui.folders_dialog import FolderItem

_root = QtCore.QFileInfo(__file__).absolutePath()


class ErrorDialog(QtWidgets.QDialog):

    def __init__(self, parent, title, message):
        super(self.__class__, self).__init__(parent=parent)
        # load user interface layout from .ui file
        uic.loadUi(osp.join(_root, "error_dialog.ui"), self)
        self.setFixedSize(460, 145)
        self.labelTitle.setText(title)
        self.labelMessage.setText(message)


class OAuth2SessionGUI(OAuth2Session):

    def __init__(self):
        super(self.__class__, self).__init__()

    def load_creds(self):
        """Pass instead of loading credentials from file."""
        pass

    def get_url(self):
        self.authorize_url = self.auth_flow.start()
        return self.authorize_url

    def verify_auth_key(self, auth_code):
        self.oAuth2FlowResult = self.auth_flow.finish(auth_code)
        self.access_token = self.oAuth2FlowResult.access_token
        self.account_id = self.oAuth2FlowResult.account_id
        self.user_id = self.oAuth2FlowResult.user_id
        self.write_creds()

        return True


class FirstSyncDialog(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        # load user interface layout from .ui file
        uic.loadUi(osp.join(_root, "first_sync_dialog.ui"), self)
        self.app_icon = QtGui.QPixmap(_root + "/resources/app_icon.svg")
        self.folder_icon = QtGui.QIcon(_root + "/resources/GenericFolderIcon.icns")
        self.home_folder_icon = QtGui.QIcon(_root + "/resources/HomeFolderIcon.icns")

        self.bb = None

        # rename dialog buttons
        self.labelIcon.setPixmap(self.app_icon)
        self.buttonBoxAuthCode.buttons()[0].setText('Link')
        self.buttonBoxDropboxPath.buttons()[0].setText('Confirm')
        self.buttonBoxFolderSelection.buttons()[0].setText('Select')
        self.buttonBoxFolderSelection.buttons()[1].setText('Back')
        self.setup_combobox()

        # connect buttons to callbacks
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.pushButtonLink.clicked.connect(self.on_link)
        self.buttonBoxAuthCode.rejected.connect(self.on_reject)
        self.buttonBoxAuthCode.accepted.connect(self.on_auth)
        self.buttonBoxDropboxPath.rejected.connect(self.on_reject)
        self.buttonBoxDropboxPath.accepted.connect(self.on_dropbox_path)
        self.buttonBoxFolderSelection.rejected.connect(
                lambda: self.stackedWidget.setCurrentIndex(2))
        self.buttonBoxFolderSelection.accepted.connect(self.on_folder_select)
        self.pushButtonClose.clicked.connect(self.on_accept)

# =============================================================================
# Main callbacks
# =============================================================================

    def closeEvent(self, event):
        if self.stackedWidget.currentIndex == 4:
            self.on_accept()
        else:
            self.on_reject()

    def on_accept(self):
        self.reject()

    def on_reject(self):
        self.bb = None
        self.accept()

    def on_link(self):
        self.auth_session = OAuth2SessionGUI()
        self.auth_url = self.auth_session.get_url()
        prompt = self.labelAuthLink.text().format(self.auth_url)
        self.labelAuthLink.setText(prompt)

        self.stackedWidget.setCurrentIndex(1)

    def on_auth(self):
        auth_code = self.lineEditAuthCode.text()
        try:
            self.auth_session.verify_auth_key(auth_code)
        except requests.HTTPError:
            msg = "Please make sure that you entered the correct authentification code."
            msg_box = ErrorDialog(self, "Authentification failed.", msg)
            msg_box.open()
            return
        except BadStateException:
            msg = "The authentification session expired. Please try again."
            msg_box = ErrorDialog(self, "Session expired.", msg)
            msg_box.open()
            self.stackedWidget.setCurrentIndex(0)
            return
        except NotApprovedException:
            msg = "Please grant BirdBox access to your Dropbox to start syncing."
            msg_box = ErrorDialog(self, "Not approved error.", msg)
            msg_box.open()
            return
        except CONNECTION_ERRORS as e:
            print(e)
            msg = "Please make sure that you are connected to the internet and try again."
            msg_box = ErrorDialog(self, "Connection failed.", msg)
            msg_box.open()
            return

        self.stackedWidget.setCurrentIndex(2)

        # start BirdBox after linking to Dropbox account
        BirdBox.FIRST_SYNC = False
        self.bb = BirdBox(run=False)
        self.bb.client.get_account_info()

    def on_dropbox_path(self):
        # switch to next page
        self.stackedWidget.setCurrentIndex(3)
        # populate folder list of next page
        self.populate_folders_list()
        # apply dropbox path
        dropbox_path = osp.join(self.dropbox_location, 'Dropbox')
        self.bb.set_dropbox_directory(dropbox_path)

    def on_folder_select(self):
        # switch to next page
        self.stackedWidget.setCurrentIndex(4)

        # exclude folders
        excluded_folders = []
        included_folders = []

        for item in self.path_items:
            if not item.isIncluded():
                excluded_folders.append("/" + item.name.lower())
            elif item.isIncluded():
                included_folders.append("/" + item.name.lower())

        CONF.set("main", "excluded_folders", excluded_folders)

        self.bb.get_remote_dropbox_async("")

# =============================================================================
# Helper functions
# =============================================================================

    def setup_combobox(self):

        self.dropbox_location = osp.expanduser('~/Dropbox')

        parent_dir = osp.split(self.dropbox_location)[0]
        short_path = self.rel_path(parent_dir)

        if parent_dir == get_home_dir():
            self.comboBoxDropboxPath.addItem(self.home_folder_icon, short_path)
        else:
            self.comboBoxDropboxPath.addItem(self.folder_icon, short_path)
        self.comboBoxDropboxPath.insertSeparator(1)
        self.comboBoxDropboxPath.addItem(QtGui.QIcon(), "Other...")
        self.comboBoxDropboxPath.currentIndexChanged.connect(self.on_comboBox)
        self.dropbox_folder_dialog = QtWidgets.QFileDialog(self)
        self.dropbox_folder_dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptOpen)
        self.dropbox_folder_dialog.setFileMode(QtWidgets.QFileDialog.Directory)
        self.dropbox_folder_dialog.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
        self.dropbox_folder_dialog.fileSelected.connect(self.on_new_dbx_folder)
        self.dropbox_folder_dialog.rejected.connect(
                lambda: self.comboBoxDropboxPath.setCurrentIndex(0))

    def on_comboBox(self, idx):
        if idx == 2:
            self.dropbox_folder_dialog.open()

    def on_new_dbx_folder(self, new_location):
        self.comboBoxDropboxPath.setCurrentIndex(0)
        if not new_location == '':
            self.comboBoxDropboxPath.setItemText(0, self.rel_path(new_location))
            if new_location == get_home_dir():
                self.comboBoxDropboxPath.setItemIcon(0, self.home_folder_icon)
            else:
                self.comboBoxDropboxPath.setItemIcon(0, self.folder_icon)

        self.dropbox_location = new_location

    def populate_folders_list(self):

        self.listWidgetFolders.addItem("Loading your folders...")

        # add new entries
        root_folders = self.bb.client.list_folder("", recursive=False)
        self.listWidgetFolders.clear()

        if root_folders is False:
            self.listWidgetFolders.addItem("Unable to connect. Please try again later.")
            self.self.buttonBoxFolderSelection.buttons()[0].setEnabled(False)
        else:
            self.buttonBoxFolderSelection.buttons()[0].setEnabled(True)
            self.folder_list = self.bb.client.flatten_results_list(root_folders)

            self.path_items = []
            for entry in self.folder_list:
                is_included = not self.bb.client.is_excluded(entry.path_lower)
                item = FolderItem(self.folder_icon, entry.name, is_included)
                self.path_items.append(item)

            for item in self.path_items:
                self.listWidgetFolders.addItem(item)

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

    # static method to create the dialog and return BirdBox instance on success
    @staticmethod
    def configureBirdBox(parent=None):
        dialog = FirstSyncDialog(parent)
        dialog.exec_()
        return dialog.bb


def get_qt_app(*args, **kwargs):
    """
    Create a new Qt app or return an existing one.
    """
    created = False
    app = QtCore.QCoreApplication.instance()

    if not app:
        if not args:
            args = ([""],)
        app = QtWidgets.QApplication(*args, **kwargs)
        created = True

    return app, created


if __name__ == "__main__":
    app, created = get_qt_app()
    app.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)

    dialog = FirstSyncDialog()
    dialog.show()

    if created:
        sys.exit(app.exec_())
