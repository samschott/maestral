"""This module defines custom logging records and handlers."""

from __future__ import annotations

import os
import concurrent.futures
import logging
import time
from logging.handlers import RotatingFileHandler
from collections import deque
from concurrent.futures import Future, InvalidStateError
from typing import Sequence

from .config import MaestralConfig
from .utils import sanitize_string
from .utils.appdirs import get_log_path
from .utils.integration import SystemdNotifier


__all__ = [
    "AwaitableHandler",
    "CachedHandler",
    "SdNotificationHandler",
    "EncodingSafeLogRecord",
    "scoped_logger",
    "scoped_logger_name",
    "LOG_FMT_LONG",
    "LOG_FMT_SHORT",
    "setup_logging",
]

LOG_FMT_LONG = logging.Formatter(
    fmt="%(asctime)s %(module)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOG_FMT_SHORT = logging.Formatter(fmt="%(message)s")


class EncodingSafeLogRecord(logging.LogRecord):
    """A log record which ensures that messages contain only unicode characters

    This is useful when log messages may contain file paths generates by OS APIs. In
    Python, such path strings may contain surrogate escapes and will therefore raise
    a :exc:`UnicodeEncodeError` under many circumstances (printing to stdout, etc.).
    """

    def getMessage(self) -> str:
        """
        Formats the log message and replaces all surrogate escapes with "�".
        """
        msg = super().getMessage()
        return sanitize_string(msg)


logging.setLogRecordFactory(EncodingSafeLogRecord)


class AwaitableHandler(logging.Handler):
    """Handler with a blocking API to wait for emits

    The method :meth:`wait_for_emit` can be used from another thread to block until a
    new record is emitted, for instance to react to state changes.

    :param level: Initial log level. Defaults to NOTSET.
    :param max_unblock_per_second: Maximum number of times per second to unblock.
    """

    _emit_future: Future[bool]

    def __init__(
        self, level: int = logging.NOTSET, max_unblock_per_second: int | None = 1
    ) -> None:
        super().__init__(level=level)

        self._emit_future = Future()
        self._last_emit = 0.0

        if max_unblock_per_second is None:
            self._min_wait = 0.0
        elif not max_unblock_per_second > 0:
            raise ValueError("max_unblock_per_second must be > 0")
        else:
            self._min_wait = 1 / max_unblock_per_second

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._emit_future.set_result(True)
        except InvalidStateError:
            pass

    def wait_for_emit(self, timeout: float | None) -> bool:
        """
        Blocks until a new record is emitted. This is effectively a longpoll API. Will
        unblock at max_unblock_per_second.

        :param timeout: Maximum time to block before returning.
        :returns: ``True`` if there was a status change, ``False`` in case of a timeout.
        """
        try:
            self._emit_future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return False

        t0 = time.monotonic()
        delay = max(self._min_wait - (t0 - self._last_emit), 0)

        if delay > 0:
            time.sleep(delay)

        self._emit_future = Future()  # reset future
        self._last_emit = time.monotonic()
        return True


class CachedHandler(logging.Handler):
    """Handler which stores past records

    This is used to populate Maestral's status and error interfaces. The method
    :meth:`wait_for_emit` can be used from another thread to block until a new record is
    emitted, for instance to react to state changes.

    :param level: Initial log level. Defaults to NOTSET.
    :param maxlen: Maximum number of records to store. If ``None``, all records will be
        stored. Defaults to ``None``.
    """

    cached_records: deque[logging.LogRecord]

    def __init__(self, level: int = logging.NOTSET, maxlen: int | None = None) -> None:
        super().__init__(level=level)
        self.cached_records = deque([], maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        """
        Logs the specified log record and saves it to the cache.

        :param record: Log record.
        """
        self.cached_records.append(record)

    def get_last_message(self) -> str:
        """
        :returns: The log message of the last record or an empty string.
        """
        try:
            last_record = self.cached_records[-1]
            return last_record.getMessage()
        except IndexError:
            return ""

    def get_all_messages(self) -> list[str]:
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

    notifier = SystemdNotifier()

    def emit(self, record: logging.LogRecord) -> None:
        """
        Sends the record message to systemd as service status.

        :param record: Log record.
        """
        self.notifier.notify(f"STATUS={record.getMessage()}")


def scoped_logger_name(module_name: str, config_name: str = "maestral") -> str:
    """
    Returns a logger name for the module ``module_name``, scoped to the given config.

    :param module_name: Module name.
    :param config_name: Config name.
    :returns: Scoped logger name.
    """
    if config_name == "maestral":
        return module_name
    else:
        return f"{config_name}-{module_name}"


def scoped_logger(module_name: str, config_name: str = "maestral") -> logging.Logger:
    """
    Returns a logger for the module ``module_name``, scoped to the given config.

    :param module_name: Module name.
    :param config_name: Config name.
    :returns: Logger instances scoped to the config.
    """
    return logging.getLogger(scoped_logger_name(module_name, config_name))


def setup_logging(
    config_name: str,
    file: bool = True,
    stderr: bool = True,
    journal: bool = True,
    status: bool = True,
) -> Sequence[logging.Handler]:
    """
    Set up loging to external channels. Systemd-related logging will fail silently if
    the current process was not started by systemd.

    :param config_name: Config name to determine log level and namespace for loggers.
        See :meth:`scoped_logger_name` for how the logger name is determined.
    :param file: Whether to log to files.
    :param stderr: Whether to log to stderr.
    :param journal: Whether to log to the systemd journal.
    :param status: Whether to log to the systemd status notifier. Note that this will
        always be performed at level INFO.
    :returns: Log handlers.
    """
    level = MaestralConfig(config_name).get("app", "log_level")
    root_logger = scoped_logger("maestral", config_name)
    root_logger.setLevel(min(level, logging.INFO))

    handlers: list[logging.Handler] = []

    # Log to file.
    if file:
        logfile = get_log_path("maestral", f"{config_name}.log")
        log_handler_file = RotatingFileHandler(logfile, maxBytes=10**7, backupCount=1)
        log_handler_file.setFormatter(LOG_FMT_LONG)
        log_handler_file.setLevel(level)
        root_logger.addHandler(log_handler_file)
        handlers.append(log_handler_file)

    # Log to systemd journal when launched as systemd service.
    if journal and os.getenv("INVOCATION_ID"):
        try:
            from systemd.journal import JournalHandler
        except ImportError:
            pass
        else:
            log_handler_journal = JournalHandler(SYSLOG_IDENTIFIER="maestral")
            log_handler_journal.setFormatter(LOG_FMT_SHORT)
            log_handler_journal.setLevel(level)
            root_logger.addHandler(log_handler_journal)
            handlers.append(log_handler_journal)

    # Log to systemd notify status when launched as systemd service.
    if status and os.getenv("NOTIFY_SOCKET"):
        log_handler_sd = SdNotificationHandler()
        log_handler_sd.setFormatter(LOG_FMT_SHORT)
        log_handler_sd.setLevel(logging.INFO)
        root_logger.addHandler(log_handler_sd)
        handlers.append(log_handler_sd)

    # Log to stderr if requested.
    if stderr:
        log_handler_stream = logging.StreamHandler()
        log_handler_stream.setFormatter(LOG_FMT_LONG)
        log_handler_stream.setLevel(level)
        root_logger.addHandler(log_handler_stream)
        handlers.append(log_handler_stream)

    return handlers
