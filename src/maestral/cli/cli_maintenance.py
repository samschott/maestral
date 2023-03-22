from __future__ import annotations

import os
import io
import ast
import logging
import textwrap
from os import path as osp
from typing import cast, TYPE_CHECKING

import click
from click.shell_completion import get_completion_class
from rich.console import Console
from rich.text import Text

from .cli_core import select_dbx_path_dialog
from .dialogs import confirm, select
from .output import ok, warn, echo, echo_via_pager, RichDateField, rich_table
from .utils import get_term_size
from .common import convert_api_errors, existing_config_option, inject_proxy
from .core import DropboxPath, ConfigKey, CliException

if TYPE_CHECKING:
    from ..main import Maestral


@click.command(help="Move the local Dropbox folder.")
@click.argument("new_path", required=False, type=click.Path(writable=True))
@inject_proxy(fallback=True, existing_config=True)
def move_dir(m: Maestral, new_path: str) -> None:
    new_path = new_path or select_dbx_path_dialog(m.config_name)
    new_path = osp.realpath(osp.expanduser(new_path))

    m.move_dropbox_directory(new_path)

    ok(f"Dropbox folder moved to {new_path}.")


@click.command(
    help="""
Rebuild the sync index.

Rebuilding may take several minutes, depending on the size of your Dropbox.
""",
)
@click.option(
    "--yes", "-Y", is_flag=True, default=False, help="Skip confirmation prompt."
)
@inject_proxy(fallback=True, existing_config=True)
@convert_api_errors
def rebuild_index(m: Maestral, yes: bool) -> None:
    size = get_term_size()

    msg = textwrap.fill(
        "Rebuilding the index may take several minutes, depending on the size of "
        "your Dropbox. Any changes to local files will be synced once rebuilding "
        "has completed. If you stop the daemon during the process, rebuilding will "
        "start again on the next launch.\nIf the daemon is not currently running, "
        "a rebuild will be scheduled for the next startup.",
        width=size.columns,
    )

    echo(msg + "\n")

    if yes or confirm("Do you want to continue?", default=False):
        m.rebuild_index()

        if m.running:
            ok("Rebuilding now. Run 'maestral status' to view progress.")
        else:
            ok("Sync is not running. Rebuilding scheduled for next startup.")


@click.command(help="List old file revisions.")
@click.argument("dropbox_path", type=DropboxPath())
@click.option(
    "-l",
    "--limit",
    help="Maximum number of revs to list.",
    show_default=True,
    type=click.IntRange(min=1, max=100),
    default=10,
)
@inject_proxy(fallback=True, existing_config=True)
@convert_api_errors
def revs(m: Maestral, dropbox_path: str, limit: int) -> None:
    table = rich_table("Revision", "Modified Time")

    for entry in m.list_revisions(dropbox_path, limit=limit):
        table.add_row(Text(entry.rev), RichDateField(entry.client_modified))

    console = Console()
    console.print(table)


@click.command(
    help="""
Compare two revisions of a file.

If no revs are passed to the command, you can select the revisions interactively. If
only one rev is passed, it is compared to the local version of the file. The diff is
shown via a pager if longer 30 lines.

Warning: The specified revisions will be downloaded to temp files and loaded into memory
to generate the diff. Depending on the file size, this may use significant disk space
and memory.
""",
)
@click.argument("dropbox_path", type=DropboxPath())
@click.option(
    "-v",
    "--rev",
    help="Revisions to compare (multiple allowed).",
    multiple=True,
    default=[],
)
@click.option("--no-color", help="Don't use colors for the diff.", is_flag=True)
@click.option("--no-pager", help="Don't use a pager for output.", is_flag=True)
@click.option(
    "-l",
    "--limit",
    help="Maximum number of revs to list.",
    show_default=True,
    type=click.IntRange(min=1, max=100),
    default=10,
)
@inject_proxy(fallback=True, existing_config=True)
@convert_api_errors
def diff(
    m: Maestral,
    dropbox_path: str,
    rev: list[str],
    no_color: bool,
    no_pager: bool,
    limit: int,
) -> None:
    class LocalDummyFile:
        rev = None

    # Ask for user input if revs are not provided as CLI arguments.
    if len(rev) == 0:
        entries = m.list_revisions(dropbox_path, limit=limit)
        modified_dates: list[str] = []

        for entry in entries:
            field = RichDateField(entry.client_modified)
            modified_dates.append(field.format(40))

        dbx_path = entries[0].path_display
        local_path = m.to_local_path(dbx_path)

        if osp.isfile(local_path):
            # prepend local version as an option
            modified_dates.insert(0, "local version")
            entries.insert(0, LocalDummyFile())  # type: ignore

        index_base = select(
            message="New revision:",
            options=modified_dates,
            hint="(↓ to see more)" if len(entries) > 6 else "",
        )

        if index_base == len(entries) - 1:
            warn("Oldest revision selected, unable to find anything to compare.")
            return

        comparable_dates = modified_dates[index_base + 1 :]
        index_new = select(
            message="Old revision:",
            options=comparable_dates,
            hint="(↓ to see more)" if len(comparable_dates) > 6 else "",
        )

        old_rev = entries[index_new + index_base + 1].rev
        new_rev = entries[index_base].rev
    elif len(rev) == 1:
        old_rev = rev[0]
        new_rev = None
    elif len(rev) == 2:
        old_rev = rev[0]
        new_rev = rev[1]
    else:
        warn("You can only compare two revisions at a time.")
        return

    # Download up to two revisions to a local temporary folder
    # and compare them with a 'diff'. Only text files are supported.
    # If an unknown file type was found, everything that doesn't match
    # 'text/*', an error message gets printed.

    echo("Loading ...\r", nl=False)

    diff_output = m.get_file_diff(old_rev, new_rev)

    if len(diff_output) == 0:
        echo("There are no changes between the two revisions.")
        return

    def color(ind: int, line: str) -> str:
        """
        Color diff lines.
        Inspiration for colors was taken from the
        well known command 'git diff'.
        """

        if ind < 2:
            line = click.style(line, bold=True)
        elif line.startswith("+"):
            line = click.style(line, fg="green")
        elif line.startswith("-"):
            line = click.style(line, fg="red")
        # Don't highlight these in the intro.
        elif line.startswith("@@ "):
            line = click.style(line, fg="cyan")
        return line

    # Color the lines.
    if not no_color:
        diff_output = [color(i, l) for i, l in enumerate(diff_output)]

    # Enter pager if diff is too long
    if len(diff_output) > 30 and not no_pager:
        echo_via_pager("".join(diff_output))
    else:
        echo("".join(diff_output))


@click.command(
    help="""
Restore a previous version of a file.

If no revision number is given, old revisions will be listed.
""",
)
@click.argument("dropbox_path", type=DropboxPath())
@click.option("-v", "--rev", help="Revision to restore.", default="")
@click.option(
    "-l",
    "--limit",
    help="Maximum number of revs to list.",
    show_default=True,
    type=click.IntRange(min=1, max=100),
    default=10,
)
@inject_proxy(fallback=True, existing_config=True)
@convert_api_errors
def restore(m: Maestral, dropbox_path: str, rev: str, limit: int) -> None:
    if not rev:
        echo("Loading...\r", nl=False)
        entries = m.list_revisions(dropbox_path, limit=limit)
        dates = []
        for entry in entries:
            field = RichDateField(entry.client_modified)
            dates.append(field.format(40))

        index = select(
            message="Select a version to restore:",
            options=dates,
            hint="(↓ to see more)" if len(entries) > 6 else "",
        )

        rev = entries[index].rev

    m.restore(dropbox_path, rev)

    ok(f'Restored {rev} to "{dropbox_path}"')


@click.group(help="View and manage the log.")
def log() -> None:
    pass


@log.command(name="show", help="View logs with a pager in the console.")
@click.option(
    "--external", "-e", is_flag=True, default=False, help="Open logs in a GUI."
)
@existing_config_option
def log_show(external: bool, config_name: str) -> None:
    from ..utils.appdirs import get_log_path

    log_file = get_log_path("maestral", config_name + ".log")

    if external:
        res = click.launch(log_file)
    else:
        try:
            with open(log_file) as f:
                text = f.read()
            echo_via_pager(text)
        except OSError:
            res = 1
        else:
            res = 0

    if res > 0:
        raise CliException(f"Could not open log file at '{log_file}'")


@log.command(name="clear", help="Clear the log files.")
@existing_config_option
def log_clear(config_name: str) -> None:
    from ..utils.appdirs import get_log_path

    log_dir = get_log_path("maestral")
    log_name = config_name + ".log"

    log_files = []

    for file_name in os.listdir(log_dir):
        if file_name.startswith(log_name):
            log_files.append(os.path.join(log_dir, file_name))

    try:
        for file in log_files:
            open(file, "w").close()
        ok("Cleared log files.")
    except FileNotFoundError:
        ok("Cleared log files.")
    except OSError:
        raise CliException(
            f"Could not clear log at '{log_dir}'. " f"Please try to delete it manually"
        )


@log.command(name="level", help="Get or set the log level.")
@click.argument(
    "level_name",
    required=False,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
)
@inject_proxy(fallback=True, existing_config=True)
def log_level(m: Maestral, level_name: str) -> None:
    if level_name:
        m.log_level = cast(int, getattr(logging, level_name))
        ok(f"Log level set to {level_name}.")
    else:
        level_name = logging.getLevelName(m.log_level)
        echo(f"Log level: {level_name}")


@click.group(
    help="""
Direct access to config values.

Warning: Changing some config values must be accompanied by maintenance tasks. For
example, changing the config value for the Dropbox location needs to be accompanied by
actually moving the folder. This command only gets / sets the value in the config file.
Most changes will also require a restart of the daemon to become effective.

Use the commands from the Settings section instead wherever possible. They will take
effect immediately, perform accompanying tasks for you, and never leave the daemon in an
inconsistent state.

Currently available config keys are:

\b
- path: the location of the local Dropbox folder
- excluded_items: list of files or folders excluded by selective sync
- account_id: the ID of the linked Dropbox account
- notification_level: the level for desktop notifications
- log_level: the log level.
- update_notification_interval: interval in secs to check for updates
- keyring: the keyring backend to use (full path of the class)
- reindex_interval: the interval in seconds for full reindexing
- max_cpu_percent: maximum CPU usage target per core
- keep_history: the sync history to keep in seconds
- upload: if upload sync is enabled
- download: if download sync is enabled
""",
)
def config() -> None:
    pass


@config.command(name="get", help="Print the value of a given configuration key.")
@click.argument("key", type=ConfigKey())
@inject_proxy(fallback=True, existing_config=True)
def config_get(m: Maestral, key: str) -> None:
    from ..config.main import KEY_SECTION_MAP

    # Check if the config key exists in any section.
    section = KEY_SECTION_MAP.get(key, "")

    if not section:
        raise CliException(f"'{key}' is not a valid configuration key.")

    value = m.get_conf(section, key)

    echo(value)


@config.command(
    name="set",
    help="""
Update configuration with a value for the given key.

Values will be cast to the proper type, raising an error where this is not possibly. For
instance, setting a boolean config value to 1 will actually set it to True.
""",
)
@click.argument("key", type=ConfigKey())
@click.argument("value")
@inject_proxy(fallback=True, existing_config=True)
@convert_api_errors
def config_set(m: Maestral, key: str, value: str) -> None:
    from ..config.main import KEY_SECTION_MAP, DEFAULTS_CONFIG

    section = KEY_SECTION_MAP.get(key, "")

    if not section:
        raise CliException(f"'{key}' is not a valid configuration key.")

    default_value = DEFAULTS_CONFIG[section][key]

    if isinstance(default_value, str):
        py_value = value
    else:
        try:
            py_value = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            py_value = value

    try:
        m.set_conf(section, key, py_value)
    except ValueError as e:
        warn(e.args[0])


@config.command(name="show", help="Show all config keys and values")
@click.option("--no-pager", help="Don't use a pager for output.", is_flag=True)
@existing_config_option
def config_show(no_pager: bool, config_name: str) -> None:
    from ..config import MaestralConfig

    conf = MaestralConfig(config_name)

    with io.StringIO() as fp:
        conf.write(fp)
        if no_pager:
            echo(fp.getvalue())
        else:
            echo_via_pager(fp.getvalue())


@click.command(
    help="""
Generate completion script for your shell.

This command can generate shell completion scripts for bash, zsh or fish. Follow the
instructions below for your shell to load the resulting script. The exact config file
locations might vary based on your system. Make sure to restart your
shell before testing whether completions are working.

### bash

You can enable shell completion for all users by generating and saving the script as
follows:

\b
    maestral completion bash > /usr/share/bash-completion/completions/maestral

To enable shell completion for the current user only, save the script in a location of
your choice, for example `~/.local/completions/maestral`, and source it in `~/.bashrc`
by adding the line:

\b
    . ~/.local/completions/maestral

### zsh

Generate a `_maestral` completion script and put it somewhere in your `$fpath`. For
example:

\b
    maestral completion zsh > /usr/local/share/zsh/site-functions/_maestral

You can also save the completion script in a location of your choice and source it
in `~/.zshrc`. Ensure that the following is present in your `~/.zshrc`:

\b
    autoload -Uz compinit && compinit

### fish

Generate and save a `maestral.fish` completion script as follows. For all users:

\b
    maestral completion fish > /usr/share/fish/vendor_completions.d/maestral.fish

For the current user only:

\b
    maestral completion fish > ~/.config/fish/completions/maestral.fish

""",
)
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completion(shell: str) -> None:
    from .cli_main import main

    comp_cls = get_completion_class(shell)

    if comp_cls is None:
        warn(f"{shell} shell is currently not supported")
        return

    comp = comp_cls(main, {}, "maestral", "_MAESTRAL_COMPLETE")

    try:
        echo(comp.source())
    except RuntimeError as exc:
        warn(exc.args[0])
