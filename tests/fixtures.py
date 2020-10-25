# -*- coding: utf-8 -*-

import os
import logging
import time
from typing import Optional

from maestral.sync import delete
from maestral.errors import NotFoundError, FolderConflictError
from maestral.main import Maestral
from maestral.utils.housekeeping import remove_configuration
from maestral.utils.path import generate_cc_name
from maestral.utils.appdirs import get_home_dir


env_token = os.environ.get("DROPBOX_TOKEN", "")


def setup_test_config(
    config_name: str = "test-config", access_token: Optional[str] = env_token
) -> Maestral:
    """
    Sets up a new maestral configuration and links it to a Dropbox account with the
    given token. Creates a new local Dropbox folder for the config.

    :param config_name: Config name to use or  create.
    :param access_token: The access token to use to link the config to an account.
    :returns: A linked Maestral instance.
    """

    m = Maestral(config_name)
    m.log_level = logging.DEBUG
    m.client._init_sdk_with_token(access_token=access_token)

    home = get_home_dir()
    local_dropbox_dir = generate_cc_name(
        os.path.join(home, "Dropbox"), suffix="test runner"
    )
    m.create_dropbox_directory(local_dropbox_dir)

    return m


def cleanup_test_config(m: Maestral, test_folder_dbx: Optional[str] = None) -> None:
    """
    Shuts down syncing for the given Maestral instance, removes all local files and
    folders related to that instance, including the local Dropbox folder, and removes
    any '.mignore' files.

    :param m: Maestral instance.
    :param test_folder_dbx: Optional test folder to clean up.
    """

    # stop syncing and clean up remote folder
    m.stop_sync()

    if test_folder_dbx:
        try:
            m.client.remove(test_folder_dbx)
        except NotFoundError:
            pass

    try:
        m.client.remove("/.mignore")
    except NotFoundError:
        pass

    # remove local files and folders
    delete(m.dropbox_path)
    remove_configuration("test-config")


class DropboxTestLock:
    """
    A lock on a Dropbox account for running sync tests. The lock will be acquired by
    create a folder at ``lock_path`` and released by deleting the folder on the remote
    Dropbox. This can be used to synchronise tests running on the same Dropbox account.

    :param m: Linked Maestral instance.
    :param lock_path: Path for the lock folder.
    """

    def __init__(self, m: Maestral, lock_path: str = "/test.lock") -> None:

        self.m = m
        self.lock_path = lock_path

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """
        Acquires the lock. When invoked with the blocking argument set to True (the
        default), this blocks until the lock is unlocked, then sets it to locked and
        returns True. When invoked with the blocking argument set to False, this call
        does not block. If the lock cannot be acquired, returns False immediately;
        otherwise, sets the lock to locked and returns True.

        :param blocking: Whether to block until the lock can be acquired.s
        :param timeout: Timeout in seconds. If negative, no timeout will be applied.
            If positive, blocking must be set to True.
        :returns: Whether the lock could be acquired (within timeout).
        """

        if not blocking and timeout > 0:
            raise ValueError("can't specify a timeout for a non-blocking call")

        t0 = time.time()

        while True:
            try:
                self.m.client.make_dir(self.lock_path)
            except FolderConflictError:
                pass
            else:
                return True

            if time.time() - t0 > timeout > 0:
                return False
            else:
                time.sleep(10)

    def release(self) -> None:
        """
        Releases the lock.

        :raises: RuntimeError if the lock was not locked.
        """

        try:
            self.m.client.remove(self.lock_path)
        except NotFoundError:
            raise RuntimeError("release unlocked lock")
