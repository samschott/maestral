# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import os
import os.path as osp
import keyring
import logging
from keyring.errors import KeyringLocked
from dropbox import DropboxOAuth2FlowNoRedirect

from maestral.config.main import CONF, SUBFOLDER
from maestral.config.base import get_conf_path
from maestral.errors import to_maestral_error

logger = logging.getLogger(__name__)

APP_KEY = os.environ["DROPBOX_API_KEY"]
APP_SECRET = os.environ["DROPBOX_API_SECRET"]


class OAuth2Session(object):
    """
    OAuth2Session provides OAuth2 login and token store.

    :ivar app_key: String containing app key provided by Dropbox.
    :ivar app_secret: String containing app secret provided by Dropbox.
    """

    TOKEN_FILE = osp.join(get_conf_path(SUBFOLDER), "o2_store.txt")
    auth_flow = None
    oAuth2FlowResult = None

    def __init__(self, app_key=APP_KEY, app_secret=APP_SECRET):
        self.app_key = app_key
        self.app_secret = app_secret

        self.account_id = CONF.get("account", "account_id")
        self.access_token = ""

        self.migrate_to_keyring()

        # prepare auth flow
        self.auth_flow = DropboxOAuth2FlowNoRedirect(self.app_key, self.app_secret)

    def load_token(self):
        """
        Check if credentials exist.
        :return:
        """
        try:
            t1 = keyring.get_password("Maestral", self.account_id)
            t2 = keyring.get_password("Maestral", "MaestralUser")
            self.access_token = t1 or t2
            return self.access_token
        except KeyringLocked:
            info = "Please make sure that your keyring is unlocked and restart Maestral."
            raise KeyringLocked(info)

    def get_auth_url(self):
        authorize_url = self.auth_flow.start()
        return authorize_url

    def verify_auth_key(self, auth_code):
        self.oAuth2FlowResult = self.auth_flow.finish(auth_code)
        self.access_token = self.oAuth2FlowResult.access_token
        self.account_id = self.oAuth2FlowResult.account_id

        return True

    def link(self):
        authorize_url = self.get_auth_url()
        print("1. Go to: " + authorize_url)
        print("2. Click \"Allow\" (you might have to log in first).")
        print("3. Copy the authorization code.")
        auth_code = input("Enter the authorization code here: ").strip()

        try:
            self.verify_auth_key(auth_code)
        except Exception as exc:
            raise to_maestral_error(exc) from exc

        self.save_creds()

    def save_creds(self):
        CONF.set("account", "account_id", self.account_id)
        try:
            keyring.set_password("Maestral", self.account_id, self.access_token)
            print(" > Credentials written.")
        except KeyringLocked:
            logger.error("Could not access the user keyring to save your authentication "
                         "token. Please make sure that the keyring is unlocked.")

    def delete_creds(self):
        CONF.set("account", "account_id", "")
        try:
            keyring.delete_password("Maestral", self.account_id)
            print(" > Credentials removed.")
        except KeyringLocked:
            logger.error("Could not access the user keyring to delete your authentication"
                         " token. Please make sure that the keyring is unlocked.")

    def migrate_to_keyring(self):

        if osp.isfile(self.TOKEN_FILE):
            print(" > Migrating access token to keyring...")

            try:
                # load old token
                with open(self.TOKEN_FILE) as f:
                    stored_creds = f.read()
                self.access_token, self.account_id, _ = stored_creds.split("|")

                # migrate old token to keyring
                self.save_creds()
                os.unlink(self.TOKEN_FILE)
                print(" [DONE]")

            except IOError:
                print(" x Could not load old token. Beginning new session.")

        elif keyring.get_password("Maestral", "MaestralUser") and self.account_id:
            print(" > Migrating access token to account_id...")
            self.access_token = keyring.get_password("Maestral", "MaestralUser")
            try:
                keyring.set_password("Maestral", self.account_id, self.access_token)
                keyring.delete_password("Maestral", "MaestralUser")
                print(" [DONE]")
            except KeyringLocked:
                raise KeyringLocked(
                    "Could not access the user keyring to load your authentication "
                    "token. Please make sure that the keyring is unlocked.")