# -*- coding: utf-8 -*-

import sys
import os
import time
import subprocess
import threading
import uuid

import pytest
from Pyro5.api import Proxy

from maestral.daemon import (
    CommunicationError,
    MaestralProxy,
    start_maestral_daemon,
    start_maestral_daemon_process,
    stop_maestral_daemon_process,
    Start,
    Stop,
    Lock,
)
from maestral.main import Maestral
from maestral.errors import NotLinkedError


# locking tests


def test_locking_from_same_thread():
    lock_name = "test-lock-" + str(uuid.uuid4())

    # initialise lock
    lock = Lock.singleton(lock_name)
    assert not lock.locked()

    # acquire lock
    res = lock.acquire()
    assert res
    assert lock.locked()

    # try to reacquire
    res = lock.acquire()
    assert not res
    assert lock.locked()

    # check pid of locking process
    assert lock.locking_pid() == os.getpid()

    # release lock
    lock.release()
    assert not lock.locked()

    # try to re-release lock
    with pytest.raises(RuntimeError):
        lock.release()


def test_locking_threaded():
    lock_name = "test-lock-" + str(uuid.uuid4())

    # initialise lock
    lock = Lock.singleton(lock_name)
    assert not lock.locked()

    # acquire lock from thread

    def acquire_in_thread():
        lock_thread = Lock.singleton(lock_name)
        lock_thread.acquire()

    t = threading.Thread(
        target=acquire_in_thread,
        daemon=True,
    )
    t.start()

    # check that lock is acquired
    assert lock.locked()

    # try to re-acquire
    res = lock.acquire()
    assert not res
    assert lock.locked()

    # check pid of locking process
    assert lock.locking_pid() == os.getpid()

    # release lock
    lock.release()
    assert not lock.locked()

    # try to re-release lock
    with pytest.raises(RuntimeError):
        lock.release()


def test_locking_multiprocess():
    lock_name = "test-lock-" + str(uuid.uuid4())

    # initialise lock
    lock = Lock.singleton(lock_name)
    assert not lock.locked()

    # try to release lock, will fail because it is not acquired
    with pytest.raises(RuntimeError):
        lock.release()

    # acquire lock from different process

    cmd = (
        "import time; from maestral.daemon import Lock; "
        f"l = Lock.singleton({lock_name!r}); l.acquire(); "
        "time.sleep(60);"
    )

    p = subprocess.Popen([sys.executable, "-c", cmd])

    time.sleep(1)

    # check that lock is acquired
    assert lock.locked()

    # try to re-acquire
    res = lock.acquire()
    assert not res
    assert lock.locked()

    # try to release lock, will fail because it is owned by a different process
    with pytest.raises(RuntimeError):
        lock.release()

    # check pid of locking process
    assert lock.locking_pid() == p.pid

    # release lock by terminating process
    p.terminate()
    p.wait()
    assert not lock.locked()


# daemon lifecycle tests


def test_lifecycle(config_name):

    # start daemon process
    res = start_maestral_daemon_process(config_name, timeout=20)
    assert res is Start.Ok

    # retry start daemon process
    res = start_maestral_daemon_process(config_name, timeout=20)
    assert res is Start.AlreadyRunning

    # retry start daemon in-process
    with pytest.raises(RuntimeError):
        start_maestral_daemon(config_name)

    # stop daemon
    res = stop_maestral_daemon_process(config_name)
    assert res is Stop.Ok

    # retry stop daemon
    res = stop_maestral_daemon_process(config_name)
    assert res is Stop.NotRunning


# proxy tests


def test_connection(config_name):

    # start daemon process
    res = start_maestral_daemon_process(config_name, timeout=20)
    assert res is Start.Ok

    # create proxy
    with MaestralProxy(config_name) as m:
        assert m.config_name == config_name
        assert not m._is_fallback
        assert isinstance(m._m, Proxy)

    # stop daemon
    res = stop_maestral_daemon_process(config_name)
    assert res is Stop.Ok


def test_fallback(config_name):

    # create proxy w/o fallback
    with pytest.raises(CommunicationError):
        MaestralProxy(config_name)

    # create proxy w/ fallback
    with MaestralProxy(config_name, fallback=True) as m:
        assert m.config_name == config_name
        assert m._is_fallback
        assert isinstance(m._m, Maestral)


def test_remote_exceptions(config_name):

    # start daemon process
    start_maestral_daemon_process(config_name, timeout=20)

    # create proxy and call a remote method which raises an error
    with MaestralProxy(config_name) as m:
        with pytest.raises(NotLinkedError):
            m.get_account_info()

    # stop daemon
    stop_maestral_daemon_process(config_name)
