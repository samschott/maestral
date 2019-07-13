# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import re
from threading import Thread
import logging
from PyQt5 import QtCore, QtWidgets, uic

from maestral.gui.resources import REBUILD_INDEX_DIALOG_PATH


class InfoHandler(logging.Handler, QtCore.QObject):
    """
    Handler which emits a signal containing the logging message for every
    logged event. The signal will be connected to "Status" field of the GUI.
    """

    info_signal = QtCore.pyqtSignal(str)

    def __init__(self):
        logging.Handler.__init__(self)
        QtCore.QObject.__init__(self)

    def emit(self, record):
        self.format(record)
        self.info_signal.emit(record.message)


info_handler = InfoHandler()
info_handler.setLevel(logging.INFO)

info_handler = InfoHandler()
info_handler.setLevel(logging.INFO)

mdbx_logger = logging.getLogger("maestral.monitor")
mdbx_logger.addHandler(info_handler)


class BaseThread(Thread):
    def __init__(self, callback=None, callback_args=None, *args, **kwargs):
        target = kwargs.pop("target")
        super(BaseThread, self).__init__(target=self.target_with_callback, *args, **kwargs)
        self.callback = callback
        self.method = target
        self.callback_args = callback_args

    def target_with_callback(self):
        self.method()
        if self.callback is not None:
            if self.callback_args is not None:
                self.callback(*self.callback_args)
            else:
                self.callback()


class RebuildIndexDialog(QtWidgets.QDialog):

    def __init__(self, monitor, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        uic.loadUi(REBUILD_INDEX_DIALOG_PATH, self)

        self.monitor = monitor

        self.cancelButton = self.buttonBox.buttons()[1]
        self.rebuildButton = self.buttonBox.buttons()[0]

        self.progressBar.hide()
        self.statusLabel.hide()

        self.buttonBox.clicked.connect(self.on_clicked)

    def accept(self):
        pass

    def on_clicked(self, button):

        if button == self.rebuildButton:
            self.start_rebuild()
        elif button == self.cancelButton or button == self.doneButton:
            self.close()
            self.deleteLater()

    def start_rebuild(self):

        self.cancelButton.setEnabled(False)
        self.rebuildButton.setEnabled(False)

        self.progressBar.show()
        self.statusLabel.show()

        self.progressBar.setMinimum(0)
        self.progressBar.setMaximum(0)
        self.progressBar.setValue(0)

        info_handler.info_signal.connect(self.update_progress)

        self.repaint()

        self.rebuild_rev_file_async()

    def update_progress(self, info_text):

        self.statusLabel.setText(info_text)

        p1 = re.compile(r"Downloading (?P<n>\d+)/(?P<n_total>\d+)...")
        m1 = p1.search(info_text)

        p2 = re.compile(r"Uploading (?P<n>\d+)/(?P<n_total>\d+)...")
        m2 = p2.search(info_text)

        m = m1 or m2

        if m:
            n = int(m1.group("n"))
            n_total = int(m1.group("n_total"))
            self.progressBar.setMaximum(n_total)
            self.progressBar.setValue(n)
        else:
            self.progressBar.setMaximum(0)
            self.progressBar.setValue(0)

    def rebuild_rev_file_async(self):

        self.rebuild_thread = BaseThread(
                target=self.monitor.rebuild_rev_file,
                callback=self.on_rebuild_done,
                name="MaestralRebuildIndex"
        )
        self.rebuild_thread.start()

    def on_rebuild_done(self):

        info_handler.info_signal.disconnect(self.statusLabel.setText)

        self.progressBar.setMaximum(100)
        self.progressBar.setValue(100)
        self.statusLabel.setText("Rebuilding complete")
        self.buttonBox.removeButton(self.rebuildButton)
        self.doneButton = self.buttonBox.addButton(
            "Done", QtWidgets.QDialogButtonBox.AcceptRole)

        self.repaint()
