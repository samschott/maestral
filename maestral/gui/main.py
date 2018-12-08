# -*- coding: utf-8 -*-
import sys
import os
import logging
import subprocess
import platform
import webbrowser
from blinker import signal
from PyQt5 import QtCore, QtWidgets, QtGui

from ..main import Maestral
from ..config.main import CONF
from .settings import SettingsWindow
from .first_sync_dialog import FirstSyncDialog

_root = QtCore.QFileInfo(__file__).absolutePath()


class TestBB(object):

    class TestClient(object):

        dropbox_path = os.path.expanduser('~/Dropbox')

        def __init__(self, *args, **kwargs):
            pass

        def is_excluded(self, *args, **kwargs):
            return False

        def list_folder(self, *args, **kwargs):
            return None

        def flatten_results_list(self, *args, **kwargs):
            return {'Test Folder 1': None, 'Test Folder 2': None}

        def include_folder(self):
            pass

        def exclude_folder(self):
            pass

    client = TestClient()
    connected = True
    syncing = True
    notify = True

    def __init__(self, *args, **kwargs):
        pass

    def unlink(self):
        pass

    def pause_sync(self):
        pass

    def resume_sync(self):
        pass


class InfoHanlder(logging.Handler, QtCore.QObject):
    """
    Handler which emits a signal containing the logging message for every
    logged event. The signal will be connected to "Status" field of the GUI.
    """
    info_signal = QtCore.pyqtSignal(str)
    usage_signal = QtCore.pyqtSignal(str)

    disconnected_signal = QtCore.pyqtSignal()
    idle_signal = QtCore.pyqtSignal()
    paused_signal = QtCore.pyqtSignal()
    syncing_signal = QtCore.pyqtSignal()

    monitor_usage_signal = signal("account_usage_signal")

    def __init__(self):
        logging.Handler.__init__(self)
        QtCore.QObject.__init__(self)
        self.monitor_usage_signal.connect(self.on_usage_available)

    def emit(self, record):
        self.format(record)
        self.info_signal.emit(record.message)
        if record.message == "Connecting...":
            self.disconnected_signal.emit()
        elif record.message == "Up to date":
            self.idle_signal.emit()
        elif record.message == "Syncing paused":
            self.paused_signal.emit()
        else:
            self.syncing_signal.emit()

    def on_usage_available(self, space_usage):
        self.usage_signal.emit(str(space_usage))


info_handler = InfoHanlder()
info_handler.setLevel(logging.INFO)

for logger_name in ["maestral.monitor", "maestral.main", "maestral.client"]:
    mdbx_logger = logging.getLogger(logger_name)
    mdbx_logger.addHandler(info_handler)


class MaestralApp(QtWidgets.QSystemTrayIcon):

    # DARK = os.popen("defaults read -g AppleInterfaceStyle &> /dev/null").read() == "Dark"
    FIRST_SYNC = (not CONF.get("internal", "lastsync") or
                  CONF.get("internal", "cursor") == "" or
                  not os.path.isdir(CONF.get("main", "path")))

    def __init__(self, parent=None):
        # Load menu bar icons as instance attributes and not as class
        # attributes since QApplication may not be running.
        self.icon_idle = QtGui.QIcon(_root + "/resources/menubar_icon_idle.svg")
        self.icon_syncing = QtGui.QIcon(_root + "/resources/menubar_icon_syncing.svg")
        self.icon_paused = QtGui.QIcon(_root + "/resources/menubar_icon_paused.svg")
        self.icon_disconnected = QtGui.QIcon(_root + "/resources/menubar_icon_disconnected.svg")

        self.icon_idle.setIsMask(True)
        self.icon_syncing.setIsMask(True)
        self.icon_disconnected.setIsMask(True)
        self.icon_paused.setIsMask(True)

        # initialize system tray widget
        QtWidgets.QSystemTrayIcon.__init__(self, self.icon_disconnected, parent)
        self.menu = QtWidgets.QMenu()
        self.show()

        self.start_maestral()

    def start_maestral(self):
        # start Maestral
        if self.FIRST_SYNC:  # run configuration wizard on first startup
            self.mdbx = FirstSyncDialog.configureMaestral(parent=None)

            if self.mdbx is None:
                self.deleteLater()
                QtCore.QCoreApplication.quit()
            else:
                self.mdbx.download_complete_signal.connect(self.mdbx.start_sync)
                self.setup_ui()
                self.on_syncing()

        else:  # start Maestral normally otherwise
            self.mdbx = Maestral()
            self.setup_ui()

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
        info_handler.disconnected_signal.connect(self.on_disconnected)
        info_handler.idle_signal.connect(self.on_idle)
        info_handler.paused_signal.connect(self.on_paused)
        info_handler.syncing_signal.connect(self.on_syncing)

        # connect actions
        self.openFolderAction.triggered.connect(self.on_open_folder_cliked)
        self.openWebsiteAction.triggered.connect(self.on_website_clicked)
        self.startstopAction.triggered.connect(self.on_start_stop_clicked)
        self.preferencesAction.triggered.connect(self.settings.show)
        self.preferencesAction.triggered.connect(self.settings.raise_)
        self.preferencesAction.triggered.connect(self.settings.activateWindow)
        self.helpAction.triggered.connect(self.on_help_clicked)
        self.quitAction.triggered.connect(self.quit_)

    def on_open_folder_cliked(self):
        """
        Opens Dropbox directory in systems file explorer.
        """
        if platform.system() == "Windows":
            os.startfile(self.mdbx.client.dropbox_path)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", self.mdbx.client.dropbox_path])
        else:
            subprocess.Popen(["xdg-open", self.mdbx.client.dropbox_path])

    def on_website_clicked(self):
        webbrowser.open_new("https://www.dropbox.com/")

    def on_help_clicked(self):
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

    def on_disconnected(self):
        self.setIcon(self.icon_disconnected)

    def on_idle(self):
        self.setIcon(self.icon_idle)

    def on_syncing(self):
        self.setIcon(self.icon_syncing)

    def on_paused(self):
        self.setIcon(self.icon_paused)


def get_qt_app(*args, **kwargs):
    """
    Create a new Qt app or return an existing one.
    """
    created = False
    app = QtCore.QCoreApplication.instance()

    if not app:
        if not args:
            args = ([""],)
        app = QtWidgets.QApplication(*args, **kwargs)
        created = True

    return app, created


def run():
    app, created = get_qt_app()
    app.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)
    app.setQuitOnLastWindowClosed(False)

    maestral_gui = MaestralApp()
    maestral_gui.show()

    if created:
        sys.exit(app.exec_())
