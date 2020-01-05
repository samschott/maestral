# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

# system imports
import logging

# external packages
import keyring
from keyring.errors import KeyringLocked

# maestral modules
from maestral.config.main import MaestralConfig
from maestral.sync.oauth_implicit import DropboxOAuth2FlowImplicit
from maestral.sync.errors import DropboxAuthError
from maestral.sync.utils import set_keyring_backend


APP_KEY = "2jmbq42w7vof78h"

logger = logging.getLogger(__name__)
set_keyring_backend()


class OAuth2Session(object):
    """
    OAuth2Session provides OAuth2 login and token store.
    """

    oAuth2FlowResult = None

    Success = 0
    InvalidToken = 1
    ConnectionFailed = 2

    def __init__(self, config_name='maestral'):

        self._conf = MaestralConfig(config_name)

        self.account_id = self._conf.get("account", "account_id")
        self.access_token = ""

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
                self.access_token = keyring.get_password("Maestral", self.account_id)
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
        except ConnectionError:
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
        self._conf.set("account", "account_id", self.account_id)
        try:
            keyring.set_password("Maestral", self.account_id, self.access_token)
            print(" > Credentials written.")
        except KeyringLocked:
            logger.error("Could not access the user keyring to save your authentication "
                         "token. Please make sure that the keyring is unlocked.")

    def delete_creds(self):
        """Deletes auth key from system keyring."""
        self._conf.set("account", "account_id", "")
        try:
            keyring.delete_password("Maestral", self.account_id)
            print(" > Credentials removed.")
        except KeyringLocked:
            logger.error("Could not access the user keyring to delete your authentication"
                         " token. Please make sure that the keyring is unlocked.")
