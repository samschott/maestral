# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import sys
import os
from PyQt5 import QtWidgets, QtCore


_root = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))

_icon_provider = QtWidgets.QFileIconProvider()

APP_ICON_PATH = _root + "/Maestral.png"
TRAY_ICON_PATH = _root + "/menubar_icon_{0}_{1}.svg"

FOLDERS_DIALOG_PATH = _root + "/folders_dialog.ui"
SETUP_DIALOG_PATH = _root + "/setup_dialog.ui"
SETTINGS_WINDOW_PATH = _root + "/settings_window.ui"
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
    # use a real folder here because Qt may return the wrong folder icon
    # in macOS with dark mode activated
    return _icon_provider.icon(QtCore.QFileInfo("/usr"))


def get_native_file_icon():
    return _icon_provider.icon(_icon_provider.File)
