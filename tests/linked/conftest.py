import os
import logging

import pytest

from maestral.client import DropboxClient
from maestral.config import remove_configuration
from maestral.exceptions import DropboxAuthError
from maestral.keyring import CredentialStorage

from .lock import DropboxTestLock


fsevents_logger = logging.getLogger("fsevents")
fsevents_logger.setLevel(logging.DEBUG)


LOCK_PATH = "/test.lock"


@pytest.fixture(scope="session", autouse=True)
def test_lock():
    """
    Returns a Dropbox client instance linked to a test account.

    Acquires a lock on the account for the duration of the test session.
    """
    config_name = "test-lock-config"

    cred_storage = CredentialStorage(config_name)
    c = DropboxClient(config_name, cred_storage)

    access_token = os.environ.get("DROPBOX_ACCESS_TOKEN")
    refresh_token = os.environ.get("DROPBOX_REFRESH_TOKEN")
    res = c.link(refresh_token=refresh_token, access_token=access_token)

    if res == 1:
        raise DropboxAuthError("Invalid token")
    elif res == 2:
        raise ConnectionError("Could not connect to Dropbox")
    elif res > 0:
        raise RuntimeError(f"[error {res}] linking failed")

    lock = DropboxTestLock(c, LOCK_PATH)
    if not lock.acquire():
        raise TimeoutError("Could not acquire test lock")

    yield lock

    remove_configuration(config_name)
    lock.release()
    cred_storage.delete_creds()
