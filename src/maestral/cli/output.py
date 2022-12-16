"""
This module provides classes and methods for beautifully formatted output to stdout.
This includes printing tables and grids, formatting dates and eliding strings.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Iterable, Callable

import click
from rich.console import Console, ConsoleOptions, RenderResult
from rich.measure import Measurement
from rich.text import Text
from rich.style import Style
from rich.table import Table, Column

TABLE_STYLE = dict(padding=(0, 2, 0, 0), box=None)


# ==== printing structured data to console =============================================


def rich_table(*headers: Column | str) -> Table:
    return Table(*headers, padding=(0, 2, 0, 0), box=None, show_header=len(headers) > 0)


class RichDateField:
    """A datetime renderable."""

    def __init__(self, dt: datetime, style: str | Style = "") -> None:
        self.dt = dt.astimezone()
        self.style = style
        self._shortest_string = self.dt.strftime("%x")

    def format(self, max_width: int) -> str:
        if max_width >= 20:
            return self.dt.strftime("%d %b %Y at %H:%M")
        elif max_width >= 17:
            return self.dt.strftime("%x, %H:%M")
        else:
            return self._shortest_string

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        yield Text(self.format(options.max_width), no_wrap=True, style=self.style)

    def __rich_measure__(
        self, console: Console, options: ConsoleOptions
    ) -> Measurement:
        return Measurement(len(self._shortest_string), 20)


# ==== printing messages to console ====================================================


class Prefix(enum.Enum):
    """Prefix for command line output"""

    Info = 0
    Ok = 1
    Warn = 2
    NONE = 3


def echo(message: str, nl: bool = True, prefix: Prefix = Prefix.NONE) -> None:
    """
    Print a message to stdout.

    :param message: The string to output.
    :param nl: Whether to end with a new line.
    :param prefix: Any prefix to output before the message,
    """
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
    """
    Print an info message to stdout. Will be prefixed with a dash.

    :param message: The string to output.
    :param nl: Whether to end with a new line.
    """
    echo(message, nl=nl, prefix=Prefix.Info)


def warn(message: str, nl: bool = True) -> None:
    """
    Print a warning to stdout. Will be prefixed with an exclamation mark.

    :param message: The string to output.
    :param nl: Whether to end with a new line.
    """
    echo(message, nl=nl, prefix=Prefix.Warn)


def ok(message: str, nl: bool = True) -> None:
    """
    Print a confirmation to stdout. Will be prefixed with a checkmark.

    :param message: The string to output.
    :param nl: Whether to end with a new line.
    """
    echo(message, nl=nl, prefix=Prefix.Ok)


def echo_via_pager(
    text_or_generator: Iterable[str] | Callable[[], Iterable[str]] | str,
    color: bool | None = None,
) -> None:
    return click.echo_via_pager(text_or_generator, color)
