# -*- coding: utf-8 -*-

import pytest

from maestral.errors import NotLinkedError


def test_client_not_linked(client):
    assert not client.linked

    with pytest.raises(NotLinkedError):
        client.get_account_info()


def test_auth_url(client):
    url = client.get_auth_url()
    assert url.startswith("https://www.dropbox.com/oauth2/authorize?")


def test_auth_error(client):
    assert client.link("invalid-token") == 1
