"""
This module is responsible for authorization and token store in the system keyring.
"""

from __future__ import annotations

# system imports
import logging
from threading import RLock
from datetime import datetime

# external imports
import requests
import keyring.backends
import keyring.backends.macOS
import keyring.backends.SecretService
import keyrings.alt.file
import keyring.backends.kwallet
from keyring.backend import KeyringBackend
from keyring.core import load_keyring
from keyring.errors import KeyringLocked, PasswordDeleteError, InitError
from dropbox.oauth import DropboxOAuth2FlowNoRedirect

# local imports
from .config import MaestralConfig, MaestralState
from .constants import DROPBOX_APP_KEY
from .exceptions import KeyringAccessError
from .cli import output
from .utils import exc_info_tuple


__all__ = ["OAuth2Session"]


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

        * macOS Keychain
        * Any keyring implementing the SecretService Dbus specification
        * KWallet
        * Plain text storage

    When the auth flow is completed, a short-lived access token and a long-lived refresh
    token are generated. They can be accessed through the properties :attr:`access_token`
    and :attr:`refresh_token`. Only the long-lived refresh token will be saved in the
    system keychain for future sessions, the Dropbox SDK will use it to generate
    short-lived access tokens as needed.

    If the auth flow was previously completed before Dropbox migrated to short-lived
    tokens, the :attr:`token_access_type` will be 'legacy' and only a long-lived access
    token will be available.

    .. note:: Once the token has been stored with a keyring backend, that backend will be
        saved in the config file and remembered until the user unlinks the account. This
        module will therefore never switch keyring backends while linked.

    .. warning:: Unlike macOS Keychain, Gnome Keyring and KWallet do not support
        app-specific access to passwords. If the user unlocks those keyrings, we and any
        other application in the same user session get access to *all* saved passwords.

    :param config_name: Name of maestral config.
    :param app_key: Public key of the app, as registered with Dropbox. Used for the
        PKCE OAuth 2.0 flow.
    """

    Success = 0
    """Exit code for successful auth."""

    InvalidToken = 1
    """Exit code for invalid token."""

    ConnectionFailed = 2
    """Exit code for connection errors."""

    default_token_access_type = "offline"

    _lock = RLock()

    def __init__(self, config_name: str, app_key: str = DROPBOX_APP_KEY) -> None:

        self._app_key = app_key
        self._config_name = config_name

        self._logger = logging.getLogger(__name__)

        self._conf = MaestralConfig(config_name)
        self._state = MaestralState(config_name)

        self._auth_flow = DropboxOAuth2FlowNoRedirect(
            self._app_key,
            use_pkce=True,
            token_access_type=self.default_token_access_type,
        )

        self._account_id: str | None = self._conf.get("auth", "account_id") or None
        self._token_access_type: str | None = (
            self._state.get("auth", "token_access_type") or None
        )

        # defer keyring access until token requested by user
        self._loaded = False
        self._keyring: KeyringBackend | None = None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: datetime | None = None

    @property
    def keyring(self) -> KeyringBackend | None:
        """
        The keyring backend currently being used to store auth tokens. Will be None if
        we are not linked or have not yet saved credentials to the keyring after
        linking.
        """
        return self._keyring

    def _set_keyring_backend(self, ring: KeyringBackend | None) -> None:

        if not ring:
            self._conf.set("auth", "keyring", "automatic")
        else:
            self._conf.set(
                "auth",
                "keyring",
                f"{ring.__class__.__module__}.{ring.__class__.__name__}",
            )

        self._keyring = ring

    def _best_keyring_backend(self) -> KeyringBackend:
        """
        Returns the keyring backend currently used. If none is used because we are not
        yet linked, use the backend specified in the config file (if valid) or choose
        the most secure of the available and supported keyring backends.
        """

        import keyring.backends

        keyring_class: str = self._conf.get("auth", "keyring").strip()

        if self._account_id and keyring_class != "automatic":
            # We are already linked and have a keyring set. Insist on using
            # the recorded backend.

            try:
                ring = load_keyring(keyring_class)
            except Exception as exc:
                # Bomb out with an exception.

                title = f"Cannot load keyring {keyring_class}"
                message = "Please relink Maestral to get a new access token."
                new_exc = KeyringAccessError(title, message).with_traceback(
                    exc.__traceback__
                )
                self._logger.error(title, exc_info=exc_info_tuple(new_exc))
                raise new_exc

        else:

            # We are not yet linked. Try loading the preset or the preferred keyring
            # backend for the platform.

            try:
                ring = load_keyring(keyring_class)
            except Exception:

                # Get preferred keyring backends for platform.
                available_rings = keyring.backend.get_all_keyring()
                supported_rings = [
                    k
                    for k in available_rings
                    if isinstance(k, supported_keyring_backends)
                ]

                ring = max(supported_rings, key=lambda x: x.priority)

        return ring

    def _get_accessor(self) -> str:
        return f"config:{self._config_name}:{self._account_id}"

    @property
    def linked(self) -> bool:
        """Whether we have a valid auth token (read only)."""

        legacy = self._token_access_type == "legacy" and self.access_token
        offline = self._token_access_type == "offline" and self.refresh_token

        if legacy or offline:
            return True

        return False

    @property
    def account_id(self) -> str | None:
        """The account ID of the linked account (read only)."""
        return self._account_id

    @property
    def token_access_type(self) -> str | None:
        """The type of access token (read only). If 'legacy', we have a long-lived
        access token. If 'offline', we have a short-lived access token with an expiry
        time and a long-lived refresh token to generate new access tokens. This call may
        block until the keyring is unlocked."""

        with self._lock:
            if not self._loaded:
                self._load_token()

            return self._token_access_type

    @property
    def access_token(self) -> str | None:
        """The access token (read only). This will always be set for a 'legacy' token.
        For an 'offline' token, this will only be set if we completed the auth flow in
        the current session. In case of an 'offline' token, use the refresh token to
        retrieve a short-lived access token through the Dropbox API instead. This call
        may block until the keyring is unlocked."""

        with self._lock:
            if not self._loaded:
                self._load_token()

            return self._access_token

    @property
    def refresh_token(self) -> str | None:
        """The refresh token (read only). This will only be set for an 'offline' token.
        This call may block until the keyring is unlocked."""

        with self._lock:
            if not self._loaded:
                self._load_token()

            return self._refresh_token

    @property
    def access_token_expiration(self) -> datetime | None:
        """The expiry time for the short-lived access token (read only). This will only
        be set for an 'offline' token and if we completed the flow during the current
        session."""

        # this will only be set if we linked in the current session

        return self._expires_at

    def _load_token(self) -> None:
        """
        Loads auth token from system keyring. This will be called automatically when
        accessing any of the properties :attr:`linked`, :attr:`access_token`,
        :attr:`refresh_token` or :attr:`token_access_type`. This call may block until
        the keyring is unlocked.

        :raises KeyringAccessError: if the system keyring is locked or otherwise cannot
            be accessed (for example if the app bundle signature has been invalidated).
        """

        if not self._account_id:
            return

        if not self._keyring:
            self._keyring = self._best_keyring_backend()

        self._logger.debug(f"Using keyring: {self._keyring}")

        try:

            token = self._keyring.get_password("Maestral", self._get_accessor())
            access_type = self._state.get("auth", "token_access_type")

            if not access_type:
                # if no token type was saved, we linked with a version < 1.2.0
                # default to legacy token access type
                access_type = "legacy"
                self._state.set("auth", "token_access_type", access_type)

            self._loaded = True

            if token:

                if access_type == "legacy":
                    self._access_token = token
                elif access_type == "offline":
                    self._refresh_token = token
                else:
                    msg = "Invalid token access type in state file."
                    err = RuntimeError("Invalid token access type in state file.")
                    self._logger.error(msg, exc_info=exc_info_tuple(err))
                    raise err

                self._token_access_type = access_type

        except (KeyringLocked, InitError):
            title = "Could not load auth token"
            msg = (
                f"{self._keyring.name} is locked. Please unlock the keyring "
                "and try again."
            )
            new_exc = KeyringAccessError(title, msg)
            self._logger.error(title, exc_info=exc_info_tuple(new_exc))
            raise new_exc
        except Exception as e:
            title = "Could not load auth token"
            new_exc = KeyringAccessError(title, e.args[0])
            self._logger.error(title, exc_info=exc_info_tuple(new_exc))
            raise new_exc

    def get_auth_url(self) -> str:
        """
        Retrieves an auth URL to start the OAuth2 implicit grant flow.

        :returns: Dropbox auth URL.
        """
        authorize_url = self._auth_flow.start()
        return authorize_url

    def verify_auth_token(self, code: str) -> int:
        """
        If the user approves the app, they will be presented with a single usage
        "authorization code". Have the user copy/paste that authorization code into the
        app and then call this method to exchange it for a long-lived auth token.

        :param code: Ephemeral auth code.
        :returns: :attr:`Success`, :attr:`InvalidToken`, or :attr:`ConnectionFailed`.
        """

        with self._lock:

            try:
                res = self._auth_flow.finish(code)

                self._access_token = res.access_token
                self._refresh_token = res.refresh_token
                self._expires_at = res.expires_at
                self._account_id = res.account_id
                self._token_access_type = self.default_token_access_type

                self._loaded = True

                return self.Success
            except requests.exceptions.HTTPError:
                return self.InvalidToken
            except CONNECTION_ERRORS:
                return self.ConnectionFailed

    def save_creds(self) -> None:
        """
        Saves the auth token to system keyring. Falls back to plain text storage if the
        user denies access to keyring. This should be called after
        :meth:`verify_auth_token` returned successfully.
        """

        with self._lock:

            self._conf.set("auth", "account_id", self._account_id)
            self._state.set("auth", "token_access_type", self._token_access_type)

            if self._token_access_type == "offline":
                token = self.refresh_token
            else:
                token = self.access_token

            if not token:
                raise RuntimeError("No credentials set")

            try:

                if not self._keyring:
                    self._keyring = self._best_keyring_backend()

                self._keyring.set_password("Maestral", self._get_accessor(), token)

                if isinstance(self._keyring, keyrings.alt.file.PlaintextKeyring):
                    output.warn(
                        "No supported keyring found, credentials stored in plain text"
                    )
                output.ok("Credentials written")
            except Exception:
                # switch to plain text keyring if we cannot access preferred backend
                plaintext_keyring = keyrings.alt.file.PlaintextKeyring()
                self._set_keyring_backend(plaintext_keyring)
                self.save_creds()

    def delete_creds(self) -> None:
        """
        Deletes auth token from system keyring.

        :raises KeyringAccessError: if the system keyring is locked or otherwise cannot
            be accessed (for example if the app bundle signature has been invalidated).
        """

        with self._lock:

            if not self._account_id:
                # when keyring.delete_password is called without a username,
                # it may delete all passwords stored by Maestral on some backends
                return

            if self._keyring:

                try:
                    self._keyring.delete_password("Maestral", self._get_accessor())
                    output.ok("Credentials removed")
                except (KeyringLocked, InitError):
                    title = "Could not delete auth token"
                    msg = (
                        f"{self._keyring.name} is locked. Please unlock the keyring "
                        "and try again."
                    )
                    exc = KeyringAccessError(title, msg)
                    self._logger.error(title, exc_info=exc_info_tuple(exc))
                    raise exc
                except PasswordDeleteError as exc:
                    # password does not exist in keyring
                    self._logger.info(exc.args[0])
                except Exception as e:
                    title = "Could not delete auth token"
                    new_exc = KeyringAccessError(title, e.args[0])
                    self._logger.error(title, exc_info=exc_info_tuple(new_exc))
                    raise new_exc

            self._set_keyring_backend(None)

            self._conf.set("auth", "account_id", "")
            self._state.set("auth", "token_access_type", "")

            self._account_id = None
            self._access_token = None
            self._refresh_token = None
            self._token_access_type = None
            self._expires_at = None

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}(config={self._config_name!r}, "
            f"account_id={self._account_id})>"
        )
