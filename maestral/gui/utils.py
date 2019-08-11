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
from PyQt5.QtCore import Qt, QRect
from PyQt5.QtGui import QBrush, QImage, QPainter, QPixmap, QWindow

from maestral.gui.resources import APP_ICON_PATH
from maestral.utils import is_macos_bundle

THEME_DARK = "dark"
THEME_LIGHT = "light"

LINE_COLOR_DARK = (95, 104, 104)
LINE_COLOR_LIGHT = (205, 203, 205)


def truncate_string(string, font=None, pixels=200, side="right"):
    """
    Elide a string to fit into the given width.

    :param str string: String to elide.
    :param font: Font to calculate size. If not given, the current styles default font
        for a QLabel is used.
    :param int pixels: Maximum width in pixels.
    :param str side: Side to truncate. Can be "right" or "left", defaults to "right".
    :return: Truncated string.
    :rtype: str
    """

    if not font:
        font = QtWidgets.QLabel().font()

    metrics = QtGui.QFontMetrics(font)
    mode = Qt.ElideRight if side is "right" else Qt.ElideLeft

    return metrics.elidedText(string, mode, pixels)


def get_scaled_font(scaling=1.0, bold=False, italic=False):
    """
    Returns the styles default font for QLabels, but scaled.

    :param float scaling: Scaling factor.
    :param bool bold: Sets the returned font to bold (defaults to ``False``)
    :param bool italic: Sets the returned font to italic (defaults to ``False``)
    :return: `QFont`` instance.
    """
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
    argument should define the upper limit otherwise.
    """
    return (0.2126*r + 0.7152*g + 0.0722*b)/base


def icon_to_pixmap(icon, width, height=None):
    """
    Converts a given icon to a pixmap. Automatically adjusts to high-DPI scaling.

    :param icon: Icon to convert.
    :param int width: Target point height.
    :param int height: Target point height.
    :return: ``QPixmap`` instance.
    """
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
    Returns one of gui.utils.THEME_LIGHT or gui.utils.THEME_DARK, corresponding to the
    current status bar theme.
    """
    # getting color of a pixel on a top bar, and identifying best-fitting color
    # theme based on its luminance
    pixel_rgb = __pixel_at(2, 2)
    luminance = _luminance(*pixel_rgb)
    return THEME_LIGHT if luminance >= 0.4 else THEME_DARK


def windowTheme():
    """
    Returns one of gui.utils.THEME_LIGHT or gui.utils.THEME_DARK, corresponding to
    current user's UI theme.
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


class UserDialog(QtWidgets.QDialog):
    """
    A template user dialog for Maestral. Shows a traceback if given in constructor.
    """
    def __init__(self, title, message, exc_info=None, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        self.setModal(True)
        self.setWindowModality(QtCore.Qt.WindowModal)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Maestral Error")  # user dialogs are only shown for errors
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

    def setAcceptButtonName(self, name):
        self.buttonBox.buttons()[0].setText(name)

    def addCancelButton(self, name="Cancel"):
        self._cancelButton = self.buttonBox.addButton(QtWidgets.QDialogButtonBox.Cancel)
        self._cancelButton.setText(name)
        self._cancelButton.clicked.connect(self.close)

    def setCancelButtonName(self, name):
        self._cancelButton.setText(name)

    def addSecondAcceptButton(self, name):
        self._acceptButton2 = self.buttonBox.addButton(QtWidgets.QDialogButtonBox.Ignore)
        self._acceptButton2.setText(name)
        self._acceptButton2.clicked.connect(lambda: self.setResult(2))
        self._acceptButton2.clicked.connect(self.close)

    def setSecondAcceptButtonName(self, name):
        self._acceptButton2.setText(name)


def quit_and_restart_maestral():
    """
    Quits and restarts Maestral. This chooses the right command to restart Maestral,
    running with the previous configuration. It also handles restarting macOS app bundles.
    """
    pid = os.getpid()  # get ID of current process
    config_name = os.getenv('MAESTRAL_CONFIG', 'maestral')

    # wait for current process to quit and then restart Maestral
    if is_macos_bundle:
        launch_command = os.path.join(sys._MEIPASS, "main")
        Popen("lsof -p {0} +r 1 &>/dev/null; {0}".format(launch_command), shell=True)
    if platform.system() == "Darwin":
        Popen("lsof -p {0} +r 1 &>/dev/null; maestral gui --config-name='{1}'".format(
            pid, config_name), shell=True)
    elif platform.system() == "Linux":
        Popen("tail --pid={0} -f /dev/null; maestral gui --config-name='{1}'".format(
            pid, config_name), shell=True)

    QtCore.QCoreApplication.quit()


def get_masked_image(path, size=64, overlay_text=""):
    """
    Returns a ``QPixmap`` from an image file masked with a smooth circle.
    The returned pixmap will have a size of *size* × *size* pixels.

    :param str path: Path to image file.
    :param int size: Target size. Will be the diameter of the masked image.
    :param overlay_text: Overlay text. This will be shown in white sans-serif on top of
        the image.
    :return: `QPixmap`` instance.
    """

    with open(path, "rb") as f:
        imgdata = f.read()

    imgtype = path.split(".")[-1]

    # Load image and convert to 32-bit ARGB (adds an alpha channel):
    image = QImage.fromData(imgdata, imgtype)
    image.convertToFormat(QImage.Format_ARGB32)

    # Crop image to a square:
    imgsize = min(image.width(), image.height())
    rect = QRect(
        (image.width() - imgsize) / 2,
        (image.height() - imgsize) / 2,
        imgsize,
        imgsize,
    )
    image = image.copy(rect)

    # Create the output image with the same dimensions and an alpha channel
    # and make it completely transparent:
    out_img = QImage(imgsize, imgsize, QImage.Format_ARGB32)
    out_img.fill(Qt.transparent)

    # Create a texture brush and paint a circle with the original image onto
    # the output image:
    brush = QBrush(image)        # Create texture brush
    painter = QPainter(out_img)  # Paint the output image
    painter.setBrush(brush)      # Use the image texture brush
    painter.setPen(Qt.NoPen)     # Don't draw an outline
    painter.setRenderHint(QPainter.Antialiasing, True)  # Use AA
    painter.drawEllipse(0, 0, imgsize, imgsize)  # Actually draw the circle

    if overlay_text:
        # draw text
        font = get_scaled_font(bold=True)
        font.setPointSize(imgsize * 0.4)
        painter.setFont(font)
        painter.setPen(Qt.white)
        painter.drawText(QRect(0, 0, imgsize, imgsize), Qt.AlignCenter, overlay_text)

    painter.end()                # We are done (segfault if you forget this)

    # Convert the image to a pixmap and rescale it.  Take pixel ratio into
    # account to get a sharp image on retina displays:
    pr = QWindow().devicePixelRatio()
    pm = QPixmap.fromImage(out_img)
    pm.setDevicePixelRatio(pr)
    size *= pr
    pm = pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    return pm
