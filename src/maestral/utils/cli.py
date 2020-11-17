# -*- coding: utf-8 -*-

import enum
import textwrap
from datetime import datetime
from typing import Optional, List, Union, TypeVar, Iterator, Sequence, Any

import click


_T = TypeVar("_T")


def _transpose(ll: List[List[_T]]) -> List[List[_T]]:
    return list(map(list, zip(*ll)))


class Align(enum.Enum):
    Left = 0
    Right = 1


class Elide(enum.Enum):
    Leading = 0
    Center = 1
    Trailing = 2


def elide(
    text: str, width: int, placeholder: str = "...", elide: Elide = Elide.Trailing
) -> str:

    if len(text) <= width:
        return text

    available = width - len(placeholder)

    if elide is Elide.Trailing:
        return text[:available] + placeholder
    elif elide is Elide.Leading:
        return placeholder + text[-available:]
    else:
        half_available = available // 2
        return text[:half_available] + placeholder + text[-half_available:]


def adjust(text: str, width: int, align: Align = Align.Left) -> str:

    needed = width - len(click.unstyle(text))

    if needed > 0:
        if align == Align.Left:
            return text + " " * needed
        else:
            return " " * needed + text
    else:
        return text


class Field:
    @property
    def display_width(self) -> int:
        raise NotImplementedError()

    def format(self, width: int) -> List[str]:
        raise NotImplementedError()


class TextField(Field):
    def __init__(
        self,
        text: str,
        align: Align = Align.Left,
        wraps: bool = False,
        elide: Elide = Elide.Trailing,
        **style,
    ) -> None:
        self.text = text
        self.align = align
        self.wraps = wraps
        self.elide = elide
        self.style = style

    @property
    def display_width(self) -> int:
        return len(self.text)

    def format(self, width: int) -> List[str]:

        if self.wraps:
            lines = textwrap.wrap(self.text, width=width)
        else:
            lines = [elide(self.text, width, elide=self.elide)]

        # apply style first, adjust later

        if self.style:
            lines = [click.style(line, **self.style) for line in lines]

        return [adjust(line, width, self.align) for line in lines]

    def __repr__(self):
        return f"<{self.__class__.__name__}('{self.text}')>"


class DateField(Field):
    def __init__(self, dt: datetime) -> None:
        self.dt = dt

    @property
    def display_width(self) -> int:
        return 20

    def format(self, width: int) -> List[str]:
        if width >= 20:
            return [self.dt.strftime("%d %b %Y at %H:%M")]
        elif width >= 17:
            return [self.dt.strftime("%x, %H:%M")]
        else:
            return [self.dt.strftime("%x")]

    def __repr__(self):
        return f"<{self.__class__.__name__}('{self.format(17)}')>"


class Column:
    def __init__(
        self,
        title: str,
        fields: Sequence = (),
        align: Align = Align.Left,
        wraps: bool = False,
        elide: Elide = Elide.Trailing,
    ) -> None:
        self.fields = [TextField(title, align=align, bold=True)]
        self.align = align
        self.wraps = wraps
        self.elide = elide

        for field in fields:
            self.fields.append(self._to_field(field))

    @property
    def display_width(self) -> int:
        return max(field.display_width for field in self.fields)

    def append(self, field: Any) -> None:
        self.fields.append(self._to_field(field))

    def insert(self, index: int, field: Any) -> None:
        self.fields.insert(index, self._to_field(field))

    def __getitem__(self, item: int) -> Field:
        return self.fields[item]

    def __setitem__(self, key: int, value: Any) -> None:
        self.fields[key] = self._to_field(value)

    def __iter__(self) -> Iterator[Field]:
        return iter(self.fields)

    def __len__(self) -> int:
        return len(self.fields)

    def _to_field(self, field: Any) -> Field:
        if isinstance(field, Field):
            return field
        elif isinstance(field, datetime):
            return DateField(field)
        else:
            return TextField(
                str(field), align=self.align, wraps=self.wraps, elide=self.elide
            )

    def __repr__(self):
        return f"<{self.__class__.__name__}(title='{self.fields[0].text}')>"


class Table:

    columns: List[Column]

    def __init__(self, columns: List[Union[Column, str]], padding: int = 2) -> None:
        self.columns = []
        self.padding = padding

        for col in columns:
            if isinstance(col, Column):
                self.columns.append(col)
            else:
                self.columns.append(Column(col))

    def append(self, row: Sequence) -> None:
        for i, col in enumerate(self.columns):
            col.append(row[i])

    def insert(self, index: int, row: Sequence) -> None:
        for i, col in enumerate(self.columns):
            col.insert(index, row[i])

    def __getitem__(self, item: int) -> List[Field]:
        return [col[item] for col in self.columns]

    def __setitem__(self, key: int, value: Sequence):
        for i, col in enumerate(self.columns):
            col[key] = value[i]

    def __iter__(self) -> Iterator[List[Field]]:
        return iter([col[i] for col in self.columns] for i in range(len(self)))

    def __len__(self) -> int:
        return max(len(col) for col in self.columns)

    def format_lines(self, width: Optional[int] = None) -> Iterator[str]:

        # get terminal width if no width is given
        if not width:
            width, height = click.get_terminal_size()

        available_width = width - self.padding * len(self.columns)
        raw_col_widths = [col.display_width for col in self.columns]

        # Allocate column width from available width,
        # weighted by the raw width of each column.
        n = 3
        sum_widths = sum(w ** n for w in raw_col_widths)
        subtract = max([sum(raw_col_widths) - available_width, 0])
        allocated_col_widths = tuple(
            round(w - subtract * w ** n / sum_widths) for w in raw_col_widths
        )

        # generate lines
        spacer = " " * self.padding

        for row in self:
            cells = []

            for field, alloc_width in zip(row, allocated_col_widths):
                cells.append(field.format(alloc_width))

            n_lines = max(len(cell) for cell in cells)

            for i in range(n_lines):

                line_parts = []

                for cell_lines, alloc_width in zip(cells, allocated_col_widths):
                    try:
                        line_parts.append(cell_lines[i])
                    except IndexError:
                        line_parts.append(adjust("", width=alloc_width))

                line = spacer.join(line_parts)
                yield line.rstrip()

    def format(self, width: Optional[int] = None) -> str:
        return "\n".join(self.format_lines(width))

    def echo(self):
        for line in self.format_lines():
            click.echo(line)


class Grid:

    fields: List[Field]

    def __init__(
        self, fields: Sequence = (), padding: int = 2, align: Align = Align.Left
    ):

        self.fields = []
        self.padding = padding
        self.align = align

        for field in fields:
            self.fields.append(self._to_field(field))

    def append(self, field) -> None:
        self.fields.append(self._to_field(field))

    def insert(self, index: int, field: Any) -> None:
        self.fields.insert(index, self._to_field(field))

    def __getitem__(self, item) -> Field:
        return self.fields[item]

    def __setitem__(self, key: int, value: Field):
        self.fields[key] = self._to_field(value)

    def __iter__(self) -> Iterator:
        return iter(self.fields)

    def __len__(self) -> int:
        return len(self.fields)

    def _to_field(self, field: Any) -> Field:
        if isinstance(field, Field):
            return field
        elif isinstance(field, datetime):
            return DateField(field)
        else:
            return TextField(str(field), align=self.align)

    def format_lines(self, width: Optional[int] = None) -> Iterator[str]:

        from . import chunks

        # get terminal width if no width is given
        if not width:
            width, height = click.get_terminal_size()

        field_width = max(field.display_width for field in self.fields)
        field_width = min(field_width, width)  # cap at terminal / total width
        field_texts = [field.format(field_width)[0] for field in self.fields]

        n_columns = max(width // (field_width + self.padding), 1)

        rows = chunks(field_texts, n_columns)
        spacer = " " * self.padding

        for row in rows:
            line = spacer.join(row)
            yield line.rstrip()

    def format(self, width: Optional[int] = None) -> str:
        return "\n".join(self.format_lines(width))

    def echo(self):
        for line in self.format_lines():
            click.echo(line)
