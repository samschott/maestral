"""
SQL column type definitions, including conversion rules from / to Python types.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Iterable, Union

ColumnValueType = Union[str, int, float, Enum, None]


class SqlType:
    """Base class to represent Python types in SQLite table"""

    sql_type = "TEXT"
    py_type: type[ColumnValueType] = str

    def sql_to_py(self, value):
        """Converts the return value from sqlite3 to the target Python type."""
        return value

    def py_to_sql(self, value):
        """Converts a Python value to a type accepted by sqlite3."""
        return value


class SqlString(SqlType):
    """Class to represent Python strings in SQLite table"""

    sql_type = "TEXT"
    py_type = str


class SqlInt(SqlType):
    """Class to represent Python integers in SQLite table"""

    sql_type = "INTEGER"
    py_type = int


class SqlFloat(SqlType):
    """Class to represent Python floats in SQLite table"""

    sql_type = "REAL"
    py_type = float


class SqlPath(SqlType):
    """
    Class to represent Python paths in SQLite table

    Paths are stored as bytes in the database to handle characters in the path which
    cannot be decoded in the reported file system encoding. On the Python side, paths
    will contain surrogate escapes in place of such characters.
    """

    sql_type = "BLOB"
    py_type = str

    def sql_to_py(self, value: bytes | None) -> str | None:
        if value is None:
            return value

        return os.fsdecode(value)

    def py_to_sql(self, value: str | None) -> bytes | None:
        if value is None:
            return value

        return os.fsencode(value)


class SqlEnum(SqlType):
    """Class to represent Python enums in SQLite table

    Enums are stored as text (attribute name) in the database.
    """

    sql_type = "TEXT"
    py_type = Enum

    def __init__(self, enum: Iterable[Enum]) -> None:
        self.enum_type = enum

    def sql_to_py(self, value: str | None) -> Enum | None:  # type: ignore
        if value is None:
            return None

        return getattr(self.enum_type, value)

    def py_to_sql(self, value: Enum | None) -> str | None:
        if value is None:
            return None

        return value.name
