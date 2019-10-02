# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

# external packages
from PyQt5 import QtCore, QtWidgets, uic

# maestral modules
from maestral.gui.resources import REBUILD_INDEX_DIALOG_PATH
from maestral.gui.utils import MaestralBackgroundTask, get_scaled_font


class RebuildIndexDialog(QtWidgets.QDialog):
    """A dialog to rebuild Maestral's sync index."""

    def __init__(self, mdbx, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        uic.loadUi(REBUILD_INDEX_DIALOG_PATH, self)
        self.setWindowFlags(QtCore.Qt.WindowStaysOnTopHint)

        self.mdbx = mdbx

        self.titleLabel.setFont(get_scaled_font(bold=True))
        self.infoLabel.setFont(get_scaled_font(scaling=0.9))
        self.statusLabel.setFont(get_scaled_font(scaling=0.9))

        self.cancelButton = self.buttonBox.buttons()[1]
        self.rebuildButton = self.buttonBox.buttons()[0]
        self.rebuildButton.setText("Rebuild")

        self.progressBar.hide()
        self.statusLabel.hide()

        self.adjustSize()

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

        self.adjustSize()

        self.progressBar.setMinimum(0)
        self.progressBar.setMaximum(0)
        self.progressBar.setValue(0)

        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self.update_progress)
        self._timer.start(70)

        self.rebuild_rev_file_async()

    def update_progress(self):

        status_string = self.mdbx.status

        self.statusLabel.setText(status_string)

        try:
            n, n_tot = _filter_text(status_string)
            self.progressBar.setValue(n)
            self.progressBar.setMaximum(n_tot)
        except ValueError:
            self.progressBar.setMaximum(0)
            self.progressBar.setValue(0)

    def rebuild_rev_file_async(self):

        self.statusLabel.setText("Indexing...")

        self.rebuild_task = MaestralBackgroundTask(self, "rebuild_index")
        self.rebuild_task.sig_done.connect(self.on_rebuild_done)

    def on_rebuild_done(self):

        self._timer.stop()

        self.progressBar.setMaximum(100)
        self.progressBar.setValue(100)
        self.statusLabel.setText("Rebuilding complete")
        self.rebuildButton.setText("Close")
        self.rebuildButton.setEnabled(True)

        self.update()


def _filter_text(text):
    f = list(filter(lambda x: x in '0123456789/', text))
    f = ''.join(f)
    s = f.split("/")

    if len(s) > 1:
        n = int(s[0])
        n_tot = int(s[1])
        return n, n_tot
    else:
        raise ValueError("Cannot get progress indication from given string.")

