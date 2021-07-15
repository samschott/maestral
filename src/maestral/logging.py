# -*- coding: utf-8 -*-
"""This module defines custom logging records and handlers."""

import os
import concurrent.futures
import logging
from logging.handlers import RotatingFileHandler
from collections import deque
from concurrent.futures import Future
from typing import Deque, Optional, List, Tuple, Union

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

from .config import MaestralConfig
from .utils import sanitize_string
from .utils.appdirs import get_log_path


__all__ = [
    "CachedHandler",
    "SdNotificationHandler",
    "setup_logging",
    "scoped_logger",
]


def safe_journal_sender(MESSAGE: str, **kwargs) -> None:

    if journal:

        MESSAGE = sanitize_string(MESSAGE)

        for key, value in kwargs.items():
            if isinstance(value, str):
                kwargs[key] = sanitize_string(value)

        journal.send(MESSAGE, **kwargs)


class EncodingSafeLogRecord(logging.LogRecord):
    """A log record which ensures that messages contain only unicode characters

    This is useful when log messages may contain file paths generates by OS APIs. In
    Python, such path strings may contain surrogate escapes and will therefore raise
    a :class:`UnicodeEncodeError` under many circumstances (printing to stdout, etc).
    """

    _safe_msg: Optional[str] = None

    def getMessage(self) -> str:
        """
        Formats the log message and replaces all surrogate escapes with "�".
        """
        if not self._safe_msg:
            msg = super().getMessage()
            self._safe_msg = sanitize_string(msg)

        return self._safe_msg


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
        try:
            self._emit_future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return False

        self._emit_future = Future()  # reset future
        return True

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
    config_name: str, log_to_stderr: bool = True
) -> Tuple[
    RotatingFileHandler,
    Union[logging.StreamHandler, logging.NullHandler],
    SdNotificationHandler,
    Union["journal.JournalHandler", logging.NullHandler],
]:
    """
    Sets up logging handlers for the given config name. The following handlers are
    installed for the root logger:

    * RotatingFileHandler: Writes logs to the appropriate log file for the config. Log
      level is determined by the config value.
    * StreamHandler: Writes logs to stderr. Log level is determined by the config value.
      This will be replaced by a null handler if ``log_to_stderr`` is ``False``.
    * SdNotificationHandler: Sends all log messages of level INFO and higher to the
      NOTIFY_SOCKET if provided as an environment variable. The log level is fixed.
    * JournalHandler: Writes logs to the systemd journal. Log level is determined by the
      config value. Will be replaced by a null handler if not started as a systemd
      service or if python-systemd is not installed.

    Any previous loggers are cleared.

    :param config_name: The config name.
    :param log_to_stderr: Whether to log to stderr.
    :returns: (log_handler_file, log_handler_stream, log_handler_sd, log_handler_journal)
    """

    conf = MaestralConfig(config_name)

    # Get log level from config or fallback to DEBUG level if config file is corrupt.
    log_level = conf.get("app", "log_level", logging.DEBUG)

    root_logger = scoped_logger("maestral", config_name)
    root_logger.setLevel(min(log_level, logging.INFO))

    root_logger.handlers.clear()  # clean up any previous handlers

    log_fmt_long = logging.Formatter(
        fmt="%(asctime)s %(module)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log_fmt_short = logging.Formatter(fmt="%(message)s")

    # Log to file.
    log_file_path = get_log_path("maestral", f"{config_name}.log")
    log_handler_file = RotatingFileHandler(
        log_file_path, maxBytes=10 ** 7, backupCount=1
    )
    log_handler_file.setFormatter(log_fmt_long)
    log_handler_file.setLevel(log_level)
    root_logger.addHandler(log_handler_file)

    # Log to systemd journal when running as systemd service.
    log_handler_journal: Union["journal.JournalHandler", logging.NullHandler]

    if journal and os.getenv("INVOCATION_ID"):
        log_handler_journal = journal.JournalHandler(
            SYSLOG_IDENTIFIER="maestral",
            sender_function=safe_journal_sender,
        )
    else:
        log_handler_journal = logging.NullHandler()

    log_handler_journal.setFormatter(log_fmt_short)
    log_handler_journal.setLevel(log_level)
    root_logger.addHandler(log_handler_journal)

    # Log to NOTIFY_SOCKET when launched as systemd notify service.
    log_handler_sd = SdNotificationHandler()
    log_handler_sd.setFormatter(log_fmt_short)
    log_handler_sd.setLevel(logging.INFO)
    root_logger.addHandler(log_handler_sd)

    # Log to stderr if requested.
    log_handler_stream: Union[logging.StreamHandler, logging.NullHandler]

    if log_to_stderr:
        log_handler_stream = logging.StreamHandler()
    else:
        log_handler_stream = logging.NullHandler()
    log_handler_stream.setFormatter(log_fmt_long)
    log_handler_stream.setLevel(log_level)
    root_logger.addHandler(log_handler_stream)

    return log_handler_file, log_handler_stream, log_handler_sd, log_handler_journal
