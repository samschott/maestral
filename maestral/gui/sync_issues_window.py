# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import os
import platform
import subprocess
import shutil
from PyQt5 import QtCore, QtGui, QtWidgets, uic

from maestral.gui.resources import (SYNC_ISSUES_WINDOW_PATH, SYNC_ISSUE_WIDGET_PATH,
                                    get_native_item_icon)
from maestral.gui.utils import (truncate_string, get_scaled_font, isDarkWindow,
                                LINE_COLOR_DARK, LINE_COLOR_LIGHT)

HAS_GTK_LAUNCH = shutil.which("gtk-launch") is not None


class SyncIssueWidget(QtWidgets.QWidget):
    """
    A class to graphically display a Maestral sync issue.
    """

    def __init__(self, sync_issue, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        uic.loadUi(SYNC_ISSUE_WIDGET_PATH, self)

        self.sync_issue = sync_issue

        self.errorLabel.setFont(get_scaled_font(scaling=0.85))
        self.update_dark_mode()  # set appropriate item icon and colors in style sheet

        self.pathLabel.setText(self.to_display_path(self.sync_issue.local_path))
        self.errorLabel.setText(self.sync_issue.title + ":\n" + self.sync_issue.message)

        def request_context_menu():
            self.actionButton.customContextMenuRequested.emit(self.actionButton.pos())

        self.actionButton.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.actionButton.pressed.connect(request_context_menu)
        self.actionButton.customContextMenuRequested.connect(self.showContextMenu)

    def showContextMenu(self, pos):

        self.actionButtonContextMenu = QtWidgets.QMenu()
        a1 = self.actionButtonContextMenu.addAction("Show Item in Folder")

        a1.triggered.connect(lambda: self.open_destination(self.sync_issue.local_path))
        self.actionButtonContextMenu.exec_(self.mapToGlobal(pos))

    def to_display_path(self, local_path):

        return truncate_string(os.path.basename(local_path), font=self.pathLabel.font(),
                               pixels=300, side="left")

    @staticmethod
    def open_destination(path, reveal=True):
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

    def changeEvent(self, QEvent):
        if QEvent.type() == QtCore.QEvent.PaletteChange:
            self.update_dark_mode()

    def update_dark_mode(self):
        # update style sheet with new colors
        line_rgb = LINE_COLOR_DARK if isDarkWindow() else LINE_COLOR_LIGHT
        bg_color = self.palette().color(QtGui.QPalette.Background)
        bg_color_rgb = [bg_color.red(), bg_color.green(), bg_color.blue()]
        # set background color to be slightly lighter than the window background
        frame_bg_color = [min([c + 16, 255]) for c in bg_color_rgb]
        self.frame.setStyleSheet("""
        .QFrame {{
            border: 1px solid rgb({0},{1},{2});
            background-color: rgb({3},{4},{5});
            border-radius: 7px;
        }}""".format(*line_rgb, *frame_bg_color))

        # update item icons (the system may supply different icons in dark mode)
        icon = get_native_item_icon(self.sync_issue.local_path)
        pixmap = icon.pixmap(self.iconLabel.width(), self.iconLabel.height())
        self.iconLabel.setPixmap(pixmap)


class SyncIssueWindow(QtWidgets.QWidget):
    """
    A class to graphically display all Maestral sync issues.
    """

    def __init__(self, sync_issues_queue, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        uic.loadUi(SYNC_ISSUES_WINDOW_PATH, self)
        self.setWindowFlags(QtCore.Qt.WindowStaysOnTopHint)

        self.sync_issues_queue = sync_issues_queue

        self.reload()

    def reload(self):

        self.clear()

        sync_issues_list = list(self.sync_issues_queue.queue)

        if len(sync_issues_list) == 0:
            no_issues_label = QtWidgets.QLabel("No sync issues :)")
            self.verticalLayout.addWidget(no_issues_label)
            self.sync_issue_widgets.append(no_issues_label)

        for issue in sync_issues_list:
            self.addIssue(issue)

        # self.verticalLayout.insertStretch(-1)

    def addIssue(self, sync_issue):

        issue_widget = SyncIssueWidget(sync_issue)
        self.sync_issue_widgets.append(issue_widget)
        self.verticalLayout.addWidget(issue_widget)

    def clear(self):

        while self.verticalLayout.itemAt(0):
            item = self.verticalLayout.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()

        self.sync_issue_widgets = []


if __name__ == "__main__":
    from maestral.client import MaestralApiError
    import queue

    app = QtWidgets.QApplication([""])
    app.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)

    text1 = ("Something went wrong with the job on Dropbox’s end. Please "
             "verify on the Dropbox website if the move succeeded and try "
             "again if it failed. This should happen very rarely.")

    text2 = ("There are too many write operations your "
             "Dropbox. Please try again later.")

    path = "/Users/samschott/Dropbox/MATLAB_scripts/CuSCN2 fitting/CuSCN2_5K_HFI_fit.mat"

    err1 = MaestralApiError("Could not download", text1, local_path=path)
    err2 = MaestralApiError("Could not delete folder", text2, local_path="/test_folder")

    err_queue = queue.Queue()
    err_queue.put(err1)
    err_queue.put(err2)
    err_queue.put(err2)
    err_queue.put(err2)
    err_queue.put(err2)

    w = SyncIssueWindow(err_queue)
    w.show()

    app.exec_()
