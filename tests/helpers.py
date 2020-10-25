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


def acquire_test_lock(
    m: Maestral, lock_path: str = "/test.lock", timeout: Optional[float] = None
) -> None:
    """
    Creates a folder at ``lock_path`` on Dropbox. This function blocks until the folder
    can be created.

    :param m: Maestral instance.
    :param lock_path: Path to lock folder on Dropbox.
    :param timeout: Time in seconds to wait until lock folder can be created.
    :raises: TimeoutError if the lock folder could not be created without ``timeout``.
    """

    t0 = time.time()

    while True:
        try:
            m.client.make_dir(lock_path)
        except FolderConflictError:
            time.sleep(10)
        else:
            break

        if timeout and time.time() - t0 > timeout:
            raise TimeoutError("Could not acquire test lock")


def release_test_lock(m: Maestral, lock_path: str = "/test.lock") -> None:
    """
    Removes the folder at ``lock_path`` on Dropbox.

    :param m: Maestral instance.
    :param lock_path: Path to lock folder on Dropbox.
    """

    try:
        m.client.remove(lock_path)
    except NotFoundError:
        pass
