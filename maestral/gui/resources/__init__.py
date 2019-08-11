# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import sys
import os
import re
import platform
from PyQt5 import QtWidgets, QtCore, QtGui

_root = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))

_icon_provider = QtWidgets.QFileIconProvider()

APP_ICON_PATH = _root + "/Maestral.png"
TRAY_ICON_PATH = _root + "/menubar_icon_{0}_{1}.svg"

FACEHOLDER_PATH = _root + "/faceholder.png"

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


def get_system_tray_icon(status, icon_color="dark"):
    assert status in ("idle", "syncing", "paused", "disconnected", "error")

    gnome_version = __get_gnome_version(return_str=False)
    has_gnome3 = gnome_version is not None and gnome_version[0] >= 3

    if platform.system() == "Linux" and has_gnome3:
        QtGui.QIcon.setFallbackSearchPaths([os.path.join(_root, "icon-theme-gnome")])
        icon = QtGui.QIcon.fromTheme("menubar_icon_{0}-symbolic".format(status))
    elif platform.system() == "Darwin":
        icon = QtGui.QIcon(TRAY_ICON_PATH.format(status, "dark"))
        icon.setIsMask(True)
    else:
        icon = QtGui.QIcon(TRAY_ICON_PATH.format(status, icon_color))
        icon.setIsMask(True)

    return icon


def __get_gnome_version(return_str=False):
    gnome3_config_path = "/usr/share/gnome/gnome-version.xml"
    gnome2_config_path = "/usr/share/gnome-about/gnome-version.xml"

    xml = None

    for path in (gnome2_config_path, gnome3_config_path):
        if os.path.isfile(path):
            try:
                with open(path, "r") as f:
                    xml = f.read()
            except OSError:
                pass

    if xml:
        p = re.compile(r"<platform>(?P<maj>\d+)</platform>\s+<minor>"
                       r"(?P<min>\d+)</minor>\s+<micro>(?P<mic>\d+)</micro>")
        m = p.search(xml)
        version = "{0}.{1}.{2}".format(m.group("maj"), m.group("min"), m.group("mic"))

        if return_str:
            return version
        else:
            return tuple(int(v) for v in version.split("."))
    else:
        return None
