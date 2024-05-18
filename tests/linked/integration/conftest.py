import os
import logging
import time

import pytest

import maestral.manager
from maestral.main import Maestral
from maestral.client import DropboxClient
from maestral.config import remove_configuration
from maestral.utils.path import generate_cc_name, delete
from maestral.utils.appdirs import get_home_dir
from maestral.exceptions import NotFoundError, DropboxAuthError


fsevents_logger = logging.getLogger("fsevents")
fsevents_logger.setLevel(logging.DEBUG)


def pytest_addoption(parser):
    parser.addoption("--fs-observer", action="store", default="auto", dest="OBSERVER")


def clean_dropbox_dir(c: DropboxClient, lock_path: str) -> None:
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
def m(pytestconfig, test_lock):
    """
    Returns a Maestral instance linked to a test account and syncing.

    Acquires a lock on the account for the duration of the session.
    """
    test_lock.renew()

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

    config_name = "test-config"

    m = Maestral(config_name)
    m.log_level = logging.DEBUG
    m.sync.max_cpu_percent = 20 * 100

    access_token = os.environ.get("DROPBOX_ACCESS_TOKEN")
    refresh_token = os.environ.get("DROPBOX_REFRESH_TOKEN")
    res = m.link(refresh_token=refresh_token, access_token=access_token)

    if res == 1:
        raise DropboxAuthError("Invalid token")
    elif res == 2:
        raise ConnectionError("Could not connect to Dropbox")
    elif res > 0:
        raise RuntimeError(f"[error {res}] linking failed")

    home = get_home_dir()
    local_dropbox_dir = generate_cc_name(home + "/Dropbox", suffix="test runner")
    m.create_dropbox_directory(local_dropbox_dir)

    clean_dropbox_dir(m.client, test_lock.lock_path)
    m.start_sync()
    wait_for_idle(m)

    yield m

    m.stop_sync()
    clean_dropbox_dir(m.client, test_lock.lock_path)
    wait_for_idle(m)
    delete(local_dropbox_dir)

    remove_configuration(m.config_name)
    m.cred_storage.delete_creds()
