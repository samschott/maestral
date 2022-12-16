"""Module for content hashing file contents."""

from __future__ import annotations

# system imports
import hashlib

from typing import BinaryIO, Union

_WritableBuffer = Union[bytes, bytearray]


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
        self._digested = False
        self._block_pos = 0

        self.digest_size = self._overall_hasher.digest_size

    def _reuse_guard(self) -> None:
        if self._digested:
            raise RuntimeError(
                "Can't use this object anymore; "
                "you already called digest() or hexdigest()"
            )

    def update(self, new_data: bytes | bytearray) -> None:
        self._reuse_guard()

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

    def _finish(self) -> "hashlib._Hash":
        self._reuse_guard()
        self._digested = True

        if self._block_pos > 0:
            self._overall_hasher.update(self._block_hasher.digest())
        return self._overall_hasher

    def digest(self) -> bytes:
        return self._finish().digest()

    def hexdigest(self) -> str:
        return self._finish().hexdigest()

    def copy(self) -> DropboxContentHasher:
        self._reuse_guard()
        c = DropboxContentHasher.__new__(DropboxContentHasher)
        c._overall_hasher = self._overall_hasher.copy()
        c._block_hasher = self._block_hasher.copy()
        c._block_pos = self._block_pos
        return c


class StreamHasher:
    """
    A wrapper around a file-like object (either for reading or writing)
    that hashes everything that passes through it.  Can be used with
    DropboxContentHasher or any 'hashlib' hasher.

    :Example:

        >>> hasher = DropboxContentHasher()
        >>> with open('some-file', 'rb') as f:
        ...     wrapped_f = StreamHasher(f, hasher)
        ...     response = some_api_client.upload(wrapped_f)
        >>> locally_computed = hasher.hexdigest()
        >>> assert response.content_hash == locally_computed

    :param f: File-like object.
    :param hasher: Hasher to use. Must implement an ``update`` method.
    """

    def __init__(self, f: BinaryIO, hasher: DropboxContentHasher) -> None:
        self._f = f
        self._hasher = hasher

    def close(self) -> None:
        return self._f.close()

    def flush(self) -> None:
        return self._f.flush()

    def fileno(self) -> int:
        return self._f.fileno()

    def tell(self) -> int:
        return self._f.tell()

    def read(self, size: int = -1) -> bytes:
        b = self._f.read(size)
        self._hasher.update(b)
        return b

    def write(self, b: _WritableBuffer) -> int:
        self._hasher.update(b)
        return self._f.write(b)

    def readline(self, size: int = -1) -> bytes:
        b = self._f.readline(size)
        self._hasher.update(b)
        return b

    def readlines(self, hint: int = -1) -> list[bytes]:
        bs = self._f.readlines(hint)
        for b in bs:
            self._hasher.update(b)
        return bs
