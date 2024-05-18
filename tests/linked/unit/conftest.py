import os
import logging
import time

import pytest

from maestral.main import Maestral
from maestral.client import DropboxClient
from maestral.config import remove_configuration
from maestral.exceptions import NotFoundError, DropboxAuthError
from maestral.keyring import CredentialStorage


fsevents_logger = logging.getLogger("fsevents")
fsevents_logger.setLevel(logging.DEBUG)


def clean_dropbox_dir(c: DropboxClient, lock_path: str):
    for link in c.list_shared_links():
        c.revoke_shared_link(link.url)

    res = c.list_folder("/", recursive=False)
    for entry in res.entries:
        if entry.path_lower == lock_path:
            continue

        try:
            c.remove(entry.path_lower)
        except NotFoundError:
            pass


def wait_for_idle(m: Maestral, cycles: int = 6) -> None:
    """Blocks until Maestral instance is idle for at least ``cycles`` sync cycles."""

    count = 0

    while count < cycles:
        if m.sync.busy():
            # Wait until we can acquire the sync lock => we are idle.
            m.sync.sync_lock.acquire()
            m.sync.sync_lock.release()
            count = 0
        else:
            time.sleep(1)
            count += 1


@pytest.fixture
def client(test_lock):
    """
    Returns a Dropbox client instance linked to a test account.
    """
    test_lock.renew()

    config_name = "test-config"

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

    clean_dropbox_dir(c, test_lock.lock_path)
    yield c
    clean_dropbox_dir(c, test_lock.lock_path)

    remove_configuration(config_name)
    cred_storage.delete_creds()
