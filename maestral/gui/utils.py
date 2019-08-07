# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import sys
import os
import platform
from subprocess import Popen
from traceback import format_exception
from PyQt5 import QtCore, QtGui, QtWidgets

from maestral.gui.resources import APP_ICON_PATH
from maestral.utils import is_macos_bundle

THEME_DARK = "dark"
THEME_LIGHT = "light"

LINE_COLOR_DARK = (95, 104, 104)
LINE_COLOR_LIGHT = (205, 203, 205)


def truncate_string(string, font=None, pixels=200, side="right"):

    if side == "right":
        return _truncate_string_right(string, font=font, pixels=pixels)
    elif side == "left":
        return _truncate_string_left(string, font=font, pixels=pixels)


def _truncate_string_right(string, font=None, pixels=200):
    """
    Truncates strings so that it is short than `pixels` in the given `font`.

    :param str string: String to truncate.
    :param font: QFont used to determine the pixel width of the text.
    :param int pixels: Maximum allowed width in pixels.

    :return: Truncated string.
    :rtype: str
    """

    if not font:
        test_label = QtWidgets.QLabel()
        font = test_label.font()

    metrics = QtGui.QFontMetrics(font)

    truncated = False
    new_string = string

    # truncate string using the average width per character
    if metrics.width(string) > pixels:
        pixel_per_char = metrics.width(string) / len(string)
        cutoff = int(pixels / pixel_per_char)
        new_string = string[0:cutoff]
        truncated = True

        # truncate further if necessary
        while metrics.width(new_string) > pixels:
            new_string = new_string[0:-1]

        # expand if truncated too far
        while metrics.width(new_string) < pixels:
            cutoff = len(new_string)
            new_string = new_string + string[cutoff:cutoff + 1]

    return new_string + ('...' if truncated else '')


def _truncate_string_left(string, font=None, pixels=300):
    """
    Truncates strings so that it is short than `pixels` in the given `font`.

    :param str string: String to truncate.
    :param int pixels: Maximum allowed width in pixels.

    :return: Truncated string.
    :rtype: str
    """
    if not font:
        test_label = QtWidgets.QLabel()
        font = test_label.font()
    metrics = QtGui.QFontMetrics(font)

    truncated = False
    new_string = string

    # truncate string using the average width per character
    if metrics.width(string) > pixels:
        pixel_per_char = metrics.width(string) / len(string)
        cutoff = int(pixels / pixel_per_char)
        new_string = string[cutoff:]
        truncated = True

        # truncate further if necessary
        while metrics.width(new_string) > pixels:
            new_string = new_string[1:]

        # expand if truncated too far
        while metrics.width(new_string) < pixels:
            cutoff = len(new_string)
            new_string = string[-cutoff:-cutoff+1] + new_string

    return ('...' if truncated else '') + new_string


def get_scaled_font(scaling=1.0, bold=False, italic=False):
    label = QtWidgets.QLabel()
    font = label.font()
    font.setBold(bold)
    font.setItalic(italic)
    font_size = font.pointSize()*scaling
    # noinspection PyTypeChecker
    font.setPointSize(font_size)

    return font


def _luminance(r, g, b, base=256):
    """
    Calculates luminance of a color, on a scale from 0 to 1, meaning that 1 is the
    highest luminance. r, g, b arguments values should be in 0..256 limits, or base
    argument should define the upper limit otherwise
    """
    return (0.2126*r + 0.7152*g + 0.0722*b)/base


def icon_to_pixmap(icon, width, height=None):
    if not height:
        height = width

    is_hidpi = QtCore.QCoreApplication.testAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)
    dpr = QtWidgets.QApplication.primaryScreen().devicePixelRatio()

    if not is_hidpi:
        width = width*dpr
        height = height*dpr
    px = icon.pixmap(width, height)
    if not is_hidpi:
        px.setDevicePixelRatio(dpr)

    return px


def __pixel_at(x, y):
    """
    Returns (r, g, b) color code for a pixel with given coordinates (each value is in
    0..256 limits)
    """
    desktop_id = QtWidgets.QApplication.desktop().winId()
    screen = QtWidgets.QApplication.primaryScreen()
    color = screen.grabWindow(desktop_id, x, y, 1, 1).toImage().pixel(0, 0)
    return ((color >> 16) & 0xff), ((color >> 8) & 0xff), (color & 0xff)


def statusBarTheme():
    """
    Returns one of THEME_LIGHT or THEME_DARK, corresponding to current status bar theme
    """
    # getting color of a pixel on a top bar, and identifying best-fitting color
    # theme based on its luminance
    pixel_rgb = __pixel_at(2, 2)
    luminance = _luminance(*pixel_rgb)
    return THEME_LIGHT if luminance >= 0.4 else THEME_DARK


def windowTheme():
    """
    Returns one of THEME_LIGHT or THEME_DARK, corresponding to current user's UI theme
    """
    # getting color of a pixel on a top bar, and identifying best-fitting color
    # theme based on its luminance
    w = QtWidgets.QWidget()
    bg_color = w.palette().color(QtGui.QPalette.Background)
    bg_color_rgb = [bg_color.red(), bg_color.green(), bg_color.blue()]
    luminance = _luminance(*bg_color_rgb)
    return THEME_LIGHT if luminance >= 0.4 else THEME_DARK


def isDarkWindow():
    return windowTheme() == THEME_DARK


def isLightWindow():
    return windowTheme() == THEME_LIGHT


def isDarkStatusBar():
    return statusBarTheme() == THEME_DARK


def isLightStatusBar():
    return statusBarTheme() == THEME_LIGHT


def get_gnome_scaling_factor():
    """Returns gnome scaling factor as str or None."""
    if __command_exists("gsettings"):
        res = os.popen("gsettings get org.gnome.desktop.interface scaling-factor").read()
        if res and res.split()[0] == "uint32" and len(res.split()) > 1:
            scaling_factor_str = res.split()[1]
            try:
                scaling_factor_float = float(scaling_factor_str)
                if scaling_factor_float > 1:
                    return scaling_factor_str
            except ValueError:
                pass
    return None


def __command_exists(command):
    return any(
        os.access(os.path.join(path, command), os.X_OK)
        for path in os.environ["PATH"].split(os.pathsep)
    )


class ErrorDialog(QtWidgets.QDialog):
    def __init__(self, title, message, exc_info=None, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        self.setWindowModality(QtCore.Qt.WindowModal)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Maestral Error")
        self.setFixedWidth(450)

        self.gridLayout = QtWidgets.QGridLayout()
        self.setLayout(self.gridLayout)

        self.iconLabel = QtWidgets.QLabel(self)
        self.titleLabel = QtWidgets.QLabel(self)
        self.infoLabel = QtWidgets.QLabel(self)

        icon_size = 70
        self.iconLabel.setMinimumSize(icon_size, icon_size)
        self.iconLabel.setMaximumSize(icon_size, icon_size)
        self.titleLabel.setFont(get_scaled_font(bold=True))
        self.infoLabel.setFont(get_scaled_font(scaling=0.9))
        self.infoLabel.setWordWrap(True)

        icon = QtGui.QIcon(APP_ICON_PATH)
        self.iconLabel.setPixmap(icon_to_pixmap(icon, icon_size))
        self.titleLabel.setText(title)
        self.infoLabel.setText(message)

        if exc_info:
            self.details = QtWidgets.QTextEdit(self)
            self.details.setText("".join(format_exception(*exc_info)))

        self.buttonBox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        self.buttonBox.accepted.connect(self.accept)

        self.gridLayout.addWidget(self.iconLabel, 0, 0, 2, 1)
        self.gridLayout.addWidget(self.titleLabel, 0, 1, 1, 1)
        self.gridLayout.addWidget(self.infoLabel, 1, 1, 1, 1)
        if exc_info:
            self.gridLayout.addWidget(self.details, 2, 0, 1, 2)
        self.gridLayout.addWidget(self.buttonBox, 3, 1, -1, -1)


def quit_and_restart_maestral():
    pid = os.getpid()  # get ID of current process

    # wait for current process to quit and then restart Maestral
    if is_macos_bundle:
        launch_command = os.path.join(sys._MEIPASS, "main")
        Popen("lsof -p {0} +r 1 &>/dev/null; {0}".format(launch_command), shell=True)
    if platform.system() == "Darwin":
        Popen("lsof -p {0} +r 1 &>/dev/null; maestral-gui".format(pid), shell=True)
    elif platform.system() == "Linux":
        Popen("tail --pid={0} -f /dev/null; maestral-gui".format(pid), shell=True)

    QtCore.QCoreApplication.quit()
