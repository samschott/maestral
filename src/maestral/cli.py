# -*- coding: utf-8 -*-
"""
This module defines the functions to configure and interact with Maestral from the
command line. Some imports are deferred to the functions that required them in order to
reduce the startup time of individual CLI commands.
"""

# system imports
import sys
import os
import os.path as osp
import functools
import time
from typing import Optional, Dict, List, Tuple, Callable, Union, cast, TYPE_CHECKING

# external imports
import click

# local imports
from . import __version__
from .utils import cli

if TYPE_CHECKING:
    from click.shell_completion import CompletionItem
    from datetime import datetime
    from .main import Maestral
    from .daemon import MaestralProxy


# ======================================================================================
# CLI dialogs and helper functions
# ======================================================================================

OK = click.style("[OK]", fg="green")
FAILED = click.style("[FAILED]", fg="red")
KILLED = click.style("[KILLED]", fg="red")


def stop_daemon_with_cli_feedback(config_name: str) -> None:
    """Wrapper around :meth:`daemon.stop_maestral_daemon_process`
    with command line feedback."""

    from .daemon import stop_maestral_daemon_process, Stop

    click.echo("Stopping Maestral...", nl=False)
    res = stop_maestral_daemon_process(config_name)
    if res == Stop.Ok:
        click.echo("\rStopping Maestral...        " + OK)
    elif res == Stop.NotRunning:
        click.echo("\rMaestral daemon is not running.")
    elif res == Stop.Killed:
        click.echo("\rStopping Maestral...        " + KILLED)
    elif res == Stop.Failed:
        click.echo("\rStopping Maestral...        " + FAILED)


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

    from .utils.path import delete

    default_dir_name = default_dir_name or f"Dropbox ({config_name.capitalize()})"

    while True:
        res = cli.select_path(
            "Please choose a local Dropbox folder:",
            default=f"~/{default_dir_name}",
            files_allowed=False,
        )
        res = res.rstrip(osp.sep)

        dropbox_path = osp.expanduser(res)

        if osp.exists(dropbox_path):
            if allow_merge:
                text = (
                    "Directory already exists. Do you want to replace it "
                    "or merge its content with your Dropbox?"
                )
                choice = cli.select(text, options=["replace", "merge", "cancel"])
            else:
                text = (
                    "Directory already exists. Do you want to replace it? "
                    "Its content will be lost!"
                )
                replace = cli.confirm(text)
                choice = 0 if replace else 2

            if choice == 0:
                err = delete(dropbox_path)
                if err:
                    cli.warn(
                        "Could not write to selected location. "
                        "Please make sure that you have sufficient permissions."
                    )
                else:
                    cli.ok("Replaced existing folder")
                    return dropbox_path
            elif choice == 1:
                cli.ok("Merging with existing folder")
                return dropbox_path

        else:
            return dropbox_path


def link_dialog(m: Union["MaestralProxy", "Maestral"]) -> None:
    """
    A CLI dialog for linking a Dropbox account.

    :param m: Proxy to Maestral daemon.
    """

    authorize_url = m.get_auth_url()

    cli.info(f"Linking new account for '{m.config_name}' config")
    cli.info("Retrieving auth code from Dropbox")
    choice = cli.select(
        "How would you like to you link your account?",
        options=["Open Dropbox website", "Print auth URL to console"],
    )

    if choice == 0:
        click.launch(authorize_url)
    else:
        cli.info("Open the URL below to retrieve an auth code:")
        cli.info(authorize_url)

    res = -1
    while res != 0:
        auth_code = cli.prompt("Enter the auth code:")
        auth_code = auth_code.strip()

        res = m.link(auth_code)

        if res == 0:
            email = m.get_state("account", "email")
            cli.ok(f"Linked to {email}")
        elif res == 1:
            cli.warn("Invalid token, please try again")
        elif res == 2:
            cli.warn("Could not connect to Dropbox, please try again")


def check_for_updates() -> None:
    """
    Checks if updates are available by reading the cached release number from the
    config file and notifies the user. Prints an update note to the command line.
    """
    from packaging.version import Version
    from .config import MaestralConfig, MaestralState

    conf = MaestralConfig("maestral")
    state = MaestralState("maestral")

    interval = conf.get("app", "update_notification_interval")
    last_update_check = state.get("app", "update_notification_last")
    latest_release = state.get("app", "latest_release")

    if interval == 0 or time.time() - last_update_check < interval:
        return

    has_update = Version(__version__) < Version(latest_release)

    if has_update:
        cli.echo(
            f"Update available v{__version__} → v{latest_release}. "
            f"Please use your package manager to update."
        )


def check_for_fatal_errors(m: Union["MaestralProxy", "Maestral"]) -> bool:
    """
    Checks the given Maestral instance for fatal errors such as revoked Dropbox access,
    deleted Dropbox folder etc. Prints a nice representation to the command line.

    :param m: Proxy to Maestral daemon or Maestral instance.
    :returns: True in case of fatal errors, False otherwise.
    """

    import textwrap
    import shutil

    maestral_err_list = m.fatal_errors

    if len(maestral_err_list) > 0:

        width, height = shutil.get_terminal_size()

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


def convert_api_errors(func: Callable) -> Callable:
    """
    Decorator that catches a MaestralApiError and prints a formatted error message to
    stdout before exiting. Calls ``sys.exit(1)`` after printing the error to stdout.
    """

    from .errors import MaestralApiError

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except MaestralApiError as exc:
            cli.warn(f"{exc.title}. {exc.message}")
            sys.exit(1)

    return wrapper


def _datetime_from_iso_str(time_str: str) -> "datetime":
    """
    Converts an ISO 8601 time string such as '2015-05-15T15:50:38Z' to a timezone aware
    datetime object in the local time zone.
    """

    from datetime import datetime

    # replace Z with +0000, required for Python 3.6 compatibility
    time_str = time_str.replace("Z", "+0000")
    return datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S%z").astimezone()


# ======================================================================================
# Custom parameter types
# ======================================================================================

# A custom parameter:
# * needs a name
# * needs to pass through None unchanged
# * needs to convert from a string
# * needs to convert its result type through unchanged (eg: needs to be idempotent)
# * needs to be able to deal with param and context being None. This can be the case
#   when the object is used with prompt inputs.


class DropboxPath(click.ParamType):
    """A command line parameter representing a Dropbox path

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
        value: Optional[str],
        param: Optional[click.Parameter],
        ctx: Optional[click.Context],
    ) -> Optional[str]:

        if value is None:
            return value

        if not value.startswith("/"):
            value = "/" + value

        return value

    def shell_complete(
        self,
        ctx: Optional[click.Context],
        param: Optional[click.Parameter],
        incomplete: str,
    ) -> List["CompletionItem"]:

        from click.shell_completion import CompletionItem
        from .utils import removeprefix
        from .config import MaestralConfig

        matches: List[str] = []
        completions: List[CompletionItem] = []

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
    """A command line parameter representing a config key"""

    name = "key"

    def shell_complete(
        self,
        ctx: Optional[click.Context],
        param: Optional[click.Parameter],
        incomplete: str,
    ) -> List["CompletionItem"]:

        from click.shell_completion import CompletionItem
        from .config.main import KEY_SECTION_MAP as KEYS

        return [CompletionItem(key) for key in KEYS if key.startswith(incomplete)]


class ConfigName(click.ParamType):
    """A command line parameter representing a Dropbox path

    :param existing: If ``True`` require an existing config, otherwise create a new
        config on demand.
    """

    name = "config"

    def __init__(self, existing: bool = True) -> None:
        self.existing = existing

    def convert(
        self,
        value: Optional[str],
        param: Optional[click.Parameter],
        ctx: Optional[click.Context],
    ) -> Optional[str]:

        if value is None:
            return value

        from .config import validate_config_name, list_configs

        if not self.existing:

            # accept all valid config names
            try:
                return validate_config_name(value)
            except ValueError:
                raise cli.CliException(
                    "Configuration name may not contain any whitespace"
                )

        else:

            # accept only existing config names
            if value in list_configs():
                return value
            else:
                raise cli.CliException(
                    f"Configuration '{value}' does not exist. "
                    f"Use 'maestral configs' to list all configurations."
                )

    def shell_complete(
        self,
        ctx: Optional[click.Context],
        param: Optional[click.Parameter],
        incomplete: str,
    ) -> List["CompletionItem"]:

        from click.shell_completion import CompletionItem
        from .config import list_configs

        matches = [conf for conf in list_configs() if conf.startswith(incomplete)]
        return [CompletionItem(m) for m in matches]


# ======================================================================================
# Command groups
# ======================================================================================


class OrderedGroup(click.Group):
    """Click command group with customizable order of help output."""

    def command(self, *args, **kwargs) -> Callable:
        """Behaves the same as :meth:`click.Group.command()` except captures a section
        name for listing command names in help.
        """
        section = kwargs.pop("section", "Commands")

        from click.decorators import command

        def decorator(f):
            cmd = command(*args, **kwargs)(f)
            cmd.section = section
            self.add_command(cmd)

            return cmd

        return decorator

    def group(self, *args, **kwargs) -> Callable:
        """Behaves the same as :meth:`click.Group.group()` except captures a section
        name for listing command names in help.
        """
        section = kwargs.pop("section", "Commands")

        from click.decorators import group

        def decorator(f):
            cmd = group(*args, **kwargs)(f)
            cmd.section = section
            self.add_command(cmd)

            return cmd

        return decorator

    def format_commands(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        """Extra format methods for multi methods that adds all the commands
        after the options.
        """
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
            limit = formatter.width - 6 - max_len  # type: ignore

            sections: Dict[str, List[Tuple[str, click.Command]]] = {}

            # group commands into sections
            for name, cmd in commands:
                try:
                    sections[cmd.section].append((name, cmd))  # type: ignore
                except KeyError:
                    sections[cmd.section] = [(name, cmd)]  # type: ignore

            # format sections individually
            for section, cmds in sections.items():

                rows = []

                for name, cmd in cmds:
                    name = name.ljust(max_len)
                    help = cmd.get_short_help_str(limit)
                    rows.append((name, help))

                if rows:
                    with formatter.section(section):
                        formatter.write_dl(rows)


@click.group(cls=OrderedGroup, help="Dropbox client for Linux and macOS.")
@click.version_option(version=__version__, message=__version__)
def main():
    pass


# ======================================================================================
# Core commands
# ======================================================================================

config_option = click.option(
    "-c",
    "--config-name",
    default="maestral",
    type=ConfigName(existing=False),
    is_eager=True,
    expose_value=True,
    help="Run command with the given configuration.",
)

existing_config_option = click.option(
    "-c",
    "--config-name",
    default="maestral",
    type=ConfigName(),
    is_eager=True,
    expose_value=True,
    help="Run command with the given configuration.",
)


@main.command(section="Core Commands", help="Start the sync daemon.")
@click.option(
    "--foreground",
    "-f",
    is_flag=True,
    default=False,
    help="Start Maestral in the foreground.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Print log messages to stderr.",
)
@config_option
@convert_api_errors
def start(foreground: bool, verbose: bool, config_name: str) -> None:

    import threading
    from .daemon import (
        MaestralProxy,
        start_maestral_daemon,
        start_maestral_daemon_process,
        wait_for_startup,
        is_running,
        Start,
        CommunicationError,
    )

    check_for_updates()

    if is_running(config_name):
        click.echo("Daemon is already running.")
        return

    @convert_api_errors
    def startup_dialog():

        try:
            wait_for_startup(config_name)
        except CommunicationError:
            return

        m = MaestralProxy(config_name)

        if m.pending_link:
            link_dialog(m)

        if m.pending_dropbox_folder:
            path = select_dbx_path_dialog(config_name, allow_merge=True)

            while True:
                try:
                    m.create_dropbox_directory(path)
                    break
                except OSError:
                    cli.warn(
                        "Could not create folder. Please make sure that you have "
                        "permissions to write to the selected location or choose a "
                        "different location."
                    )

            include_all = cli.confirm("Would you like sync all folders?")

            if not include_all:
                # get all top-level Dropbox folders
                cli.info("Loading...")
                entries = m.list_folder("/", recursive=False)

                names = [
                    cast(str, e["name"])
                    for e in entries
                    if e["type"] == "FolderMetadata"
                ]

                choices = cli.select_multiple(
                    "Choose which folders to include", options=names
                )

                excluded_paths = [
                    f"/{name}"
                    for index, name in enumerate(names)
                    if index not in choices
                ]

                m.excluded_items = excluded_paths

            cli.ok("Setup completed. Starting sync.")

        m.start_sync()

    t = threading.Thread(target=startup_dialog)
    t.start()

    if foreground:
        start_maestral_daemon(config_name, log_to_stderr=verbose)
    else:
        cli.echo("Starting Maestral...", nl=False)

        res = start_maestral_daemon_process(config_name)

        if res == Start.Ok:
            cli.echo("\rStarting Maestral...        " + OK)
        elif res == Start.AlreadyRunning:
            cli.echo("\rStarting Maestral...        " + "Already running.")
        else:
            cli.echo("\rStarting Maestral...        " + FAILED)
            cli.echo("Please check logs for more information.")
            return


@main.command(section="Core Commands", help="Stop the sync daemon.")
@existing_config_option
def stop(config_name: str) -> None:
    stop_daemon_with_cli_feedback(config_name)


@main.command(section="Core Commands", help="Run the GUI if installed.")
@config_option
def gui(config_name: str) -> None:

    from packaging.version import Version
    from packaging.requirements import Requirement

    try:
        from importlib.metadata import entry_points, requires, version  # type: ignore
    except ImportError:
        from importlib_metadata import entry_points, requires, version  # type: ignore

    # find all "maestral_gui" entry points registered by other packages
    gui_entry_points = entry_points().get("maestral_gui")

    if not gui_entry_points or len(gui_entry_points) == 0:
        raise cli.CliException(
            "No maestral GUI installed. Please run 'pip3 install maestral[gui]'."
        )

    # check if 1st party defaults "maestral_cocoa" or "maestral_qt" are installed
    default_gui = "maestral_cocoa" if sys.platform == "darwin" else "maestral_qt"
    default_entry_point = next(
        (e for e in gui_entry_points if e.name == default_gui), None
    )

    if default_entry_point:
        # check gui requirements
        requirements = [Requirement(r) for r in requires("maestral")]  # type: ignore

        for r in requirements:
            if r.marker and r.marker.evaluate({"extra": "gui"}):
                version_str = version(r.name)
                if not r.specifier.contains(Version(version_str), prereleases=True):
                    raise cli.CliException(
                        f"{r.name}{r.specifier} required but you have {version_str}"
                    )

        # load entry point
        run = default_entry_point.load()

    else:
        # load any 3rd party GUI
        fallback_entry_point = next(iter(gui_entry_points))
        run = fallback_entry_point.load()

    run(config_name)


@main.command(section="Core Commands", help="Pause syncing.")
@existing_config_option
def pause(config_name: str) -> None:

    from .daemon import MaestralProxy, CommunicationError

    try:
        with MaestralProxy(config_name) as m:
            m.stop_sync()
        cli.ok("Syncing paused.")
    except CommunicationError:
        cli.echo("Maestral daemon is not running.")


@main.command(section="Core Commands", help="Resume syncing.")
@existing_config_option
def resume(config_name: str) -> None:

    from .daemon import MaestralProxy, CommunicationError

    try:
        with MaestralProxy(config_name) as m:
            if not check_for_fatal_errors(m):
                m.start_sync()
                cli.ok("Syncing resumed.")

    except CommunicationError:
        cli.echo("Maestral daemon is not running.")


@main.group(section="Core Commands", help="Link, unlink and view the Dropbox account.")
def auth():
    pass


@auth.command(name="link", help="Link a new Dropbox account.")
@click.option(
    "--relink",
    "-r",
    is_flag=True,
    default=False,
    help="Relink to the existing account. Keeps the sync state.",
)
@config_option
@convert_api_errors
def auth_link(relink: bool, config_name: str) -> None:

    from .daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:

        if m.pending_link or relink:
            link_dialog(m)
        else:
            cli.echo(
                "Maestral is already linked. Use '-r' to relink to the same "
                "account or specify a new config name with '-c'."
            )


@auth.command(
    name="unlink",
    help="""
Unlink your Dropbox account.

If Maestral is running, it will be stopped before unlinking.
""",
)
@click.option(
    "--yes", "-Y", is_flag=True, default=False, help="Skip confirmation prompt."
)
@existing_config_option
@convert_api_errors
def auth_unlink(yes: bool, config_name: str) -> None:

    if not yes:
        yes = cli.confirm("Are you sure you want unlink your account?", default=False)

    if yes:
        from .main import Maestral

        stop_daemon_with_cli_feedback(config_name)
        m = Maestral(config_name)
        m.unlink()

        cli.ok("Unlinked Maestral.")


@auth.command(name="status", help="View authentication status.")
@existing_config_option
def auth_status(config_name: str) -> None:

    from .config import MaestralConfig, MaestralState

    conf = MaestralConfig(config_name)
    state = MaestralState(config_name)

    dbid = conf.get("auth", "account_id")
    email = state.get("account", "email")
    account_type = state.get("account", "type").capitalize()

    cli.echo("")
    cli.echo(f"Email:         {email}")
    cli.echo(f"Account-type:  {account_type}")
    cli.echo(f"Dropbox-ID:    {dbid}")
    cli.echo("")


@main.group(section="Core Commands", help="Create and manage shared links.")
def sharelink():
    pass


@sharelink.command(name="create", help="Create a shared link for a file or folder.")
@click.argument("dropbox_path", type=DropboxPath())
@click.option(
    "-p",
    "--password",
    help="Optional password for the link.",
)
@click.option(
    "-e",
    "--expiry",
    metavar="DATE",
    type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]),
    help="Expiry time for the link (e.g. '2025-07-24 20:50').",
)
@existing_config_option
@convert_api_errors
def sharelink_create(
    dropbox_path: str,
    password: str,
    expiry: Optional["datetime"],
    config_name: str,
) -> None:

    from .daemon import MaestralProxy

    expiry_dt: Optional[float]

    if expiry:
        expiry_dt = expiry.timestamp()
    else:
        expiry_dt = None

    if password:
        visibility = "password"
    else:
        visibility = "public"

    with MaestralProxy(config_name, fallback=True) as m:
        link_info = m.create_shared_link(dropbox_path, visibility, password, expiry_dt)

    cli.echo(link_info["url"])


@sharelink.command(name="revoke", help="Revoke a shared link.")
@click.argument("url")
@existing_config_option
@convert_api_errors
def sharelink_revoke(url: str, config_name: str) -> None:

    from .daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:
        m.revoke_shared_link(url)

    cli.ok("Revoked shared link.")


@sharelink.command(
    name="list", help="List shared links for a path or all shared links."
)
@click.argument("dropbox_path", required=False, type=DropboxPath())
@existing_config_option
@convert_api_errors
def sharelink_list(dropbox_path: Optional[str], config_name: str) -> None:

    from .daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:
        links = m.list_shared_links(dropbox_path)

    link_table = cli.Table(["URL", "Item", "Access", "Expires"])

    for link in links:
        url = cast(str, link["url"])
        file_name = cast(str, link["name"])
        visibility = cast(str, link["link_permissions"]["resolved_visibility"][".tag"])

        dt_field: cli.Field

        if "expires" in link:
            expires = cast(str, link["expires"])
            dt_field = cli.DateField(_datetime_from_iso_str(expires))
        else:
            dt_field = cli.TextField("-")

        link_table.append([url, file_name, visibility, dt_field])

    cli.echo("")
    link_table.echo()
    cli.echo("")


# ======================================================================================
# Information commands
# ======================================================================================


@main.command(section="Information", help="Show the status of the daemon.")
@existing_config_option
@convert_api_errors
def status(config_name: str) -> None:

    from .daemon import MaestralProxy, CommunicationError

    check_for_updates()

    try:
        with MaestralProxy(config_name) as m:

            email = m.get_state("account", "email")
            account_type = m.get_state("account", "type").capitalize()
            usage = m.get_state("account", "usage")
            status_info = m.status
            n_errors = len(m.sync_errors)
            color = "red" if n_errors > 0 else "green"
            n_errors_str = click.style(str(n_errors), fg=color)

            cli.echo("")
            cli.echo(f"Account:      {email} ({account_type})")
            cli.echo(f"Usage:        {usage}")
            cli.echo(f"Status:       {status_info}")
            cli.echo(f"Sync errors:  {n_errors_str}")
            cli.echo("")

            check_for_fatal_errors(m)

            sync_errors = m.sync_errors

            if len(sync_errors) > 0:

                path_column = cli.Column(title="Path")
                message_column = cli.Column(title="Error", wraps=True)

                for error in sync_errors:
                    path_column.append(error["dbx_path"])
                    message_column.append("{title}. {message}".format(**error))

                table = cli.Table([path_column, message_column])

                table.echo()
                cli.echo("")

    except CommunicationError:
        cli.echo("Maestral daemon is not running.")


@main.command(
    section="Information",
    help="""
Show the sync status of a local file or folder.

Returned value will be 'uploading', 'downloading', 'up to date', 'error', or 'unwatched'
(for files outside of the Dropbox directory). This will always be 'unwatched' if syncing
is paused. This command can be used to for instance to query information for a plugin to
a file-manager.
""",
)
@click.argument("local_path", type=click.Path(exists=True, resolve_path=True))
@existing_config_option
def filestatus(local_path: str, config_name: str) -> None:

    from .daemon import MaestralProxy, CommunicationError

    try:
        with MaestralProxy(config_name) as m:
            stat = m.get_file_status(local_path)
            cli.echo(stat)

    except CommunicationError:
        cli.echo("unwatched")


@main.command(section="Information", help="Live view of all items being synced.")
@existing_config_option
@convert_api_errors
def activity(config_name: str) -> None:

    import curses
    from .utils import natural_size
    from .daemon import MaestralProxy, CommunicationError

    try:
        with MaestralProxy(config_name) as m:

            if check_for_fatal_errors(m):
                return

            def curses_loop(screen) -> None:  # no type hints for screen provided yet

                curses.use_default_colors()  # don't change terminal background
                screen.nodelay(1)  # sets `screen.getch()` to non-blocking

                while True:

                    height, width = screen.getmaxyx()

                    # create header
                    lines = [
                        f"Status: {m.status}, Sync errors: {len(m.sync_errors)}",
                        "",
                    ]

                    # create table
                    filenames = []
                    states = []
                    col_len = 4

                    for event in m.get_activity(limit=height - 3):

                        dbx_path = cast(str, event["dbx_path"])
                        direction = cast(str, event["direction"])
                        state = cast(str, event["status"])
                        size = cast(int, event["size"])
                        completed = cast(int, event["completed"])

                        filename = os.path.basename(dbx_path)
                        filenames.append(filename)

                        arrow = "↓" if direction == "down" else "↑"

                        if completed > 0:
                            done_str = natural_size(completed, sep=False)
                            todo_str = natural_size(size, sep=False)
                            states.append(f"{done_str}/{todo_str} {arrow}")
                        else:
                            if state == "syncing" and direction == "up":
                                states.append("uploading")
                            elif state == "syncing" and direction == "down":
                                states.append("downloading")
                            else:
                                states.append(state)

                        col_len = max(len(filename), col_len)

                    for name, state in zip(filenames, states):  # create rows
                        lines.append(name.ljust(col_len + 2) + state)

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

    except CommunicationError:
        cli.echo("Maestral daemon is not running.")


@main.command(section="Information", help="Show recently changed or added files.")
@existing_config_option
@convert_api_errors
def history(config_name: str) -> None:

    from datetime import datetime
    from .daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:
        events = m.get_history()

    table = cli.Table(
        [
            cli.Column("Path", elide=cli.Elide.Leading),
            cli.Column("Change"),
            cli.Column("Time"),
        ]
    )

    for event in events:

        dbx_path = cast(str, event["dbx_path"])
        change_type = cast(str, event["change_type"])
        change_time_or_sync_time = cast(float, event["change_time_or_sync_time"])
        dt = datetime.fromtimestamp(change_time_or_sync_time)

        table.append([dbx_path, change_type, dt])

    cli.echo("")
    table.echo()
    cli.echo("")


@main.command(section="Information", help="List contents of a Dropbox directory.")
@click.argument("dropbox_path", type=DropboxPath(), default="")
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
    help="Include deleted items in listing.",
)
@existing_config_option
@convert_api_errors
def ls(long: bool, dropbox_path: str, include_deleted: bool, config_name: str) -> None:

    from .utils import natural_size
    from .daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:

        cli.echo("Loading...\r", nl=False)

        entries_iter = m.list_folder_iterator(
            dropbox_path,
            recursive=False,
            include_deleted=include_deleted,
        )

        if long:

            to_short_type = {
                "FileMetadata": "file",
                "FolderMetadata": "folder",
                "DeletedMetadata": "deleted",
            }

            table = cli.Table(
                columns=[
                    cli.Column("Name"),
                    cli.Column("Type"),
                    cli.Column("Size", align=cli.Align.Right),
                    cli.Column("Shared"),
                    cli.Column("Syncing"),
                    cli.Column("Last Modified"),
                ]
            )

            for entries in entries_iter:

                for entry in entries:

                    item_type = to_short_type[cast(str, entry["type"])]
                    name = cast(str, entry["name"])
                    path_lower = cast(str, entry["path_lower"])

                    text = "shared" if "sharing_info" in entry else "private"
                    color = "bright_black" if text == "private" else None
                    shared_field = cli.TextField(text, fg=color)

                    excluded_status = m.excluded_status(path_lower)
                    color = "green" if excluded_status == "included" else None
                    text = "✓" if excluded_status == "included" else excluded_status
                    excluded_field = cli.TextField(text, fg=color)

                    if "size" in entry:
                        size = natural_size(cast(float, entry["size"]))
                    else:
                        size = "-"

                    dt_field: cli.Field

                    if "client_modified" in entry:
                        cm = cast(str, entry["client_modified"])
                        dt_field = cli.DateField(_datetime_from_iso_str(cm))
                    else:
                        dt_field = cli.TextField("-")

                    table.append(
                        [name, item_type, size, shared_field, excluded_field, dt_field]
                    )

            cli.echo(" " * 15)
            table.echo()
            cli.echo(" " * 15)

        else:

            grid = cli.Grid()

            for entries in entries_iter:
                for entry in entries:
                    name = cast(str, entry["name"])
                    color = "blue" if entry["type"] == "DeletedMetadata" else None

                    grid.append(cli.TextField(name, fg=color))

            grid.echo()


@main.command(section="Information", help="List all configured Dropbox accounts.")
@click.option(
    "--clean",
    is_flag=True,
    default=False,
    help="Remove config files without a linked account.",
)
def config_files(clean: bool) -> None:

    from .daemon import is_running
    from .config import (
        MaestralConfig,
        MaestralState,
        list_configs,
        remove_configuration,
    )

    if clean:

        # Clean up stale config files.

        for name in list_configs():
            conf = MaestralConfig(name)
            dbid = conf.get("account", "account_id")

            if dbid == "" and not is_running(name):
                remove_configuration(name)
                cli.echo(f"Removed: {conf.config_path}")

    else:
        # Display config files.
        names = list_configs()
        emails = []
        paths = []

        for name in names:
            conf = MaestralConfig(name)
            state = MaestralState(name)

            emails.append(state.get("account", "email"))
            paths.append(conf.config_path)

        table = cli.Table(
            [
                cli.Column("Config name", names),
                cli.Column("Account", emails),
                cli.Column("Path", paths, elide=cli.Elide.Leading),
            ]
        )

        cli.echo("")
        table.echo()
        cli.echo("")


# ======================================================================================
# Settings
# ======================================================================================


@main.command(
    section="Settings",
    help="""
Automatically start the sync daemon on login.

A systemd or launchd service will be created to start a sync daemon for the given
configuration on user login.
""",
)
@click.option("--yes", "-Y", is_flag=True, default=False)
@click.option("--no", "-N", is_flag=True, default=False)
@existing_config_option
def autostart(yes: bool, no: bool, config_name: str) -> None:

    from .autostart import AutoStart

    auto_start = AutoStart(config_name)

    if not auto_start.implementation:
        cli.echo(
            "Autostart is currently not supported for your platform.\n"
            "Autostart requires systemd on Linux or launchd on macOS."
        )
        return

    if yes or no:
        if yes:
            auto_start.enable()
            cli.ok("Enabled start on login.")
        else:
            auto_start.disable()
            cli.ok("Disabled start on login.")
    else:
        if auto_start.enabled:
            cli.echo("Autostart is enabled. Use -N to disable.")
        else:
            cli.echo("Autostart is disabled. Use -Y to enable.")


@main.group(section="Settings", help="View and manage excluded folders.")
def excluded():
    pass


@excluded.command(name="list", help="List all excluded files and folders.")
@existing_config_option
def excluded_list(config_name: str) -> None:

    from .daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:

        excluded_items = m.excluded_items
        excluded_items.sort()

        if len(excluded_items) == 0:
            cli.echo("No excluded files or folders.")
        else:
            for item in excluded_items:
                cli.echo(item)


@excluded.command(
    name="add",
    help="Add a file or folder to the excluded list and re-sync.",
)
@click.argument("dropbox_path", type=DropboxPath())
@existing_config_option
@convert_api_errors
def excluded_add(dropbox_path: str, config_name: str) -> None:

    from .daemon import MaestralProxy

    if dropbox_path == "/":
        raise cli.CliException("Cannot exclude the root directory.")

    with MaestralProxy(config_name, fallback=True) as m:
        m.exclude_item(dropbox_path)
        cli.ok(f"Excluded '{dropbox_path}'.")


@excluded.command(
    name="remove",
    help="""
Remove a file or folder from the excluded list and re-sync.

It is safe to call this method with items which have already been included, they will
not be downloaded again. If the given path lies inside an excluded folder, the parent
folder will be included as well (but no other items inside it).
""",
)
@click.argument("dropbox_path", type=DropboxPath())
@existing_config_option
@convert_api_errors
def excluded_remove(dropbox_path: str, config_name: str) -> None:

    from .daemon import MaestralProxy, CommunicationError

    if dropbox_path == "/":
        return cli.echo("The root directory is always included")

    try:
        with MaestralProxy(config_name) as m:
            m.include_item(dropbox_path)
            cli.ok(f"Included '{dropbox_path}'. Now downloading...")

    except CommunicationError:
        raise cli.CliException("Daemon must be running to download folders.")


@main.group(section="Settings", help="Manage desktop notifications.")
def notify():
    pass


@notify.command(
    name="level",
    help="Get or set the level for desktop notifications.",
)
@click.argument(
    "level_name",
    required=False,
    type=click.Choice(["ERROR", "SYNCISSUE", "FILECHANGE"], case_sensitive=False),
)
@existing_config_option
def notify_level(level_name: str, config_name: str) -> None:

    from . import notify as _notify
    from .daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:
        if level_name:
            m.notification_level = _notify.level_name_to_number(level_name)
            cli.ok(f"Notification level set to {level_name}.")
        else:
            level_name = _notify.level_number_to_name(m.notification_level)
            cli.echo(f"Notification level: {level_name}.")


@notify.command(
    name="snooze",
    help="Snooze desktop notifications of file changes.",
)
@click.argument("minutes", type=click.IntRange(min=0))
@existing_config_option
def notify_snooze(minutes: int, config_name: str) -> None:

    from .daemon import MaestralProxy, CommunicationError

    try:
        with MaestralProxy(config_name) as m:
            m.notification_snooze = minutes
    except CommunicationError:
        cli.echo("Maestral daemon is not running.")
    else:
        if minutes > 0:
            cli.ok(
                f"Notifications snoozed for {minutes} min. Set snooze to 0 to reset."
            )
        else:
            cli.ok("Notifications enabled.")


# ======================================================================================
# Maintenance
# ======================================================================================


@main.command(section="Maintenance", help="Move the local Dropbox folder.")
@click.argument("new_path", required=False, type=click.Path(writable=True))
@existing_config_option
def move_dir(new_path: str, config_name: str) -> None:

    from .daemon import MaestralProxy

    new_path = new_path or select_dbx_path_dialog(config_name)

    with MaestralProxy(config_name, fallback=True) as m:
        m.move_dropbox_directory(new_path)

    cli.ok(f"Dropbox folder moved to {new_path}.")


@main.command(
    section="Maintenance",
    help="""
Rebuild the sync index.

Rebuilding may take several minutes, depending on the size of your Dropbox.
""",
)
@click.option(
    "--yes", "-Y", is_flag=True, default=False, help="Skip confirmation prompt."
)
@existing_config_option
@convert_api_errors
def rebuild_index(yes: bool, config_name: str) -> None:

    import textwrap
    import shutil
    from .daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:

        width, height = shutil.get_terminal_size()

        msg = textwrap.fill(
            "Rebuilding the index may take several minutes, depending on the size of "
            "your Dropbox. Any changes to local files will be synced once rebuilding "
            "has completed. If you stop the daemon during the process, rebuilding will "
            "start again on the next launch.\nIf the daemon is not currently running, "
            "a rebuild will be scheduled for the next startup.",
            width=width,
        )

        cli.echo(msg + "\n")

        if yes or cli.confirm("Do you want to continue?", default=False):

            m.rebuild_index()

            if m._is_fallback:
                cli.ok("Daemon is not running. Rebuilding scheduled for next startup.")
            else:
                cli.ok("Rebuilding now. Run 'maestral status' to view progress.")


@main.command(section="Maintenance", help="List old file revisions.")
@click.argument("dropbox_path", type=DropboxPath())
@click.option(
    "-l",
    "--limit",
    help="Maximum number of revs to list.",
    show_default=True,
    type=click.IntRange(min=1, max=100),
    default=10,
)
@existing_config_option
@convert_api_errors
def revs(dropbox_path: str, limit: int, config_name: str) -> None:

    from .daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:
        entries = m.list_revisions(dropbox_path, limit=limit)

    table = cli.Table(["Revision", "Modified Time"])

    for entry in entries:

        rev = cast(str, entry["rev"])
        dt = _datetime_from_iso_str(cast(str, entry["client_modified"]))

        table.append([cli.TextField(rev), cli.DateField(dt)])

    cli.echo("")
    table.echo()
    cli.echo("")


@main.command(
    section="Maintenance",
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
@convert_api_errors
@existing_config_option
def diff(
    dropbox_path: str,
    rev: List[str],
    no_color: bool,
    no_pager: bool,
    limit: int,
    config_name: str,
) -> None:

    from .daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:

        # Ask for user input if revs are not provided as CLI arguments.
        if len(rev) == 0:
            entries = m.list_revisions(dropbox_path, limit=limit)

            for entry in entries:
                cm = cast(str, entry["client_modified"])
                field = cli.DateField(_datetime_from_iso_str(cm))
                entry["desc"] = field.format(40)[0]

            dbx_path = cast(str, entries[0]["path_display"])
            local_path = m.to_local_path(dbx_path)

            if osp.isfile(local_path):
                # prepend local version as an option
                entries.insert(0, {"desc": "local version", "rev": None})

            index_base = cli.select(
                message="New revision:",
                options=[e["desc"] for e in entries],
                hint="(↓ to see more)" if len(entries) > 6 else "",
            )

            if index_base == len(entries) - 1:
                cli.warn(
                    "Oldest revision selected, unable to find anything to compare."
                )
                return

            comparable_versions = entries[index_base + 1 :]
            index_new = cli.select(
                message="Old revision:",
                options=[e["desc"] for e in comparable_versions],
                hint="(↓ to see more)" if len(comparable_versions) > 6 else "",
            )

            old_rev = entries[index_new + index_base + 1]["rev"]
            new_rev = entries[index_base]["rev"]
        elif len(rev) == 1:
            old_rev = rev[0]
            new_rev = None
        elif len(rev) == 2:
            old_rev = rev[0]
            new_rev = rev[1]
        elif len(rev) > 2:
            cli.warn("You can only compare two revisions at a time.")
            return

        # Download up to two revisions to a local temporary folder
        # and compare them with a 'diff'. Only text files are supported.
        # If an unknown file type was found, everything that doesn't match
        # 'text/*', an error message gets printed.

        click.echo("Loading ...\r", nl=False)

        diff_output = m.get_file_diff(old_rev, new_rev)

        if len(diff_output) == 0:
            click.echo("There are no changes between the two revisions.")
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
            click.echo_via_pager("".join(diff_output))
        else:
            click.echo("".join(diff_output))


@main.command(
    section="Maintenance",
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
@existing_config_option
@convert_api_errors
def restore(dropbox_path: str, rev: str, limit: int, config_name: str) -> None:

    from .daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:

        if not rev:
            cli.echo("Loading...\r", nl=False)
            entries = m.list_revisions(dropbox_path, limit=limit)
            dates = []
            for entry in entries:
                cm = cast(str, entry["client_modified"])
                field = cli.DateField(_datetime_from_iso_str(cm))
                dates.append(field.format(40)[0])

            index = cli.select(
                message="Select a version to restore:",
                options=dates,
                hint="(↓ to see more)" if len(entries) > 6 else "",
            )
            rev = cast(str, entries[index]["rev"])

        m.restore(dropbox_path, rev)

    cli.ok(f'Restored {rev} to "{dropbox_path}"')


@main.group(section="Maintenance", help="View and manage the log.")
def log():
    pass


@log.command(name="show", help="Print logs to the console.")
@click.option(
    "--external", "-e", is_flag=True, default=False, help="Open in external program."
)
@existing_config_option
def log_show(external: bool, config_name: str) -> None:

    from .utils.appdirs import get_log_path

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
        raise cli.CliException(f"Could not open log file at '{log_file}'")


@log.command(name="clear", help="Clear the log files.")
@existing_config_option
def log_clear(config_name: str) -> None:

    from .utils.appdirs import get_log_path

    log_dir = get_log_path("maestral")
    log_name = config_name + ".log"

    log_files = []

    for file_name in os.listdir(log_dir):
        if file_name.startswith(log_name):
            log_files.append(os.path.join(log_dir, file_name))

    try:
        for file in log_files:
            open(file, "w").close()
        cli.ok("Cleared log files.")
    except FileNotFoundError:
        cli.ok("Cleared log files.")
    except OSError:
        raise cli.CliException(
            f"Could not clear log at '{log_dir}'. " f"Please try to delete it manually"
        )


@log.command(name="level", help="Get or set the log level.")
@click.argument(
    "level_name",
    required=False,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
)
@existing_config_option
def log_level(level_name: str, config_name: str) -> None:

    import logging
    from .daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:
        if level_name:
            m.log_level = cast(int, getattr(logging, level_name))
            cli.ok(f"Log level set to {level_name}.")
        else:
            level_name = logging.getLevelName(m.log_level)
            cli.echo(f"Log level: {level_name}")


@main.group(
    section="Maintenance",
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
def config():
    pass


@config.command(name="get", help="Print the value of a given configuration key.")
@click.argument("key", type=ConfigKey())
@config_option
def config_get(key: str, config_name: str) -> None:

    from .config import MaestralConfig
    from .config.main import KEY_SECTION_MAP
    from .daemon import MaestralProxy, CommunicationError

    # Check if the config key exists in any section.
    section = KEY_SECTION_MAP.get(key, "")

    if not section:
        raise cli.CliException(f"'{key}' is not a valid configuration key.")

    try:
        with MaestralProxy(config_name) as m:
            value = m.get_conf(section, key)
    except CommunicationError:
        value = MaestralConfig(config_name).get(section, key)

    cli.echo(value)


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
@config_option
@convert_api_errors
def config_set(key: str, value: str, config_name: str) -> None:

    import ast
    from .config.main import KEY_SECTION_MAP, DEFAULTS_CONFIG
    from .daemon import MaestralProxy

    section = KEY_SECTION_MAP.get(key, "")

    if not section:
        raise cli.CliException(f"'{key}' is not a valid configuration key.")

    default_value = DEFAULTS_CONFIG[section][key]

    if isinstance(default_value, str):
        py_value = value
    else:
        try:
            py_value = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            py_value = value

    try:
        with MaestralProxy(config_name, fallback=True) as m:
            m.set_conf(section, key, py_value)
    except ValueError as e:
        cli.warn(e.args[0])


@config.command(name="show", help="Show all config keys and values")
@click.option("--no-pager", help="Don't use a pager for output.", is_flag=True)
@config_option
def config_show(no_pager: bool, config_name: str) -> None:

    import io
    from .config import MaestralConfig

    conf = MaestralConfig(config_name)

    with io.StringIO() as fp:
        conf.write(fp)
        if no_pager:
            click.echo(fp.getvalue())
        else:
            click.echo_via_pager(fp.getvalue())


@main.command(
    section="Maintenance",
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

    from click.shell_completion import get_completion_class

    comp_cls = get_completion_class(shell)

    if comp_cls is None:
        cli.warn(f"{shell} shell is currently not supported")
        return

    comp = comp_cls(main, {}, "maestral", "_MAESTRAL_COMPLETE")

    try:
        click.echo(comp.source())
    except RuntimeError as exc:
        cli.warn(exc.args[0])
