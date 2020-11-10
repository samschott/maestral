import sys
import os
import time
import subprocess
import threading
import multiprocessing as mp
import unittest
from unittest import TestCase

from maestral.daemon import (
    CommunicationError,
    Proxy,
    MaestralProxy,
    start_maestral_daemon,
    start_maestral_daemon_process,
    stop_maestral_daemon_process,
    Start,
    Stop,
    Lock,
    IS_MACOS,
)
from maestral.utils.housekeeping import remove_configuration
from maestral.main import Maestral
from maestral.errors import NotLinkedError


class TestDaemonLock(TestCase):
    def test_locking_from_same_thread(self):

        # initialise lock
        lock = Lock.singleton("test-lock")
        self.assertFalse(lock.locked())

        # acquire lock
        res = lock.acquire()
        self.assertTrue(res)
        self.assertTrue(lock.locked())

        # try to reacquire
        res = lock.acquire()
        self.assertFalse(res)
        self.assertTrue(lock.locked())

        # check pid of locking process
        self.assertEqual(lock.locking_pid(), os.getpid())

        # release lock
        lock.release()
        self.assertFalse(lock.locked())

        # try to re-release lock
        with self.assertRaises(RuntimeError):
            lock.release()

    def test_locking_threaded(self):

        # initialise lock
        lock = Lock.singleton("test-lock")
        self.assertFalse(lock.locked())

        # acquire lock from thread

        def acquire_in_thread():
            l = Lock.singleton("test-lock")
            l.acquire()

        t = threading.Thread(
            target=acquire_in_thread,
            daemon=True,
        )
        t.start()

        # check that lock is acquired
        self.assertTrue(lock.locked())

        # try to re-acquire
        res = lock.acquire()
        self.assertFalse(res)
        self.assertTrue(lock.locked())

        # check pid of locking process
        self.assertEqual(lock.locking_pid(), os.getpid())

        # release lock
        lock.release()
        self.assertFalse(lock.locked())

        # try to re-release lock
        with self.assertRaises(RuntimeError):
            lock.release()

    def test_locking_multiprocess(self):

        # initialise lock
        lock = Lock.singleton("test-lock")
        self.assertFalse(lock.locked())

        # acquire lock from different process

        cmd = (
            "import time; from maestral.daemon import Lock; "
            "l = Lock.singleton('test-lock'); l.acquire(); "
            "time.sleep(60);"
        )

        p = subprocess.Popen([sys.executable, "-c", cmd])

        time.sleep(1)

        # check that lock is acquired
        self.assertTrue(lock.locked())

        # try to re-acquire
        res = lock.acquire()
        self.assertFalse(res)
        self.assertTrue(lock.locked())

        # try to release lock, will fail because it is owned by a different process
        with self.assertRaises(RuntimeError):
            lock.release()

        # check pid of locking process
        self.assertEqual(lock.locking_pid(), p.pid)

        # release lock by terminating process
        p.terminate()
        p.wait()
        self.assertFalse(lock.locked())


class TestDaemonLifecycle(TestCase):
    config_name = "daemon-lifecycle-test"

    def cleanUp(self):
        stop_maestral_daemon_process(self.config_name)

    def test_lifecycle_detached(self):

        # start daemon process
        res = start_maestral_daemon_process(self.config_name)
        self.assertEqual(res, Start.Ok)

        # retry start daemon process
        res = start_maestral_daemon_process(self.config_name)
        self.assertEqual(res, Start.AlreadyRunning)

        # retry start daemon in-process
        with self.assertRaises(RuntimeError):
            start_maestral_daemon(self.config_name)

        # stop daemon
        res = stop_maestral_daemon_process(self.config_name)
        self.assertEqual(res, Stop.Ok)

        # retry stop daemon
        res = stop_maestral_daemon_process(self.config_name)
        self.assertEqual(res, Stop.NotRunning)

        # clean up config
        remove_configuration(self.config_name)

    def test_lifecycle_attached(self):

        # start daemon process
        res = start_maestral_daemon_process(self.config_name, detach=False)
        self.assertEqual(res, Start.Ok)

        # check that we have attached process
        ctx = mp.get_context("spawn" if IS_MACOS else "fork")
        daemon = ctx.active_children()[0]
        self.assertEqual(daemon.name, "maestral-daemon")

        # stop daemon
        res = stop_maestral_daemon_process(self.config_name)
        self.assertEqual(res, Stop.Ok)

        # retry stop daemon
        res = stop_maestral_daemon_process(self.config_name)
        self.assertEqual(res, Stop.NotRunning)

        # clean up config
        remove_configuration(self.config_name)


class TestMaestralProxy(TestCase):
    config_name = "daemon-proxy-test"

    def cleanUp(self):
        stop_maestral_daemon_process(self.config_name)

    def test_connection(self):

        # start daemon process
        res = start_maestral_daemon_process(self.config_name)
        self.assertEqual(Start.Ok, res)

        # create proxy
        with MaestralProxy(self.config_name) as m:
            self.assertEqual(m.config_name, self.config_name)
            self.assertFalse(m._is_fallback)
            self.assertIsInstance(m._m, Proxy)

        # stop daemon
        res = stop_maestral_daemon_process(self.config_name)
        self.assertEqual(res, Stop.Ok)

        # clean up config
        remove_configuration(self.config_name)

    def test_fallback(self):

        config_name = "daemon-lifecycle-test"

        # create proxy w/o fallback
        with self.assertRaises(CommunicationError):
            MaestralProxy(config_name)

        # create proxy w/ fallback
        with MaestralProxy(config_name, fallback=True) as m:
            self.assertEqual(m.config_name, config_name)
            self.assertTrue(m._is_fallback)
            self.assertIsInstance(m._m, Maestral)

        # clean up config
        remove_configuration(config_name)

    def test_remote_exceptions(self):

        # start daemon process
        start_maestral_daemon_process(self.config_name)

        # create proxy and call a remote method which raises an error
        with MaestralProxy(self.config_name) as m:
            with self.assertRaises(NotLinkedError):
                m.get_account_info()

        # stop daemon
        stop_maestral_daemon_process(self.config_name)

        # clean up config
        remove_configuration(self.config_name)


if __name__ == "__main__":
    unittest.main()
