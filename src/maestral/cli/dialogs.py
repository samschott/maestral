"""
This module provides interactive commandline dialogs which are based on the
:mod:`survey` Python library.
"""
from __future__ import annotations

from typing import Callable, Sequence

import click


def _style_message(message: str) -> str:
    return f"{message} "


def _style_hint(hint: str) -> str:
    return f"{hint} " if hint else ""


def prompt(
    message: str, default: str | None = None, validate: Callable | None = None
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


def confirm(message: str, default: bool | None = True) -> bool:

    import survey

    styled_message = _style_message(message)

    return survey.confirm(styled_message, default=default)


def select(message: str, options: Sequence[str], hint="") -> int:

    import survey

    try:
        styled_hint = _style_hint(hint)
        styled_message = _style_message(message)

        index = survey.select(options, styled_message, hint=styled_hint)

        return index
    except (KeyboardInterrupt, SystemExit):
        survey.respond()
        raise


def select_multiple(message: str, options: Sequence[str], hint="") -> list[int]:

    import survey

    try:
        styled_hint = _style_hint(hint)
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
    default: str | None = None,
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
