import os
import logging
import time

import pytest

import maestral.manager
from maestral.main import Maestral
from maestral.config import remove_configuration
from maestral.utils.path import generate_cc_name, delete
from maestral.utils.appdirs import get_home_dir
from maestral.exceptions import NotFoundError, DropboxAuthError

from ..lock import DropboxTestLock


resources = os.path.dirname(os.path.dirname(__file__)) + "/resources"

fsevents_logger = logging.getLogger("fsevents")
fsevents_logger.setLevel(logging.DEBUG)


def pytest_addoption(parser):
    parser.addoption("--fs-observer", action="store", default="auto", dest="OBSERVER")


@pytest.fixture
def m(pytestconfig):
    """
    Returns a Maestral instance linked to a test account and syncing. Acquires a lock
    on the account for the duration of the test and removes all items from the server
    after completing the test.
    """

    # Patch file event observer backend if requested.
    if pytestconfig.option.OBSERVER == "inotify":
        from watchdog.observers.inotify import InotifyObserver

        maestral.manager.Observer = InotifyObserver
    elif pytestconfig.option.OBSERVER == "fsevents":
        from watchdog.observers.fsevents import FSEventsObserver

        maestral.manager.Observer = FSEventsObserver
    elif pytestconfig.option.OBSERVER == "kqueue":
        from watchdog.observers.kqueue import KqueueObserver

        maestral.manager.Observer = KqueueObserver
    elif pytestconfig.option.OBSERVER == "polling":
        from maestral.fsevents.polling import OrderedPollingObserver

        maestral.manager.Observer = OrderedPollingObserver

    # Initialize Maestral.
    config_name = "test-config"

    m = Maestral(config_name)
    m.log_level = logging.DEBUG

    # link with the given token
    access_token = os.environ.get("DROPBOX_ACCESS_TOKEN")
    refresh_token = os.environ.get("DROPBOX_REFRESH_TOKEN")
    res = m.link(refresh_token=refresh_token, access_token=access_token)

    if res == 1:
        raise DropboxAuthError("Invalid token")
    elif res == 2:
        raise ConnectionError("Could not connect to Dropbox")
    elif res > 0:
        raise RuntimeError(f"[error {res}] linking failed")

    # set local Dropbox directory
    home = get_home_dir()
    local_dropbox_dir = generate_cc_name(home + "/Dropbox", suffix="test runner")
    m.create_dropbox_directory(local_dropbox_dir)

    # acquire test lock and perform initial sync
    lock = DropboxTestLock(m.client)
    if not lock.acquire(timeout=60 * 60):
        raise TimeoutError("Could not acquire test lock")

    # clean dropbox directory
    res = m.client.list_folder("/", recursive=False)
    for entry in res.entries:
        m.client.remove(entry.path_lower)

    # disable throttling for tests
    m.sync.max_cpu_percent = 20 * 100

    # start syncing
    m.start_sync()
    wait_for_idle(m)

    # return synced and running instance
    yield m

    # stop syncing
    m.stop_sync()
    wait_for_idle(m)

    # clean dropbox directory
    res = m.client.list_folder("/", recursive=False)
    for entry in res.entries:
        try:
            m.client.remove(entry.path_lower)
        except NotFoundError:
            pass

    # remove all shared links
    links = m.client.list_shared_links()

    for link in links:
        m.revoke_shared_link(link.url)

    # remove local files and folders
    delete(m.dropbox_path)
    remove_configuration(m.config_name)

    # release lock
    lock.release()

    # remove creds from system keyring but don't unlink so that tokens remain valid
    m.cred_storage.delete_creds()


# helper functions


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
