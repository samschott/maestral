# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import sys
import os
import os.path as osp
import platform
import re
from PyQt5 import QtWidgets, QtCore, QtGui

_root = getattr(sys, '_MEIPASS', osp.dirname(osp.abspath(__file__)))

_icon_provider = QtWidgets.QFileIconProvider()

APP_ICON_PATH = osp.join(_root, "maestral.png")
TRAY_ICON_DIR_SVG = osp.join(_root, "tray-icons-svg")
TRAY_ICON_DIR_GNOME = osp.join(_root, "tray-icons-gnome")
TRAY_ICON_DIR_PNG = osp.join(_root, "tray-icons-png")
TRAY_ICON_PATH_SVG = osp.join(TRAY_ICON_DIR_SVG, "maestral-icon-{0}-{1}.svg")
TRAY_ICON_PATH_GNOME = osp.join(TRAY_ICON_DIR_GNOME, "maestral-icon-{0}-symbolic.svg")
TRAY_ICON_PATH_PNG = osp.join(TRAY_ICON_DIR_PNG, "maestral-icon-{0}-{1}.png")

FACEHOLDER_PATH = osp.join(_root, "faceholder.png")

FOLDERS_DIALOG_PATH = osp.join(_root, "folders_dialog.ui")
SETUP_DIALOG_PATH = osp.join(_root, "setup_dialog.ui")
SETTINGS_WINDOW_PATH = osp.join(_root, "settings_window.ui")
UNLINK_DIALOG_PATH = osp.join(_root, "unlink_dialog.ui")
RELINK_DIALOG_PATH = osp.join(_root, "relink_dialog.ui")
REBUILD_INDEX_DIALOG_PATH = osp.join(_root, "rebuild_index_dialog.ui")
SYNC_ISSUES_WINDOW_PATH = osp.join(_root, "sync_issues_window.ui")
SYNC_ISSUE_WIDGET_PATH = osp.join(_root, "sync_issue_widget.ui")

THEME_DARK = "dark"
THEME_LIGHT = "light"

QT_VERSION_TUPLE = tuple(int(x) for x in QtCore.QT_VERSION_STR.split("."))


def get_desktop():
    """
    Determines the current desktop environment. This is used for instance to decide
    which keyring backend is preferred to store the auth token.

    :returns: "gnome", "kde", "xfce", "cocoa", "" or any other string if the desktop
        $XDG_CURRENT_DESKTOP if the desktop environment is not known to us.
    :rtype: str
    """

    if platform.system() == "Linux":
        current_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        desktop_session = os.environ.get("GDMSESSION", "").lower()

        for desktop in ("gnome", "kde", "xfce", ""):
            if desktop in current_desktop or desktop in desktop_session:
                return desktop

        return current_desktop

    elif platform.system() == "Darwin":
        return "cocoa"


DESKTOP = get_desktop()


def get_native_item_icon(item_path):
    """Returns the system icon for the given file or folder. If there is no item at the
    given path, the systems default file icon will be returned.

    :param str item_path: Path to local item.
    """
    if not osp.exists(item_path):
        return get_native_file_icon()
    else:
        return _icon_provider.icon(QtCore.QFileInfo(item_path))


def get_native_folder_icon():
    """Returns the system's default folder icon."""
    # use a real folder here because Qt may otherwise
    # return the wrong folder icon in some cases
    return _icon_provider.icon(QtCore.QFileInfo("/usr"))


def get_native_file_icon():
    """Returns the system's default file icon."""
    return _icon_provider.icon(_icon_provider.File)


def get_system_tray_icon(status, color=None, geometry=None):
    """Returns the system tray icon for the given status and color. The following icons
    will be used:

    1) macOS: Black SVG icons with transparent background. macOS will adapt the appearance
       as necessary.
    3) Gnome 3: SVG icons that follow the Gnome 3 "symbolic" icon specification.
    3) KDE Plasma with PtQt 5.13 and higher: SVG icons with a color contrasting the
       system tray background.
    4) Other: PNG icons with a color contrasting the system tray background.

    :param str status: Maestral status. Must be "idle", "syncing", "paused",
        "disconnected" or "error".
    :param str color: Must be "dark" or "light". If not given, the color will be chosen
        automatically to contrast the system tray background.
    :param geometry: Tray icon geometry on screen. If given, this location will be used to
        to determine the system tray background color.
    """
    assert status in ("idle", "syncing", "paused", "disconnected", "error")

    gnome_version = _get_gnome_version()
    is_gnome3 = gnome_version is not None and gnome_version[0] >= 3

    if DESKTOP == "cocoa":
        icon_color = color or "dark"
        is_mask = False if color else True

        icon = QtGui.QIcon(TRAY_ICON_PATH_SVG.format(status, icon_color))
        icon.setIsMask(is_mask)

    elif DESKTOP == "gnome" and is_gnome3:
        icon = QtGui.QIcon.fromTheme("maestral-icon-{}-symbolic".format(status))
        if not icon.name():  # icon was not found, fall back to our own
            icon_color = color or "light" if isDarkStatusBar(geometry) else "dark"
            icon = QtGui.QIcon(TRAY_ICON_PATH_SVG.format(status, icon_color))
    else:
        icon_color = color or "light" if isDarkStatusBar(geometry) else "dark"

        if DESKTOP == "kde" and QT_VERSION_TUPLE >= (5, 13, 0):
            icon = QtGui.QIcon(TRAY_ICON_PATH_SVG.format(status, icon_color))
        else:
            icon = QtGui.QIcon(TRAY_ICON_PATH_PNG.format(status, icon_color))

    return icon


def statusBarTheme(icon_geometry=None):
    """
    Returns one of gui.utils.THEME_LIGHT or gui.utils.THEME_DARK, corresponding to the
    current status bar theme.

    `icon_geometry` provides the geometry (location and dimensions) of the tray
    icon. If not given, we try to guess the location of the system tray.
    """

    # --------------------- check for the status bar color -------------------------

    # see if we can trust returned pixel colors
    # (work around for a bug in Qt with KDE where all screenshots return black)

    c0 = _pixel_at(10, 10)
    c1 = _pixel_at(300, 400)
    c2 = _pixel_at(800, 800)

    if not c0 == c1 == c2 == (0, 0, 0):  # we can trust pixel colors from screenshots

        if not icon_geometry or icon_geometry.isEmpty():  # guess the location of the status bar

            rec_screen = QtWidgets.QApplication.desktop().screenGeometry()  # screen size
            rec_available = QtWidgets.QApplication.desktop().availableGeometry()  # available size

            # convert to regions for subtraction
            region_screen = QtGui.QRegion(rec_screen)
            region_available = QtGui.QRegion(rec_available)

            # subtract and convert back to rect
            rects_diff = region_screen.subtracted(region_available).rects()
            if len(rects_diff) > 0:
                # there seems to be a task bar
                taskBarRect = rects_diff[0]
            else:
                taskBarRect = rec_screen

            px = taskBarRect.left() + 2
            py = taskBarRect.bottom() - 2

        else:  # use the given location from icon_geometry
            px = icon_geometry.left()
            py = icon_geometry.bottom()

        # get pixel luminance from icon corner or status bar
        pixel_rgb = _pixel_at(px, py)
        lum = rgb_to_luminance(*pixel_rgb)

        return THEME_LIGHT if lum >= 0.4 else THEME_DARK

    else:
        # -------------------- check icon theme for hints --------------------------
        theme_name = QtGui.QIcon.themeName().lower()

        if theme_name in ("breeze-dark", "adwaita-dark", "ubuntu-mono-dark", "humanity-dark"):
            return THEME_DARK
        elif theme_name in ("breeze", "adwaita", "ubuntu-mono-light", "humanity"):
            return THEME_LIGHT
        else:  # we give up, we will never guess the right color!
            return THEME_DARK


def isDarkStatusBar(icon_geometry=None):
    """Detects the current status bar brightness and returns ``True`` for a dark status
    bar. `icon_geometry` provides the geometry (location and dimensions) of the tray
    icon. If not given, we try to guess the location of the system tray."""
    return statusBarTheme(icon_geometry) == THEME_DARK


def rgb_to_luminance(r, g, b, base=256):
    """
    Calculates luminance of a color, on a scale from 0 to 1, meaning that 1 is the
    highest luminance. r, g, b arguments values should be in 0..256 limits, or base
    argument should define the upper limit otherwise.
    """
    return (0.2126*r + 0.7152*g + 0.0722*b)/base


def _get_gnome_version():
    gnome3_config_path = "/usr/share/gnome/gnome-version.xml"
    gnome2_config_path = "/usr/share/gnome-about/gnome-version.xml"

    xml = None

    for path in (gnome2_config_path, gnome3_config_path):
        if osp.isfile(path):
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


def _pixel_at(x, y):
    """
    Returns (r, g, b) color code for a pixel with given coordinates (each value is in
    0..256 limits)
    """

    desktop_id = QtWidgets.QApplication.desktop().winId()
    screen = QtWidgets.QApplication.primaryScreen()
    color = screen.grabWindow(desktop_id, x, y, 1, 1).toImage().pixel(0, 0)

    return ((color >> 16) & 0xff), ((color >> 8) & 0xff), (color & 0xff)
