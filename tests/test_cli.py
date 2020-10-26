import os
import unittest
import subprocess
from unittest import TestCase

import Pyro5.errors
from maestral.daemon import MaestralProxy

from .fixtures import setup_test_config, cleanup_test_config, DropboxTestLock


@unittest.skipUnless(os.environ.get("DROPBOX_TOKEN"), "Requires auth token")
class TestCLI(TestCase):

    config_name = "cli-test-config"

    @classmethod
    def setUpClass(cls):

        # link to an existing Dropbox account

        cls.m = setup_test_config(cls.config_name)
        cls.lock = DropboxTestLock(cls.m)
        if not cls.lock.acquire(timeout=60 * 60):
            raise TimeoutError("Could not acquire test lock")

    @classmethod
    def tearDownClass(cls):

        # clean up linking and config

        if hasattr(cls, "m"):
            cleanup_test_config(cls.m)

        if hasattr(cls, "lock"):
            cls.lock.release()

    def test_start_stop(self):
        subprocess.run(["maestral", "start", "-c", self.config_name])

        with MaestralProxy(self.config_name) as m:
            self.assertTrue(m.running)
            self.assertTrue(m.syncing)

        subprocess.run(["maestral", "stop", "-c", self.config_name])

        with self.assertRaises(Pyro5.errors.CommunicationError):
            MaestralProxy(self.config_name)
