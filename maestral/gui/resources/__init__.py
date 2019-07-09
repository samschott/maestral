import os
import platform

_root = os.path.dirname(os.path.realpath(__file__))

if platform.system() == "Darwin":
    GENERIC_FOLDER_ICON = _root + "/GenericFolderIcon.icns"
    HOME_FOLDER_ICON = _root + "/HomeFolderIcon.icns"
else:
    GENERIC_FOLDER_ICON = _root + "/GenericFolderIcon.png"
    HOME_FOLDER_ICON = _root + "/HomeFolderIcon.png"

APP_ICON = _root + "/app_icon.svg"

ICON_PATH = _root + "/menubar_icon_{0}_{1}.svg"

FOLDERS_DIALOG = _root + "/folders_dialog.ui"
FIRST_SYNC_DIALOG = _root + "/first_sync_dialog.ui"
SETTINGS_WINDOW = _root + "/settings.ui"
ERROR_DIALOG = _root + "/error_dialog.ui"
UNLINK_DIALOG = _root + "/unlink_dialog.ui"
