# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

import sys
import os
import logging
import platform
import subprocess
import webbrowser
import shutil
from blinker import signal
from PyQt5 import QtCore, QtWidgets
from PyQt5.QtGui import QIcon

from maestral.main import Maestral
from maestral.monitor import IDLE, SYNCING, PAUSED, DISCONNECTED
from maestral.config.main import CONF
from maestral.gui.settings import SettingsWindow
from maestral.gui.first_sync_dialog import FirstSyncDialog

_root = QtCore.QFileInfo(__file__).absolutePath()

FIRST_SYNC = (not CONF.get("internal", "lastsync") or
              CONF.get("internal", "cursor") == "" or
              not os.path.isdir(CONF.get("main", "path")))
ICON_PATH = _root + "/resources/menubar_icon_"
logger = logging.getLogger(__name__)

HAS_GTK_LAUNCH = shutil.which("gtk-launch") is not None


class InfoHandler(logging.Handler, QtCore.QObject):
    """
    Handler which emits a signal containing the logging message for every
    logged event. The signal will be connected to "Status" field of the GUI.
    """

    info_signal = QtCore.pyqtSignal(str)
    status_signal = QtCore.pyqtSignal(str)

    def __init__(self):
        logging.Handler.__init__(self)
        QtCore.QObject.__init__(self)

    def emit(self, record):
        self.format(record)
        self.info_signal.emit(record.message)


info_handler = InfoHandler()
info_handler.setLevel(logging.INFO)

for logger_name in ["maestral.monitor", "maestral.main", "maestral.client"]:
    mdbx_logger = logging.getLogger(logger_name)
    mdbx_logger.addHandler(info_handler)


# noinspection PyTypeChecker
class MaestralApp(QtWidgets.QSystemTrayIcon):

    usage_signal = signal("account_usage_signal")

    def __init__(self, mdbx, parent=None):
        # ------------- load try icons -------------------
        self.icons = dict()
        icon_color = ""

        if not platform.system() == "Darwin":
            from maestral.gui.ui import THEME
            if THEME is "dark":
                icon_color = "_white"
        short_status = ("idle", "syncing", "paused", "disconnected")
        for long, short in zip((IDLE, SYNCING, PAUSED, DISCONNECTED), short_status):
            self.icons[long] = QIcon(ICON_PATH + short + icon_color + ".svg")

        if platform.system() == "Darwin":
            # macOS will take care of adapting the icon color to the system theme if
            # the icons are given as "masks"
            for status in self.icons:
                self.icons[status].setIsMask(True)

        # ------------- initialize tray icon -------------------
        QtWidgets.QSystemTrayIcon.__init__(self, self.icons[IDLE], parent)
        self.show_when_systray_available()

        # ------------- set up remaining ui -------------------
        self.menu = QtWidgets.QMenu()
        self.mdbx = mdbx
        self.setup_ui()

    def show_when_systray_available(self):
        # If available, show icon, otherwise, set a timer to check back later.
        # This is a workaround for https://bugreports.qt.io/browse/QTBUG-61898
        if self.isSystemTrayAvailable():
            self.show()
        else:
            QtCore.QTimer.singleShot(1000, self.show_when_systray_available)

    def setup_ui(self):

        # ------------- create settings window -------------------
        self.settings = SettingsWindow(self.mdbx, parent=None)

        # ------------- populate context menu -------------------
        self.openDropboxFolderAction = self.menu.addAction("Open Dropbox Folder")
        self.openWebsiteAction = self.menu.addAction("Launch Dropbox Website")

        self.separator1 = self.menu.addSeparator()

        self.accountUsageAction = self.menu.addAction(CONF.get("account", "usage"))
        self.accountUsageAction.setEnabled(False)

        self.separator2 = self.menu.addSeparator()

        if self.mdbx.connected and self.mdbx.syncing:
            self.statusAction = self.menu.addAction(IDLE)
        elif self.mdbx.connected and not self.mdbx.syncing:
            self.statusAction = self.menu.addAction(PAUSED)
        elif not self.mdbx.connected:
            self.statusAction = self.menu.addAction(DISCONNECTED)
        self.statusAction.setEnabled(False)
        if self.mdbx.syncing:
            self.pauseAction = self.menu.addAction("Pause Syncing")
        else:
            self.pauseAction = self.menu.addAction("Resume Syncing")
        self.recentFilesMenu = self.menu.addMenu("Recently Changed Files")

        self.separator3 = self.menu.addSeparator()

        self.preferencesAction = self.menu.addAction("Preferences...")
        self.helpAction = self.menu.addAction("Help Center")
        self.separator4 = self.menu.addSeparator()
        self.quitAction = self.menu.addAction("Quit Maestral")
        self.setContextMenu(self.menu)

        # ------------- connect callbacks for menu items -------------------
        self.openDropboxFolderAction.triggered.connect(
            lambda x: self.goto_file(self.mdbx.client.dropbox_path))
        self.openWebsiteAction.triggered.connect(self.on_website_clicked)
        self.pauseAction.triggered.connect(self.on_start_stop_clicked)
        self.preferencesAction.triggered.connect(self.settings.show)
        self.preferencesAction.triggered.connect(self.settings.raise_)
        self.preferencesAction.triggered.connect(self.settings.activateWindow)
        self.helpAction.triggered.connect(self.on_help_clicked)
        self.quitAction.triggered.connect(self.quit_)

        if platform.system() == "Linux":
            # on linux, submenu.aboutToShow may not be emitted
            # (see https://bugreports.qt.io/browse/QTBUG-55911)
            # therefore, we update the recent files list when the tray icon menu is loaded
            self.menu.aboutToShow.connect(self.update_recent_files)
        else:
            self.recentFilesMenu.aboutToShow.connect(self.update_recent_files)

        def callback(action):
            dbx_path = action.data()
            local_path = self.mdbx.client.to_local_path(dbx_path)
            self.goto_file(local_path)

        self.recentFilesMenu.triggered.connect(callback)

        # ------------- connect UI to signals -------------------
        info_handler.info_signal.connect(self.statusAction.setText)
        info_handler.info_signal.connect(self.change_icon)
        self.usage_signal.connect(self.on_usage_available)

    # callbacks for user interaction

    @staticmethod
    def goto_file(path):
        path = os.path.abspath(os.path.normpath(path))
        if platform.system() == "Darwin":
            subprocess.run(["open", "--reveal", path])
        elif platform.system() == "Linux":
            if HAS_GTK_LAUNCH:
                # if gtk-launch is available, query for the default file manager and
                # reveal file in the latter
                file_manager = os.popen("xdg-mime query default inode/directory").read()
                subprocess.run(["gtk-launch", file_manager.strip(), path])
            else:
                # otherwise open containing directory
                subprocess.run(["xdg-open", os.path.dirname(path)])
        else:
            pass

    @staticmethod
    def on_website_clicked():
        webbrowser.open_new("https://www.dropbox.com/")

    @staticmethod
    def on_help_clicked():
        webbrowser.open_new("https://dropbox.com/help")

    def on_start_stop_clicked(self):
        if self.pauseAction.text() == "Pause Syncing":
            self.mdbx.pause_sync()
            self.pauseAction.setText("Resume Syncing")
        elif self.pauseAction.text() == "Resume Syncing":
            self.mdbx.resume_sync()
            self.pauseAction.setText("Pause Syncing")

    def on_usage_available(self, space_usage):
        usage_string = str(space_usage)
        self.accountUsageAction.setText(usage_string)
        self.settings.labelSpaceUsage2.setText(usage_string)

    # callbacks to update GUI

    def update_recent_files(self):
        self.recentFilesMenu.clear()
        for dbx_path in reversed(CONF.get("internal", "recent_changes")):
            file_name = os.path.basename(dbx_path)
            action = self.recentFilesMenu.addAction(file_name)
            action.setData(dbx_path)

    def change_icon(self, status):
        new_icon = self.icons.get(status, self.icons[SYNCING])
        self.setIcon(new_icon)

    def quit_(self):
        self.mdbx.stop_sync()
        self.deleteLater()
        QtCore.QCoreApplication.quit()


def run():
    app = QtWidgets.QApplication([""])
    app.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)
    app.setQuitOnLastWindowClosed(False)

    if FIRST_SYNC:
        maestral = FirstSyncDialog.configureMaestral()  # returns None if aborted by user
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
