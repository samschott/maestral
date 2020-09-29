#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

This file defines the functions to configure and interact with Maestral from the command
line. Some imports are deferred to the functions that required them in order to reduce the
startup time of individual CLI commands.

"""

# system imports
import os
import os.path as osp
import functools
import logging
import textwrap
import platform
import time
from typing import Optional, List, Dict, Iterable, Callable, Union, TypeVar, cast

# external imports
import click
import Pyro5.errors  # type: ignore

# local imports
from maestral import __version__
from maestral.daemon import (
    freeze_support,
    start_maestral_daemon,
    start_maestral_daemon_process,
    stop_maestral_daemon_process,
    Start,
    Stop,
    MaestralProxy,
    MaestralProxyType,
    is_running,
)
from maestral.config import MaestralConfig, MaestralState, list_configs
from maestral.utils.housekeeping import remove_configuration, validate_config_name


OK = click.style("[OK]", fg="green")
FAILED = click.style("[FAILED]", fg="red")
KILLED = click.style("[KILLED]", fg="red")

LEFT, CENTER, RIGHT = range(3)

T = TypeVar("T")


def stop_daemon_with_cli_feedback(config_name: str) -> None:
    """Wrapper around :meth:`daemon.stop_maestral_daemon_process`
    with command line feedback."""

    click.echo("Stopping Maestral...", nl=False)
    res = stop_maestral_daemon_process(config_name)
    if res == Stop.Ok:
        click.echo("\rStopping Maestral...        " + OK)
    elif res == Stop.NotRunning:
        click.echo("Maestral daemon is not running.")
    elif res == Stop.Killed:
        click.echo("\rStopping Maestral...        " + KILLED)


def select_dbx_path_dialog(
    config_name: str, default_dir_name: Optional[str] = None, allow_merge: bool = False
) -> str:
    """
    A CLI dialog to ask for a local Dropbox folder location.

    :param config_name: The configuration to use for the default folder name.
    :param default_dir_name: The default directory name. Defaults to
        "Dropbox ({config_name})" if not given.
    :param allow_merge: If ``True``, allows the selection of an existing folder without
        deleting it. Defaults to ``False``.
    :returns: Path given by user.
    """

    from maestral.utils.appdirs import get_home_dir
    from maestral.utils.path import delete

    default_dir_name = default_dir_name or f"Dropbox ({config_name.capitalize()})"
    default = osp.join(get_home_dir(), default_dir_name)

    while True:
        res = click.prompt(
            "Please give Dropbox folder location",
            default=default,
            type=click.Path(writable=True),
        )

        res = res.rstrip(osp.sep)

        dropbox_path = osp.expanduser(res or default)

        if osp.exists(dropbox_path):
            if allow_merge:
                choice = click.prompt(
                    text=(
                        f'Directory "{dropbox_path}" already exists.\nDo you want to '
                        f"replace it or merge its content with your Dropbox?"
                    ),
                    type=click.Choice(["replace", "merge", "cancel"]),
                )
            else:
                replace = click.confirm(
                    text=(
                        f'Directory "{dropbox_path}" already exists. Do you want to '
                        f"replace it? Its content will be lost!"
                    ),
                )
                choice = "replace" if replace else "cancel"

            if choice == "replace":
                err = delete(dropbox_path)
                if err:
                    click.echo(
                        f'Could not write to location "{dropbox_path}". Please '
                        "make sure that you have sufficient permissions."
                    )
                else:
                    return dropbox_path
            elif choice == "merge":
                return dropbox_path

        else:
            return dropbox_path


def link_dialog(m: MaestralProxyType) -> None:
    """
    A CLI dialog for linking a Dropbox account.

    :param m: Maestral or MaestralProxy instance.
    """

    authorize_url = m.get_auth_url()
    click.echo("1. Go to: " + authorize_url)
    click.echo('2. Click "Allow" (you may have to log in first).')
    click.echo("3. Copy the authorization token.")

    res = -1
    while res != 0:
        auth_code = click.prompt("Enter the authorization token here", type=str)
        auth_code = auth_code.strip()
        res = m.link(auth_code)

        if res == 1:
            click.secho("Invalid token. Please try again.", fg="red")
        elif res == 2:
            click.secho("Could not connect to Dropbox. Please try again.", fg="red")


def check_for_updates() -> None:
    """
    Checks if updates are available by reading the cached release number from the
    config file and notifies the user. Prints an update note to the command line.
    """
    from packaging.version import Version

    conf = MaestralConfig("maestral")
    state = MaestralState("maestral")

    interval = conf.get("app", "update_notification_interval")
    last_update_check = state.get("app", "update_notification_last")
    latest_release = state.get("app", "latest_release")

    if interval == 0 or time.time() - last_update_check < interval:
        return

    has_update = Version(__version__) < Version(latest_release)

    if has_update:
        click.echo(
            f"Maestral v{latest_release} has been released, you have v{__version__}. "
            f"Please use your package manager to update."
        )


def check_for_fatal_errors(m: MaestralProxyType) -> bool:
    """
    Checks the given Maestral instance for fatal errors such as revoked Dropbox access,
    deleted Dropbox folder etc. Prints a nice representation to the command line.

    :param m: Maestral or MaestralProxy instance.
    :returns: True in case of fatal errors, False otherwise.
    """

    maestral_err_list = m.fatal_errors

    if len(maestral_err_list) > 0:

        width, height = click.get_terminal_size()

        err = maestral_err_list[0]
        err_title = cast(str, err["title"])
        err_msg = cast(str, err["message"])

        wrapped_msg = textwrap.fill(err_msg, width=width)

        click.echo("")
        click.secho(err_title, fg="red")
        click.secho(wrapped_msg, fg="red")
        click.echo("")

        return True
    else:
        return False


def catch_maestral_errors(func: Callable) -> Callable:
    """
    Decorator that catches all MaestralApiErrors and prints them as a useful message to
    the command line instead of printing the full stacktrace.
    """

    from maestral.errors import MaestralApiError

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except MaestralApiError as exc:
            raise click.ClickException(f"{exc.title}. {exc.message}")
        except ConnectionError:
            raise click.ClickException("Could not connect to Dropbox.")

    return wrapper


def _transpose(ll: List[List[T]]) -> List[List[T]]:
    return list(map(list, zip(*ll)))


def format_table(
    rows: Optional[List[List[str]]] = None,
    columns: Optional[List[List[str]]] = None,
    headers: Optional[List[str]] = None,
    padding: int = 2,
    alignment: Optional[Union[int, List[int]]] = None,
    wrap: bool = True,
):
    """
    Prints given data as a pretty table. Either rows or columns must be given.s

    :param rows: List of strings for table rows.
    :param columns: List of strings for table columns.
    :param headers: List of strings for column titles.
    :param padding: Padding between columns.
    :param alignment: List of alignments for every column. Values can be ``LEFT``,
        ``CENTER``, ``RIGHT``. If not given, defaults to left alignment.
    :param wrap: If ``True``, wrap cell content to fit the terminal width.
    :returns: Formatted multi-line string.
    """

    if rows and columns:
        raise ValueError("Cannot give both rows and columns as input.")

    if not columns:
        if rows:
            columns = list(columns) if columns else _transpose(rows)
        else:
            raise ValueError("Must give either rows or columns as input.")

    if headers:
        for i, col in enumerate(columns):
            col.insert(0, headers[i])

    # return early if all columns are empty (including headers)
    if all(len(col) == 0 for col in columns):
        return ""

    # default to left alignment
    if not alignment:
        alignment = [LEFT] * len(columns)
    elif isinstance(alignment, int):
        alignment = [alignment] * len(columns)
    elif len(alignment) != len(columns):
        raise ValueError("Must give an alignment for every column.")

    # determine column widths from terminal width and padding

    col_widths = tuple(max(len(cell) for cell in col) for col in columns)

    if wrap:

        terminal_width, terminal_height = click.get_terminal_size()
        available_width = terminal_width - padding * len(columns)

        n = 3
        sum_widths = sum(w ** n for w in col_widths)
        subtract = max([sum(col_widths) - available_width, 0])
        col_widths = tuple(
            round(w - subtract * w ** n / sum_widths) for w in col_widths
        )

    # wrap strings to fit column

    wrapped_columns: List[List[List[str]]] = []

    for column, width in zip(columns, col_widths):
        wrapped_columns.append([textwrap.wrap(cell, width=width) for cell in column])

    wrapped_rows = _transpose(wrapped_columns)

    # generate lines by filling columns

    lines = []

    for row in wrapped_rows:
        n_lines = max(len(cell) for cell in row)
        for cell in row:
            cell += [""] * (n_lines - len(cell))

        for i in range(n_lines):
            line = ""
            for cell, width, align in zip(row, col_widths, alignment):

                if align == LEFT:
                    line += cell[i].ljust(width)
                elif align == RIGHT:
                    line += cell[i].rjust(width)
                elif align == CENTER:
                    line += cell[i].center(width)
                else:
                    raise ValueError(
                        "Alignment must be LEFT = 0, CENTER = 1, RIGHT = 2"
                    )

                line += " " * padding

            lines.append(line)

    return "\n".join(lines)


# ========================================================================================
# Command groups
# ========================================================================================


class SpecialHelpOrder(click.Group):
    """
    Click command group with customizable order of help output.
    """

    def __init__(self, *args, **kwargs) -> None:
        self.help_priorities: Dict[str, int] = {}
        super(SpecialHelpOrder, self).__init__(*args, **kwargs)

    def get_help(self, ctx: click.Context) -> str:
        self.list_commands = self.list_commands_for_help  # type: ignore
        return super(SpecialHelpOrder, self).get_help(ctx)

    def list_commands_for_help(self, ctx: click.Context) -> Iterable[str]:
        """reorder the list of commands when listing the help"""
        commands = super(SpecialHelpOrder, self).list_commands(ctx)
        return (
            c[1]
            for c in sorted(
                (self.help_priorities.get(command, 1), command) for command in commands
            )
        )

    def command(self, *args, **kwargs) -> Callable:
        """Behaves the same as `click.Group.command()` except capture
        a priority for listing command names in help.
        """
        help_priority = kwargs.pop("help_priority", 1)
        help_priorities = self.help_priorities

        def decorator(f):
            cmd = super(SpecialHelpOrder, self).command(*args, **kwargs)(f)
            help_priorities[cmd.name] = help_priority
            return cmd

        return decorator

    def group(self, *args, **kwargs) -> Callable:
        """Behaves the same as `click.Group.group()` except capture
        a priority for listing command names in help.
        """
        help_priority = kwargs.pop("help_priority", 1)
        help_priorities = self.help_priorities

        def decorator(f):
            cmd = super(SpecialHelpOrder, self).group(*args, **kwargs)(f)
            help_priorities[cmd.name] = help_priority
            return cmd

        return decorator


def _check_config_exists(ctx: click.Context, param: click.Parameter, value: str) -> str:
    """
    Checks if the selected config name, passed as :param:`value`, is valid.

    :param ctx: Click context to be passed to command.
    :param param: Name of click parameter, in our case 'config_name'.
    :param value: Value  of click parameter, in our case the selected config.
    """

    # check if valid config
    if value not in list_configs() and not value == "maestral":
        raise click.ClickException(
            f"Configuration '{value}' does not exist. You can "
            f"list all existing configurations with "
            f"'maestral configs'."
        )

    return value


def _validate_config_name(
    ctx: click.Context, param: click.Parameter, value: str
) -> str:
    """
    Checks if the selected config name, passed as :param:`value`, is valid.

    :param ctx: Click context to be passed to command.
    :param param: Name of click parameter, in our case 'config_name'.
    :param value: Value  of click parameter, in our case the selected config.
    """

    try:
        return validate_config_name(value)
    except ValueError:
        raise click.ClickException("Configuration name may not contain any whitespace")


existing_config_option = click.option(
    "-c",
    "--config-name",
    default="maestral",
    is_eager=True,
    expose_value=True,
    metavar="NAME",
    callback=_check_config_exists,
    help="Select an existing configuration for the command.",
)

config_option = click.option(
    "-c",
    "--config-name",
    default="maestral",
    is_eager=True,
    expose_value=True,
    metavar="NAME",
    callback=_validate_config_name,
    help="Run Maestral with the given configuration name.",
)


CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(cls=SpecialHelpOrder, context_settings=CONTEXT_SETTINGS)
def main():
    """Maestral Dropbox client for Linux and macOS."""
    freeze_support()
    check_for_updates()


@main.group(cls=SpecialHelpOrder, help_priority=14)
def excluded():
    """View and manage excluded folders."""


@main.group(cls=SpecialHelpOrder, help_priority=18)
def notify():
    """Manage Desktop notifications."""


@main.group(cls=SpecialHelpOrder, help_priority=19)
def log():
    """View and manage Maestral's log."""


# ========================================================================================
# Main commands
# ========================================================================================


@main.command(help_priority=0)
@config_option
def gui(config_name: str) -> None:
    """Runs Maestral with a GUI."""

    import pkg_resources

    gui_entry_points = dict()
    for entry_point in pkg_resources.iter_entry_points("maestral_gui"):
        gui_entry_points[entry_point.name] = entry_point.load()

    if len(gui_entry_points) == 0:
        raise click.ClickException(
            "No maestral GUI installed. Please run " "'pip3 install maestral[gui]'."
        )

    if platform.system() == "Darwin":
        default = "maestral_cocoa"
    else:
        default = "maestral_qt"

    run = gui_entry_points.get(default)

    if not run:
        # the platform default gui is not available
        run = next(iter(gui_entry_points.values()))

    run(config_name)


@main.command(help_priority=1)
@click.option(
    "--foreground",
    "-f",
    is_flag=True,
    default=False,
    help="Starts Maestral in the foreground.",
)
@click.option(
    "--verbose", "-v", is_flag=True, default=False, help="Print log messages to stdout."
)
@config_option
@catch_maestral_errors
def start(foreground: bool, verbose: bool, config_name: str) -> None:
    """Starts the Maestral daemon."""

    # ---- run setup if necessary --------------------------------------------------------

    # We run the setup in the current process. This avoids starting a subprocess despite
    # running with the --foreground flag, prevents leaving a zombie process if the setup
    # fails with an exception and does not confuse systemd.

    from maestral.main import Maestral

    m = Maestral(config_name)

    if m.pending_link:  # this may raise KeyringAccessError
        link_dialog(m)

    if m.pending_dropbox_folder:
        path = select_dbx_path_dialog(config_name, allow_merge=True)
        m.create_dropbox_directory(path)

        exclude_folders_q = click.confirm(
            "Would you like to exclude any folders from syncing?",
        )

        if exclude_folders_q:
            click.echo(
                "Please choose which top-level folders to exclude. You can exclude\n"
                'individual files or subfolders later with "maestral excluded add".\n'
            )

            click.echo("Loading...", nl=False)

            # get all top-level Dropbox folders
            entries = m.list_folder("/", recursive=False)
            excluded_items: List[str] = []

            click.echo("\rLoading...   Done")

            # paginate through top-level folders, ask to exclude
            for e in entries:
                if e["type"] == "FolderMetadata":
                    yes = click.confirm(
                        'Exclude "{path_display}" from sync?'.format(**e)
                    )
                    if yes:
                        path_lower = cast(str, e["path_lower"])
                        excluded_items.append(path_lower)

            m.set_excluded_items(excluded_items)

    del m

    if foreground:
        # stop daemon process after setup and restart in our current process
        stop_maestral_daemon_process(config_name)
        start_maestral_daemon(config_name, log_to_stdout=verbose, start_sync=True)
    else:

        # start daemon process
        click.echo("Starting Maestral...", nl=False)

        res = start_maestral_daemon_process(
            config_name, log_to_stdout=verbose, start_sync=True
        )

        if res == Start.Ok:
            click.echo("\rStarting Maestral...        " + OK)
        elif res == Start.AlreadyRunning:
            click.echo("\rStarting Maestral...        Already running.")
        else:
            click.echo("\rStarting Maestral...        " + FAILED)
            click.echo("Please check logs for more information.")


@main.command(help_priority=2)
@existing_config_option
def stop(config_name: str) -> None:
    """Stops the Maestral daemon."""

    stop_daemon_with_cli_feedback(config_name)


@main.command(help_priority=3)
@click.option(
    "--foreground",
    "-f",
    is_flag=True,
    default=False,
    help="Starts Maestral in the foreground.",
)
@click.option(
    "--verbose", "-v", is_flag=True, default=False, help="Print log messages to stdout."
)
@existing_config_option
@click.pass_context
def restart(ctx, foreground: bool, verbose: bool, config_name: str) -> None:
    """Restarts the Maestral daemon."""

    stop_daemon_with_cli_feedback(config_name)
    ctx.forward(start)


@main.command(help_priority=4)
@click.option("--yes", "-Y", is_flag=True, default=False)
@click.option("--no", "-N", is_flag=True, default=False)
@existing_config_option
def autostart(yes: bool, no: bool, config_name: str) -> None:
    """
    Automatically start the maestral daemon on log-in.

    A systemd or launchd service will be created to start a sync daemon for the given
    configuration on user login.
    """

    from maestral.utils.autostart import AutoStart

    auto_start = AutoStart(config_name)

    if not auto_start.implementation:
        click.echo(
            "Autostart is currently not supported for your platform.\n"
            "Autostart requires systemd on Linux or launchd on macOS."
        )
        return

    if yes or no:
        if yes:
            auto_start.enable()
        else:
            auto_start.disable()
        enabled_str = "Enabled" if yes else "Disabled"
        click.echo(f"{enabled_str} start on login.")
    else:
        enabled_str = "enabled" if auto_start.enabled else "disabled"
        click.echo(f"Autostart is currently {enabled_str}.")


@main.command(help_priority=5)
@existing_config_option
def pause(config_name: str) -> None:
    """Pauses syncing."""

    try:
        with MaestralProxy(config_name) as m:
            m.pause_sync()
        click.echo("Syncing paused.")
    except Pyro5.errors.CommunicationError:
        click.echo("Maestral daemon is not running.")


@main.command(help_priority=6)
@existing_config_option
def resume(config_name: str) -> None:
    """Resumes syncing."""

    try:
        with MaestralProxy(config_name) as m:
            if not check_for_fatal_errors(m):
                m.resume_sync()
                click.echo("Syncing resumed.")

    except Pyro5.errors.CommunicationError:
        click.echo("Maestral daemon is not running.")


@main.command(help_priority=7)
@existing_config_option
@catch_maestral_errors
def status(config_name: str) -> None:
    """Returns the current status of the Maestral daemon."""

    try:
        with MaestralProxy(config_name) as m:

            n_errors = len(m.sync_errors)
            color = "red" if n_errors > 0 else "green"
            n_errors_str = click.style(str(n_errors), fg=color)
            click.echo("")
            click.echo("Account:      {}".format(m.get_state("account", "email")))
            click.echo("Usage:        {}".format(m.get_state("account", "usage")))
            click.echo("Status:       {}".format(m.status))
            click.echo("Sync threads: {}".format("Running" if m.running else "Stopped"))
            click.echo("Sync errors:  {}".format(n_errors_str))
            click.echo("")

            check_for_fatal_errors(m)

            sync_err_list = m.sync_errors

            if len(sync_err_list) > 0:
                headers = ["PATH", "ERROR"]
                col0 = ["'{}'".format(err["dbx_path"]) for err in sync_err_list]
                col1 = ["{title}. {message}".format(**err) for err in sync_err_list]

                click.echo(format_table(columns=[col0, col1], headers=headers))
                click.echo("")

    except Pyro5.errors.CommunicationError:
        click.echo("Maestral daemon is not running.")


@main.command(help_priority=8)
@click.argument("local_path", type=click.Path(exists=True))
@existing_config_option
def file_status(local_path: str, config_name: str) -> None:
    """
    Returns the current sync status of a given file or folder.

    Returned value will be 'uploading', 'downloading', 'up to date', 'error', or
    'unwatched' (for files outside of the Dropbox directory). This will always be
    'unwatched' if syncing is paused.
    """

    try:
        with MaestralProxy(config_name) as m:

            if check_for_fatal_errors(m):
                return

            stat = m.get_file_status(local_path)
            click.echo(stat)

    except Pyro5.errors.CommunicationError:
        click.echo("unwatched")


@main.command(help_priority=9)
@existing_config_option
@catch_maestral_errors
def activity(config_name: str) -> None:
    """Live view of all items being synced."""

    import curses
    import time
    from maestral.utils import natural_size

    try:
        with MaestralProxy(config_name) as m:

            if check_for_fatal_errors(m):
                return

            def curses_loop(screen):

                curses.use_default_colors()  # don't change terminal background
                screen.nodelay(1)  # sets `screen.getch()` to non-blocking

                while True:

                    # get info from daemon
                    activity = m.get_activity()
                    status = m.status
                    n_errors = len(m.sync_errors)

                    # create header
                    lines = [
                        f"Status: {status}, Sync errors: {n_errors}",
                        "",
                    ]

                    # create table

                    file_names = ["PATH"]
                    states = ["STATUS"]
                    col_len = 4

                    for event in activity:

                        dbx_path = cast(str, event["dbx_path"])
                        direction = cast(str, event["direction"])
                        status = cast(str, event["status"])
                        size = cast(int, event["size"])
                        completed = cast(int, event["completed"])

                        filename = os.path.basename(dbx_path)
                        file_names.append(filename)

                        if completed > 0:
                            done_str = natural_size(completed, sep=False)
                            todo_str = natural_size(size, sep=False)
                            states.append(f"{done_str}/{todo_str}")
                        else:
                            if status == "syncing" and direction == "up":
                                states.append("uploading")
                            elif status == "syncing" and direction == "down":
                                states.append("downloading")
                            else:
                                states.append(status)

                        col_len = max(len(filename), col_len)

                    for fn, s in zip(file_names, states):  # create rows
                        lines.append(fn.ljust(col_len + 2) + s)

                    # print to console screen
                    screen.clear()
                    try:
                        screen.addstr("\n".join(lines))
                    except curses.error:
                        pass
                    screen.refresh()

                    # abort when user presses 'q', refresh otherwise
                    key = screen.getch()
                    if key == ord("q"):
                        break
                    elif key < 0:
                        time.sleep(1)

            # enter curses event loop
            curses.wrapper(curses_loop)

    except Pyro5.errors.CommunicationError:
        click.echo("Maestral daemon is not running.")


@main.command(help_priority=10)
@click.argument("dropbox_path", type=click.Path(), default="")
@click.option(
    "-l",
    "--long",
    is_flag=True,
    default=False,
    help="Show output in long format with metadata.",
)
@click.option(
    "-d",
    "--include-deleted",
    is_flag=True,
    default=False,
    help="Include deleted items in listing. This can be slow.",
)
@existing_config_option
@catch_maestral_errors
def ls(long: bool, dropbox_path: str, include_deleted: bool, config_name: str) -> None:
    """Lists contents of a Dropbox directory."""

    from datetime import datetime
    from maestral.utils import natural_size

    if not dropbox_path.startswith("/"):
        dropbox_path = "/" + dropbox_path

    with MaestralProxy(config_name, fallback=True) as m:

        entries = m.list_folder(
            dropbox_path, recursive=False, include_deleted=include_deleted
        )
        entries.sort(key=lambda x: cast(str, x["name"]).lower())

    if long:

        names = []
        types = []
        sizes = []
        shared = []
        last_modified = []
        excluded = []

        to_short_type = {
            "FileMetadata": "file",
            "FolderMetadata": "folder",
            "DeletedMetadata": "deleted",
        }

        for e in entries:

            long_type = cast(str, e["type"])
            name = cast(str, e["name"])
            path_lower = cast(str, e["path_lower"])

            types.append(to_short_type[long_type])
            names.append(name)

            shared.append("shared" if "sharing_info" in e else "private")
            excluded.append(m.excluded_status(path_lower))

            if "size" in e:
                size = cast(float, e["size"])
                sizes.append(natural_size(size))
            else:
                sizes.append("-")

            if "client_modified" in e:
                cm = cast(str, e["client_modified"])
                dt = datetime.strptime(cm, "%Y-%m-%dT%H:%M:%S%z").astimezone()
                last_modified.append(dt.strftime("%d %b %Y %H:%M"))
            else:
                last_modified.append("-")

        click.echo("")
        click.echo(
            format_table(
                headers=["Name", "Type", "Size", "Shared", "Syncing", "Last modified"],
                columns=[names, types, sizes, shared, excluded, last_modified],
                alignment=[LEFT, LEFT, RIGHT, LEFT, LEFT, LEFT],
                wrap=False,
            ),
        )
        click.echo("")

    else:

        from maestral.utils import chunks

        names = []
        colors = []
        formatted_names = []
        max_len = 0

        for e in entries:
            name = cast(str, e["name"])

            max_len = max(max_len, len(name))
            names.append(name)
            colors.append("blue" if e["type"] == "DeletedMetadata" else None)

        max_len += 2  # add 2 spaces padding

        for name, color in zip(names, colors):
            formatted_names.append(click.style(name.ljust(max_len), fg=color))

        width, height = click.get_terminal_size()
        n_columns = max(width // max_len, 1)

        rows = chunks(formatted_names, n_columns)

        for row in rows:
            click.echo("".join(row))


@main.command(help_priority=11)
@click.option(
    "-r",
    "relink",
    is_flag=True,
    default=False,
    help="Relink to the current account. Keeps the sync state.",
)
@config_option
@catch_maestral_errors
def link(relink: bool, config_name: str) -> None:
    """Links Maestral with your Dropbox account."""

    with MaestralProxy(config_name, fallback=True) as m:

        if m.pending_link or relink:
            link_dialog(m)
        else:
            click.echo(
                "Maestral is already linked. Use the option "
                "'-r' to relink to the same account."
            )


@main.command(help_priority=12)
@existing_config_option
@catch_maestral_errors
def unlink(config_name: str) -> None:
    """
    Unlinks your Dropbox account.

    If Maestral is running, it will be stopped before unlinking.
    """

    if click.confirm("Are you sure you want unlink your account?"):

        from maestral.main import Maestral

        stop_daemon_with_cli_feedback(config_name)
        m = Maestral(config_name)
        m.unlink()

        click.echo("Unlinked Maestral.")


@main.command(help_priority=13)
@click.argument("new_path", required=False, type=click.Path(writable=True))
@existing_config_option
def move_dir(new_path: str, config_name: str) -> None:
    """Change the location of your local Dropbox folder."""

    new_path = new_path or select_dbx_path_dialog(config_name)

    with MaestralProxy(config_name, fallback=True) as m:
        m.move_dropbox_directory(new_path)

    click.echo(f"Dropbox folder moved to {new_path}.")


@main.command(help_priority=15)
@existing_config_option
@catch_maestral_errors
def rebuild_index(config_name: str) -> None:
    """
    Rebuilds Maestral's index.

    Rebuilding may take several minutes, depending on the size of your Dropbox.
    """

    import textwrap

    with MaestralProxy(config_name, fallback=True) as m:

        width, height = click.get_terminal_size()

        msg = textwrap.fill(
            "Rebuilding the index may take several minutes, depending on the size of "
            "your Dropbox. Any changes to local files will be synced once rebuilding "
            "has completed. If you stop the daemon during the process, rebuilding will "
            "start again on the next launch.\nIf the daemon is not currently running, a "
            "rebuild will be schedules for the next startup.",
            width=width,
        )

        click.echo(msg + "\n")
        click.confirm("Do you want to continue?", abort=True)

        m.rebuild_index()

        if isinstance(m, MaestralProxy):
            click.echo('Rebuilding now. Run "maestral status" to view progress.')
        else:
            click.echo("Daemon is not running. Rebuilding scheduled for next startup.")


@main.command(help_priority=16)
@click.argument("dropbox_path", type=click.Path())
@existing_config_option
@catch_maestral_errors
def revs(dropbox_path: str, config_name: str) -> None:
    """Lists old revisions of a file."""

    from datetime import datetime

    if not dropbox_path.startswith("/"):
        dropbox_path = "/" + dropbox_path

    with MaestralProxy(config_name, fallback=True) as m:

        entries = m.list_revisions(dropbox_path)

    revs = []
    last_modified = []

    for e in entries:

        rev = cast(str, e["rev"])
        cm = cast(str, e["client_modified"])

        revs.append(rev)

        dt = datetime.strptime(cm, "%Y-%m-%dT%H:%M:%S%z").astimezone()
        last_modified.append(dt.strftime("%d %b %Y %H:%M"))

    click.echo(format_table(columns=[revs, last_modified]))


@main.command(help_priority=17)
@click.argument("dropbox_path", type=click.Path())
@click.argument("rev")
@existing_config_option
@catch_maestral_errors
def restore(dropbox_path: str, rev: str, config_name: str) -> None:
    """Restores an old revision of a file to the given path."""

    with MaestralProxy(config_name, fallback=True) as m:
        m.restore(dropbox_path, rev)

    click.echo(f'Restored {rev} to "{dropbox_path}"')


@main.command(help_priority=18)
@existing_config_option
def history(config_name: str) -> None:
    """Shows a list of recently changed or added files."""

    from datetime import datetime

    with MaestralProxy(config_name, fallback=True) as m:

        history = m.get_history()
        paths = []
        change_times = []
        change_types = []

        for event in history:

            dbx_path = cast(str, event["dbx_path"])
            change_type = cast(str, event["change_type"])
            change_time_or_sync_time = cast(float, event["change_time_or_sync_time"])
            dt = datetime.fromtimestamp(change_time_or_sync_time)

            paths.append(dbx_path)
            change_times.append(dt.strftime("%d %b %Y %H:%M"))
            change_types.append(change_type)

        click.echo(
            format_table(columns=[paths, change_types, change_times], wrap=False)
        )


@main.command(help_priority=19)
def configs() -> None:
    """Lists all configured Dropbox accounts."""

    # clean up stale configs
    config_names = list_configs()

    for name in config_names:
        dbid = MaestralConfig(name).get("account", "account_id")
        if dbid == "" and not is_running(name):
            remove_configuration(name)

    # display remaining configs
    names = list_configs()
    emails = [MaestralState(c).get("account", "email") for c in names]

    click.echo("")
    click.echo(
        format_table(columns=[names, emails], headers=["Config name", "Account"])
    )
    click.echo("")


@main.command(help_priority=21)
@click.option("--yes", "-Y", is_flag=True, default=False)
@click.option("--no", "-N", is_flag=True, default=False)
@existing_config_option
def analytics(yes: bool, no: bool, config_name: str) -> None:
    """
    Enables or disables sharing of error reports.

    Sharing is disabled by default. If enabled, error reports are shared with bugsnag and
    no personal information will typically be collected. Shared tracebacks may however
    include file names, depending on the error.
    """

    if yes or no:
        with MaestralProxy(config_name, fallback=True) as m:
            m.analytics = yes

        enabled_str = "Enabled" if yes else "Disabled"
        click.echo(f"{enabled_str} automatic error reports.")
    else:
        with MaestralProxy(config_name, fallback=True) as m:
            state = m.analytics

        enabled_str = "enabled" if state else "disabled"
        click.echo(f"Automatic error reports are {enabled_str}.")


@main.command(help_priority=23)
@existing_config_option
def account_info(config_name: str) -> None:
    """Shows your Dropbox account information."""

    with MaestralProxy(config_name, fallback=True) as m:

        email = m.get_state("account", "email")
        account_type = m.get_state("account", "type").capitalize()
        usage = m.get_state("account", "usage")
        dbid = m.get_conf("account", "account_id")

        click.echo("")
        click.echo(f"Email:             {email}")
        click.echo(f"Account-type:      {account_type}")
        click.echo(f"Usage:             {usage}")
        click.echo(f"Dropbox-ID:        {dbid}")
        click.echo("")


@main.command(help_priority=24)
def about() -> None:
    """Returns the version number and other information."""
    import time
    from maestral import __url__
    from maestral import __author__
    from maestral import __version__

    year = time.localtime().tm_year
    click.echo("")
    click.echo(f"Version:    {__version__}")
    click.echo(f"Website:    {__url__}")
    click.echo(f"Copyright:  (c) 2018-{year}, {__author__}.")
    click.echo("")


# ========================================================================================
# Exclude commands
# ========================================================================================


@excluded.command(name="list", help_priority=0)
@existing_config_option
def excluded_list(config_name: str) -> None:
    """Lists all excluded files and folders."""

    with MaestralProxy(config_name, fallback=True) as m:

        excluded_items = m.excluded_items
        excluded_items.sort()

        if len(excluded_items) == 0:
            click.echo("No excluded files or folders.")
        else:
            for item in excluded_items:
                click.echo(item)


@excluded.command(name="add", help_priority=1)
@click.argument("dropbox_path", type=click.Path())
@existing_config_option
@catch_maestral_errors
def excluded_add(dropbox_path: str, config_name: str) -> None:
    """Adds a file or folder to the excluded list and re-syncs."""

    if not dropbox_path.startswith("/"):
        dropbox_path = "/" + dropbox_path

    if dropbox_path == "/":
        click.echo(click.style("Cannot exclude the root directory.", fg="red"))
        return

    with MaestralProxy(config_name, fallback=True) as m:
        if check_for_fatal_errors(m):
            return

        m.exclude_item(dropbox_path)
        click.echo(f"Excluded '{dropbox_path}'.")


@excluded.command(name="remove", help_priority=2)
@click.argument("dropbox_path", type=click.Path())
@existing_config_option
@catch_maestral_errors
def excluded_remove(dropbox_path: str, config_name: str) -> None:
    """Removes a file or folder from the excluded list and re-syncs."""

    if not dropbox_path.startswith("/"):
        dropbox_path = "/" + dropbox_path

    if dropbox_path == "/":
        click.echo(click.style("The root directory is always included.", fg="red"))
        return

    try:
        with MaestralProxy(config_name) as m:
            if check_for_fatal_errors(m):
                return

            m.include_item(dropbox_path)
            click.echo(f"Included '{dropbox_path}'. Now downloading...")

    except Pyro5.errors.CommunicationError:
        raise click.ClickException(
            "Maestral daemon must be running to download folders."
        )


# ========================================================================================
# Log commands
# ========================================================================================


@log.command(name="show", help_priority=0)
@click.option(
    "--external", "-e", is_flag=True, default=False, help="Open in external program."
)
@existing_config_option
def log_show(external: bool, config_name: str) -> None:
    """Prints Maestral's logs to the console."""

    from maestral.utils.appdirs import get_log_path

    log_file = get_log_path("maestral", config_name + ".log")

    if external:
        res = click.launch(log_file)
    else:
        try:
            with open(log_file) as f:
                text = f.read()
            click.echo_via_pager(text)
        except OSError:
            res = 1
        else:
            res = 0

    if res > 0:
        raise click.ClickException(f"Could not open log file at '{log_file}'")


@log.command(name="clear", help_priority=1)
@existing_config_option
def log_clear(config_name: str) -> None:
    """Clears Maestral's log file."""

    from maestral.utils.appdirs import get_log_path

    log_dir = get_log_path("maestral")
    log_name = config_name + ".log"

    log_files = []

    for file_name in os.listdir(log_dir):
        if file_name.startswith(log_name):
            log_files.append(os.path.join(log_dir, file_name))

    try:
        for file in log_files:
            open(file, "w").close()
        click.echo("Cleared Maestral's log.")
    except FileNotFoundError:
        click.echo("Cleared Maestral's log.")
    except OSError:
        raise click.ClickException(
            f"Could not clear log at '{log_dir}'. " f"Please try to delete it manually"
        )


@log.command(name="level", help_priority=2)
@click.argument(
    "level_name",
    required=False,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
)
@existing_config_option
def log_level(level_name: str, config_name: str) -> None:
    """Gets or sets the log level."""

    with MaestralProxy(config_name, fallback=True) as m:
        if level_name:
            m.log_level = cast(int, getattr(logging, level_name))
            click.echo(f"Log level set to {level_name}.")
        else:
            level_name = logging.getLevelName(m.log_level)
            click.echo(f"Log level: {level_name}")


# ========================================================================================
# Notification commands
# ========================================================================================


@notify.command(name="level", help_priority=0)
@click.argument(
    "level_name",
    required=False,
    type=click.Choice(["NONE", "ERROR", "SYNCISSUE", "FILECHANGE"]),
)
@existing_config_option
def notify_level(level_name: str, config_name: str) -> None:
    """Gets or sets the level for desktop notifications."""

    from maestral.utils.notify import MaestralDesktopNotifier as MDN

    with MaestralProxy(config_name, fallback=True) as m:
        if level_name:
            m.notification_level = MDN.level_name_to_number(level_name)
            click.echo(f"Notification level set to {level_name}.")
        else:
            level_name = MDN.level_number_to_name(m.notification_level)
            click.echo(f"Notification level: {level_name}.")


@notify.command(name="snooze", help_priority=1)
@click.argument("minutes", type=click.IntRange(min=0))
@existing_config_option
def notify_snooze(minutes: int, config_name: str) -> None:
    """Snoozes desktop notifications of file changes."""

    try:
        with MaestralProxy(config_name) as m:
            m.notification_snooze = minutes
    except Pyro5.errors.CommunicationError:
        click.echo("Maestral daemon is not running.")
    else:
        if minutes > 0:
            click.echo(
                f"Notifications snoozed for {minutes} min. " "Set snooze to 0 to reset."
            )
        else:
            click.echo("Notifications enabled.")


if __name__ == "__main__":
    main()
