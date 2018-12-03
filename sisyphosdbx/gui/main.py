# -*- coding: utf-8 -*-


import sys
import os
import logging
from PyQt5 import QtCore, QtWidgets, QtGui, uic

direct = os.path.dirname(os.path.realpath(__file__))


class EmitInfoHanlder(logging.Handler, QtCore.QObject):
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


info_handler = EmitInfoHanlder()
info_handler.setLevel(logging.INFO)
sdbx_logger = logging.getLogger('sisyphosdbx')
sdbx_logger.addHandler(info_handler)


class SystemTrayIcon(QtWidgets.QSystemTrayIcon):

    def __init__(self, icon, parent=None):
        QtWidgets.QSystemTrayIcon.__init__(self, icon, parent)
        self.menu = QtWidgets.QMenu(parent)
        self.openFolderAction = self.menu.addAction("Open Dropbox Folder")
        self.openWebsiteAction = self.menu.addAction("Launch Dropbox Website")
        self.separatorAction1 = self.menu.addSeparator()
        self.accountInfoAction = self.menu.addAction("No Account Linked")
        self.accountInfoAction.setEnabled(False)
        self.separatorAction2 = self.menu.addSeparator()
        self.statusAction = self.menu.addAction("Connecting...")
        self.statusAction.setEnabled(False)
        self.exitAction = self.menu.addAction("Pause Syncing")
        self.separatorAction3 = self.menu.addSeparator()
        self.pauseAction = self.menu.addAction("Preferences...")
        self.helpAction = self.menu.addAction("Help Center")
        self.separatorAction4 = self.menu.addSeparator()
        self.quitAction = self.menu.addAction("Quit SisyphosDBX")
        self.setContextMenu(self.menu)

        info_handler.info_signal.connect(self.statusAction.setText)
        self.quitAction.triggered.connect(QtCore.QCoreApplication.quit)


def get_qt_app(*args, **kwargs):
    """
    Create a new Qt app or return an existing one.
    """
    created = False
    app = QtCore.QCoreApplication.instance()

    if not app:
        if not args:
            args = ([''],)
        app = QtWidgets.QApplication(*args, **kwargs)
        created = True

    return app, created


if __name__ == "__main__!":
    app, created = get_qt_app()

    w = QtWidgets.QWidget()
    pixmap = QtGui.QPixmap(os.path.join(direct, "resources/menubar_icon_active.png"))
    trayIcon = SystemTrayIcon(QtGui.QIcon(pixmap), w)

    trayIcon.show()

    if created:
        sys.exit(app.exec_())
