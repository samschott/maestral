
Logging
=======

Maestral makes extensive use of Python's logging module to collect debug, status and
error information from different parts of the program and distribute it to the
appropriate channels.

Broadly speaking, the builtin log levels are used as follows:

.. csv-table::
   :file: log_levels.csv
   :widths: 20, 70
   :header-rows: 1

Maestral defines a number of log handlers to process those messages, some of them for
internal usage, others for external communication. For instance, cached logging handlers
are used to populate the public APIs :attr:`Maestral.status` and
:attr:`Maestral.fatal_errors` and therefore use fixed log levels. Logging to stderr,
the systemd journal (if applicable) and to our log files uses the user defined log level
from :attr:`Maestral.log_level` which defaults to INFO.

.. csv-table::
   :file: log_handlers.csv
   :widths: 50, 50, 50
   :header-rows: 1

All custom handlers are defined in the :mod:`maestral.logging` module. Maestral also
subclasses the default :class:`logging.LogRecord` to guarantee that any surrogate escape
characters in file paths are replaced before emitting a log and flushing to any streams.
Otherwise, incorrectly encoded file paths could prevent logging from working properly
when it would be particularly useful.

In addition to those automated logging facilities, desktop notifications are sent
manually on file changes and sync issues with appropriate buttons and callbacks for user
interaction. Those notifications can be separately enabled, disabled or snoozed though
the GUI or the CLI with ``maestral notify level`` and ``maestral notify snooze``.
