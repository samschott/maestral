"""
This module provides custom click command line parameters for Maestral such
:class:`DropboxPath`, :class:`ConfigKey` and :class:`ConfigName`, as well as an ordered
command group class which prints its help output in sections.
"""
from __future__ import annotations

import os
from os import path as osp
from typing import Any

import click
from click.shell_completion import CompletionItem

from .output import warn


# ==== Custom parameter types ==========================================================

# A custom parameter:
# * needs a name
# * needs to pass through None unchanged
# * needs to convert from a string
# * needs to convert its result type through unchanged (eg: needs to be idempotent)
# * needs to be able to deal with param and context being None. This can be the case
#   when the object is used with prompt inputs.


class DropboxPath(click.ParamType):
    """A command line parameter representing a Dropbox path

    This parameter type provides custom shell completion for items inside the local
    Dropbox folder.

    :param file_okay: Controls if a file is a possible value.
    :param dir_okay: Controls if a directory is a possible value.
    """

    name = "Dropbox path"
    envvar_list_splitter = osp.pathsep

    def __init__(self, file_okay: bool = True, dir_okay: bool = True) -> None:
        self.file_okay = file_okay
        self.dir_okay = dir_okay

    def convert(
        self,
        value: str | None,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> str | None:
        if value is None:
            return value
        if not value.startswith("/"):
            value = "/" + value
        return value

    def shell_complete(
        self,
        ctx: click.Context | None,
        param: click.Parameter | None,
        incomplete: str,
    ) -> list[CompletionItem]:
        from click.shell_completion import CompletionItem
        from ..utils import removeprefix
        from ..config import MaestralConfig

        matches: list[str] = []
        completions: list[CompletionItem] = []

        # check if we have been given an absolute path
        absolute = incomplete.startswith("/")
        incomplete = incomplete.lstrip("/")

        # get the Maestral config for which to complete paths
        config_name = ctx.params.get("config_name", "maestral") if ctx else "maestral"

        # get all matching paths in our local Dropbox folder
        # TODO: query from server if not too slow

        config = MaestralConfig(config_name)
        dropbox_dir = config.get("sync", "path")
        local_incomplete = osp.join(dropbox_dir, incomplete)
        local_dirname = osp.dirname(local_incomplete)

        try:
            with os.scandir(local_dirname) as it:
                for entry in it:
                    if entry.path.startswith(local_incomplete):
                        if self.file_okay and entry.is_file():
                            dbx_path = removeprefix(entry.path, dropbox_dir)
                            matches.append(dbx_path)
                        if self.dir_okay and entry.is_dir():
                            dbx_path = removeprefix(entry.path, dropbox_dir)
                            matches.append(dbx_path)
        except OSError:
            pass

        # get all matching excluded items

        for dbx_path in config.get("sync", "excluded_items"):
            if dbx_path.startswith("/" + incomplete):
                matches.append(dbx_path)

        for match in matches:
            if not absolute:
                match = match.lstrip("/")
            completions.append(CompletionItem(match))

        return completions


class ConfigKey(click.ParamType):
    """A command line parameter representing a config key

    This parameter type provides custom shell completion for existing config keys.
    """

    name = "key"

    def shell_complete(
        self,
        ctx: click.Context | None,
        param: click.Parameter | None,
        incomplete: str,
    ) -> list[CompletionItem]:
        from click.shell_completion import CompletionItem
        from ..config.main import KEY_SECTION_MAP as KEYS

        return [CompletionItem(key) for key in KEYS if key.startswith(incomplete)]


class ConfigName(click.ParamType):
    """A command line parameter representing a Dropbox path

    This parameter type provides custom shell completion for existing config names.

    :param existing: If ``True`` require an existing config, otherwise create a new
        config on demand.
    """

    name = "config"

    def __init__(self, existing: bool = True) -> None:
        self.existing = existing

    def convert(
        self,
        value: str | None,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> str | None:
        if value is None:
            return value

        from ..config import validate_config_name, list_configs

        if not self.existing:
            # accept all valid config names
            try:
                return validate_config_name(value)
            except ValueError:
                raise CliException("Configuration name may not contain any whitespace")

        else:
            # accept only existing config names
            if value in list_configs():
                return value
            else:
                raise CliException(
                    f"Configuration '{value}' does not exist. "
                    f"Use 'maestral config-files' to list all configurations."
                )

    def shell_complete(
        self,
        ctx: click.Context | None,
        param: click.Parameter | None,
        incomplete: str,
    ) -> list[CompletionItem]:
        from click.shell_completion import CompletionItem
        from ..config import list_configs

        matches = [conf for conf in list_configs() if conf.startswith(incomplete)]
        return [CompletionItem(m) for m in matches]


# ==== custom command group with ordered output ========================================


class OrderedGroup(click.Group):
    """Click command group with customizable sections of help output."""

    sections: dict[str, list[tuple[str, click.Command]]] = {}

    def add_command(
        self, cmd: click.Command, name: str | None = None, section: str = ""
    ) -> None:
        name = name or cmd.name

        if name is None:
            raise TypeError("Command has no name.")

        self.sections[section] = self.sections.get(section, []) + [(name, cmd)]
        super().add_command(cmd, name)

    def format_commands(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        commands = []

        for name in self.commands:
            cmd = self.get_command(ctx, name)
            # What is this, the tool lied about a command.  Ignore it
            if cmd is None:
                continue
            if cmd.hidden:
                continue

            commands.append((name, cmd))

        # allow for 3 times the default spacing
        if len(commands) > 0:
            max_len = max(len(name) for name, cmd in commands)
            limit = formatter.width - 6 - max_len

            # format sections individually
            for section, cmd_list in self.sections.items():
                rows = []

                for name, cmd in cmd_list:
                    name = name.ljust(max_len)
                    help_str = cmd.get_short_help_str(limit)
                    rows.append((name, help_str))

                if rows:
                    with formatter.section(section):
                        formatter.write_dl(rows)


# ==== custom exceptions ===============================================================


class CliException(click.ClickException):
    """
    Subclass of :class:`click.CliException` exception with a nicely formatted error
    message.
    """

    def show(self, file: Any = None) -> None:
        warn(self.format_message())
