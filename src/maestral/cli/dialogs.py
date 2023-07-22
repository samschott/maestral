"""
This module provides interactive commandline dialogs which are based on the
:mod:`survey` Python library.
"""
from __future__ import annotations

import functools
from typing import Callable, Sequence, TypeVar
from typing_extensions import ParamSpec

import click


P = ParamSpec("P")
T = TypeVar("T")


def _style_message(message: str) -> str:
    return f"{message} "


def _style_hint(hint: str) -> str:
    return f"{hint} " if hint else ""


def _style_error(message: str) -> str:
    return click.style(message, fg="red")


def exit_on_keyboard_interrupt(func: Callable[P, T]) -> Callable[P, T]:
    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        import survey

        try:
            return func(*args, **kwargs)
        except (KeyboardInterrupt, survey.widgets.Escape):
            raise SystemExit("Aborted")

    return wrapper


@exit_on_keyboard_interrupt
def prompt(
    message: str,
    validate: Callable[[str], bool] | None = None,
) -> str:
    import survey

    def check(value: str) -> None:
        if validate is not None and not validate(value):
            raise survey.widgets.Abort(_style_error(f"'{value}' is not allowed"))

    return survey.routines.input(
        _style_message(message), validate=check, escapable=True
    )


@exit_on_keyboard_interrupt
def confirm(message: str, default: bool | None = True) -> bool:
    import survey

    default_to_str = {True: "y", False: "n", None: None}

    return survey.routines.inquire(
        _style_message(message), default=default_to_str[default], escapable=True
    )


@exit_on_keyboard_interrupt
def select(message: str, options: Sequence[str], hint: str | None = "") -> int:
    import survey

    if hint is None:
        kwargs = {}
    else:
        kwargs = {"hint": _style_hint(hint)}

    return survey.routines.select(
        _style_message(message), options=options, escapable=True, **kwargs
    )


@exit_on_keyboard_interrupt
def select_multiple(
    message: str, options: Sequence[str], hint: str | None = None
) -> list[int]:
    import survey

    if hint is None:
        kwargs = {}
    else:
        kwargs = {"hint": _style_hint(hint)}

    def reply(widget: survey.widgets.Widget, indices: set[int]) -> str:
        chosen = [options[index] for index in indices]
        response = ", ".join(chosen)

        if len(indices) == 0 or len(response) > 10:
            response = f"[{len(indices)} chosen]"

        return survey.utils.paint(survey.colors.basic("cyan"), response)

    return survey.routines.basket(
        _style_message(message),
        options=options,
        positive_mark="[✓] ",
        negative_mark="[ ] ",
        reply=reply,
        escapable=True,
        **kwargs,
    )


@exit_on_keyboard_interrupt
def select_path(
    message: str,
    default: str | None = None,
    validate: Callable[[str], bool] = lambda x: True,
    exists: bool = False,
    files_allowed: bool = True,
    dirs_allowed: bool = True,
) -> str:
    import os
    import survey
    import wrapio

    track = wrapio.Track()

    styled_message = _style_message(message)

    def check(value: str) -> None:
        value = value.strip()

        if value == "" and default:
            return

        full_path = os.path.expanduser(value)
        forbidden_dir = os.path.isdir(full_path) and not dirs_allowed
        forbidden_file = os.path.isfile(full_path) and not files_allowed
        exist_condition = os.path.exists(full_path) or not exists

        if not exist_condition:
            raise survey.widgets.Abort(_style_error(f"'{value}' does not exist"))
        elif forbidden_dir:
            raise survey.widgets.Abort(_style_error(f"'{value}' is not a file"))
        elif forbidden_file:
            raise survey.widgets.Abort(_style_error(f"'{value}' is not a folder"))
        elif not validate(value):
            raise survey.widgets.Abort(_style_error(f"'{value}' is not allowed"))

    def reply(widget: survey.widgets.Widget, value: str) -> str:
        return survey.utils.paint(survey.colors.basic("cyan"), value or default)

    kwargs = {"hint": f"[{default}] "} if default else {}

    result = survey.routines.input(
        styled_message,
        reply=reply,
        callback=track.invoke,
        validate=check,
        escapable=True,
        **kwargs,
    )

    result = result.strip()

    if result == "" and default:
        return default
    elif result == "":
        raise RuntimeError("No result and no default")

    return result
