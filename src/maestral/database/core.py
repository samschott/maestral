"""
This model defines our core SQLite database interface.
"""

from __future__ import annotations

from typing import Any

import sqlite3


class Database:
    """Wrapper around sqlite3.Connection with atomic transactions."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row
        self.connection = connection

    def close(self) -> None:
        """Closes the SQL connection."""
        self.connection.close()

    def execute(self, sql: str, *args: Any) -> sqlite3.Cursor:
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
