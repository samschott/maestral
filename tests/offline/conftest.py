import logging
import os
import os.path as osp

import pytest

from maestral.client import DropboxClient
from maestral.config import list_configs, remove_configuration
from maestral.daemon import Stop, stop_maestral_daemon_process
from maestral.fsevents import Observer
from maestral.keyring import CredentialStorage
from maestral.main import Maestral
from maestral.sync import SyncEngine
from maestral.utils.appdirs import get_home_dir
from maestral.utils.path import delete


@pytest.fixture
def m():
    m = Maestral("test-config")
    m.log_level = logging.DEBUG
    yield m
    remove_configuration(m.config_name)


@pytest.fixture
def sync():
    local_dir = osp.join(get_home_dir(), "dummy_dir")
    os.mkdir(local_dir)

    sync = SyncEngine(DropboxClient("test-config", CredentialStorage("test-config")))
    sync.fs_events.enable()
    sync.dropbox_path = local_dir

    observer = Observer()
    observer.schedule(sync.fs_events, sync.dropbox_path, recursive=True)
    observer.start()

    yield sync

    observer.stop()
    observer.join()

    remove_configuration("test-config")
    delete(sync.dropbox_path)


@pytest.fixture
def client():
    yield DropboxClient("test-config", CredentialStorage("test-config"))
    remove_configuration("test-config")


@pytest.fixture
def config_name(prefix: str = "test-config"):
    i = 0
    config_name = f"{prefix}-{i}"

    while config_name in list_configs():
        i += 1
        config_name = f"{prefix}-{i}"

    yield config_name

    res = stop_maestral_daemon_process(config_name)

    if res is Stop.Failed:
        raise RuntimeError("Could not stop test daemon")

    remove_configuration(config_name)
