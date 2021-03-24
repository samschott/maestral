# -*- coding: utf-8 -*-
"""Module for content hashing."""

# system imports
import hashlib


class DropboxContentHasher:
    """
    Computes a hash using the same algorithm that the Dropbox API uses for the
    the "content_hash" metadata field.

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

    def __init__(self):
        self._overall_hasher = hashlib.sha256()
        self._block_hasher = hashlib.sha256()
        self._block_pos = 0

        self.digest_size = self._overall_hasher.digest_size

    def update(self, new_data):
        if self._overall_hasher is None:
            raise RuntimeError(
                "can't use this object anymore; you already called digest()"
            )

        if not isinstance(new_data, bytes):
            raise ValueError("Expecting a byte string, got {!r}".format(new_data))

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
                "can't use this object anymore; you already called digest() or hexdigest()"
            )

        if self._block_pos > 0:
            self._overall_hasher.update(self._block_hasher.digest())
            self._block_hasher = None
        h = self._overall_hasher
        self._overall_hasher = None  # Make sure we can't use this object anymore.
        return h

    def digest(self):
        return self._finish().digest()

    def hexdigest(self):
        return self._finish().hexdigest()

    def copy(self):
        c = DropboxContentHasher.__new__(DropboxContentHasher)
        c._overall_hasher = self._overall_hasher.copy()
        c._block_hasher = self._block_hasher.copy()
        c._block_pos = self._block_pos
        return c


class StreamHasher:
    """
    A wrapper around a file-like object (either for reading or writing)
    that hashes everything that passes through it. Can be used with
    :class:`DropboxContentHasher` or any 'hashlib' hasher.

    :Example:

        >>> hasher = DropboxContentHasher()
        >>> with open('some-file', 'rb') as f:
        ...     wrapped_f = StreamHasher(f, hasher)
        ...     response = some_api_client.upload(wrapped_f)
        >>> locally_computed = hasher.hexdigest()
        >>> assert response.content_hash == locally_computed
    """

    def __init__(self, f, hasher):
        self._f = f
        self._hasher = hasher

    def close(self):
        return self._f.close()

    def flush(self):
        return self._f.flush()

    def fileno(self):
        return self._f.fileno()

    def tell(self):
        return self._f.tell()

    def read(self, *args):
        b = self._f.read(*args)
        self._hasher.update(b)
        return b

    def write(self, b):
        self._hasher.update(b)
        return self._f.write(b)

    def next(self):
        b = self._f.next()
        self._hasher.update(b)
        return b

    def readline(self, *args):
        b = self._f.readline(*args)
        self._hasher.update(b)
        return b

    def readlines(self, *args):
        bs = self._f.readlines(*args)
        for b in bs:
            self._hasher.update(b)
        return b
