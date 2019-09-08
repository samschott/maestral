# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

# system imports
import sys
import os
import logging
import platform
import subprocess
import webbrowser
import shutil

# external packages
from PyQt5 import QtCore, QtWidgets

# maestral modules
from maestral.sync.main import Maestral
from maestral.sync.monitor import IDLE, SYNCING, PAUSED, DISCONNECTED, SYNC_ERROR
from maestral.gui.settings_window import SettingsWindow
from maestral.gui.setup_dialog import SetupDialog
from maestral.gui.relink_dialog import RelinkDialog
from maestral.gui.sync_issues_window import SyncIssueWindow
from maestral.gui.rebuild_index_dialog import RebuildIndexDialog
from maestral.gui.resources import get_system_tray_icon
from maestral.gui.utils import (elide_string, UserDialog, quit_and_restart_maestral,
                                get_gnome_scaling_factor)
from maestral.gui.autostart import AutoStart
from maestral.config.main import CONF
from maestral.sync.daemon import (start_daemon_subprocess, stop_maestral_daemon,
                                  get_maestral_process_info, get_maestral_daemon_proxy)

logger = logging.getLogger(__name__)

HAS_GTK_LAUNCH = shutil.which("gtk-launch") is not None
CONFIG_NAME = os.getenv("MAESTRAL_CONFIG", "maestral")


# noinspection PyTypeChecker
class MaestralGuiApp(QtWidgets.QSystemTrayIcon):

    mdbx = None

    def __init__(self):
        # ------------- initialize tray icon -------------------
        QtWidgets.QSystemTrayIcon.__init__(self)

        self.icons = self.load_tray_icons()
        self.setIcon(self.icons[DISCONNECTED])
        self.show_when_systray_available()

        self.menu = QtWidgets.QMenu()
        self.setContextMenu(self.menu)

        self.setup_ui_unlinked()

        self._n_errors = None
        self._status = None

        self.update_ui_timer = QtCore.QTimer()
        self.update_ui_timer.timeout.connect(self.update_ui)
        self.update_ui_timer.start(200)  # every 200 ms

    def update_ui(self):
        self.on_error()
        if self.mdbx:
            self.on_status()

    def show_when_systray_available(self):
        # If available, show icon, otherwise, set a timer to check back later.
        # This is a workaround for https://bugreports.qt.io/browse/QTBUG-61898
        if self.isSystemTrayAvailable():
            self.setIcon(self.icon())  # reload icon
            self.show()
        else:
            QtCore.QTimer.singleShot(1000, self.show_when_systray_available)

    @staticmethod
    def load_tray_icons():

        icons = dict()
        short = ("idle", "syncing", "paused", "disconnected", "error")

        for l, s in zip((IDLE, SYNCING, PAUSED, DISCONNECTED, SYNC_ERROR), short):
            icons[l] = get_system_tray_icon(s)

        return icons

    def load_maestral(self):

        pending_link = Maestral.pending_link()
        pending_dbx_folder = Maestral.pending_dropbox_folder()

        if pending_link or pending_dbx_folder:
            logger.info("Setting up Maestral...")
            done = SetupDialog.configureMaestral(pending_link)
            if done:
                self.mdbx = self._get_or_start_maestral_daemon()
                self.mdbx.get_remote_dropbox_async("", callback=self.mdbx.start_sync)
                logger.info("Successfully set up Maestral")
            else:
                logger.info("Setup aborted. Quitting.")
                self.quit()
        else:
            self.mdbx = self._get_or_start_maestral_daemon()
            self.mdbx.start_sync()

        self.setup_ui_linked()

    @staticmethod
    def _get_or_start_maestral_daemon():
        pid, _ = get_maestral_process_info(CONFIG_NAME)
        if not pid:
            start_daemon_subprocess(CONFIG_NAME)

        return get_maestral_daemon_proxy(CONFIG_NAME)

    def setup_ui_unlinked(self):

        self.setToolTip("Not linked.")

        self.autostart = AutoStart()

        # ------------- populate context menu -------------------

        self.menu.clear()

        self.openDropboxFolderAction = self.menu.addAction("Open Dropbox Folder")
        self.openDropboxFolderAction.setEnabled(False)
        self.openWebsiteAction = self.menu.addAction("Launch Dropbox Website")

        self.separator1 = self.menu.addSeparator()

        self.accountEmailAction = self.menu.addAction("Not linked")
        self.accountEmailAction.setEnabled(False)

        self.separator2 = self.menu.addSeparator()

        self.loginAction = self.menu.addAction("Start on login")
        self.loginAction.setCheckable(True)
        self.loginAction.triggered.connect(self.autostart.toggle)
        self.helpAction = self.menu.addAction("Help Center")

        self.separator5 = self.menu.addSeparator()

        self.quitAction = self.menu.addAction("Quit Maestral")

        # ------------- connect callbacks for menu items -------------------
        self.openDropboxFolderAction.triggered.connect(
            lambda: self.open_destination(self.mdbx.dropbox_path))
        self.openWebsiteAction.triggered.connect(self.on_website_clicked)
        self.loginAction.setChecked(self.autostart.enabled)
        self.helpAction.triggered.connect(self.on_help_clicked)
        self.quitAction.triggered.connect(self.quit)

    def setup_ui_linked(self):

        if not self.mdbx:
            return

        self.setToolTip(IDLE)

        # ----------------- create windows ----------------------
        self.settings = SettingsWindow(self.mdbx, parent=None)
        self.sync_issues_window = SyncIssueWindow(self.mdbx)

        # ------------- populate context menu -------------------

        self.menu.clear()

        self.openDropboxFolderAction = self.menu.addAction("Open Dropbox Folder")
        self.openWebsiteAction = self.menu.addAction("Launch Dropbox Website")

        self.separator1 = self.menu.addSeparator()

        self.accountEmailAction = self.menu.addAction(CONF.get("account", "email"))
        self.accountEmailAction.setEnabled(False)

        self.accountUsageAction = self.menu.addAction(CONF.get("account", "usage"))
        self.accountUsageAction.setEnabled(False)

        self.separator2 = self.menu.addSeparator()

        self.statusAction = self.menu.addAction(IDLE)
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

        self.syncIssuesAction = self.menu.addAction("Show Sync Issues...")
        self.rebuiltAction = self.menu.addAction("Rebuild index...")

        self.separator5 = self.menu.addSeparator()

        self.quitAction = self.menu.addAction("Quit Maestral")

        # --------- connect callbacks for menu items ------------
        self.openDropboxFolderAction.triggered.connect(
            lambda: self.open_destination(self.mdbx.dropbox_path))
        self.openWebsiteAction.triggered.connect(self.on_website_clicked)
        self.pauseAction.triggered.connect(self.on_start_stop_clicked)
        self.preferencesAction.triggered.connect(self.settings.show)
        self.preferencesAction.triggered.connect(self.settings.raise_)
        self.preferencesAction.triggered.connect(self.settings.activateWindow)
        self.syncIssuesAction.triggered.connect(self.sync_issues_window.reload)
        self.syncIssuesAction.triggered.connect(self.sync_issues_window.show)
        self.syncIssuesAction.triggered.connect(self.sync_issues_window.raise_)
        self.syncIssuesAction.triggered.connect(self.sync_issues_window.activateWindow)
        self.rebuiltAction.triggered.connect(self.on_rebuild)
        self.helpAction.triggered.connect(self.on_help_clicked)
        self.quitAction.triggered.connect(self.quit)

        if platform.system() == "Linux":
            # on linux, submenu.aboutToShow may not be emitted
            # (see https://bugreports.qt.io/browse/QTBUG-55911)
            # therefore, we update the recent files list when the tray icon menu is loaded
            self.menu.aboutToShow.connect(self.update_recent_files)
        else:
            self.recentFilesMenu.aboutToShow.connect(self.update_recent_files)

        # --------------- switch to idle icon -------------------
        self.setIcon(self.icons[IDLE])

    # callbacks for user interaction

    @staticmethod
    def open_destination(path, reveal=False):
        """Open the item at the given path. If the item is a file, attempt to open it
        in the systems default program. If ``reveal == True``, reveal the file in the
        systems default file manager instead."""
        path = os.path.abspath(os.path.normpath(path))
        if platform.system() == "Darwin":
            if reveal:
                subprocess.run(["open", "--reveal", path])
            else:
                subprocess.run(["open", path])
        elif platform.system() == "Linux":
            if reveal:
                if HAS_GTK_LAUNCH:
                    # if gtk-launch is available, query for the default file manager and
                    # reveal file in the latter
                    file_manager = os.popen("xdg-mime query default inode/directory").read()
                    subprocess.run(["gtk-launch", file_manager.strip(), path])
                else:
                    # otherwise open the containing directory
                    if not os.path.isdir(path):
                        path = os.path.dirname(path)
                    subprocess.run(["xdg-open", path])
            else:
                subprocess.run(["xdg-open", path])
        else:
            pass

    @staticmethod
    def on_website_clicked():
        """Open the Dropbox website."""
        webbrowser.open_new("https://www.dropbox.com/")

    @staticmethod
    def on_help_clicked():
        """Open the Dropbox help website."""
        webbrowser.open_new("https://dropbox.com/help")

    def on_start_stop_clicked(self):
        """Pause / resume syncing on menu item clicked."""
        if self.pauseAction.text() == "Pause Syncing":
            self.mdbx.pause_sync()
            self.pauseAction.setText("Resume Syncing")
        elif self.pauseAction.text() == "Resume Syncing":
            self.mdbx.resume_sync()
            self.pauseAction.setText("Pause Syncing")
        elif self.pauseAction.text() == "Start Syncing":
            self.mdbx.start_sync()
            self.pauseAction.setText("Pause Syncing")

    def on_space_usage(self):
        """Update account usage info in UI."""
        self.accountUsageAction.setText(CONF.get("account", "usage"))

    def on_error(self):
        errs = self.mdbx.get_maestral_errors()
        self.mdbx.clear_maestral_errors()

        if len(errs) == 0:
            return

        err = errs[-1]

        if err["type"] in ("RevFileError", "BadInputError"):
            # show error dialog to user
            title = "Maestral Error"
            message = err["message"]
            self._stop_and_exec_error_dialog(title, message)
        elif err["type"] == "CursorResetError":
            title = "Dropbox has reset its sync state."
            message = 'Please go to "Rebuild index..." to re-sync your Dropbox.'
            self._stop_and_exec_error_dialog(title, message)
        elif err["type"] == "DropboxDeletedError":
            self.mdbx.stop_sync()
            quit_and_restart_maestral()
        elif err["type"] == "DropboxAuthError":
            self._stop_and_exec_relink_dialog(RelinkDialog.REVOKED)
        elif err["type"] == "TokenExpiredError":
            self._stop_and_exec_relink_dialog(RelinkDialog.EXPIRED)
        else:
            title = "An unexpected error occurred."
            message = "Please contact the Maestral developer with the information below."
            self._stop_and_exec_error_dialog(title, message, err["traceback"])

    def on_rebuild(self):

        self.rebuild_dialog = RebuildIndexDialog(self.mdbx)
        self.rebuild_dialog.show()
        self.rebuild_dialog.activateWindow()
        self.rebuild_dialog.raise_()

    def _stop_and_exec_relink_dialog(self, reason):
        self.setIcon(self.icons[SYNC_ERROR])

        if self.mdbx:
            self.mdbx.stop_sync()
        if hasattr(self, "pauseAction"):
            self.pauseAction.setText("Start Syncing")
            self.pauseAction.setEnabled(False)

        relink_dialog = RelinkDialog(reason)
        # Will either just return (Cancel), relink the account (Link) or unlink it and
        # delete the old creds (Unlink). In the first case

        relink_dialog.exec_()  # this will perform quit actions as appropriate

    def _stop_and_exec_error_dialog(self, title, message, exc_info=None):
        self.setIcon(self.icons[SYNC_ERROR])

        if self.mdbx:
            self.mdbx.stop_sync()
        if hasattr(self, "pauseAction"):
            self.pauseAction.setText("Start Syncing")

        error_dialog = UserDialog(title, message, exc_info)
        error_dialog.exec_()

    # callbacks to update GUI

    def update_recent_files(self):
        """Update menu with list of recently changed files."""
        self.recentFilesMenu.clear()
        for dbx_path in reversed(CONF.get("internal", "recent_changes")):
            file_name = os.path.basename(dbx_path)
            local_path = self.mdbx.sync.to_local_path(dbx_path)
            truncated_name = elide_string(file_name, font=self.menu.font(),
                                          side="right")
            a = self.recentFilesMenu.addAction(truncated_name)
            a.triggered.connect(lambda: self.open_destination(local_path, reveal=True))

    def on_status(self):
        """Change icon according to status."""

        n_errors = len(self.mdbx.sync_errors)
        status = self.mdbx.status

        if status == self._status and n_errors == self._n_errors:
            return

        if n_errors > 0:
            self.syncIssuesAction.setText("Show Sync Issues ({0})...".format(n_errors))
        else:
            self.syncIssuesAction.setText("Show Sync Issues...")

        if self.mdbx.paused:
            new_icon = self.icons[PAUSED]
        elif n_errors > 0:
            new_icon = self.icons[SYNC_ERROR]
        else:
            new_icon = self.icons.get(status, self.icons[SYNCING])

        self.setIcon(new_icon)
        self.statusAction.setText(status)
        self.setToolTip(status)

        self._n_errors = n_errors
        self._status = status

    def setToolTip(self, text):
        if not platform.system() == "Darwin":
            # tray icons in macOS should not have tooltips
            QtWidgets.QSystemTrayIcon.setToolTip(self, text)

    def quit(self):
        """Quit Maestral"""
        if self.mdbx:
            self.mdbx.stop_sync()
            stop_maestral_daemon(CONFIG_NAME)
        self.deleteLater()
        QtCore.QCoreApplication.quit()
        sys.exit(0)


def run():
    gsf = get_gnome_scaling_factor()
    if gsf:
        os.environ["QT_SCREEN_SCALE_FACTORS"] = gsf

    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)

    app = QtWidgets.QApplication(["Maestral"])
    app.setQuitOnLastWindowClosed(False)

    maestral_gui = MaestralGuiApp()
    maestral_gui.load_maestral()
    sys.exit(app.exec_())


if __name__ == "__main__":
    run()
