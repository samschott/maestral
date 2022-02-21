"""Module for content hashing file contents."""

# system imports
import hashlib


class DropboxContentHasher:
    """
    Computes a hash using the same algorithm that the Dropbox API uses for the
    "content_hash" metadata field.

    The :meth:`digest` method returns a raw binary representation of the hash.  The
    :meth:`hexdigest` convenience method returns a hexadecimal-encoded version, which
    is what the "content_hash" metadata field uses.

    This class has the same interface as the hashers in the standard 'hashlib' package.

    :Example:

        Read a file in chunks of 1024 bytes and compute its content hash:

        >>> hasher = DropboxContentHasher()
        >>> with open('some-file', 'rb') as f:
        ...     while True:
        ...         chunk = f.read(1024)
        ...         if len(chunk) == 0:
        ...             break
        ...         hasher.update(chunk)
        ... print(hasher.hexdigest())

    """

    BLOCK_SIZE = 4 * 1024 * 1024

    def __init__(self) -> None:
        self._overall_hasher = hashlib.sha256()
        self._block_hasher = hashlib.sha256()
        self._block_pos = 0

        self.digest_size = self._overall_hasher.digest_size

    def update(self, new_data: bytes) -> None:
        if self._overall_hasher is None:
            raise RuntimeError(
                "can't use this object anymore; you already called digest()"
            )

        if not isinstance(new_data, bytes):
            raise ValueError(f"Expecting a byte string, got {new_data!r}")

        new_data_pos = 0
        while new_data_pos < len(new_data):
            if self._block_pos == self.BLOCK_SIZE:
                self._overall_hasher.update(self._block_hasher.digest())
                self._block_hasher = hashlib.sha256()
                self._block_pos = 0

            space_in_block = self.BLOCK_SIZE - self._block_pos
            part = new_data[new_data_pos : (new_data_pos + space_in_block)]
            self._block_hasher.update(part)

            self._block_pos += len(part)
            new_data_pos += len(part)

    def _finish(self):
        if self._overall_hasher is None:
            raise RuntimeError(
                "Can't use this object anymore; "
                "you already called digest() or hexdigest()"
            )

        if self._block_pos > 0:
            self._overall_hasher.update(self._block_hasher.digest())
            self._block_hasher = None
        h = self._overall_hasher
        self._overall_hasher = None  # Make sure we can't use this object anymore.
        return h

    def digest(self) -> bytes:
        return self._finish().digest()

    def hexdigest(self) -> str:
        return self._finish().hexdigest()

    def copy(self) -> "DropboxContentHasher":
        c = DropboxContentHasher.__new__(DropboxContentHasher)
        c._overall_hasher = self._overall_hasher.copy()
        c._block_hasher = self._block_hasher.copy()
        c._block_pos = self._block_pos
        return c
