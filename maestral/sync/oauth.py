# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

# system imports
import os
import os.path as osp
import logging

# external packages
import keyring
from keyring.errors import KeyringLocked

# maestral modules
from maestral.sync.utils import is_macos_bundle
from maestral.config.main import CONF, SUBFOLDER
from maestral.config.base import get_conf_path
from maestral.sync.oauth_implicit import DropboxOAuth2FlowImplicit
from maestral.sync.errors import CONNECTION_ERRORS, DropboxAuthError

logger = logging.getLogger(__name__)

APP_KEY = "2jmbq42w7vof78h"


if is_macos_bundle:
    import keyring.backends.OS_X
    keyring.set_keyring(keyring.backends.OS_X.Keyring())
else:
    # get preferred keyring backends for platform, excluding the chainer backend
    all_keyrings = keyring.backend.get_all_keyring()
    preferred_kreyrings = [k for k in all_keyrings if not isinstance(k, keyring.backends.chainer.ChainerBackend)]

    keyring.set_keyring(max(preferred_kreyrings, key=lambda x: x.priority))


class OAuth2Session(object):
    """
    OAuth2Session provides OAuth2 login and token store.
    """

    TOKEN_FILE = osp.join(get_conf_path(SUBFOLDER), "o2_store.txt")  # before v0.2.0
    oAuth2FlowResult = None

    Success = 0
    InvalidToken = 1
    ConnectionFailed = 2

    def __init__(self):

        self.account_id = CONF.get("account", "account_id")
        self.access_token = ""

        self.migrate_to_keyring()

    def load_token(self):
        """
        Check if auth key has been saved.

        :raises: ``KeyringLocked`` if the system keyring cannot be accessed.
        """
        logger.debug("Using keyring: %s" % keyring.get_keyring())
        try:
            if self.account_id == "":
                self.access_token = None
            else:
                t1 = keyring.get_password("Maestral", self.account_id)
                t2 = keyring.get_password("Maestral", "MaestralUser")  # before v0.2.2
                self.access_token = t1 or t2
            return self.access_token
        except KeyringLocked:
            info = "Please make sure that your keyring is unlocked and restart Maestral."
            raise KeyringLocked(info)

    def get_auth_url(self):
        """Gets the auth URL to start the OAuth2 implicit grant flow."""

        self.auth_flow = DropboxOAuth2FlowImplicit(APP_KEY)
        authorize_url = self.auth_flow.start()
        return authorize_url

    def verify_auth_token(self, token):
        """
        Verify the provided authorization token with Dropbox servers.

        :return: OAuth2Session.Success, OAuth2Session.InvalidToken, or
            OAuth2Session.ConnectionFailed
        :rtype: int
        """

        try:
            self.oAuth2FlowResult = self.auth_flow.finish(token)
            self.access_token = self.oAuth2FlowResult.access_token
            self.account_id = self.oAuth2FlowResult.account_id
            return self.Success
        except DropboxAuthError:
            return self.InvalidToken
        except CONNECTION_ERRORS:
            return self.ConnectionFailed

    def link(self):
        """Command line flow to get an auth key from Dropbox and save it in the system
        keyring."""
        authorize_url = self.get_auth_url()
        print("1. Go to: " + authorize_url)
        print("2. Click \"Allow\" (you might have to log in first).")
        print("3. Copy the authorization token.")

        res = 1
        while res > 0:
            auth_code = input("Enter the authorization token here: ").strip()
            res = self.verify_auth_token(auth_code)

            if res == 1:
                print("Invalid token. Please try again.")
            elif res == 2:
                print("Could not connect to Dropbox. Please try again.")

        self.save_creds()

    def save_creds(self):
        """Saves auth key to system keyring."""
        CONF.set("account", "account_id", self.account_id)
        try:
            keyring.set_password("Maestral", self.account_id, self.access_token)
            print(" > Credentials written.")
        except KeyringLocked:
            logger.error("Could not access the user keyring to save your authentication "
                         "token. Please make sure that the keyring is unlocked.")

    def delete_creds(self):
        """Deletes auth key from system keyring."""
        CONF.set("account", "account_id", "")
        try:
            keyring.delete_password("Maestral", self.account_id)
            print(" > Credentials removed.")
        except KeyringLocked:
            logger.error("Could not access the user keyring to delete your authentication"
                         " token. Please make sure that the keyring is unlocked.")

    def migrate_to_keyring(self):
        """Migrates auth key from text file (prior to v0.2.0) to system keyring."""

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
