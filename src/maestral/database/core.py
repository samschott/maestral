"""
This model defines our core SQLite database interface.
"""

from __future__ import annotations

import sqlite3


class Database:
    """Proxy class to access sqlite3.connect method."""

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        """Returns an existing SQL connection or creates a new one."""

        if self._connection:
            return self._connection
        else:
            connection = sqlite3.connect(*self.args, **self.kwargs)
            connection.row_factory = sqlite3.Row
            self._connection = connection
            return connection

    def close(self) -> None:
        """Closes the SQL connection."""
        if self._connection:
            self._connection.close()
        self._connection = None

    def commit(self) -> None:
        """Commits SQL changes."""
        self.connection.commit()

    def execute(self, sql: str, *args) -> sqlite3.Cursor:
        """
        Creates a cursor and executes the given SQL statement.

        :param sql: SQL statement to execute.
        :param args: Parameters to substitute for placeholders in SQL statement.
        :returns: The created cursor.
        """
        with self.connection:
            return self.connection.execute(sql, args)

    def executescript(self, script: str) -> None:
        """
        Creates a cursor and executes the given SQL script.

        :param script: SQL script to execute.
        :returns: The created cursor.
        """
        with self.connection:
            self.connection.cursor().executescript(script)
