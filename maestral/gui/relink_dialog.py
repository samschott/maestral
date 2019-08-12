import time
from PyQt5 import QtCore, QtWidgets, QtGui, uic

from maestral.oauth import OAuth2Session
from maestral.gui.resources import RELINK_DIALOG_PATH, APP_ICON_PATH
from maestral.gui.utils import (quit_and_restart_maestral, get_scaled_font,
                                icon_to_pixmap, QProgressIndicator)


class RelinkDialog(QtWidgets.QDialog):

    auth_session = OAuth2Session()

    VALID_MSG = "Verified. Restarting Maestral..."
    INVALID_MSG = "Invalid token"
    CONNECTION_ERR_MSG = "Connection failed"

    def __init__(self, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        # load user interface layout from .ui file
        uic.loadUi(RELINK_DIALOG_PATH, self)
        self.setModal(True)
        self.setWindowFlags(QtCore.Qt.Sheet)

        # format text labels
        self.titleLabel.setFont(get_scaled_font(bold=True))
        self.infoLabel.setFont(get_scaled_font(scaling=0.9))
        formatted_text = self.infoLabel.text().format(self.auth_session.get_auth_url())
        self.infoLabel.setText(formatted_text)

        # add app icon
        icon = QtGui.QIcon(APP_ICON_PATH)
        pixmap = icon_to_pixmap(icon, self.iconLabel.width(), self.iconLabel.height())
        self.iconLabel.setPixmap(pixmap)

        # format progress indicator
        self._layout = QtWidgets.QHBoxLayout()
        self._layout.setContentsMargins(0, 0, 3, 0)
        self._layout.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.lineEditAuthCode.setLayout(self._layout)
        self.progressIndicator = QProgressIndicator(self.lineEditAuthCode)
        self._layout.addWidget(self.progressIndicator)
        height = self.lineEditAuthCode.height()*0.7
        self.progressIndicator.setMinimumSize(height, height)
        self.progressIndicator.setMaximumSize(height, height)

        # format line edit
        self.lineEditAuthCode.setTextMargins(3, 0, 0, 0)

        # connect callbacks
        self.lineEditAuthCode.textChanged.connect(self.on_text_changed)
        self.pushButtonCancel.clicked.connect(QtWidgets.QApplication.quit)
        self.pushButtonLink.clicked.connect(self.on_link_clicked)

        # other
        self.pushButtonCancel.setFocus()
        self.adjustSize()

    def on_text_changed(self, text):
        if text == "":
            self.pushButtonLink.setEnabled(False)
            self.lineEditAuthCode.setStyleSheet("")
        elif text == self.INVALID_MSG:
            self.pushButtonLink.setEnabled(False)
            self.lineEditAuthCode.setStyleSheet("color: rgb(205, 0, 0); font: bold;")
        elif text == self.CONNECTION_ERR_MSG:
            self.pushButtonLink.setEnabled(False)
            self.lineEditAuthCode.setStyleSheet("color: rgb(205, 0, 0); font: bold;")
        elif text == self.VALID_MSG:
            self.pushButtonLink.setEnabled(False)
            self.lineEditAuthCode.setStyleSheet("color: rgb(0, 129, 0); font: bold;")
        else:
            self.pushButtonLink.setEnabled(True)
            self.lineEditAuthCode.setStyleSheet("")

    def on_link_clicked(self):
        token = self.lineEditAuthCode.text()
        if token == "":
            return

        self.adjustSize()

        self.progressIndicator.startAnimation()
        self.pushButtonLink.setEnabled(False)
        self.lineEditAuthCode.setEnabled(False)

        self.auth_thread = AuthThread(self.auth_session, token)
        self.auth_thread.result_sig.connect(self.on_verify_token_finished)
        self.auth_thread.finished.connect(self.auth_thread.deleteLater)
        self.auth_thread.start()

    def on_verify_token_finished(self, res):

        if res == OAuth2Session.Success:
            self.auth_session.save_creds()
            self.lineEditAuthCode.setText(self.VALID_MSG)
            time.sleep(500)
            quit_and_restart_maestral()
        elif res == OAuth2Session.InvalidToken:
            self.lineEditAuthCode.setText(self.INVALID_MSG)
        elif res == OAuth2Session.ConnectionFailed:
            self.lineEditAuthCode.setText(self.CONNECTION_ERR_MSG)

        self.progressIndicator.stopAnimation()
        self.lineEditAuthCode.setEnabled(True)
