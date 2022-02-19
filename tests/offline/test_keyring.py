from unittest import mock

import pytest

from maestral.keyring import CredentialStorage, TokenType
from maestral.config import remove_configuration
from maestral.exceptions import KeyringAccessError
from keyring.backend import KeyringBackend
from keyring.errors import KeyringLocked
from keyring.backends.SecretService import Keyring as SecretServiceKeyring
from keyrings.alt.file import PlaintextKeyring


@pytest.fixture
def cred_storage():
    storage = CredentialStorage("test-config")

    yield storage

    storage.delete_creds()
    remove_configuration("test-config")


def test_unlinked_state(cred_storage):
    """Test unlinked state"""

    assert not cred_storage.loaded
    assert cred_storage.account_id is None
    assert cred_storage.token is None
    assert cred_storage.token_type is None
    assert cred_storage.keyring is None


def test_save_creds(cred_storage):
    """Test linked state"""

    cred_storage.save_creds("account_id", "token", TokenType.Offline)

    assert cred_storage.loaded
    assert cred_storage.account_id == "account_id"
    assert cred_storage.token == "token"
    assert cred_storage.token_type is TokenType.Offline
    assert isinstance(cred_storage.keyring, KeyringBackend)


def test_delete_creds(cred_storage):
    """Test resetting state on `delete_creds`"""

    cred_storage.save_creds("account_id", "token", TokenType.Offline)
    cred_storage.delete_creds()

    assert not cred_storage.loaded
    assert cred_storage.account_id is None
    assert cred_storage.token is None
    assert cred_storage.token_type is None
    assert cred_storage.keyring is None


def test_plaintext_fallback(cred_storage):

    cred_storage.set_keyring_backend(SecretServiceKeyring())

    with mock.patch.object(
        cred_storage.keyring, "set_password", side_effect=KeyringLocked("")
    ):
        cred_storage.save_creds("account_id", "token", TokenType.Offline)

    assert isinstance(cred_storage.keyring, PlaintextKeyring)


def test_load_error(cred_storage):
    """Test loading state from config file and keyring"""

    cred_storage.save_creds("account_id", "token", TokenType.Offline)

    cred_storage2 = CredentialStorage("test-config")

    cred_storage2.set_keyring_backend(PlaintextKeyring())

    with mock.patch.object(
        cred_storage2.keyring, "get_password", side_effect=KeyringLocked("")
    ):
        with pytest.raises(KeyringAccessError):
            cred_storage2.token


def test_delete_error(cred_storage):
    """Test loading state from config file and keyring"""

    cred_storage.save_creds("account_id", "token", TokenType.Offline)

    with mock.patch.object(
        cred_storage.keyring, "delete_password", side_effect=KeyringLocked("")
    ):
        with pytest.raises(KeyringAccessError):
            cred_storage.delete_creds()
