# -*- coding: utf-8 -*-
"""
This module is responsible for authorization and token store in the system keyring.
"""

# system imports
import logging
from threading import RLock
from typing import Optional
from datetime import datetime

# external imports
import keyring.backends  # type: ignore
import keyring.backends.macOS  # type: ignore
import keyring.backends.SecretService  # type: ignore
import keyring.backends.kwallet  # type: ignore
from keyring.backend import KeyringBackend  # type: ignore
from keyring.core import load_keyring  # type: ignore
from keyring.errors import KeyringLocked, PasswordDeleteError, InitError  # type: ignore
import keyrings.alt.file  # type: ignore
import requests
from dropbox.oauth import DropboxOAuth2FlowNoRedirect  # type: ignore

# local imports
from .config import MaestralConfig, MaestralState
from .constants import DROPBOX_APP_KEY
from .errors import KeyringAccessError
from .utils import cli


__all__ = ["OAuth2Session"]

logger = logging.getLogger(__name__)

supported_keyring_backends = (
    keyring.backends.macOS.Keyring,
    keyring.backends.SecretService.Keyring,
    keyring.backends.kwallet.DBusKeyring,
    keyring.backends.kwallet.DBusKeyringKWallet4,
    keyrings.alt.file.PlaintextKeyring,
)

CONNECTION_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.RetryError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ConnectionError,
    ConnectionError,
)


class OAuth2Session:
    """Provides Dropbox OAuth flow and key store interface

    OAuth2Session provides OAuth 2 login and token store in the preferred system keyring.
    To authenticate with Dropbox, run :meth:`get_auth_url` first and direct the user to
    visit that URL and retrieve an auth token. Verify the provided auth token with
    :meth:`verify_auth_token` and save it in the system keyring together with the
    corresponding Dropbox ID by calling :meth:`save_creds`. Supported keyring backends
    are, in order of preference:

        * MacOS Keychain
        * Any keyring implementing the SecretService Dbus specification
        * KWallet
        * Gnome Keyring
        * Plain text storage

    When the auth flow is completed, a short-lived access token and a long-lived refresh
    token are generated. Only the long-lived refresh token will be saved in the system
    keychain for future sessions, it can be used to generate short-lived access tokens
    as needed.

    If the auth flow was previously completed before Dropbox migrated to short-lived
    tokens, the :attr:`token_access_type` will be 'legacy' and only a long-lived access
    token will be available.

    .. warning:: Unlike MacOS Keychain, Gnome Keyring and KWallet do not support
        app-specific access to passwords. If the user unlocks those keyrings, we and any
        other application in the same user session get access to *all* saved passwords.

    :param config_name: Name of maestral config.

    :cvar int Success: Exit code for successful auth.
    :cvar int InvalidToken: Exit code for invalid token.
    :cvar int ConnectionFailed: Exit code for connection errors.
    """

    Success = 0
    InvalidToken = 1
    ConnectionFailed = 2

    default_token_access_type = "offline"

    _lock = RLock()

    def __init__(self, config_name: str, app_key: str = DROPBOX_APP_KEY) -> None:

        self._app_key = app_key
        self._config_name = config_name

        self._conf = MaestralConfig(config_name)
        self._state = MaestralState(config_name)

        self._auth_flow = DropboxOAuth2FlowNoRedirect(
            self._app_key,
            use_pkce=True,
            token_access_type=self.default_token_access_type,
        )

        self._account_id: Optional[str] = (
            self._conf.get("account", "account_id") or None
        )
        self._token_access_type: Optional[str] = (
            self._state.get("account", "token_access_type") or None
        )

        # defer keyring access until token requested by user
        self.loaded = False
        self._keyring: Optional[KeyringBackend] = None
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._expires_at: Optional[datetime] = None

    @property
    def keyring(self) -> KeyringBackend:

        if not self._keyring:
            self._keyring = self._get_keyring_backend()

        return self._keyring

    @keyring.setter
    def keyring(self, ring: KeyringBackend) -> None:
        self._keyring = ring

    def _get_keyring_backend(self) -> KeyringBackend:
        """
        Returns the keyring backend currently used. If none is used because we are not
        yet linked, use the backend specified in the config file (if valid) or choose
        the most secure of the available and supported keyring backends.
        """

        import keyring.backends

        keyring_class: str = self._conf.get("app", "keyring").strip()

        if self._account_id and keyring_class != "automatic":
            # we are already linked and have a keyring set

            try:
                ring = load_keyring(keyring_class)
            except Exception as exc:
                # reset the keyring and prompt to relink
                # them bomb out with an exception

                self._conf.set("app", "keyring", "automatic")

                title = f"Cannot load keyring {keyring_class}"
                message = "Please relink Maestral to get a new access token"
                new_exc = KeyringAccessError(title, message).with_traceback(
                    exc.__traceback__
                )
                logger.error(title, exc_info=_exc_info(new_exc))
                raise new_exc
            else:
                return ring

        else:

            try:
                ring = load_keyring(keyring_class)
            except Exception:

                # get preferred keyring backends for platform
                available_rings = keyring.backend.get_all_keyring()
                supported_rings = [
                    k
                    for k in available_rings
                    if isinstance(k, supported_keyring_backends)
                ]

                ring = max(supported_rings, key=lambda x: x.priority)

            self._conf.set(
                "app",
                "keyring",
                f"{ring.__class__.__module__}.{ring.__class__.__name__}",
            )

            return ring

    def _get_accessor(self) -> str:
        return f"config:{self._config_name}:{self._account_id}"

    def _migrate_keyring(self) -> None:
        token = self.keyring.get_password("Maestral", self._account_id)
        if token:
            self.keyring.set_password("Maestral", self._get_accessor(), token)
            self.keyring.delete_password("Maestral", self._account_id)

    @property
    def linked(self) -> bool:
        """Returns ``True`` if we have full auth credentials, ``False`` otherwise."""

        if self.account_id:

            legacy = self._token_access_type == "legacy" and self.access_token
            offline = self._token_access_type == "offline" and self.refresh_token

            if legacy or offline:
                return True

        return False

    @property
    def account_id(self) -> Optional[str]:
        """Returns the account ID (read only). This call may block until the keyring is
        unlocked."""

        return self._account_id

    @property
    def token_access_type(self) -> Optional[str]:
        """Returns the type of access token. If 'legacy', we have a long-lived access
        token. If 'offline', we have a short-lived access token with an expiry time and
        a long-lived refresh token to generate new access tokens."""

        with self._lock:
            if not self.loaded:
                self.load_token()

            return self._token_access_type

    @property
    def access_token(self) -> Optional[str]:
        """Returns the access token (read only). This will always be set for a 'legacy'
        token. For an 'offline' token, this will only be set if we completed the auth
        flow in the current session. In case of an 'offline' token, use the refresh
        token to retrieve a short-lived access token through the Dropbox API instead.
        The call may block until the keyring is unlocked."""

        with self._lock:
            if not self.loaded:
                self.load_token()

            return self._access_token

    @property
    def refresh_token(self) -> Optional[str]:
        """Returns the refresh token (read only). This will only be set for an 'offline'
        token. The call may block until the keyring is unlocked."""

        with self._lock:
            if not self.loaded:
                self.load_token()

            return self._refresh_token

    @property
    def access_token_expiration(self) -> Optional[datetime]:
        """Returns the expiry time for the short-lived access token. This will only be
        set for an 'offline' token and if we completed the flow during the current
        session."""

        # this will only be set if we linked in the current session

        return self._expires_at

    def load_token(self) -> None:
        """
        Loads auth token from system keyring. This will be called automatically when
        accessing of the properties :attr:`linked`, :attr:`access_token`,
        :attr:`refresh_token` or :attr:`token_access_type`.

        :raises KeyringAccessError: If the system keyring is locked.
        """

        logger.debug(f"Using keyring: {self.keyring}")

        if not self._account_id:
            return

        try:

            self._migrate_keyring()

            token = self.keyring.get_password("Maestral", self._get_accessor())
            access_type = self._state.get("account", "token_access_type")

            if not access_type:
                # if no token type was saved, we linked with a version < 1.2.0
                # default to legacy token access type
                access_type = "legacy"
                self._state.set("account", "token_access_type", access_type)

            self.loaded = True

            if token:

                if access_type == "legacy":
                    self._access_token = token
                elif access_type == "offline":
                    self._refresh_token = token
                else:
                    msg = "Invalid token access type in state file."
                    err = RuntimeError("Invalid token access type in state file.")
                    logger.error(msg, exc_info=_exc_info(err))
                    raise err

                self._token_access_type = access_type

        except (KeyringLocked, InitError):
            title = f"Could not load auth token, {self.keyring.name} is locked"
            msg = "Please unlock the keyring and try again."
            exc = KeyringAccessError(title, msg)
            logger.error(title, exc_info=_exc_info(exc))
            raise exc

    def get_auth_url(self) -> str:
        """
        Gets the auth URL to start the OAuth2 implicit grant flow.

        :returns: Dropbox auth URL.
        """
        authorize_url = self._auth_flow.start()
        return authorize_url

    def verify_auth_token(self, token) -> int:
        """
        Verify the provided authorization token with Dropbox servers.

        :returns: :attr:`Success`, :attr:`InvalidToken`, or :attr:`ConnectionFailed`.
        """

        with self._lock:

            try:
                res = self._auth_flow.finish(token)

                self._access_token = res.access_token
                self._refresh_token = res.refresh_token
                self._expires_at = res.expires_at
                self._account_id = res.account_id
                self._token_access_type = self.default_token_access_type

                self.loaded = True

                return self.Success
            except requests.exceptions.HTTPError:
                return self.InvalidToken
            except CONNECTION_ERRORS:
                return self.ConnectionFailed

    def save_creds(self) -> None:
        """
        Saves the auth token to system keyring. Falls back to plain text storage if the
        user denies access to keyring.
        """

        with self._lock:

            self._conf.set("account", "account_id", self._account_id)
            self._state.set("account", "token_access_type", self._token_access_type)

            if self._token_access_type == "offline":
                token = self.refresh_token
            else:
                token = self.access_token

            try:
                self.keyring.set_password("Maestral", self._get_accessor(), token)
                cli.ok("Credentials written")
                if isinstance(self.keyring, keyrings.alt.file.PlaintextKeyring):
                    cli.warn(
                        "No supported keyring found, credentials stored in plain text"
                    )
            except (KeyringLocked, InitError):
                # switch to plain text keyring if user won't unlock
                self.keyring = keyrings.alt.file.PlaintextKeyring()
                self._conf.set("app", "keyring", "keyrings.alt.file.PlaintextKeyring")
                self.save_creds()

    def delete_creds(self) -> None:
        """Deletes auth token from system keyring."""

        with self._lock:

            if not self._account_id:
                # when keyring.delete_password is called without a username,
                # it may delete all passwords stored by Maestral on some backends
                return

            try:
                self.keyring.delete_password("Maestral", self._get_accessor())
                cli.ok("Credentials removed")
            except (KeyringLocked, InitError):
                title = f"Could not delete auth token, {self.keyring.name} is locked"
                msg = "Please unlock the keyring and try again."
                exc = KeyringAccessError(title, msg)
                logger.error(title, exc_info=_exc_info(exc))
                raise exc
            except PasswordDeleteError as exc:
                # password does not exist in keyring
                logger.info(exc.args[0])

            self._conf.set("account", "account_id", "")
            self._state.set("account", "token_access_type", "")
            self._conf.set("app", "keyring", "automatic")

            self._account_id = None
            self._access_token = None
            self._refresh_token = None
            self._token_access_type = None

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}(config={self._config_name!r}, "
            f"account_id={self._account_id})>"
        )


def _exc_info(exc):
    return type(exc), exc, exc.__traceback__
