"""
SQL column type definitions, including conversion rules from / to Python types.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Iterable, TypeVar, Generic, cast


T = TypeVar("T")
ST = TypeVar("ST")
ET = TypeVar("ET", bound=Enum)


class SqlType(Generic[T, ST]):
    """Base class to represent Python types in SQLite table"""

    sql_type = "TEXT"

    def sql_to_py(self, value: ST) -> T:
        """Converts the return value from sqlite3 to the target Python type."""
        return cast(T, value)

    def py_to_sql(self, value: T) -> ST:
        """Converts a Python value to a type accepted by sqlite3."""
        return cast(ST, value)


class SqlString(SqlType[str, str]):
    """Class to represent Python strings in SQLite table"""

    sql_type = "TEXT"


class SqlInt(SqlType[int, int]):
    """
    Class to represent Python integers in SQLite table

    SQLite supports up to 64-bit signed integers (-2**63 < int < 2**63 - 1)
    """

    sql_type = "INTEGER"


class SqlFloat(SqlType[float, float]):
    """Class to represent Python floats in SQLite table"""

    sql_type = "REAL"


class SqlLargeInt(SqlType[int, str]):
    """Class to represent large integers > 64bit in SQLite table

    Integers are stored as text in the database and converted on read / write.
    """

    sql_type = "TEXT"

    def sql_to_py(self, value: str) -> int:
        return int(value)

    def py_to_sql(self, value: int) -> str:
        return str(value)


class SqlPath(SqlType[str, bytes]):
    """
    Class to represent Python paths in SQLite table

    Paths are stored as bytes in the database to handle characters in the path which
    cannot be decoded in the reported file system encoding. On the Python side, paths
    will contain surrogate escapes in place of such characters.
    """

    sql_type = "BLOB"

    def sql_to_py(self, value: bytes) -> str:
        return os.fsdecode(value)

    def py_to_sql(self, value: str) -> bytes:
        return os.fsencode(value)


class SqlEnum(SqlType[ET, str]):
    """Class to represent Python enums in SQLite table

    Enums are stored as text (attribute name) in the database.
    """

    sql_type = "TEXT"

    def __init__(self, enum: Iterable[ET]) -> None:
        self.enum_type = enum

    def sql_to_py(self, value: str) -> ET:
        res = getattr(self.enum_type, value)
        return cast(ET, res)

    def py_to_sql(self, value: ET) -> str:
        return value.name
