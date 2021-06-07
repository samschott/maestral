# -*- coding: utf-8 -*-
"""Module to print neatly formatted tables and grids to the terminal."""

import enum
from typing import (
    Optional,
    List,
    Union,
    Iterator,
    Sequence,
    Any,
    Callable,
    TYPE_CHECKING,
)

import click
import shutil

if TYPE_CHECKING:
    from datetime import datetime


# ==== enums ===========================================================================


class Align(enum.Enum):
    """Text alignment in column"""

    Left = 0
    Right = 1


class Elide(enum.Enum):
    """Elide directives"""

    Leading = 0
    Center = 1
    Trailing = 2


class Prefix(enum.Enum):
    """Prefix for command line output"""

    Info = 0
    Ok = 1
    Warn = 2
    NONE = 3


# ==== text adjustment helpers =========================================================


def elide(
    text: str, width: int, placeholder: str = "...", elide: Elide = Elide.Trailing
) -> str:
    """
    Elides a string to fit into the given width.

    :param text: Text to truncate.
    :param width: Target width.
    :param placeholder: Placeholder string to indicate truncated text.
    :param elide: Which part to truncate.
    :returns: Truncated text.
    """

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
    """
    Pads a string with spaces up the desired width. Preserves ANSI color codes without
    counting them towards the width.

    This function is similar to ``str.ljust`` and ``str.rjust``.

    :param text: Initial string.
    :param width: Target width. If smaller than the given text, nothing is done.
    :param align: Side to align the padded string: to the left or to the right.
    """

    needed = width - len(click.unstyle(text))

    if needed > 0:
        if align == Align.Left:
            return text + " " * needed
        else:
            return " " * needed + text
    else:
        return text


# ==== printing structured data to console =============================================


class Field:
    """Base class to represent a field in a table."""

    @property
    def display_width(self) -> int:
        """
        The requested total width of the content in characters when not wrapped or
        shortened in any way.
        """
        raise NotImplementedError()

    def format(self, width: int) -> List[str]:
        """
        Returns the field content formatted to fit the requested width.

        :param width: Width to fit.
        :returns: Shortened or wrapped string.
        """
        raise NotImplementedError()


class TextField(Field):
    """
    A text field for a table.

    :param text: Text to represent.
    :param align: Text alignment: right or left.
    :param wraps: Whether to wrap the text instead of truncating it to fit into a
        requested width.
    :elide: Truncation strategy: trailing, center or leading.
    :param style: Styling passed on to :meth:`click.style` when styling the text.
    """

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

        import textwrap

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
    """
    A datetime field for a table. The formatting of the datetime will be adjusted
    depending on the available width. Does not currently support localisation.

    :param dt: Datetime to represent.
    :param style: Styling passed on to :meth:`click.style` when styling the text.
    """

    def __init__(self, dt: "datetime", **style) -> None:
        self.dt = dt
        self.style = style

    @property
    def display_width(self) -> int:
        return 20

    def format(self, width: int) -> List[str]:
        if width >= 20:
            string = self.dt.strftime("%d %b %Y at %H:%M")
        elif width >= 17:
            string = self.dt.strftime("%x, %H:%M")
        else:
            string = self.dt.strftime("%x")

        if self.style:
            return [click.style(string, **self.style)]
        else:
            return [string]

    def __repr__(self):
        return f"<{self.__class__.__name__}('{self.format(17)}')>"


class Column:
    """
    A table column.

    :param title: Column title.
    :param fields: Fields in the table. Any sequence of objects can be given and will be
        converted to :class:`Field` instances as appropriate.
    :param align: How to align text inside the column. Will only be used for
        :class:`TextField`s.
    :param wraps: Whether to wrap fields to fit into the column width instead of
        truncating them. Will only be used for :class:`TextField`s.
    :param elide: How to elide text which is too wide for a column. Will only be used
        for :class:`TextField`s.
    """

    fields: List[Field]

    def __init__(
        self,
        title: Optional[str],
        fields: Sequence = (),
        align: Align = Align.Left,
        wraps: bool = False,
        elide: Elide = Elide.Trailing,
    ) -> None:
        self.title = TextField(title, align=align, bold=True) if title else None
        self.align = align
        self.wraps = wraps
        self.elide = elide

        self.fields = []

        for field in fields:
            self.fields.append(self._to_field(field))

    @property
    def display_width(self) -> int:
        if self.title:
            all_fields = self.fields + [self.title]
        else:
            all_fields = self.fields
        return max(field.display_width for field in all_fields)

    @property
    def has_title(self):
        return self.title is not None

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

        from datetime import datetime

        if isinstance(field, Field):
            return field
        elif isinstance(field, datetime):
            return DateField(field)
        else:
            return TextField(
                str(field), align=self.align, wraps=self.wraps, elide=self.elide
            )

    def __repr__(self):
        title = self.title.text if self.title else "untitled"
        return f"<{self.__class__.__name__}(title='{title}')>"


class Table:
    """
    A table which can be printed to stdout.

    :param columns: Table columns. Can be a list of :class:`Column` instances or table
        titles.
    :param padding: Padding between columns.
    """

    columns: List[Column]

    def __init__(self, columns: List[Union[Column, str]], padding: int = 2) -> None:
        self.columns = []
        self.padding = padding

        for col in columns:
            if isinstance(col, Column):
                self.columns.append(col)
            else:
                self.columns.append(Column(col))

    @property
    def ncols(self) -> int:
        """The number of columns"""
        return len(self.columns)

    @property
    def nrows(self) -> int:
        """The number of rows"""
        return max(len(col) for col in self.columns)

    def append(self, row: Sequence) -> None:
        """
        Appends a new row to the table.

        :param row: List of fields to append to each column. Length must match the
            number of columns.
        """

        if len(row) != self.ncols:
            raise ValueError(f"Got {len(row)} fields but have {self.ncols} columns")

        for i, col in enumerate(self.columns):
            col.append(row[i])

    def rows(self) -> List[List[Field]]:
        """
        Returns a list of rows in the table. Each row is a list of fields.
        """
        return [[col[i] for col in self.columns] for i in range(len(self))]

    def __len__(self) -> int:
        return self.nrows

    def format_lines(self, width: Optional[int] = None) -> Iterator[str]:
        """
        Iterator over formatted lines of the table. Fields may span multiple lines if
        they are set to wrap instead of truncate.

        :param width: Width to fit the table.
        :returns: Iterator over lines which can be printed to the terminal.
        """

        # get terminal width if no width is given
        if not width:
            width, height = shutil.get_terminal_size()

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

        spacer = " " * self.padding

        # generate line for titles
        if any(col.has_title for col in self.columns):
            titles: List[str] = []

            for col, width in zip(self.columns, allocated_col_widths):
                if col.title:
                    titles.append(col.title.format(width)[0])
                else:
                    titles.append(adjust("", width))

            line = spacer.join(titles)
            yield line.rstrip()

        # generate lines for rows
        for row in self.rows():
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
        """
        Returns a fully formatted table as a string with linebreaks.

        :param width: Width to fit the table.
        :returns: Formatted table.
        """
        return "\n".join(self.format_lines(width))

    def echo(self):
        """Prints the table to the terminal."""
        for line in self.format_lines():
            click.echo(line)


class Grid:
    """
    A grid of fields which can be printed to stdout.

    :param fields: A sequence of fields (strings, datetimes, any objects with a string
        representation).
    :param padding: Padding between fields.
    :param align: Alignment of strings in the grid.
    """

    fields: List[Field]

    def __init__(
        self, fields: Sequence = (), padding: int = 2, align: Align = Align.Left
    ):

        self.fields = []
        self.padding = padding
        self.align = align

        for field in fields:
            self.fields.append(self._to_field(field))

    def append(self, field: Any) -> None:
        """Appends a field to the grid."""
        self.fields.append(self._to_field(field))

    def __iter__(self) -> Iterator:
        return iter(self.fields)

    def __len__(self) -> int:
        return len(self.fields)

    def _to_field(self, field: Any) -> Field:

        from datetime import datetime

        if isinstance(field, Field):
            return field
        elif isinstance(field, datetime):
            return DateField(field)
        else:
            return TextField(str(field), align=self.align)

    def format_lines(self, width: Optional[int] = None) -> Iterator[str]:
        """
        Iterator over formatted lines of the grid.

        :param width: Width to fit the grid.
        :returns: Iterator over lines which can be printed to the terminal.
        """

        if len(self.fields) > 0:

            from . import chunks

            # get terminal width if no width is given
            if not width:
                width, height = shutil.get_terminal_size()

            field_width = max(field.display_width for field in self.fields)
            field_width = min(field_width, width)  # cap at terminal / total width
            field_texts = [field.format(field_width)[0] for field in self.fields]

            n_columns = max(width // (field_width + self.padding), 1)

            rows = chunks(field_texts, n_columns)
            spacer = " " * self.padding

            for row in rows:
                line = spacer.join(row)
                yield line.rstrip()

        else:
            yield ""

    def format(self, width: Optional[int] = None) -> str:
        """
        Returns a fully formatted grid as a string with linebreaks.

        :param width: Width to fit the table.
        :returns: Formatted grid.
        """
        return "\n".join(self.format_lines(width))

    def echo(self):
        """Prints the grid to the terminal."""
        for line in self.format_lines():
            click.echo(line)


# ==== interactive prompts =============================================================


def echo(message: str, nl: bool = True, prefix: Prefix = Prefix.NONE) -> None:

    if prefix is Prefix.Ok:
        pre = click.style("✓", fg="green") + " "
    elif prefix is Prefix.Warn:
        pre = click.style("!", fg="red") + " "
    elif prefix is Prefix.Info:
        pre = "- "
    else:
        pre = ""

    click.echo(f"{pre}{message}", nl=nl)


def info(message: str, nl: bool = True) -> None:
    echo(message, nl=nl, prefix=Prefix.Info)


def warn(message: str, nl: bool = True) -> None:
    echo(message, nl=nl, prefix=Prefix.Warn)


def ok(message: str, nl: bool = True) -> None:
    echo(message, nl=nl, prefix=Prefix.Ok)


def _style_message(message: str) -> str:
    return f"{message} "


def _syle_hint(hint: str) -> str:
    return f"{hint} " if hint else ""


def prompt(
    message: str, default: Optional[str] = None, validate: Optional[Callable] = None
) -> str:

    import survey

    styled_message = _style_message(message)

    def check(value: str) -> bool:
        if validate is not None:
            return validate(value)
        else:
            return True

    res = survey.input(styled_message, default=default, check=check)

    return res


def confirm(message: str, default: Optional[bool] = True) -> bool:

    import survey

    styled_message = _style_message(message)

    return survey.confirm(styled_message, default=default)


def select(message: str, options: Sequence[str], hint="") -> int:

    import survey

    try:
        styled_hint = _syle_hint(hint)
        styled_message = _style_message(message)

        index = survey.select(options, styled_message, hint=styled_hint)

        return index
    except (KeyboardInterrupt, SystemExit):
        survey.respond()
        raise


def select_multiple(message: str, options: Sequence[str], hint="") -> List[int]:

    import survey

    try:
        styled_hint = _syle_hint(hint)
        styled_message = _style_message(message)

        kwargs = {"hint": styled_hint} if hint else {}

        indices = survey.select(
            options, styled_message, multi=True, pin="[✓] ", unpin="[ ] ", **kwargs
        )

        chosen = [options[index] for index in indices]
        response = ", ".join(chosen)

        if len(indices) == 0 or len(response) > 50:
            response = f"[{len(indices)} chosen]"

        survey.respond(response)

        return indices

    except (KeyboardInterrupt, SystemExit):
        survey.respond()
        raise


def select_path(
    message: str,
    default: Optional[str] = None,
    validate: Callable = lambda x: True,
    exists: bool = False,
    files_allowed: bool = True,
    dirs_allowed: bool = True,
) -> str:

    import os

    import survey
    import wrapio

    track = wrapio.Track()

    styled_message = _style_message(message)

    failed = False

    def check(value: str) -> bool:

        nonlocal failed

        if value == "" and default:
            return True

        full_path = os.path.expanduser(value)
        forbidden_dir = os.path.isdir(full_path) and not dirs_allowed
        forbidden_file = os.path.isfile(full_path) and not files_allowed
        exist_condition = os.path.exists(full_path) or not exists

        if not exist_condition:
            survey.update(click.style("(not found) ", fg="red"))
        elif forbidden_dir:
            survey.update(click.style("(not a file) ", fg="red"))
        elif forbidden_file:
            survey.update(click.style("(not a folder) ", fg="red"))

        failed = (
            not exist_condition
            or forbidden_dir
            or forbidden_file
            or not validate(value)
        )

        return not failed

    res = survey.input(
        styled_message,
        default=default,
        callback=track.invoke,
        check=check,
    )

    return res


class CliException(click.ClickException):
    def show(self, file=None) -> None:
        warn(self.format_message())
