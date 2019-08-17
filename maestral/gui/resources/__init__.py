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
RELINK_DIALOG_PATH = _root + "/relink_dialog.ui"
REBUILD_INDEX_DIALOG_PATH = _root + "/rebuild_index_dialog.ui"
SYNC_ISSUES_WINDOW_PATH = _root + "/sync_issues_window.ui"
SYNC_ISSUE_WIDGET_PATH = _root + "/sync_issue_widget.ui"

THEME_DARK = "dark"
THEME_LIGHT = "light"


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


def get_system_tray_icon(status):
    assert status in ("idle", "syncing", "paused", "disconnected", "error")

    desktop = get_desktop()
    gnome_version = __get_gnome_version()
    is_gnome3 = gnome_version is not None and gnome_version[0] >= 3

    if desktop == "gnome" and is_gnome3:
        icon_theme_paths = QtGui.QIcon.themeSearchPaths()
        icon_theme_paths += os.path.join(_root, "icon-theme-gnome")
        QtGui.QIcon.themeSearchPaths(icon_theme_paths)
        icon = QtGui.QIcon.fromTheme("menubar_icon_{}-symbolic".format(status))
    elif desktop == "kde":
        icon_theme_paths = QtGui.QIcon.themeSearchPaths()
        icon_theme_paths += os.path.join(_root, "icon-theme-kde")
        QtGui.QIcon.themeSearchPaths(icon_theme_paths)
        icon = QtGui.QIcon.fromTheme("menubar_icon_{}-symbolic".format(status))
    elif desktop == "cocoa":
        icon = QtGui.QIcon(TRAY_ICON_PATH.format(status, "dark"))
        icon.setIsMask(True)
    else:
        icon_color = "light" if isDarkStatusBar() else "dark"
        icon = QtGui.QIcon(TRAY_ICON_PATH.format(status, icon_color))
        icon.setIsMask(True)

    return icon


def statusBarTheme():
    """
    Returns one of gui.utils.THEME_LIGHT or gui.utils.THEME_DARK, corresponding to the
    current status bar theme. This function assumes that the status is located at the top.
    Do not use it if the platform supports symbolic status bar icons that automatically
    adapt their color.
    """
    # getting color of a pixel on a top bar, and identifying best-fitting color
    # theme based on its luminance

    pixel_rgb = __pixel_at(2, 2)
    lum = rgb_to_luminance(*pixel_rgb)

    return THEME_LIGHT if lum >= 0.4 else THEME_DARK


def get_desktop():
    desktop = ""

    if platform.system() == "Linux":
        if os.popen("pidof gnome-session").read():
            desktop = "gnome"
        elif os.popen("pidof ksmserver").read():
            desktop = "kde"
        elif os.popen("pidof xfce-mcs-manage").read():
            desktop = "xfce"
        else:
            desktop = ""
    elif platform.system() == "Darwin":
        desktop = "cocoa"

    return desktop


def isDarkStatusBar():
    """Detects the current status bar brighness and returns ``True`` for a dark status
    bar."""
    return statusBarTheme() == THEME_DARK


def rgb_to_luminance(r, g, b, base=256):
    """
    Calculates luminance of a color, on a scale from 0 to 1, meaning that 1 is the
    highest luminance. r, g, b arguments values should be in 0..256 limits, or base
    argument should define the upper limit otherwise.
    """
    return (0.2126*r + 0.7152*g + 0.0722*b)/base


def __get_gnome_version():
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

        return tuple(int(v) for v in version.split("."))
    else:
        return None


def __pixel_at(x, y):
    """
    Returns (r, g, b) color code for a pixel with given coordinates (each value is in
    0..256 limits)
    """
    desktop_id = QtWidgets.QApplication.desktop().winId()
    screen = QtWidgets.QApplication.primaryScreen()
    color = screen.grabWindow(desktop_id, x, y, 1, 1).toImage().pixel(0, 0)
    return ((color >> 16) & 0xff), ((color >> 8) & 0xff), (color & 0xff)