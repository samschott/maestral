# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

# system imports
import sys
import os
import platform
from subprocess import Popen

# external packages
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt, QRect
from PyQt5.QtGui import QBrush, QImage, QPainter, QPixmap, QWindow

# maestral modules
from maestral.gui.resources import APP_ICON_PATH, rgb_to_luminance
from maestral.sync.utils import is_macos_bundle
from maestral.sync.daemon import MaestralProxy, stop_maestral_daemon_process

THEME_DARK = "dark"
THEME_LIGHT = "light"

LINE_COLOR_DARK = (70, 70, 70)
LINE_COLOR_LIGHT = (213, 213, 213)


def elide_string(string, font=None, pixels=200, side="right"):
    """
    Elide a string to fit into the given width.

    :param str string: String to elide.
    :param font: Font to calculate size. If not given, the current style's default font
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
    font_size = round(font.pointSize()*scaling)
    # noinspection PyTypeChecker
    font.setPointSize(font_size)

    return font


def icon_to_pixmap(icon, width, height=None):
    """Converts a given icon to a pixmap. Automatically adjusts to high-DPI scaling.

    :param icon: Icon to convert.
    :param int width: Target point height.
    :param int height: Target point height.
    :return: ``QPixmap`` instance.
    """
    if not height:
        height = width

    is_hidpi = QtCore.QCoreApplication.testAttribute(Qt.AA_UseHighDpiPixmaps)
    pr = QWindow().devicePixelRatio()

    if not is_hidpi:
        width = width*pr
        height = height*pr
    px = icon.pixmap(width, height)
    if not is_hidpi:
        px.setDevicePixelRatio(pr)

    return px


def windowTheme():
    """Returns one of gui.utils.THEME_LIGHT or gui.utils.THEME_DARK, corresponding to
    current user's UI theme."""
    w = QtWidgets.QWidget()
    bg_color = w.palette().color(QtGui.QPalette.Background)
    bg_color_rgb = [bg_color.red(), bg_color.green(), bg_color.blue()]
    luminance = rgb_to_luminance(*bg_color_rgb)
    return THEME_LIGHT if luminance >= 0.4 else THEME_DARK


def isDarkWindow():
    """Returns ``True`` if windows have a dark UI theme."""
    return windowTheme() == THEME_DARK


class Worker(QtCore.QObject):
    """A worker object. To be used in QThreads."""

    sig_done = QtCore.pyqtSignal(object)

    def __init__(self, target=None, args=None, kwargs=None):
        QtCore.QObject.__init__(self)
        self._target = target
        self._args = args or ()
        self._kwargs = kwargs or {}

    def start(self):
        res = self._target(*self._args, **self._kwargs)
        self.sig_done.emit(res)


class MaestralWorker(Worker):
    """A worker object for Maestral. It uses a separate Maestral proxy to prevent
    the main connection from blocking."""

    def start(self):
        config_name = os.getenv("MAESTRAL_CONFIG", "maestral")
        with MaestralProxy(config_name) as m:
            func = m.__getattr__(self._target)
            res = func(*self._args, **self._kwargs)
        self.sig_done.emit(res)


class BackgroundTask(QtCore.QObject):
    """A utility class to manage a worker thread."""

    sig_done = QtCore.pyqtSignal(object)

    def __init__(self, parent=None, target=None, args=None, kwargs=None, autostart=True):
        QtCore.QObject.__init__(self, parent)
        self._target = target
        self._args = args or ()
        self._kwargs = kwargs or {}

        if autostart:
            self.start()

    def start(self):

        self.thread = QtCore.QThread(self)
        self.worker = Worker(
            target=self._target, args=self._args, kwargs=self._kwargs)
        self.worker.sig_done.connect(self.sig_done.emit)
        self.worker.sig_done.connect(self.thread.quit)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.thread.start()

    def wait(self, timeout=None):
        if timeout:
            self.thread.wait(msecs=timeout)
        else:
            self.thread.wait()


class MaestralBackgroundTask(BackgroundTask):
    """A utility class to manage a worker thread. It uses a separate Maestral proxy
    to prevent the main connection from blocking."""

    def start(self):

        self.thread = QtCore.QThread(self)
        self.worker = MaestralWorker(
            target=self._target, args=self._args, kwargs=self._kwargs)
        self.worker.sig_done.connect(self.sig_done.emit)
        self.worker.sig_done.connect(self.thread.quit)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.thread.start()


class UserDialog(QtWidgets.QDialog):
    """A template user dialog for Maestral. Shows a traceback if given in constructor."""

    def __init__(self, title, message, details=None, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        self.setModal(True)
        self.setWindowModality(Qt.WindowModal)
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Sheet | Qt.WindowTitleHint |
                            Qt.CustomizeWindowHint)
        self.setWindowTitle("")
        width = 550 if details else 450
        self.setFixedWidth(width)

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
        self.infoLabel.setFixedWidth(width-150)
        self.infoLabel.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding,
                                     QtWidgets.QSizePolicy.MinimumExpanding)
        self.infoLabel.setWordWrap(True)
        self.infoLabel.setOpenExternalLinks(True)

        icon = QtGui.QIcon(APP_ICON_PATH)
        self.iconLabel.setPixmap(icon_to_pixmap(icon, icon_size))
        self.titleLabel.setText(title)
        self.infoLabel.setText(message)

        if details:
            self.details = QtWidgets.QTextBrowser(self)
            self.details.setText("".join(details))
            self.details.setOpenExternalLinks(True)

        self.buttonBox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        self.buttonBox.accepted.connect(self.accept)

        self.gridLayout.addWidget(self.iconLabel, 0, 0, 2, 1)
        self.gridLayout.addWidget(self.titleLabel, 0, 1, 1, 1)
        self.gridLayout.addWidget(self.infoLabel, 1, 1, 1, 1)
        if details:
            self.gridLayout.addWidget(self.details, 2, 1, 1, 1)
        self.gridLayout.addWidget(self.buttonBox, 3, 1, -1, -1)

        self.adjustSize()

    def setAcceptButtonName(self, name):
        self.buttonBox.buttons()[0].setText(name)

    def addCancelButton(self, name="Cancel"):
        self._cancelButton = self.buttonBox.addButton(QtWidgets.QDialogButtonBox.Cancel)
        self._cancelButton.setText(name)
        self._cancelButton.clicked.connect(self.close)

    def setCancelButtonName(self, name):
        self._cancelButton.setText(name)

    def addSecondAcceptButton(self, name, icon="dialog-ok"):
        self._acceptButton2 = self.buttonBox.addButton(QtWidgets.QDialogButtonBox.Ignore)
        self._acceptButton2.setText(name)
        if isinstance(icon, QtGui.QIcon):
            self._acceptButton2.setIcon(icon)
        elif isinstance(icon, str):
            self._acceptButton2.setIcon(QtGui.QIcon.fromTheme(icon))
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
    config_name = os.getenv("MAESTRAL_CONFIG", "maestral")

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

    if not is_macos_bundle:
        stop_maestral_daemon_process(config_name)
    QtCore.QCoreApplication.quit()
    sys.exit(0)


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
        font = QtGui.QFont("Arial Rounded MT Bold")
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


class FaderWidget(QtWidgets.QWidget):

    pixmap_opacity = 1.0

    def __init__(self, old_widget, new_widget, duration=300):
        QtWidgets.QWidget.__init__(self, new_widget)

        pr = QWindow().devicePixelRatio()
        self.old_pixmap = QPixmap(new_widget.size()*pr)
        self.old_pixmap.setDevicePixelRatio(pr)
        old_widget.render(self.old_pixmap)

        self.timeline = QtCore.QTimeLine()
        self.timeline.valueChanged.connect(self.animate)
        self.timeline.finished.connect(self.close)
        self.timeline.setDuration(duration)
        self.timeline.start()

        self.resize(new_widget.size())
        self.show()

    def paintEvent(self, event):
        painter = QPainter()
        painter.begin(self)
        painter.setOpacity(self.pixmap_opacity)
        painter.drawPixmap(0, 0, self.old_pixmap)
        painter.end()

    def animate(self, value):
        self.pixmap_opacity = 1.0 - value
        self.repaint()


class AnimatedStackedWidget(QtWidgets.QStackedWidget):
    """
    A subclass of ``QStackedWidget`` with sliding or fading animations between stacks.
    """

    def __init__(self, parent=None):
        super(AnimatedStackedWidget, self).__init__(parent)

        self.m_direction = Qt.Horizontal
        self.m_speed = 300
        self.m_animationtype = QtCore.QEasingCurve.OutCubic
        self.m_now = 0
        self.m_next = 0
        self.m_wrap = False
        self.m_pnow = QtCore.QPoint(0, 0)
        self.m_active = False

    def setDirection(self, direction):
        self.m_direction = direction

    def setSpeed(self, speed):
        self.m_speed = speed

    def setAnimation(self, animationtype):
        self.m_animationtype = animationtype

    def setWrap(self, wrap):
        self.m_wrap = wrap

    @QtCore.pyqtSlot()
    def slideInPrev(self):
        now = self.currentIndex()
        if self.m_wrap or now > 0:
            self.slideInIdx(now - 1)

    @QtCore.pyqtSlot()
    def slideInNext(self):
        now = self.currentIndex()
        if self.m_wrap or now < (self.count() - 1):
            self.slideInIdx(now + 1)

    def slideInIdx(self, idx):
        if idx > (self.count() - 1):
            idx = idx % self.count()
        elif idx < 0:
            idx = (idx + self.count()) % self.count()
        self.slideInWgt(self.widget(idx))

    def slideInWgt(self, newwidget):
        if self.m_active:
            return

        self.m_active = True

        _now = self.currentIndex()
        _next = self.indexOf(newwidget)

        if _now == _next:
            self.m_active = False
            return

        offsetx, offsety = self.frameRect().width(), self.frameRect().height()
        self.widget(_next).setGeometry(self.frameRect())

        if not self.m_direction == Qt.Horizontal:
            if _now < _next:
                offsetx, offsety = 0, -offsety
            else:
                offsetx = 0
        else:
            if _now < _next:
                offsetx, offsety = -offsetx, 0
            else:
                offsety = 0

        pnext = self.widget(_next).pos()
        pnow = self.widget(_now).pos()
        self.m_pnow = pnow

        offset = QtCore.QPoint(offsetx, offsety)
        self.widget(_next).move(pnext - offset)
        self.widget(_next).show()
        self.widget(_next).raise_()

        anim_group = QtCore.QParallelAnimationGroup(
            self, finished=self.animationDoneSlot
        )

        for index, start, end in zip(
            (_now, _next), (pnow, pnext - offset), (pnow + offset, pnext)
        ):
            animation = QtCore.QPropertyAnimation(
                self.widget(index),
                b"pos",
                duration=self.m_speed,
                easingCurve=self.m_animationtype,
                startValue=start,
                endValue=end,
            )
            anim_group.addAnimation(animation)

        self.m_next = _next
        self.m_now = _now
        self.m_active = True
        anim_group.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

    @QtCore.pyqtSlot()
    def animationDoneSlot(self):
        self.setCurrentIndex(self.m_next)
        self.widget(self.m_now).hide()
        self.widget(self.m_now).move(self.m_pnow)
        self.m_active = False

    def fadeInIdx(self, index):
        self.fader_widget = FaderWidget(self.currentWidget(), self.widget(index),
                                        self.m_speed)
        self.setCurrentIndex(index)


class QProgressIndicator(QtWidgets.QWidget):
    """
    A macOS style spinning progress indicator. ``QProgressIndicator`` automatically
    detects and adjusts to "dark mode" appearances.
    """

    m_angle = None
    m_timerId = None
    m_delay = None
    m_displayedWhenStopped = None
    m_color = None
    m_light_color = QtGui.QColor(170, 170, 170)
    m_dark_color = QtGui.QColor(40, 40, 40)

    def __init__(self, parent=None):
        # Call parent class constructor first
        super(QProgressIndicator, self).__init__(parent)

        # Initialize instance variables
        self.m_angle = 0
        self.m_timerId = -1
        self.m_delay = 5/60*1000
        self.m_displayedWhenStopped = False
        self.m_color = self.m_dark_color

        self.update_dark_mode()

        # Set size and focus policy
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.NoFocus)

    def animationDelay(self):
        return self.delay

    def isAnimated(self):
        return self.m_timerId != -1

    def isDisplayedWhenStopped(self):
        return self.m_displayedWhenStopped

    def getColor(self):
        return self.color

    def sizeHint(self):
        return QtCore.QSize(20, 20)

    def startAnimation(self):
        self.m_angle = 0

        if self.m_timerId == -1:
            self.m_timerId = self.startTimer(self.m_delay)

    def stopAnimation(self):
        if self.m_timerId != -1:
            self.killTimer(self.m_timerId)

        self.m_timerId = -1
        self.update()

    def setAnimationDelay(self, delay):
        if self.m_timerId != -1:
            self.killTimer(self.m_timerId)

        self.m_delay = delay

        if self.m_timerId != -1:
            self.m_timerId = self.startTimer(self.m_delay)

    def setDisplayedWhenStopped(self, state):
        self.m_displayedWhenStopped = state
        self.update()

    def setColor(self, color):
        self.m_color = color
        self.update()

    def timerEvent(self, event):
        self.m_angle = (self.m_angle + 30) % 360
        self.update()

    def paintEvent(self, event):
        if (not self.m_displayedWhenStopped) and (not self.isAnimated()):
            return

        width = min(self.width(), self.height())

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        outerRadius = (width - 1) * 0.5
        innerRadius = (width - 1) * 0.5 * 0.4375

        capsuleHeight = outerRadius - innerRadius
        capsuleWidth  = width * 3/32
        capsuleRadius = capsuleWidth / 2

        for i in range(0, 12):
            color = QtGui.QColor(self.m_color)

            if self.isAnimated():
                color.setAlphaF(1.0 - (i / 12.0))
            else:
                color.setAlphaF(0.2)

            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.save()
            painter.translate(self.rect().center())
            painter.rotate(self.m_angle - (i * 30.0))
            painter.drawRoundedRect(capsuleWidth * -0.5,
                                    (innerRadius + capsuleHeight) * -1, capsuleWidth,
                                    capsuleHeight, capsuleRadius, capsuleRadius)
            painter.restore()

    def changeEvent(self, QEvent):

        if QEvent.type() == QtCore.QEvent.PaletteChange:
            self.update_dark_mode()

    def update_dark_mode(self):
        if isDarkWindow():
            self.setColor(self.m_light_color)
        else:
            self.setColor(self.m_dark_color)
