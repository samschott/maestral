# -*- coding: utf-8 -*-

import os
import logging
import time
from datetime import datetime
import uuid
from typing import Optional

from dropbox.files import WriteMode, FileMetadata
from maestral.main import Maestral
from maestral.errors import NotFoundError, FileConflictError
from maestral.client import convert_api_errors
from maestral.utils.housekeeping import remove_configuration
from maestral.utils.path import generate_cc_name, delete
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

    # link with given token
    m.client._init_sdk_with_token(access_token=access_token)

    # get corresponding Dropbox ID and store in keyring for other processes
    res = m.client.get_account_info()
    m.client.auth._account_id = res.account_id
    m.client.auth._access_token = access_token
    m.client.auth._token_access_type = "legacy"
    m.client.auth.save_creds()

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

    # remove creds from system keyring
    m.client.auth.delete_creds()

    # remove local files and folders
    delete(m.dropbox_path)
    remove_configuration("test-config")


class DropboxTestLock:
    """
    A lock on a Dropbox account for running sync tests. The lock will be acquired by
    create a file at ``lock_path`` and released by deleting the file on the remote
    Dropbox. This can be used to synchronise tests running on the same Dropbox account.
    Lock files older than 1h are considered expired and will be discarded.

    :param m: Linked Maestral instance.
    :param lock_path: Path for the lock folder.
    :param expires_after: The lock will be considered as expired after the given time in
        seconds since the acquire call. Defaults to 15 min.
    """

    def __init__(
        self,
        m: Maestral,
        lock_path: str = "/test.lock",
        expires_after: float = 15 * 60,
    ) -> None:

        self.m = m
        self.lock_path = lock_path
        self.expires_after = expires_after
        self._rev = None

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """
        Acquires the lock. When invoked with the blocking argument set to True (the
        default), this blocks until the lock is unlocked, then sets it to locked and
        returns True. When invoked with the blocking argument set to False, this call
        does not block. If the lock cannot be acquired, returns False immediately;
        otherwise, sets the lock to locked and returns True.

        :param blocking: Whether to block until the lock can be acquired.
        :param timeout: Timeout in seconds. If negative, no timeout will be applied.
            If positive, blocking must be set to True.
        :returns: Whether the lock could be acquired (within timeout).
        """

        if not blocking and timeout > 0:
            raise ValueError("can't specify a timeout for a non-blocking call")

        t0 = time.time()

        # we encode the expiry time in the client_modified time stamp
        expiry_time = datetime.utcfromtimestamp(time.time() + self.expires_after)

        while True:
            try:
                with convert_api_errors(dbx_path=self.lock_path):
                    md = self.m.client.dbx.files_upload(
                        uuid.uuid4().bytes,
                        self.lock_path,
                        mode=WriteMode.add,
                        client_modified=expiry_time,
                    )
                    self._rev = md.rev
            except FileConflictError:
                if not self.locked():
                    continue
            else:
                return True

            if time.time() - t0 > timeout > 0:
                return False
            else:
                time.sleep(5)

    def locked(self):
        """
        Check if locked. Clean up any expired lock files.

        :returns: True if locked, False otherwise.
        """

        md = self.m.client.get_metadata(self.lock_path)

        if not md:
            return False

        elif isinstance(md, FileMetadata) and md.client_modified < datetime.utcnow():
            # lock has expired, remove
            try:
                self.m.client.remove(self.lock_path, parent_rev=md.rev)
            except NotFoundError:
                # protect against race
                pass

            return False
        else:
            return True

    def release(self) -> None:
        """
        Releases the lock.

        :raises: RuntimeError if the lock was not locked.
        """

        if not self._rev:
            raise RuntimeError("release unlocked lock")

        try:
            self.m.client.remove(self.lock_path, parent_rev=self._rev)
        except NotFoundError:
            raise RuntimeError("release unlocked lock")
        else:
            self._rev = None
