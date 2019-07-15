# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import time
from threading import Thread
import logging
from PyQt5 import QtCore, QtWidgets, uic

from maestral.gui.resources import REBUILD_INDEX_DIALOG_PATH
from maestral.gui.utils import get_scaled_font


class InfoHandler(logging.Handler, QtCore.QObject):
    """
    Handler which emits a signal containing the logging message for every
    logged event. The signal will be connected to "Status" field of the GUI.
    """

    info_signal = QtCore.pyqtSignal(str)

    def __init__(self):
        logging.Handler.__init__(self)
        QtCore.QObject.__init__(self)
        self._last_emit = time.time()

    def emit(self, record):
        self.format(record)
        if time.time() - self._last_emit > 1:
            self.info_signal.emit(record.message)
            self._last_emit = time.time()


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
        self.setWindowFlags(QtCore.Qt.WindowStaysOnTopHint)

        self.monitor = monitor

        self.titleLabel.setFont(get_scaled_font(bold=True))
        self.infoLabel.setFont(get_scaled_font(scaling=0.9))

        self.cancelButton = self.buttonBox.buttons()[1]
        self.rebuildButton = self.buttonBox.buttons()[0]
        self.rebuildButton.setText("Rebuild")

        self.progressBar.hide()
        self.statusLabel.hide()

    def accept(self):
        if self.rebuildButton.text() == "Rebuild":
            self.start_rebuild()
        else:
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

        self.rebuild_rev_file_async()

    def update_progress(self, info_text):

        self.statusLabel.setText(info_text)

        try:
            n, n_tot = _filter_text(info_text)
            self.progressBar.setValue(n)
            self.progressBar.setMaximum(n_tot)
        except ValueError:
            self.progressBar.setMaximum(0)
            self.progressBar.setValue(0)

    def rebuild_rev_file_async(self):

        self.statusLabel.setText("Indexing...")

        self.rebuild_thread = BaseThread(
                target=self.monitor.rebuild_rev_file,
                callback=self.on_rebuild_done,
                name="MaestralRebuildIndex"
        )
        self.rebuild_thread.start()

    def on_rebuild_done(self):

        info_handler.info_signal.disconnect(self.update_progress)

        self.progressBar.setMaximum(100)
        self.progressBar.setValue(100)
        self.statusLabel.setText("Rebuilding complete")
        self.rebuildButton.setText("Close")
        self.rebuildButton.setEnabled(True)

        self.update()

        self.monitor.start()


def _filter_text(text):
    s = text[12:-3]
    s_list = s.split("/")
    n = int(s_list[0])
    n_tot = int(s_list[1])

    return n, n_tot
