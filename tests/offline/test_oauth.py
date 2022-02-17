from datetime import datetime
from unittest import mock

import pytest

from maestral.oauth import OAuth2Session
from maestral.config import remove_configuration
from dropbox.oauth import OAuth2FlowNoRedirectResult
from keyring.backend import KeyringBackend


@pytest.fixture
def oauth():
    auth = OAuth2Session("test-config")

    auth_res = OAuth2FlowNoRedirectResult(
        access_token="1234",
        account_id="1234",
        user_id="1234",
        refresh_token="1234",
        expiration=datetime.fromisoformat("2300-12-04"),
        scope=[],
    )

    auth_flow = mock.MagicMock()
    auth_flow.start = mock.Mock(return_value="https://auth_url.com")
    auth_flow.finish = mock.Mock(return_value=auth_res)

    auth._auth_flow = auth_flow

    yield auth

    auth.delete_creds()
    remove_configuration("test-config")


def test_unlinked_state(oauth):
    """Test unlinked state"""

    assert not oauth.linked
    assert oauth.account_id is None
    assert oauth.access_token is None
    assert oauth.refresh_token is None
    assert oauth.token_access_type is None
    assert oauth.access_token_expiration is None
    assert oauth.keyring is None


def test_linked_state(oauth):
    """Test linked state"""

    oauth.verify_auth_token("ephemeral code")

    assert oauth.linked
    assert oauth.account_id == "1234"
    assert oauth.access_token == "1234"
    assert oauth.refresh_token == "1234"
    assert oauth.token_access_type == "offline"
    assert oauth.access_token_expiration == datetime.fromisoformat("2300-12-04")
    assert oauth.keyring is None


def test_save_creds(oauth):
    """Test loading state from config file and keyring"""

    oauth.verify_auth_token("ephemeral code")
    oauth.save_creds()

    oauth2 = OAuth2Session("test-config")

    assert oauth2.linked
    assert oauth2.account_id == "1234"
    assert oauth2.access_token is None
    assert oauth2.refresh_token == "1234"
    assert oauth2.token_access_type == "offline"
    assert isinstance(oauth.keyring, KeyringBackend)


def test_delete_creds(oauth):
    """Test resetting state on `delete_creds`"""

    oauth.verify_auth_token("ephemeral code")
    oauth.delete_creds()

    assert not oauth.linked
    assert oauth.account_id is None
    assert oauth.access_token is None
    assert oauth.refresh_token is None
    assert oauth.token_access_type is None
    assert oauth.access_token_expiration is None
    assert oauth.keyring is None


def test_delete_saved_creds(oauth):
    """Test resetting state on `delete_creds`"""

    oauth.verify_auth_token("ephemeral code")
    oauth.save_creds()

    oauth.delete_creds()

    oauth2 = OAuth2Session("test-config")

    for auth in (oauth, oauth2):

        assert not auth.linked
        assert auth.account_id is None
        assert auth.access_token is None
        assert auth.refresh_token is None
        assert auth.token_access_type is None
        assert auth.access_token_expiration is None
        assert auth.keyring is None
