"""Module containing cache implementations."""

from __future__ import annotations

from collections import OrderedDict
from threading import RLock
from typing import Any, Hashable


class LRUCache:
    """A simple LRU cache implementation

    :param capacity: Maximum number of entries to keep.
    """

    def __init__(self, capacity: int) -> None:
        self._lock = RLock()
        self._cache: OrderedDict[Hashable, Any] = OrderedDict()
        self.capacity = capacity

    def get(self, key: Hashable) -> Any:
        """
        Get the cached value for a key. Mark as most recently used.

        :param key: Key to query.
        :returns: Cached value or None.
        """
        with self._lock:
            try:
                self._cache.move_to_end(key)
                return self._cache[key]
            except KeyError:
                return None

    def put(self, key: Hashable, value: Any) -> None:
        """
        Set the cached value for a key. Mark as most recently used.

        :param key: Key to use. Must be hashable.
        :param value: Value to cache.
        """
        with self._lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            if len(self._cache) > self.capacity:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        """
        Clears the cache.
        """
        with self._lock:
            self._cache.clear()
