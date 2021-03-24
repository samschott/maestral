# -*- coding: utf-8 -*-
"""
A basic object relational mapper for SQLite.

This is a very simple ORM implementation which contains only functionality needed by
Maestral. Many operations will still require explicit SQL statements. This module is no
alternative to fully featured ORMs such as sqlalchemy but may be useful when system
memory is constrained.
"""
import sqlite3
from enum import Enum
from weakref import WeakValueDictionary
from typing import Union, Type, Any, Dict, Generator, List, Optional, TypeVar, Iterable


ColumnValueType = Union[str, int, float, Enum, None]
DefaultColumnValueType = Union[ColumnValueType, Type["NoDefault"]]
SQLSafeType = Union[str, int, float, None]
T = TypeVar("T")


__all__ = [
    "SqlType",
    "SqlString",
    "SqlInt",
    "SqlFloat",
    "SqlPath",
    "SqlEnum",
    "Column",
    "NoDefault",
    "Database",
    "Manager",
    "Model",
    "ColumnValueType",
]


class NoDefault:
    """
    Class to denote the absence of a default value.

    This is distinct from ``None`` which may be a valid default.
    """


class SqlType:
    """Base class to represent Python types in SQLite table"""

    sql_type = "TEXT"
    py_type: Type[ColumnValueType] = str

    @staticmethod
    def sql_to_py(value):
        """Converts the return value from sqlite3 to the target Python type."""
        return value

    @staticmethod
    def py_to_sql(value):
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

    This class contains special handling for strings with surrogate escape characters
    which can appear in badly encoded file names.
    """

    sql_type = "TEXT"
    py_type = str


class SqlEnum(SqlType):
    """Class to represent Python enums in SQLite table"""

    sql_type = "TEXT"
    py_type = Enum

    def __init__(self, enum: Iterable[Enum]) -> None:
        self.enum_type = enum

    def sql_to_py(self, value: Optional[str]) -> Optional[Enum]:  # type: ignore
        if value is None:
            return None
        else:
            return getattr(self.enum_type, value)

    @staticmethod
    def py_to_sql(value: Optional[Enum]) -> Optional[str]:
        if value is None:
            return None
        else:
            return value.name


class Column(property):
    """
    Represents a column in a database table.

    :param type: Column type in database table. Python types which don't have SQLite
        equivalents, such as :class:`enum.Enum`, will be converted appropriately.
    :param nullable: When set to ``False``, will cause the “NOT NULL” phrase to be added
        when generating the column.
    :param unique: If ``True``, sets a unique constraint on the column.
    :param primary_key: If ``True``, marks this column as a primary key column.
        Currently, only a single primary key column is supported.
    :param default: Default value for the column. Set to :class:`NoDefault` if no
        default value should be used. Note than None / NULL is a valid default for an
        SQLite column.
    """

    def __init__(
        self,
        type: SqlType,
        nullable: bool = True,
        unique: bool = False,
        primary_key: bool = False,
        default: DefaultColumnValueType = None,
    ):
        super().__init__(fget=self._fget, fset=self._fset)

        self.type = type
        self.nullable = nullable
        self.unique = unique
        self.primary_key = primary_key

        self.default: DefaultColumnValueType

        if not nullable and default is None:
            self.default = NoDefault
        else:
            self.default = default

    def __set_name__(self, owner: Any, name: str):
        self.name = name
        self.private_name = "_" + name

    def _fget(self, obj: Any) -> Any:
        if self.default is NoDefault:
            return getattr(obj, self.private_name)
        else:
            return getattr(obj, self.private_name, self.default)

    def _fset(self, obj: Any, value: Any) -> None:
        setattr(obj, self.private_name, value)

    def render_constraints(self) -> str:
        """Returns a string with constraints for the SQLite column definition."""

        constraints = []

        if isinstance(self.type, SqlEnum):
            values = ", ".join(repr(member.name) for member in self.type.enum_type)
            constraints.append(f"CHECK( {self.name} IN ({values}) )")

        if not self.nullable:
            constraints.append("NOT NULL")

        if self.unique:
            constraints.append("UNIQUE")

        return " ".join(constraints)

    def render_properties(self) -> str:
        """Returns a string with properties for the SQLite column definition."""

        properties = []

        if self.primary_key:
            properties.append("PRIMARY KEY")

        if self.default in (None, NoDefault):
            properties.append("DEFAULT NULL")
        else:
            properties.append(f"DEFAULT {repr(self.default)}")

        return " ".join(properties)

    def render_column(self) -> str:
        """Returns a string with the full SQLite column definition."""
        return " ".join(
            [
                self.name,
                self.type.sql_type,
                self.render_constraints(),
                self.render_properties(),
            ]
        )

    def py_to_sql(self, value: ColumnValueType) -> SQLSafeType:
        """
        Converts a Python value to a value which can be stored in the database column.

        :param value: Native Python value.
        :returns: Converted Python value to store in database. Will only return str,
            int, float or None.
        """
        return self.type.py_to_sql(value)

    def sql_to_py(self, value: SQLSafeType) -> ColumnValueType:
        """
        Converts a database column value to the original Python type.

        :param value: Value from database column. Only accepts  str, int, float or None.
        :returns: Converted Python value.
        """
        return self.type.sql_to_py(value)


def columns(klass: Type["Model"]) -> List[Column]:
    """Returns all columns of a :class:`Model` which represents a database table."""
    return [attr for attr in vars(klass).values() if isinstance(attr, Column)]


def column_value_dict(obj: "Model") -> Dict[str, Any]:
    """
    Return dictionary with column names and values for a :class:`Model` instance which
    represents a row in the database table.
    """
    cols = columns(type(obj))

    return {col.name: getattr(obj, col.name) for col in cols}


class Database:
    """Proxy class to access sqlite3.connect method."""

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self._connection: Optional[sqlite3.Connection] = None
        self.Model = type(f"Model{self}", (Model,), {"_db": self})

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


class Manager:
    """
    A data mapper interface for a table model.

    Creates the table as defined in the model if it doesn't already exist. Keeps a cache
    of weak references to all retrieved and created rows to speed up queries. The cache
    should be cleared manually changes where made to the table from outside this
    manager.

    :param db: Database to use.
    :param model: Model for database table.
    """

    def __init__(self, db: Database, model: Type["Model"]) -> None:
        self.db = db
        self.model = model
        self.table_name = model.__tablename__
        self.pk_column = self._find_primary_key(model)

        self._cache: "WeakValueDictionary[SQLSafeType, Model]" = WeakValueDictionary()

        # Precompute expensive SQL query strings.
        self._columns = columns(model)

        column_names = [col.name for col in self._columns]
        column_names_str = ", ".join(column_names)
        column_refs = ", ".join(["?"] * len(self._columns))

        self._sql_insert_template = "INSERT INTO {} ({}) VALUES ({})".format(
            self.table_name, column_names_str, column_refs
        )

        where_expressions = [f"{name} = ?" for name in column_names]
        where_expressions_str = ", ".join(where_expressions)
        self._sql_update_template = "UPDATE {} SET {} WHERE {} = ?".format(
            self.table_name,
            where_expressions_str,
            self.pk_column.name,
        )

        # Create table if required.
        if not self._has_table():
            self.create_table()

    @staticmethod
    def _find_primary_key(model: Type["Model"]) -> Column:
        for col in columns(model):
            if col.primary_key:
                return col

        raise ValueError("Model has no primary key")

    def create_table(self) -> None:
        """Creates the table as defined by the model."""

        column_defs = [col.render_column() for col in columns(self.model)]
        column_defs_str = ", ".join(column_defs)
        sql = f"CREATE TABLE {self.model.__tablename__} ({column_defs_str});"

        self.db.executescript(sql)

    def clear_cache(self) -> None:
        """Clears our cache."""
        self._cache.clear()

    def get_primary_key(self, obj: "Model") -> SQLSafeType:
        """
        Returns the primary key for a model object / row in the table.

        :param obj: Model instance which represents the row.
        :returns: Primary key for row.
        """
        pk_py = getattr(obj, self.pk_column.name)
        return self.pk_column.py_to_sql(pk_py)

    def all(self) -> List["Model"]:
        """
        Get all model objects / rows from database in a single query.

        :returns: List of model objects.
        """
        result = self.db.execute(f"SELECT * FROM {self.table_name}")
        return [self.create(**row) for row in result.fetchall()]

    def iter_all(self, size: int = 1000) -> Generator[List["Model"], Any, None]:
        """
        Get all model objects / rows from database in multiple queries.

        :param size: Number of rows to fetch in each query.
        :returns: Iterator over lists of model objects.
        """
        result = self.db.execute(f"SELECT * FROM {self.table_name}")
        rows = result.fetchmany(size)

        while len(rows) > 0:
            yield [self.create(**row) for row in rows]
            rows = result.fetchmany(size)

    def create(self, **kwargs) -> "Model":
        """
        Create a model object from SQL column values

        :param kwargs: Column values.
        :returns: Model object.
        """

        # Convert any types as appropriate.
        for key, value in kwargs.items():
            col = getattr(self.model, key)
            kwargs[key] = col.sql_to_py(value)

        obj = self.model(**kwargs)

        pk_sql = self.get_primary_key(obj)
        self._cache[pk_sql] = obj

        return obj

    def delete(self, obj: "Model") -> None:
        """
        Delete a model object / row from database

        :param obj: Object / row to delete.
        """
        pk_sql = self.get_primary_key(obj)
        sql = f"DELETE from {self.table_name} WHERE {self.pk_column.name} = ?"

        try:
            self.db.execute(sql, pk_sql)
        except UnicodeEncodeError:
            # Item was not in the database in the first place.
            pass

        try:
            del self._cache[pk_sql]
        except KeyError:
            pass

    def get(self, primary_key: ColumnValueType) -> Optional["Model"]:
        """
        Gets a model object from database by its primary key. This will return a cached
        value if available and None if no row with the primary key exists.

        :param primary_key: Primary key for row.
        :returns: Model object representing the row.
        """

        pk_sql = self.pk_column.py_to_sql(primary_key)

        try:
            return self._cache[pk_sql]
        except KeyError:
            pass

        sql = f"SELECT * FROM {self.table_name} WHERE {self.pk_column.name} = ?"

        try:
            result = self.db.execute(sql, pk_sql)
        except UnicodeEncodeError:
            return None

        row = result.fetchone()

        if not row:
            return None

        return self.create(**row)

    def has(self, primary_key: ColumnValueType) -> bool:
        """
        Checks if a model object exists in database by its primary key

        :param primary_key: The primary key.
        :returns: Whether the corresponding row exists in the table.
        """
        sql = f"SELECT {self.pk_column.name} FROM {self.table_name} WHERE {self.pk_column.name} = ?"
        try:
            result = self.db.execute(sql, primary_key)
        except UnicodeEncodeError:
            # Item cannot be in the table.
            return False

        return bool(result.fetchone())

    def save(self, obj: "Model") -> "Model":
        """
        Saves a model object to the database table. If the primary key is None, a new
        primary key will be generated by SQLite on inserting the row. This key will be
        retrieved and stored in the primary key property of the object.

        :param obj: Model object to save.
        :returns: Saved model object.
        """
        pk_sql = self.get_primary_key(obj)

        if self.has(pk_sql):
            msg = f"Object with primary key {pk_sql} is already registered"
            raise ValueError(msg)

        py_values = column_value_dict(obj).values()
        sql_values = (col.py_to_sql(val) for col, val in zip(self._columns, py_values))

        self.db.execute(self._sql_insert_template, *sql_values)

        if pk_sql is None:
            # Round trip to fetch created primary key.
            res = self.db.execute("SELECT last_insert_rowid()").fetchone()
            pk_sql = res["last_insert_rowid()"]
            pk_py = self.pk_column.sql_to_py(pk_sql)
            setattr(obj, self.pk_column.name, pk_py)

        self._cache[pk_sql] = obj

        return obj

    def update(self, obj: "Model") -> None:
        """
        Updates the database table from a model object.

        :param obj: The object to update.
        """
        py_values = column_value_dict(obj).values()
        sql_values = (col.py_to_sql(val) for col, val in zip(self._columns, py_values))
        pk_sql = self.get_primary_key(obj)
        self.db.execute(self._sql_update_template, *(list(sql_values) + [pk_sql]))

    def query_to_objects(self, sql: str, *args) -> List["Model"]:
        """
        Performs the given SQL query and converts any returned rows to model objects.

        :param sql: SQL statement to execute.
        :param args: Parameters to substitute for placeholders in SQL statement.
        :returns: List of model objects from the query.
        """
        result = self.db.execute(sql, *args)
        return [self.create(**row) for row in result.fetchall()]

    def count(self) -> int:
        """Returns the number of rows in the table."""
        res = self.db.execute(f"SELECT COUNT(*) FROM {self.table_name};")
        counts = res.fetchone()
        return counts[0]

    def _has_table(self) -> bool:
        """Checks if entity model already has a database table."""
        sql = "SELECT name len FROM sqlite_master WHERE type = 'table' AND name = ?"
        result = self.db.execute(sql, self.table_name.strip("'\""))
        return bool(result.fetchall())


class Model:
    """
    Abstract object model to represent an SQL table.

    Instances of this class are model objects which correspond to rows in the database
    table.

    To define a table, subclass this Model and define :class:`Column`s as class
    properties. Override the ``__tablename__`` attribute with the actual table name.
    """

    __tablename__ = ""

    def __init__(self, **kwargs) -> None:
        """
        Initialise with keyword arguments corresponding to column names and values.

        :param kwargs: Keyword arguments assigning values to table columns.
        """

        for name, value in kwargs.items():
            setattr(self, name, value)

    def __repr__(self) -> str:
        attributes = ", ".join(f"{k}={v}" for k, v in column_value_dict(self).items())
        return f"<{type(self).__name__}({attributes})>"
