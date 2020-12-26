# -*- coding: utf-8 -*-
"""This module defines custom logging records and handlers."""

import logging
from collections import deque
from concurrent.futures import Future, wait
from typing import Deque, Optional, List

try:
    from concurrent.futures import InvalidStateError  # type: ignore
except ImportError:
    # Python 3.7 and lower
    InvalidStateError = RuntimeError  # type: ignore

import sdnotify  # type: ignore

try:
    from systemd import journal  # type: ignore
except ImportError:
    journal = None

from .utils import sanitize_string


__all__ = [
    "EncodingSafeLogRecord",
    "CachedHandler",
    "SdNotificationHandler",
    "safe_journal_sender",
]


if journal:

    def safe_journal_sender(MESSAGE: str, **kwargs) -> None:

        MESSAGE = sanitize_string(MESSAGE)

        for key, value in kwargs.items():
            if isinstance(value, str):
                kwargs[key] = sanitize_string(value)

        journal.send(MESSAGE, **kwargs)


else:

    def safe_journal_sender(MESSAGE: str, **kwargs) -> None:
        pass


class EncodingSafeLogRecord(logging.LogRecord):
    """A log record which ensures that messages contain only unicode characters

    This is useful when log messages may contain file paths generates by OS APIs. In
    Python, such path strings may contain surrogate escapes and will therefore raise
    a :class:`UnicodeEncodeError` under many circumstances (printing to stdout, etc).
    """

    def getMessage(self) -> str:
        """
        Formats the log message and replaces all surrogate escapes with "�".
        """
        msg = super().getMessage()
        return sanitize_string(msg)


logging.setLogRecordFactory(EncodingSafeLogRecord)


class CachedHandler(logging.Handler):
    """Handler which stores past records

    This is used to populate Maestral's status and error interfaces. The method
    :meth:`wait_for_emit` can be used from another thread to block until a new record is
    emitted, for instance to react to state changes.

    :param level: Initial log level. Defaults to NOTSET.
    :param maxlen: Maximum number of records to store. If ``None``, all records will be
        stored. Defaults to ``None``.
    """

    cached_records: Deque[logging.LogRecord]
    _emit_future: Future

    def __init__(
        self, level: int = logging.NOTSET, maxlen: Optional[int] = None
    ) -> None:
        super().__init__(level=level)
        self.cached_records = deque([], maxlen)
        self._emit_future = Future()

    def emit(self, record: logging.LogRecord) -> None:
        """
        Logs the specified log record and saves it to the cache.

        :param record: Log record.
        """
        self.cached_records.append(record)

        # notify any waiting coroutines that we have a status change
        try:
            self._emit_future.set_result(True)
        except InvalidStateError:
            pass

    def wait_for_emit(self, timeout: Optional[float]) -> bool:
        """
        Blocks until a new record is emitted.

        :param timeout: Maximum time to block before returning.
        :returns: ``True`` if there was a status change, ``False`` in case of a timeout.
        """
        done, not_done = wait([self._emit_future], timeout=timeout)
        self._emit_future = Future()  # reset future
        return len(done) == 1

    def getLastMessage(self) -> str:
        """
        :returns: The log message of the last record or an empty string.
        """
        try:
            last_record = self.cached_records[-1]
            return last_record.getMessage()
        except IndexError:
            return ""

    def getAllMessages(self) -> List[str]:
        """
        :returns: A list of all record messages.
        """
        return [r.getMessage() for r in self.cached_records]

    def clear(self) -> None:
        """
        Clears all cached records.
        """
        self.cached_records.clear()


class SdNotificationHandler(logging.Handler):
    """Handler which emits messages as systemd notifications

    This is useful when used from a systemd service and will do nothing when no
    NOTIFY_SOCKET is provided.
    """

    notifier = sdnotify.SystemdNotifier()

    def emit(self, record: logging.LogRecord) -> None:
        """
        Sends the record massage to systemd as service status.

        :param record: Log record.
        """
        self.notifier.notify(f"STATUS={record.message}")
