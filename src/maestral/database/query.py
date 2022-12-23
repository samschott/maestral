"""
SQL query definitions that facilitate writing object-oriented code to generate SQL
queries.
"""

from __future__ import annotations

import os
from typing import Sequence, Iterator, Any, TYPE_CHECKING

from .types import SqlPath

if TYPE_CHECKING:
    from .orm import Column


class Query:
    """Base type for query"""

    def clause(self) -> tuple[str, Sequence[Any]]:
        """
        Generate the corresponding SQL clause.

        :returns: SQL clause and arguments to substitute.
        """
        raise NotImplementedError()

    def order_by(self, expr: str) -> OrderedQuery:
        return OrderedQuery(self, expr)


class OrderedQuery(Query):
    """Ordered query"""

    def __init__(self, query: Query, order_expr: str) -> None:
        self.base_query = query
        self.order_expr = order_expr

    def clause(self) -> tuple[str, Sequence[Any]]:
        query, args = self.base_query.clause()
        return f"{query} ORDER BY {self.order_expr}", args


class PathTreeQuery(Query):
    """
    Query for an entire subtree at the given path.

    :param column: Column to match.
    :param path: Root path for the subtree.
    """

    def __init__(self, column: Column[Any, Any], path: str):
        if not isinstance(column.type, SqlPath):
            raise ValueError("Only accepts columns with type SqlPath")

        self.column = column
        self.file_blob = os.fsencode(path)
        self.dir_blob = os.path.join(self.file_blob, b"")

    def clause(self) -> tuple[str, Sequence[Any]]:
        query_part = f"({self.column.name} = ? OR substr({self.column.name}, 1, ?) = ?)"
        args = (self.file_blob, len(self.dir_blob), self.dir_blob)

        return query_part, args


class MatchQuery(Query):
    """
    Query to match an exact value.

    :param column: Column to match.
    :param value: Value to match.
    """

    def __init__(self, column: Column[Any, Any], value: Any):
        self.column = column
        self.value = value

    def clause(self) -> tuple[str, Sequence[Any]]:
        args = (self.column.py_to_sql(self.value),)
        return f"{self.column.name} = ?", args


class AllQuery(Query):
    """
    Query to match everything.
    """

    def clause(self) -> tuple[str, Sequence[Any]]:
        # Note: Use "1" instead of "TRUE" here for compatibility with SQLite versions
        # pre SQLite 3.23.0 (2018-04-02).
        return "1", ()


class CollectionQuery(Query):
    """An abstract query class that aggregates other queries. Can be
    indexed like a list to access the sub-queries.

    :param subqueries: Subqueries to aggregate.
    """

    def __init__(self, *subqueries: Query):
        self.subqueries = subqueries

    # Act like a sequence.

    def __len__(self) -> int:
        return len(self.subqueries)

    def __getitem__(self, key: int) -> Query:
        return self.subqueries[key]

    def __iter__(self) -> Iterator[Query]:
        return iter(self.subqueries)

    def __contains__(self, item: Query) -> bool:
        return item in self.subqueries

    def clause_with_joiner(self, joiner: str) -> tuple[str, Sequence[Any]]:
        """Return a clause created by joining together the clauses of
        all subqueries with the string joiner (padded by spaces).
        """
        clause_parts = []
        subvals: list[Any] = []

        for subq in self.subqueries:
            subq_clause, subq_subvals = subq.clause()
            clause_parts.append("(" + subq_clause + ")")
            subvals += subq_subvals

        clause = (" " + joiner + " ").join(clause_parts)
        return clause, subvals

    def clause(self) -> tuple[str, Sequence[Any]]:
        raise NotImplementedError()


class AndQuery(CollectionQuery):
    """A conjunction of a list of other queries."""

    def clause(self) -> tuple[str, Sequence[Any]]:
        return self.clause_with_joiner("AND")


class OrQuery(CollectionQuery):
    """A conjunction of a list of other queries."""

    def clause(self) -> tuple[str, Sequence[Any]]:
        return self.clause_with_joiner("OR")


class NotQuery(Query):
    """A query that matches the negation of its `subquery`, as a shortcut for
    performing `not(subquery)` without using regular expressions.

    :param subquery: Query to negate.
    """

    def __init__(self, subquery: Query):
        self.subquery = subquery

    def clause(self) -> tuple[str, Sequence[Any]]:
        clause, subvals = self.subquery.clause()
        if clause:
            return f"not ({clause})", subvals
        else:
            # If there is no clause, there is nothing to negate. All the logic
            # is handled by match() for slow queries.
            return clause, subvals
