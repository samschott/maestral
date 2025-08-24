import time
import uuid
from datetime import datetime, timezone

from dropbox import files

from maestral.client import DropboxClient
from maestral.core import FileMetadata
from maestral.errorhandling import convert_api_errors
from maestral.exceptions import FileConflictError, NotFoundError


class DropboxTestLock:
    """
    A lock on a Dropbox account to synchronize running tests.

    The lock is acquired by creating a file at ``lock_path`` and released by deleting
    the file on the remote Dropbox.

    :param client: Linked client instance.
    :param lock_path: Path for the lock folder.
    :param expires_after: The lock will be considered as expired after the given time in
        seconds since the acquire call. Defaults to 10 min.
    """

    def __init__(
        self,
        client: DropboxClient,
        lock_path,
        expires_after: float = 10 * 60,
    ) -> None:
        self.client = client
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

        # we store the expiry time in the client_modified time stamp
        expiry_time = datetime.fromtimestamp(
            time.time() + self.expires_after, tz=timezone.utc
        )

        while True:
            try:
                with convert_api_errors(dbx_path=self.lock_path):
                    md = self.client.dbx.files_upload(
                        uuid.uuid4().bytes,
                        self.lock_path,
                        mode=files.WriteMode.add,
                        client_modified=expiry_time,
                    )
                    self._rev = md.rev
            except FileConflictError:
                # Check if lockfile has expired. If yes, delete it retry to acquire.
                if not self.locked():
                    continue
            else:
                return True

            if not blocking:
                return False
            elif time.time() - t0 > timeout > 0:
                return False
            else:
                time.sleep(5)

    def renew(self, expires_after: float = 10 * 60) -> None:
        expiry_time = datetime.utcfromtimestamp(time.time() + expires_after)
        md = self.client.dbx.files_upload(
            uuid.uuid4().bytes,
            self.lock_path,
            mode=files.WriteMode.update(self._rev),
            client_modified=expiry_time,
        )
        self._rev = md.rev

    def locked(self):
        """
        Check if locked. Clean up any expired lock files.

        :returns: True if locked, False otherwise.
        """
        md = self.client.get_metadata(self.lock_path)

        if not md or not isinstance(md, FileMetadata):
            return False

        if md.client_modified < datetime.now(timezone.utc):
            # lock has expired, remove
            try:
                self.client.remove(self.lock_path, parent_rev=md.rev)
            except NotFoundError:
                # protect against race
                pass

            return False

        return True

    def release(self) -> None:
        """
        Releases the lock.

        :raises: RuntimeError we did not acquire the lock.
        """

        if not self._rev:
            raise RuntimeError("release unlocked lock")

        try:
            self.client.remove(self.lock_path, parent_rev=self._rev)
        except NotFoundError:
            pass

        self._rev = None
