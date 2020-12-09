# -*- coding: utf-8 -*-

import pytest

from maestral.config import remove_configuration
from maestral.client import DropboxClient
from maestral.errors import NotLinkedError


def test_client_not_linked():
    try:
        client = DropboxClient("test-config")

        assert not client.linked

        with pytest.raises(NotLinkedError):
            client.get_account_info()
    finally:
        remove_configuration("test-config")


def test_auth_url():
    try:
        client = DropboxClient("test-config")
        url = client.get_auth_url()
        assert url.startswith("https://www.dropbox.com/oauth2/authorize?")
    finally:
        remove_configuration("test-config")


def test_auth_error():
    try:
        client = DropboxClient("test-config")
        assert client.link("invalid-token") == 1

    finally:
        remove_configuration("test-config")
