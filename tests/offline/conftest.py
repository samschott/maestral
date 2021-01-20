# -*- coding: utf-8 -*-

import os
import os.path as osp
import logging
from threading import Event

import pytest

from maestral.main import Maestral, logger
from maestral.sync import SyncEngine, Observer, FSEventHandler
from maestral.client import DropboxClient
from maestral.config import list_configs, remove_configuration
from maestral.daemon import stop_maestral_daemon_process, Stop
from maestral.utils.appdirs import get_home_dir
from maestral.utils.path import delete


logger.setLevel(logging.DEBUG)


@pytest.fixture
def m():
    m = Maestral("test-config")
    m._conf.save()
    yield m
    remove_configuration(m.config_name)


@pytest.fixture
def sync():
    syncing = Event()
    startup = Event()
    syncing.set()

    local_dir = osp.join(get_home_dir(), "dummy_dir")
    os.mkdir(local_dir)

    sync = SyncEngine(DropboxClient("test-config"), FSEventHandler(syncing, startup))

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
    yield DropboxClient("test-config")
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
