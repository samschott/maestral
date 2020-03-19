# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
# system imports
import logging

# external packages
import click
import keyring
from keyring.errors import KeyringLocked

# maestral modules
from maestral.config import MaestralConfig
from maestral.constants import DROPBOX_APP_KEY
from maestral.errors import DropboxAuthError
from maestral.utils.backend import set_keyring_backend
from maestral.utils.oauth_implicit import DropboxOAuth2FlowImplicit

logger = logging.getLogger(__name__)
set_keyring_backend()


class OAuth2Session:
    """
    OAuth2Session provides OAuth2 login and token store. To authenticate with Dropbox,
    run ``get_auth_url`` first and direct the user to visit that URL and retrieve an auth
    token. Verify the provided auth token with ``verify_auth_token`` and save it in the
    system keyring together with the corresponding Dropbox ID by calling ``save_creds``.

    The convenience method ``link`` runs through the above auth flow in a command line
    user dialog.
    """

    oAuth2FlowResult = None

    Success = 0
    InvalidToken = 1
    ConnectionFailed = 2

    def __init__(self, config_name):

        self._conf = MaestralConfig(config_name)

        self.account_id = self._conf.get('account', 'account_id')
        self.access_token = ""

        self.auth_flow = None

    def load_token(self):
        """
        Check if auth key has been saved.

        :raises: ``KeyringLocked`` if the system keyring cannot be accessed.
        """
        logger.debug(f'Using keyring: {keyring.get_keyring()}')
        try:
            if self.account_id == "":
                self.access_token = None
            else:
                self.access_token = keyring.get_password('Maestral', self.account_id)
            return self.access_token
        except KeyringLocked:
            info = 'Please make sure that your keyring is unlocked and restart Maestral.'
            raise KeyringLocked(info)

    def get_auth_url(self):
        """Gets the auth URL to start the OAuth2 implicit grant flow."""

        self.auth_flow = DropboxOAuth2FlowImplicit(DROPBOX_APP_KEY)
        authorize_url = self.auth_flow.start()
        return authorize_url

    def verify_auth_token(self, token):
        """
        Verify the provided authorization token with Dropbox servers.

        :returns: OAuth2Session.Success, OAuth2Session.InvalidToken, or
            OAuth2Session.ConnectionFailed
        :rtype: int
        """

        if not self.auth_flow:
            raise RuntimeError('Auth flow not yet started. Please call "get_auth_url".')

        try:
            self.oAuth2FlowResult = self.auth_flow.finish(token)
            self.access_token = self.oAuth2FlowResult.access_token
            self.account_id = self.oAuth2FlowResult.account_id
            return self.Success
        except DropboxAuthError:
            return self.InvalidToken
        except ConnectionError:
            return self.ConnectionFailed

    def save_creds(self):
        """Saves auth key to system keyring."""
        self._conf.set('account', 'account_id', self.account_id)
        try:
            keyring.set_password('Maestral', self.account_id, self.access_token)
            click.echo(' > Credentials written.')
        except KeyringLocked:
            logger.error('Could not access the user keyring to save your authentication '
                         'token. Please make sure that the keyring is unlocked.')

    def link(self):
        """
        Command line flow to get an auth key from Dropbox and save it in the system
        keyring.
        """
        authorize_url = self.get_auth_url()
        click.echo('1. Go to: ' + authorize_url)
        click.echo('2. Click "Allow" (you might have to log in first).')
        click.echo('3. Copy the authorization token.')

        res = self.InvalidToken
        while res != self.Success:
            auth_code = click.prompt('Enter the authorization token here', type=str)
            auth_code = auth_code.strip()
            res = self.verify_auth_token(auth_code)

            if res == self.InvalidToken:
                click.secho('Invalid token. Please try again.', fg='red')
            elif res == self.ConnectionFailed:
                click.secho('Could not connect to Dropbox. Please try again.', fg='red')

        self.save_creds()

    def delete_creds(self):
        """Deletes auth key from system keyring."""
        self._conf.set('account', 'account_id', "")
        try:
            keyring.delete_password('Maestral', self.account_id)
            click.echo(' > Credentials removed.')
        except KeyringLocked:
            logger.error('Could not access the user keyring to delete your authentication'
                         ' token. Please make sure that the keyring is unlocked.')
