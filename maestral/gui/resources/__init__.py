import sys
import os
from PyQt5 import QtWidgets, QtCore

if getattr(sys, "frozen", False):
    # running in a bundle
    _root = os.path.dirname(os.path.join(sys._MEIPASS)) + "/Resources"
else:
    # running from python
    _root = os.path.dirname(os.path.realpath(__file__))
_icon_provider = QtWidgets.QFileIconProvider()

APP_ICON_PATH = _root + "/Maestral.png"
TRAY_ICON_PATH = _root + "/menubar_icon_{0}_{1}.svg"

FOLDERS_DIALOG_PATH = _root + "/folders_dialog.ui"
FIRST_SYNC_DIALOG_PATH = _root + "/first_sync_dialog.ui"
SETTINGS_WINDOW_PATH = _root + "/settings_window.ui"
ERROR_DIALOG_PATH = _root + "/error_dialog.ui"
UNLINK_DIALOG_PATH = _root + "/unlink_dialog.ui"
REBUILD_INDEX_DIALOG_PATH = _root + "/rebuild_index_dialog.ui"
SYNC_ISSUES_WINDOW_PATH = _root + "/sync_issues_window.ui"
SYNC_ISSUE_WIDGET_PATH = _root + "/sync_issue_widget.ui"


def get_native_item_icon(item_path):

    if not os.path.exists(item_path):
        # fall back to default file icon
        return get_native_file_icon()
    else:
        # get system icon for file type
        return _icon_provider.icon(QtCore.QFileInfo(item_path))


def get_native_folder_icon():
    return _icon_provider.icon(_icon_provider.Folder)


def get_native_file_icon():
    return _icon_provider.icon(_icon_provider.File)
