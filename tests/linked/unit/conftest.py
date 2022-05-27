import os

import pytest

from maestral.client import DropboxClient
from maestral.config import remove_configuration
from maestral.keyring import TokenType, CredentialStorage
from maestral.exceptions import NotFoundError

from ..lock import DropboxTestLock


resources = os.path.dirname(os.path.dirname(__file__)) + "/resources"


@pytest.fixture
def client():
    """
    Returns a Dropbox client instance linked to a test account and syncing. Acquires a
    lock on the account for the duration of the test and removes all items from the
    server after completing the test.
    """
    config_name = "test-config"

    cred_storage = CredentialStorage(config_name)
    c = DropboxClient(config_name, cred_storage)

    # link with given token and store auth info in keyring for other processes
    access_token = os.environ.get("DROPBOX_ACCESS_TOKEN")
    refresh_token = os.environ.get("DROPBOX_REFRESH_TOKEN")
    token = access_token or refresh_token
    token_type = TokenType.Legacy if access_token else TokenType.Offline
    cred_storage.save_creds("1234", token, token_type)
    c.update_path_root()

    # acquire test lock
    lock = DropboxTestLock(c)
    if not lock.acquire(timeout=60 * 60):
        raise TimeoutError("Could not acquire test lock")

    # clean dropbox directory
    res = c.list_folder("/", recursive=False)
    for entry in res.entries:
        c.remove(entry.path_lower)

    # return linked client
    yield c

    # clean dropbox directory
    res = c.list_folder("/", recursive=False)
    for entry in res.entries:
        try:
            c.remove(entry.path_lower)
        except NotFoundError:
            pass

    # remove all shared links
    links = c.list_shared_links()

    for link in links:
        c.revoke_shared_link(link.url)

    # remove local files and folders
    remove_configuration(config_name)

    # release lock
    lock.release()

    # remove creds from system keyring
    cred_storage.delete_creds()
