import sys
import logging
from PyQt5 import QtCore, QtWidgets, QtGui, uic
from PyQt5.QtCore import Qt

from maestral.oauth import OAuth2Session
from maestral.gui.setup_dialog import AuthThread
from maestral.gui.resources import RELINK_DIALOG_PATH, APP_ICON_PATH
from maestral.gui.utils import get_scaled_font, icon_to_pixmap, QProgressIndicator
from maestral.gui.utils import quit_and_restart_maestral

logger = logging.getLogger(__name__)


class RelinkDialog(QtWidgets.QDialog):

    auth_session = OAuth2Session()

    VALID_MSG = "Verified. Restarting Maestral..."
    INVALID_MSG = "Invalid token"
    CONNECTION_ERR_MSG = "Connection failed"

    EXPIRED = 0
    REVOKED = 1

    def __init__(self, reason=EXPIRED, parent=None):
        super(self.__class__, self).__init__(parent=parent)
        # load user interface layout from .ui file
        uic.loadUi(RELINK_DIALOG_PATH, self)
        self.setModal(True)
        self.setWindowFlags(Qt.WindowTitleHint | Qt.CustomizeWindowHint)

        assert reason in (self.EXPIRED, self.REVOKED)

        # format text labels
        if reason is self.EXPIRED:
            self.titleLabel.setText("Dropbox Access expired")
            formatted_text = self.infoLabel.text().format(
                "has expired", self.auth_session.get_auth_url())
        else:
            self.titleLabel.setText("Dropbox Access revoked")
            formatted_text = self.infoLabel.text().format(
                "has been revoked", self.auth_session.get_auth_url())
        self.infoLabel.setText(formatted_text)
        self.titleLabel.setFont(get_scaled_font(bold=True))
        self.infoLabel.setFont(get_scaled_font(scaling=0.9))

        # add app icon
        icon = QtGui.QIcon(APP_ICON_PATH)
        pixmap = icon_to_pixmap(icon, self.iconLabel.width(), self.iconLabel.height())
        self.iconLabel.setPixmap(pixmap)

        # format progress indicator
        self._layout = QtWidgets.QHBoxLayout()
        self._layout.setContentsMargins(0, 0, 3, 0)
        self._layout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lineEditAuthCode.setLayout(self._layout)
        self.progressIndicator = QProgressIndicator(self.lineEditAuthCode)
        self._layout.addWidget(self.progressIndicator)

        # format line edit
        self.lineEditAuthCode.setTextMargins(3, 0, 0, 0)

        # connect callbacks
        self.lineEditAuthCode.textChanged.connect(self._set_text_style)
        self.pushButtonCancel.clicked.connect(self.quit)
        self.pushButtonUnlink.clicked.connect(self.delete_creds_and_quit)
        self.pushButtonLink.clicked.connect(self.on_link_clicked)

        # other
        self.pushButtonCancel.setFocus()
        self.adjustSize()

    def quit(self):
        QtCore.QCoreApplication.quit()
        sys.exit(0)

    def delete_creds_and_quit(self):
        self.auth_session.delete_creds
        self.quit()

    def _set_text_style(self, text):
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
            # this should not occur because link button will be inactivate when there
            # is no text in QLineEdit
            return

        self.set_ui_linking()

        self.auth_thread = AuthThread(self.auth_session, token)
        self.auth_thread.result_sig.connect(self.on_verify_token_finished)
        self.auth_thread.finished.connect(self.auth_thread.deleteLater)
        self.auth_thread.start()

    def on_verify_token_finished(self, res):

        if res == OAuth2Session.Success:
            self.auth_session.save_creds()
            self.lineEditAuthCode.setText(self.VALID_MSG)
            QtWidgets.QApplication.processEvents()
            QtCore.QTimer.singleShot(500, quit_and_restart_maestral)
        elif res == OAuth2Session.InvalidToken:
            self.lineEditAuthCode.setText(self.INVALID_MSG)
        elif res == OAuth2Session.ConnectionFailed:
            self.lineEditAuthCode.setText(self.CONNECTION_ERR_MSG)

        self.set_ui_linking(False)

    def set_ui_linking(self, enabled=True):
        height = round(self.lineEditAuthCode.height()*0.8)
        self.progressIndicator.setMinimumHeight(height)
        self.progressIndicator.setMaximumHeight(height)

        if enabled:
            self.progressIndicator.startAnimation()
            self.lineEditAuthCode.setEnabled(False)
            self.pushButtonLink.setEnabled(False)
        else:
            self.progressIndicator.stopAnimation()
            self.lineEditAuthCode.setEnabled(True)
            self.pushButtonLink.setEnabled(True)
