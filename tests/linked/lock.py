import time
import uuid
from datetime import datetime

from dropbox import files

from maestral.core import FileMetadata
from maestral.client import DropboxClient
from maestral.exceptions import NotFoundError, FileConflictError
from maestral.errorhandling import convert_api_errors


class DropboxTestLock:
    """
    A lock on a Dropbox account for running sync tests. The lock will be acquired by
    create a file at ``lock_path`` and released by deleting the file on the remote
    Dropbox. This can be used to synchronise tests running on the same Dropbox account.
    Lock files older than 1h are considered expired and will be discarded.

    :param client: Linked client instance.
    :param lock_path: Path for the lock folder.
    :param expires_after: The lock will be considered as expired after the given time in
        seconds since the acquire call. Defaults to 15 min.
    """

    def __init__(
        self,
        client: DropboxClient,
        lock_path: str = "/test.lock",
        expires_after: float = 15 * 60,
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

        # we encode the expiry time in the client_modified time stamp
        expiry_time = datetime.utcfromtimestamp(time.time() + self.expires_after)

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

        md = self.client.get_metadata(self.lock_path)

        if not md:
            return False

        elif isinstance(md, FileMetadata) and md.client_modified < datetime.utcnow():
            # lock has expired, remove
            try:
                self.client.remove(self.lock_path, parent_rev=md.rev)
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
            self.client.remove(self.lock_path, parent_rev=self._rev)
        except NotFoundError:
            pass

        self._rev = None
