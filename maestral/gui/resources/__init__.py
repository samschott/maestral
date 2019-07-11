import os
from PyQt5 import QtWidgets, QtCore

_root = os.path.dirname(os.path.realpath(__file__))
_icon_provider = QtWidgets.QFileIconProvider()

APP_ICON_PATH = _root + "/app_icon.svg"
TRAY_ICON_PATH = _root + "/menubar_icon_{0}_{1}.svg"

FOLDERS_DIALOG_PATH = _root + "/folders_dialog.ui"
FIRST_SYNC_DIALOG_PATH = _root + "/first_sync_dialog.ui"
SETTINGS_WINDOW_PATH = _root + "/settings.ui"
ERROR_DIALOG_PATH = _root + "/error_dialog.ui"
UNLINK_DIALOG_PATH = _root + "/unlink_dialog.ui"


def get_native_item_icon(item_path):

    if not os.path.exists(item_path):
        raise IOError("Given path does not correspond to an existing item.")

    return _icon_provider.icon(QtCore.QFileInfo(item_path))


def get_native_folder_icon():
    return _icon_provider.icon(_icon_provider.Folder)
