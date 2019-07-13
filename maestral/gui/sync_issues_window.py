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
from PyQt5 import QtCore, QtWidgets, uic

from maestral.gui.resources import SYNC_ISSUES_WINDOW_PATH, get_native_item_icon
from maestral.gui.utils import truncate_string

HAS_GTK_LAUNCH = shutil.which("gtk-launch") is not None


class SyncIssueWidget(QtWidgets.QWidget):
    """
    A class to graphically display a Maestral sync issue.
    """

    def __init__(self, sync_issue, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        # self.setFixedWidth(500)
        self.sync_issue = sync_issue

        # set widgets
        self.gridLayout = QtWidgets.QGridLayout()
        self.gridLayout.setHorizontalSpacing(20)
        self.setLayout(self.gridLayout)

        self.iconLabel = QtWidgets.QLabel(self)
        self.iconLabel.setMinimumSize(50, 50)
        self.iconLabel.setMaximumSize(50, 50)

        self.pathLabel = QtWidgets.QLabel(self)

        self.errorLabel = QtWidgets.QLabel(self)
        self.errorLabel.setWordWrap(True)
        self.errorLabel.setStyleSheet("color: rgba(213, 0, 24, 162); font: 11pt")

        self.actionButton = QtWidgets.QPushButton(self)
        self.actionButton.setText("•••")
        self.actionButton.setStyleSheet("""
            QPushButton {
                border: none;
                background-color: none;
                font: Arial;
                font-size: 17pt
            }
            QPushButton:hover {
                color: rgb(11,95,255);
            }""")
        self.actionButton.setMinimumWidth(30)
        self.actionButton.setMaximumWidth(30)

        self.gridLayout.addWidget(self.iconLabel, 0, 0, 2, 1)
        self.gridLayout.addWidget(self.pathLabel, 0, 1, 1, 1)
        self.gridLayout.addWidget(self.errorLabel, 1, 1, 1, 1)
        self.gridLayout.addWidget(self.actionButton, 0, 2, 2, 1)

        # fill with content
        icon = get_native_item_icon(self.sync_issue.local_path)
        pixmap = icon.pixmap(self.iconLabel.width(), self.iconLabel.height())
        pixmap.setDevicePixelRatio(2.0)
        self.iconLabel.setPixmap(pixmap)

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

        a1.triggered.connect(lambda: self.open_destination(self.sync_issue.local_path, reveal=True))
        self.actionButtonContextMenu.exec_(self.mapToGlobal(pos))

    def to_display_path(self, local_path):

        return truncate_string(os.path.basename(local_path), font=self.pathLabel.font(),
                               pixels=300, side="left")

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


class SyncIssueWindow(QtWidgets.QWidget):
    """
    A class to graphically display all Maestral sync issues.
    """

    def __init__(self, sync_issues_queue, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        uic.loadUi(SYNC_ISSUES_WINDOW_PATH, self)

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

        self.verticalLayout.addStretch()

    def addIssue(self, sync_issue):

        if len(self.sync_issue_widgets) > 0:
            self._addLine()

        issue_widget = SyncIssueWidget(sync_issue)
        self.sync_issue_widgets.append(issue_widget)
        self.verticalLayout.addWidget(issue_widget)

    def clear(self):

        while self.verticalLayout.itemAt(0):
            item = self.verticalLayout.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()

        self.sync_issue_widgets = []

    def _addLine(self, width=350):
        """
        Adds a horizontal line to separate sections.

        :param int width: Width in pixels.
        :return: Instance of :class:`PyQt5.QtWidgets.QFrame`.
        """

        h_line = QtWidgets.QFrame(self)
        h_line.setFrameShape(QtWidgets.QFrame.HLine)
        h_line.setFixedWidth(width)
        h_line.setStyleSheet("color: rgb(205, 203, 205)")

        self.verticalLayout.addWidget(h_line, alignment=QtCore.Qt.AlignHCenter)

        return h_line
