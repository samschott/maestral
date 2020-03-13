# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""

# system imports
import os
import base64
import logging

# external packages
import dropbox
from dropbox.oauth import (
    OAuth2FlowNoRedirectResult,
    WEB_HOST, API_HOST,
    pinned_session, url_path_quote,
    _params_to_urlencoded
)

# maestral modules
from maestral.errors import dropbox_to_maestral_error

logger = logging.getLogger(__name__)


class DropboxOAuth2FlowImplicitBase:

    def __init__(self, consumer_key, locale=None):
        self.consumer_key = consumer_key
        self.locale = locale
        self.requests_session = pinned_session()

    def _get_authorize_url(self, redirect_uri, state):
        params = dict(response_type='token',
                      client_id=self.consumer_key)
        if redirect_uri is not None:
            params['redirect_uri'] = redirect_uri
        if state is not None:
            params['state'] = state

        return self.build_url('/oauth2/authorize', params, WEB_HOST)

    def build_path(self, target, params=None):
        """Build the path component for an API URL.

        This method url-encodes the parameters, adds them to the end
        of the target url, and puts a marker for the API version in front.

        :param str target: A target url (e.g. '/files') to build upon.
        :param dict params: Optional dictionary of parameters.
        :returns: The path and parameters components of an API URL.
        :rtype: str
        """

        target_path = url_path_quote(target)

        params = params or {}
        params = params.copy()

        if self.locale:
            params['locale'] = self.locale

        if params:
            query_string = _params_to_urlencoded(params)
            return '%s?%s' % (target_path, query_string)
        else:
            return target_path

    def build_url(self, target, params=None, host=API_HOST):
        """Build an API URL.

        This method adds scheme and hostname to the path
        returned from build_path.

        :param str target: A target url (e.g. '/files') to build upon.
        :param dict params: Optional dictionary of parameters (name to value).
        :param str host: The host url.
        :returns: The full API URL.
        :rtype: str
        """
        return 'https://%s%s' % (host, self.build_path(target, params))


class DropboxOAuth2FlowImplicit(DropboxOAuth2FlowImplicitBase):
    """
    OAuth 2 authorization helper.  Use this for client-side applications.

    DropboxOAuth2FlowImplicit will perform authorization through an implicit flow.
    """

    REDIRECT_URI = 'https://www.dropbox.com/1/oauth2/display_token'

    def __init__(self, consumer_key, redirect_uri=REDIRECT_URI, session=None,
                 csrf_token_session_key='dropbox-auth-csrf-token', locale=None):
        """
        Construct an instance.

        :param str consumer_key: Your API app's 'app key'.
        :param str redirect_uri: The URI that the Dropbox server will redirect
            the user to after the user finishes authorizing your app.  This URI
            must be HTTPS-based and pre-registered with the Dropbox servers,
            though localhost URIs are allowed without pre-registration and can
            be either HTTP or HTTPS.
        :param dict session: A dict-like object that represents the current
            user's web session (will be used to save the CSRF token).
        :param str csrf_token_session_key: The key to use when storing the CSRF
            token in the session (for example: 'dropbox-auth-csrf-token').
        :param str locale: The locale of the user of your application.  For
            example 'en' or 'en_US'. Some API calls return localized data and
            error messages; this setting tells the server which locale to use.
            By default, the server uses 'en_US'.
        """
        super(DropboxOAuth2FlowImplicit, self).__init__(consumer_key, locale)
        self.redirect_uri = redirect_uri
        if session is None:
            self.session = dict()
        else:
            self.session = session
        self.csrf_token_session_key = csrf_token_session_key

    def start(self, url_state=None):
        """
        Starts the OAuth 2 authorization process.

        This function builds an 'authorization URL'.  You should redirect your
        user's browser to this URL, which will give them an opportunity to
        grant your app access to their Dropbox account.  When the user
        completes this process, they will be automatically redirected to the
        ``redirect_uri`` you passed in to the constructor.

        This function will also save a CSRF token to
        ``session[csrf_token_session_key]`` (as provided to the constructor).
        This CSRF token will be checked on :meth:`finish()` to prevent request
        forgery.

        :param str url_state: Any data that you would like to keep in the URL
            through the authorization process.  This exact value will be
            returned to you by :meth:`finish()`.
        :returns: The URL for a page on Dropbox's website.  This page will let
            the user 'approve' your app, which gives your app permission to
            access the user's Dropbox account. Tell the user to visit this URL
            and approve your app.
        """

        csrf_token = base64.urlsafe_b64encode(os.urandom(16)).decode('ascii')
        state = csrf_token
        if url_state is not None:
            state += '|' + url_state
        self.session[self.csrf_token_session_key] = csrf_token

        return self._get_authorize_url(self.redirect_uri, state)

    @staticmethod
    def finish(access_token):
        """
        Finish OAuth Implicit Grant flow by verifying token and retrieving
        account info.

        :param str access_token: Dropbox API access token.
        :returns: Authentication result containing access token and account id.
        :rtype: :class:`dropbox.oauth.OAuth2FlowNoRedirectResult`
        :raises: :class:`maestral.errors.MaestralApiError`
        """
        dbx = dropbox.Dropbox(access_token)
        try:
            res = dbx.users_get_current_account()
        except dropbox.exceptions.DropboxException as exc:
            raise dropbox_to_maestral_error(exc)

        return OAuth2FlowNoRedirectResult(access_token, res.account_id, '')

    @staticmethod
    def invalidate_token(access_token):
        """
        Invalidates :param:`access_token` with Dropbox. Call this when
        unlinking an app.

        :param str access_token: Dropbox API access token.
        :raises: :class:`maestral.errors.MaestralApiError`
        """
        dbx = dropbox.Dropbox(access_token)
        try:
            dbx.auth_token_revoke()
        except dropbox.exceptions.DropboxException as exc:
            raise dropbox_to_maestral_error(exc)
