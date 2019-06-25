# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

import sys
import os
import logging
import subprocess
import platform
import webbrowser
from blinker import signal
from PyQt5 import QtCore, QtWidgets
from PyQt5.QtGui import QIcon

from maestral.main import Maestral
from maestral.config.main import CONF
from maestral.gui.settings import SettingsWindow
from maestral.gui.first_sync_dialog import FirstSyncDialog

_root = QtCore.QFileInfo(__file__).absolutePath()

FIRST_SYNC = (not CONF.get("internal", "lastsync") or
              CONF.get("internal", "cursor") == "" or
              not os.path.isdir(CONF.get("main", "path")))
ICON_PATH = _root + "/resources/menubar_icon_"
logger = logging.getLogger(__name__)


class InfoHandler(logging.Handler, QtCore.QObject):
    """
    Handler which emits a signal containing the logging message for every
    logged event. The signal will be connected to "Status" field of the GUI.
    """

    monitor_usage_signal = signal("account_usage_signal")

    info_signal = QtCore.pyqtSignal(str)
    usage_signal = QtCore.pyqtSignal(str)
    status_signal = QtCore.pyqtSignal(str)

    def __init__(self):
        logging.Handler.__init__(self)
        QtCore.QObject.__init__(self)
        self.monitor_usage_signal.connect(self.on_usage_available)

    def emit(self, record):
        self.format(record)
        self.info_signal.emit(record.message)
        if record.message == "Connecting...":
            self.status_signal.emit("disconnected")
        elif record.message == "Up to date":
            self.status_signal.emit("idle")
        elif record.message == "Syncing paused":
            self.status_signal.emit("paused")
        else:
            self.status_signal.emit("syncing")

    def on_usage_available(self, space_usage):
        self.usage_signal.emit(str(space_usage))


info_handler = InfoHandler()
info_handler.setLevel(logging.INFO)

for logger_name in ["maestral.monitor", "maestral.main", "maestral.client"]:
    mdbx_logger = logging.getLogger(logger_name)
    mdbx_logger.addHandler(info_handler)


# noinspection PyTypeChecker
class MaestralApp(QtWidgets.QSystemTrayIcon):

    def __init__(self, mdbx, parent=None):
        # Load menu bar icons as instance attributes and not as class
        # attributes since QApplication may not be running.
        self.icons = dict()
        icon_color = ""

        if not platform.system() == "Darwin":
            from maestral.gui.ui import THEME
            if THEME is "dark":
                icon_color = "_white"

        for status in ("idle", "syncing", "paused", "disconnected"):
            self.icons[status] = QIcon(ICON_PATH + status + icon_color + ".svg")

        if platform.system() == "Darwin":
            # macOS will take care of adapting the icon color to the system theme if
            # the icons are given as "masks"
            for state in self.icons:
                self.icons[state].setIsMask(True)

        # initialize system tray widget
        QtWidgets.QSystemTrayIcon.__init__(self, self.icons["disconnected"], parent)
        self.menu = QtWidgets.QMenu()
        self._show_when_systray_available()

        self.mdbx = mdbx
        self.setup_ui()

    def _show_when_systray_available(self):
        """Shows status icon when system tray is available

        If available, show icon, otherwise, set a timer to check back later.
        This is a workaround for https://bugreports.qt.io/browse/QTBUG-61898
        """
        if self.isSystemTrayAvailable():
            self.show()
        else:
            QtCore.QTimer.singleShot(1000, self._show_when_systray_available)

    def setup_ui(self):
        # create settings window
        self.settings = SettingsWindow(self.mdbx, parent=None)
        # populate context menu
        self.openFolderAction = self.menu.addAction("Open Dropbox Folder")
        self.openWebsiteAction = self.menu.addAction("Launch Dropbox Website")
        self.separator1 = self.menu.addSeparator()
        self.accountUsageAction = self.menu.addAction(CONF.get("account", "usage"))
        self.accountUsageAction.setEnabled(False)
        self.separator2 = self.menu.addSeparator()
        if self.mdbx.connected and self.mdbx.syncing:
            self.statusAction = self.menu.addAction("Up to date")
        elif self.mdbx.connected:
            self.statusAction = self.menu.addAction("Syncing paused")
        elif not self.mdbx.connected:
            self.statusAction = self.menu.addAction("Connecting...")
        self.statusAction.setEnabled(False)
        if self.mdbx.syncing:
            self.startstopAction = self.menu.addAction("Pause Syncing")
        else:
            self.startstopAction = self.menu.addAction("Resume Syncing")
        self.separator3 = self.menu.addSeparator()
        self.preferencesAction = self.menu.addAction("Preferences...")
        self.helpAction = self.menu.addAction("Help Center")
        self.separator4 = self.menu.addSeparator()
        self.quitAction = self.menu.addAction("Quit Maestral")
        self.setContextMenu(self.menu)

        # connect UI to signals
        info_handler.info_signal.connect(self.statusAction.setText)
        info_handler.usage_signal.connect(self.accountUsageAction.setText)
        info_handler.usage_signal.connect(self.settings.labelSpaceUsage2.setText)
        info_handler.status_signal.connect(self.on_status_changed)

        # connect actions
        self.openFolderAction.triggered.connect(self.on_open_folder_clicked)
        self.openWebsiteAction.triggered.connect(self.on_website_clicked)
        self.startstopAction.triggered.connect(self.on_start_stop_clicked)
        self.preferencesAction.triggered.connect(self.settings.show)
        self.preferencesAction.triggered.connect(self.settings.raise_)
        self.preferencesAction.triggered.connect(self.settings.activateWindow)
        self.helpAction.triggered.connect(self.on_help_clicked)
        self.quitAction.triggered.connect(self.quit_)

    def on_open_folder_clicked(self):
        """
        Opens Dropbox directory in systems file explorer.
        """
        if platform.system() == "Windows":
            os.startfile(self.mdbx.client.dropbox_path)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", self.mdbx.client.dropbox_path])
        else:
            subprocess.Popen(["xdg-open", self.mdbx.client.dropbox_path])

    @staticmethod
    def on_website_clicked():
        webbrowser.open_new("https://www.dropbox.com/")

    @staticmethod
    def on_help_clicked():
        webbrowser.open_new("https://dropbox.com/help")

    def on_start_stop_clicked(self):
        if self.startstopAction.text() == "Pause Syncing":
            self.mdbx.pause_sync()
            self.startstopAction.setText("Resume Syncing")
        elif self.startstopAction.text() == "Resume Syncing":
            self.mdbx.resume_sync()
            self.startstopAction.setText("Pause Syncing")

    def quit_(self):
        self.mdbx.stop_sync()
        self.deleteLater()
        QtCore.QCoreApplication.quit()

    def on_status_changed(self, status):
        self.setIcon(self.icons[status])


def run():
    app = QtWidgets.QApplication([""])
    app.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)
    app.setQuitOnLastWindowClosed(False)

    if FIRST_SYNC:
        maestral = FirstSyncDialog.configureMaestral()  # returns None if aborted
    else:
        maestral = Maestral()

    if maestral:
        maestral.download_complete_signal.connect(maestral.start_sync)
        maestral_gui = MaestralApp(maestral)
        sys.exit(app.exec_())
    else:
        logger.info("Setup aborted. Quitting.")


if __name__ == "__main__":
    run()
