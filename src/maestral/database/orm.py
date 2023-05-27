"""
A basic object relational mapper for SQLite.

This is a very simple ORM implementation which contains only functionality needed by
Maestral. Many operations will still require explicit SQL statements. This module is no
alternative to fully featured ORMs such as sqlalchemy but may be useful when system
memory is constrained.
"""

from __future__ import annotations

from weakref import WeakValueDictionary
from typing import Any, Generator, TypeVar, Generic, Union, Optional, cast, overload

from .core import Database
from .query import Query
from .types import SqlType, SqlEnum

SQLSafeType = Union[str, int, float, None]
T = TypeVar("T")
ST = TypeVar("ST")
M = TypeVar("M", bound="Model")


__all__ = [
    "Column",
    "NonNullColumn",
    "NoDefault",
    "Manager",
    "Model",
]


class NoDefault:
    """
    Class to denote the absence of a default value.

    This is distinct from ``None`` which may be a valid default.
    """


class Column(Generic[T, ST]):
    """
    Represents a column in a database table.

    :param sql_type: Column type in database table. Python types which don't have SQLite
        equivalents, such as :class:`enum.Enum`, will be converted appropriately.
    :param unique: If ``True``, sets a unique constraint on the column.
    :param primary_key: If ``True``, marks this column as a primary key column.
        Currently, only a single primary key column is supported.
    :param index: If ``True``, create an index on this column.
    :param default: Default value for the column. Set to :class:`NoDefault` if no
        default value should be used. Note than None / NULL is a valid default for an
        SQLite column.
    """

    def __init__(
        self,
        sql_type: SqlType[T, ST],
        unique: bool = False,
        primary_key: bool = False,
        index: bool = False,
        default: T | type[NoDefault] | None = None,
    ):
        self.type = sql_type
        self.unique = unique
        self.primary_key = primary_key
        self.index = index
        self.name = ""

        self.default: T | type[NoDefault] | None = default

    def __set_name__(self, owner: Any, name: str) -> None:
        self.name = name
        self.private_name = "_" + name

    @overload
    def __get__(self, obj: None, objtype: type | None = None) -> Column[T, ST]:
        ...

    @overload
    def __get__(self, obj: Any, objtype: type | None = None) -> T | None:
        ...

    def __get__(
        self, obj: Any, objtype: type | None = None
    ) -> Column[T, ST] | T | None:
        if obj is None:
            return self

        if self.default is NoDefault:
            res = getattr(obj, self.private_name)
        else:
            res = getattr(obj, self.private_name, self.default)

        return cast(Optional[T], res)

    def __set__(self, obj: Any, value: T) -> None:
        setattr(obj, self.private_name, value)

    def render_constraints(self) -> str:
        """Returns a string with constraints for the SQLite column definition."""
        constraints = []

        if isinstance(self.type, SqlEnum):
            # Mypy type narrowing does not work well with generics.
            # See https://github.com/python/mypy/issues/12060.
            values = ", ".join(
                repr(member.name) for member in self.type.enum_type  # type:ignore
            )
            constraints.append(f"CHECK( {self.name} IN ({values}) )")

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

    def py_to_sql(self, value: T | None) -> ST | None:
        """
        Converts a Python value to a value which can be stored in the database column.

        :param value: Native Python value.
        :returns: Converted Python value to store in database. Will only return str,
            int, float or None.
        """
        if value is None:
            return value
        return self.type.py_to_sql(value)

    def sql_to_py(self, value: ST | None) -> T | None:
        """
        Converts a database column value to the original Python type.

        :param value: Value from database column. Only accepts  str, int, float or None.
        :returns: Converted Python value.
        """
        if value is None:
            return value
        return self.type.sql_to_py(value)


class NonNullColumn(Column[T, ST]):
    """Subclass of :class:`Column` which is not nullable, i.e., does not accept or
    return None as a value."""

    def __init__(
        self,
        sql_type: SqlType[T, ST],
        unique: bool = False,
        primary_key: bool = False,
        index: bool = False,
        default: T | type[NoDefault] = NoDefault,
    ):
        super().__init__(sql_type, unique, primary_key, index, default)

    def __set__(self, obj: Any, value: T | None) -> None:
        setattr(obj, self.private_name, value)

    @overload
    def __get__(self, obj: None, objtype: type | None = None) -> Column[T, ST]:
        ...

    @overload
    def __get__(self, obj: Any, objtype: type | None = None) -> T:
        ...

    def __get__(self, obj: Any, objtype: type | None = None) -> Column[T, ST] | T:
        res = super().__get__(obj, objtype)
        return cast(T, res)

    def py_to_sql(self, value: T | None) -> ST:
        if value is None:
            raise ValueError("This column does not allow NULL values")
        return self.type.py_to_sql(value)

    def sql_to_py(self, value: ST | None) -> T:
        if value is None:
            raise ValueError("Unexpected value None / NULL")
        return self.type.sql_to_py(value)

    def render_constraints(self) -> str:
        constraints = super().render_constraints()
        return f"{constraints} NOT NULL"


class Manager(Generic[M]):
    """
    A data mapper interface for a table model.

    Creates the table as defined in the model if it doesn't already exist. Keeps a cache
    of weak references to all retrieved and created rows to speed up queries. The cache
    should be cleared manually changes where made to the table from outside this
    manager.

    :param db: Database to use.
    :param model: Model for database table.
    """

    def __init__(self, db: Database, model: type[M]) -> None:
        self.db = db
        self.model = model
        self.table_name = model.__tablename__
        self.pk_column = next(col for col in model.__columns__ if col.primary_key)

        self._cache: WeakValueDictionary[SQLSafeType, M] = WeakValueDictionary()

        # Precompute often-used SQL query strings.
        self._columns = model.__columns__

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

        self.create_table_if_not_exists()

    def create_table_if_not_exists(self) -> None:
        """Creates the table as defined by the model."""
        column_defs = [col.render_column() for col in self.model.__columns__]
        column_defs_str = ", ".join(column_defs)
        sql = f"CREATE TABLE IF NOT EXISTS {self.table_name} ({column_defs_str});"

        self.db.executescript(sql)

        for column in self.model.__columns__:
            if column.index:
                table_name_stripped = self.table_name.strip("'\"")
                idx_name = f"idx_{table_name_stripped}_{column.name}"
                sql = f"CREATE INDEX IF NOT EXISTS {idx_name} ON {self.table_name} ({column.name});"
                self.db.executescript(sql)

        self._did_create_table = True

    def clear_cache(self) -> None:
        """Clears our cache."""
        self._cache.clear()

    def delete(self, query: Query) -> None:
        clause, args = query.clause()
        sql = f"DELETE FROM {self.table_name} WHERE {clause}"
        self.db.execute(sql, *args)
        self.clear_cache()

    def select(self, query: Query) -> list[M]:
        clause, args = query.clause()
        sql = f"SELECT * FROM {self.table_name} WHERE {clause}"
        result = self.db.execute(sql, *args)

        return [self._item_from_kwargs(**row) for row in result.fetchall()]

    def select_iter(
        self, query: Query, size: int = 1000
    ) -> Generator[list[M], Any, None]:
        clause, args = query.clause()
        sql = f"SELECT * FROM {self.table_name} WHERE {clause}"
        result = self.db.execute(sql, *args)
        rows = result.fetchmany(size)

        while len(rows) > 0:
            yield [self._item_from_kwargs(**row) for row in rows]
            rows = result.fetchmany(size)

    def select_sql(self, sql: str, *args: Any) -> list[M]:
        """
        Performs the given SQL query and converts any returned rows to model objects.

        :param sql: SQL statement to execute.
        :param args: Parameters to substitute for placeholders in SQL statement.
        :returns: List of model objects from the query.
        """
        result = self.db.execute(f"SELECT * FROM {self.table_name} {sql}", *args)
        return [self._item_from_kwargs(**row) for row in result.fetchall()]

    def delete_primary_key(self, primary_key: Any) -> None:
        """
        Delete a model object / row from database by primary key.

        :param primary_key: Primary key for row.
        """
        pk_sql = self.pk_column.py_to_sql(primary_key)
        sql = f"DELETE from {self.table_name} WHERE {self.pk_column.name} = ?"
        self.db.execute(sql, pk_sql)

        try:
            del self._cache[pk_sql]
        except KeyError:
            pass

    def get(self, primary_key: Any) -> M | None:
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
        result = self.db.execute(sql, pk_sql)

        row = result.fetchone()

        if not row:
            return None

        return self._item_from_kwargs(**row)

    def has(self, primary_key: Any) -> bool:
        """
        Checks if a model object exists in database by its primary key

        :param primary_key: The primary key.
        :returns: Whether the corresponding row exists in the table.
        """
        pk_sql = self.pk_column.py_to_sql(primary_key)
        sql = f"SELECT {self.pk_column.name} FROM {self.table_name} WHERE {self.pk_column.name} = ?"
        result = self.db.execute(sql, pk_sql)

        return bool(result.fetchone())

    def save(self, obj: M) -> M:
        """
        Saves a model object to the database table. If the primary key is None, a new
        primary key will be generated by SQLite on inserting the row. This key will be
        retrieved and stored in the primary key property of the object.

        :param obj: Model object to save.
        :returns: Saved model object.
        """
        pk_sql = self._get_primary_key(obj)

        if self.has(pk_sql):
            raise ValueError(f"Object with primary key {pk_sql} is already registered")

        sql_values = (col.py_to_sql(getattr(obj, col.name)) for col in self._columns)

        self.db.execute(self._sql_insert_template, *sql_values)

        if pk_sql is None:
            # Round trip to fetch created primary key.
            res = self.db.execute("SELECT last_insert_rowid()").fetchone()
            pk_sql = res["last_insert_rowid()"]
            pk_py = self.pk_column.sql_to_py(pk_sql)
            setattr(obj, self.pk_column.name, pk_py)

        self._cache[pk_sql] = obj

        return obj

    def update(self, obj: M) -> None:
        """
        Updates the database table from a model object.

        :param obj: The object to update.
        """
        pk_sql = self._get_primary_key(obj)

        if pk_sql is None:
            raise ValueError("Primary key is required to update row")

        if self.has(pk_sql):
            sql_vals = (col.py_to_sql(getattr(obj, col.name)) for col in self._columns)
            self.db.execute(self._sql_update_template, *(list(sql_vals) + [pk_sql]))
        else:
            self.save(obj)

    def count(self) -> int:
        """Returns the number of rows in the table."""
        res = self.db.execute(f"SELECT COUNT(*) FROM {self.table_name};")
        counts = res.fetchone()
        return cast(int, counts[0])

    def clear(self) -> None:
        """Delete all rows from table."""
        self.db.execute(f"DROP TABLE {self.table_name}")
        self.clear_cache()
        self.create_table_if_not_exists()

    def _get_primary_key(self, obj: M) -> SQLSafeType:
        """
        Returns the primary key value for a model object / row in the table.

        :param obj: Model instance which represents the row.
        :returns: Primary key for row.
        """
        pk_py = getattr(obj, self.pk_column.name)
        return self.pk_column.py_to_sql(pk_py)

    def _item_from_kwargs(self, **kwargs: Any) -> M:
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

        pk_sql = self._get_primary_key(obj)
        self._cache[pk_sql] = obj

        return obj


class ModelBase(type):
    def __new__(
        mcs, cls_name: str, bases: tuple[type], namespace: dict[str, Any], **kwargs: Any
    ) -> ModelBase:
        columns: list[Column[Any, Any]] = []
        slots: list[str] = []

        # Find all columns in namespace.
        for name, value in namespace.items():
            if isinstance(value, Column):
                columns.append(value)
                slots.append(f"_{name}")

        # Add __columns__ attribute to namespace.
        namespace["__columns__"] = frozenset(columns)

        # Add slots to namespace if we have declared columns. Otherwise, don't set slots
        # because this prevents subclasses from having weakrefs.
        if slots:
            namespace["__slots__"] = slots

        return super().__new__(mcs, cls_name, bases, namespace, **kwargs)


class Model(metaclass=ModelBase):
    """
    Abstract object model to represent an SQL table.

    Instances of this class are model objects which correspond to rows in the database
    table.

    To define a table, subclass :class:`Model` and define class properties as
    :class:`Column`. Override the ``__tablename__`` attribute with the SQLite table name
    to use. The ``__columns__`` attribute will be populated automatically for you.
    """

    __tablename__: str
    """The name of the database table"""

    __columns__: frozenset[Column[Any, Any]]
    """The columns of the database table"""

    def __init__(self, **kwargs: Any) -> None:
        """
        Initialise with keyword arguments corresponding to column names and values.

        :param kwargs: Keyword arguments assigning values to table columns.
        """
        columns_names = {col.name for col in self.__columns__}
        missing_columns = {
            c.name for c in self.__columns__ if isinstance(c, NonNullColumn)
        }

        for name, value in kwargs.items():
            missing_columns.discard(name)

            if name in columns_names:
                setattr(self, name, value)
            else:
                raise TypeError(f"{self.__class__.__name__} has no column '{name}'")

        if len(missing_columns) > 0:
            raise TypeError(f"Column values required for {missing_columns}")

    def __repr__(self) -> str:
        attributes = ", ".join(
            f"{col.name}={getattr(self, col.name)}" for col in self.__columns__
        )
        return f"<{self.__class__.__name__}({attributes})>"
