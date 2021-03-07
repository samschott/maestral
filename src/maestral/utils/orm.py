# -*- coding: utf-8 -*-
"""
A basic object relational mapper for SQLite.
Contains only functionality needed by Maestral.
"""
import os
import sqlite3
from enum import Enum, EnumMeta
from weakref import WeakValueDictionary
from typing import Union, Type, Any, Dict, Generator, List, Optional, TypeVar


ColumnValueType = Union[str, int, float, Enum, None]
DefaultColumnValueType = Union[ColumnValueType, Type["NoDefault"]]
SQLSafeType = Union[str, int, float, None]
T = TypeVar("T")


class NoDefault:
    pass


class SqlType:
    sql_type = "TEXT"
    py_type: Type[ColumnValueType] = str

    def sql_to_py(self, value):
        raise NotImplementedError()

    def py_to_sql(self, value):
        raise NotImplementedError()


class SqlString(SqlType):
    sql_type = "TEXT"
    py_type = str

    def sql_to_py(self, value: Optional[str]) -> Optional[str]:
        return value

    def py_to_sql(self, value: Optional[str]) -> Optional[str]:
        return value


class SqlInt(SqlType):
    sql_type = "INTEGER"
    py_type = int

    def sql_to_py(self, value: Optional[int]) -> Optional[int]:
        return value

    def py_to_sql(self, value: Optional[int]) -> Optional[int]:
        return value


class SqlFloat(SqlType):
    sql_type = "REAL"
    py_type = float

    def sql_to_py(self, value: Optional[float]) -> Optional[float]:
        return value

    def py_to_sql(self, value: Optional[float]) -> Optional[float]:
        return value


class SqlPath(SqlType):
    sql_type = "TEXT"
    py_type = str

    def sql_to_py(self, value: Optional[str]) -> Optional[str]:
        return value

    def py_to_sql(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        else:
            return os.fsencode(value).decode(errors="replace")


class SqlEnum(SqlType):
    sql_type = "TEXT"
    py_type = Enum

    def __init__(self, enum: EnumMeta) -> None:
        self.enum_type = enum

    def sql_to_py(self, value: Optional[str]) -> Optional[Enum]:
        if value is None:
            return None
        else:
            return getattr(self.enum_type, value)

    def py_to_sql(self, value: Optional[Enum]) -> Optional[str]:
        if value is None:
            return None
        else:
            return value.name


class Column(property):
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

        constraints = []

        if isinstance(self.type, SqlEnum):
            values = ", ".join(  # type: ignore
                repr(member.name) for member in self.type.enum_type
            )
            constraints.append(f"CHECK( {self.name} IN ({values}) )")

        if not self.nullable:
            constraints.append("NOT NULL")

        if self.unique:
            constraints.append("UNIQUE")

        return " ".join(constraints)

    def render_properties(self) -> str:

        properties = []

        if self.primary_key:
            properties.append("PRIMARY KEY")

        if self.default in (None, NoDefault):
            properties.append("DEFAULT NULL")
        else:
            properties.append(f"DEFAULT {repr(self.default)}")

        return " ".join(properties)

    def render_column(self) -> str:
        return " ".join(
            [
                self.name,
                self.type.sql_type,
                self.render_constraints(),
                self.render_properties(),
            ]
        )

    def py_to_sql(self, value: ColumnValueType) -> SQLSafeType:
        return self.type.py_to_sql(value)

    def sql_to_py(self, value: SQLSafeType) -> ColumnValueType:
        return self.type.sql_to_py(value)


def columns(klass: Type["Model"]) -> List[Column]:
    """ Return column values dictionary for an object """
    return [attr for attr in vars(klass).values() if isinstance(attr, Column)]


def column_value_dict(obj: "Model") -> Dict[str, Any]:
    """ Return column values dictionary for an object """
    cols = columns(type(obj))

    return dict((col.name, getattr(obj, col.name)) for col in cols)


class Database:
    """ Proxy class to access sqlite3.connect method """

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self._connection: Optional[sqlite3.Connection] = None
        self.Model = type(f"Model{self}", (Model,), {"_db": self})

    @property
    def connection(self) -> sqlite3.Connection:
        """ Create SQL connection """

        if self._connection:
            return self._connection
        else:
            connection = sqlite3.connect(*self.args, **self.kwargs)
            connection.row_factory = sqlite3.Row
            self._connection = connection
            return connection

    def close(self) -> None:
        """ Close SQL connection """
        if self._connection:
            self._connection.close()
        self._connection = None

    def commit(self) -> None:
        """ Commit SQL changes """
        self.connection.commit()

    def execute(self, sql: str, *args) -> sqlite3.Cursor:
        """ Execute SQL """
        return self.connection.execute(sql, args)

    def executescript(self, script: str) -> None:
        """ Execute SQL script """
        self.connection.cursor().executescript(script)
        self.commit()


class Manager:
    """ Data mapper interface (generic repository) for models """

    def __init__(self, db: Database, model: Type["Model"]) -> None:
        self.db = db
        self.model = model
        self.table_name = model.__tablename__
        self.primary_key_name = self._find_primary_key(model)

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
            self.primary_key_name,
        )

        # Create table if required.
        if not self._has_table():
            self.create_table()

    def create_table(self) -> None:

        column_defs = [col.render_column() for col in columns(self.model)]
        column_defs_str = ", ".join(column_defs)
        sql = f"CREATE TABLE {self.model.__tablename__} ({column_defs_str});"

        self.db.executescript(sql)

    def clear_cache(self) -> None:
        self._cache.clear()

    def _find_primary_key(self, model: Type["Model"]) -> str:
        for col in columns(model):
            if col.primary_key:
                return col.name

        raise ValueError("Model has no primary key")

    def get_primary_key(self, obj: "Model") -> SQLSafeType:
        return getattr(obj, self.primary_key_name)

    def all(self) -> List["Model"]:
        """ Get all model objects from database """
        result = self.db.execute(f"SELECT * FROM {self.table_name}")
        return [self.create(**row) for row in result.fetchall()]

    def iter_all(self, size: int = 1000) -> Generator[List["Model"], Any, None]:
        result = self.db.execute(f"SELECT * FROM {self.table_name}")
        rows = result.fetchmany(size)

        while len(rows) > 0:
            yield [self.create(**row) for row in rows]
            rows = result.fetchmany(size)

    def create(self, **kwargs) -> "Model":
        """ Create a model object from SQL column values"""

        # Convert any types as appropriate.
        for key, value in kwargs.items():
            col = getattr(self.model, key)
            kwargs[key] = col.sql_to_py(value)

        obj = self.model(**kwargs)

        pk = self.get_primary_key(obj)
        self._cache[pk] = obj

        return obj

    def delete(self, obj: "Model") -> None:
        """ Delete a model object from database """
        pk = self.get_primary_key(obj)
        sql = f"DELETE from {self.table_name} WHERE {self.primary_key_name} = ?"
        self.db.execute(sql, self.get_primary_key(obj))

        try:
            del self._cache[pk]
        except KeyError:
            pass

    def get(self, primary_key) -> Optional["Model"]:
        """ Get a model object from database by its primary key """
        sql = f"SELECT * FROM {self.table_name} WHERE {self.primary_key_name} = ?"
        result = self.db.execute(sql, primary_key)
        row = result.fetchone()

        if not row:
            return None

        return self.create(**row)

    def has(self, primary_key) -> bool:
        """ Check if a model object exists in database by its id """
        sql = f"SELECT {self.primary_key_name} FROM {self.table_name} WHERE {self.primary_key_name} = ?"
        result = self.db.execute(sql, primary_key)
        return True if result.fetchone() else False

    def save(self, obj: "Model") -> "Model":
        """ Save a model object """
        primary_key = self.get_primary_key(obj)

        if self.has(primary_key):
            msg = f"Object with primary key {primary_key} is already registered"
            raise ValueError(msg)

        py_values = column_value_dict(obj).values()
        sql_values = (col.py_to_sql(val) for col, val in zip(self._columns, py_values))

        self.db.execute(self._sql_insert_template, *sql_values)

        if primary_key is None:
            # Round trip to fetch created primary key.
            res = self.db.execute("SELECT last_insert_rowid()").fetchone()
            setattr(obj, self.primary_key_name, res["last_insert_rowid()"])

        self._cache[primary_key] = obj

        return obj

    def update(self, obj: "Model") -> None:
        """ Update a model object """
        py_values = column_value_dict(obj).values()
        sql_values = (col.py_to_sql(val) for col, val in zip(self._columns, py_values))
        pk = self.get_primary_key(obj)
        self.db.execute(self._sql_update_template, *(list(sql_values) + [pk]))

    def query_to_objects(self, q: str, *args) -> List["Model"]:
        result = self.db.execute(q, *args)
        return [self.create(**row) for row in result.fetchall()]

    def count(self) -> int:
        res = self.db.execute(f"SELECT COUNT(*) FROM {self.table_name};")
        counts = res.fetchone()
        return counts[0]

    def _has_table(self) -> bool:
        """ Check if entity model already has a database table """
        sql = "SELECT name len FROM sqlite_master WHERE type = 'table' AND name = ?"
        result = self.db.execute(sql, self.table_name.strip("'\""))
        return True if result.fetchall() else False


class Model:
    """ Abstract entity model with an active record interface """

    __tablename__ = ""
    _manager = None

    def __init__(self, **kwargs) -> None:
        """Allows initialization from kwargs.

        Sets attributes on the constructed instance using the names and values in
        ``kwargs``.
        """

        cls_ = type(self)

        for col in columns(cls_):

            if isinstance(col, Column):
                if col.default is NoDefault:
                    try:
                        setattr(self, col.name, kwargs[col.name])
                    except KeyError:
                        raise TypeError(f"Column value for '{col.name}' must be given")
                else:
                    setattr(self, col.name, kwargs.get(col.name, col.default))

    def __repr__(self) -> str:
        attributes = ", ".join(f"{k}={v}" for k, v in column_value_dict(self).items())
        return f"<{type(self).__name__}({attributes})>"
