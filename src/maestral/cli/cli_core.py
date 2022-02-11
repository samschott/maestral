from __future__ import annotations

import sys
from datetime import datetime
from os import path as osp
from typing import cast, TYPE_CHECKING

import click

from .dialogs import select_path, select, confirm, prompt, select_multiple
from .output import warn, ok, info, echo, Table, Field, DateField, TextField
from .utils import datetime_from_iso_str
from .common import (
    convert_api_errors,
    check_for_fatal_errors,
    config_option,
    existing_config_option,
)
from .core import DropboxPath, CliException

if TYPE_CHECKING:
    from ..daemon import MaestralProxy
    from ..main import Maestral


OK = click.style("[OK]", fg="green")
FAILED = click.style("[FAILED]", fg="red")
KILLED = click.style("[KILLED]", fg="red")


def stop_daemon_with_cli_feedback(config_name: str) -> None:
    """Wrapper around :meth:`daemon.stop_maestral_daemon_process`
    with command line feedback."""

    from ..daemon import stop_maestral_daemon_process, Stop

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
    config_name: str, default_dir_name: str | None = None, allow_merge: bool = False
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

    from ..utils.path import delete

    default_dir_name = default_dir_name or f"Dropbox ({config_name.capitalize()})"

    while True:
        res = select_path(
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
                choice = select(text, options=["replace", "merge", "cancel"])
            else:
                text = (
                    "Directory already exists. Do you want to replace it? "
                    "Its content will be lost!"
                )
                replace = confirm(text)
                choice = 0 if replace else 2

            if choice == 0:
                err = delete(dropbox_path)
                if err:
                    warn(
                        "Could not write to selected location. "
                        "Please make sure that you have sufficient permissions."
                    )
                else:
                    ok("Replaced existing folder")
                    return dropbox_path
            elif choice == 1:
                ok("Merging with existing folder")
                return dropbox_path

        else:
            return dropbox_path


def link_dialog(m: MaestralProxy | Maestral) -> None:
    """
    A CLI dialog for linking a Dropbox account.

    :param m: Proxy to Maestral daemon.
    """

    authorize_url = m.get_auth_url()

    info(f"Linking new account for '{m.config_name}' config")
    info("Retrieving auth code from Dropbox")
    choice = select(
        "How would you like to you link your account?",
        options=["Open Dropbox website", "Print auth URL to console"],
    )

    if choice == 0:
        click.launch(authorize_url)
    else:
        info("Open the URL below to retrieve an auth code:")
        info(authorize_url)

    res = -1
    while res != 0:
        auth_code = prompt("Enter the auth code:")
        auth_code = auth_code.strip()

        res = m.link(auth_code)

        if res == 0:
            email = m.get_state("account", "email")
            ok(f"Linked to {email}")
        elif res == 1:
            warn("Invalid token, please try again")
        elif res == 2:
            warn("Could not connect to Dropbox, please try again")


@click.command(help="Start the sync daemon.")
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
    from ..daemon import (
        MaestralProxy,
        start_maestral_daemon,
        start_maestral_daemon_process,
        wait_for_startup,
        is_running,
        Start,
        CommunicationError,
    )

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
                    warn(
                        "Could not create folder. Please make sure that you have "
                        "permissions to write to the selected location or choose a "
                        "different location."
                    )

            include_all = confirm("Would you like sync all folders?")

            if not include_all:
                # get all top-level Dropbox folders
                info("Loading...")
                entries = m.list_folder("/", recursive=False)

                names = [
                    cast(str, e["name"])
                    for e in entries
                    if e["type"] == "FolderMetadata"
                ]

                choices = select_multiple(
                    "Choose which folders to include", options=names
                )

                excluded_paths = [
                    f"/{name}"
                    for index, name in enumerate(names)
                    if index not in choices
                ]

                m.excluded_items = excluded_paths

            ok("Setup completed. Starting sync.")

        m.start_sync()

    if foreground:

        setup_thread = threading.Thread(target=startup_dialog, daemon=True)
        setup_thread.start()

        start_maestral_daemon(config_name, log_to_stderr=verbose)

    else:
        echo("Starting Maestral...", nl=False)

        res = start_maestral_daemon_process(config_name)

        if res == Start.Ok:
            echo("\rStarting Maestral...        " + OK)
        elif res == Start.AlreadyRunning:
            echo("\rStarting Maestral...        " + "Already running.")
        else:
            echo("\rStarting Maestral...        " + FAILED)
            echo("Please check logs for more information.")

        startup_dialog()


@click.command(help="Stop the sync daemon.")
@existing_config_option
def stop(config_name: str) -> None:
    stop_daemon_with_cli_feedback(config_name)


@click.command(help="Run the GUI if installed.")
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
        raise CliException(
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
                    raise CliException(
                        f"{r.name}{r.specifier} required but you have {version_str}"
                    )

        # load entry point
        run = default_entry_point.load()

    else:
        # load any 3rd party GUI
        fallback_entry_point = next(iter(gui_entry_points))
        run = fallback_entry_point.load()

    run(config_name)


@click.command(help="Pause syncing.")
@existing_config_option
def pause(config_name: str) -> None:

    from ..daemon import MaestralProxy, CommunicationError

    try:
        with MaestralProxy(config_name) as m:
            m.stop_sync()
        ok("Syncing paused.")
    except CommunicationError:
        echo("Maestral daemon is not running.")


@click.command(help="Resume syncing.")
@existing_config_option
def resume(config_name: str) -> None:

    from ..daemon import MaestralProxy, CommunicationError

    try:
        with MaestralProxy(config_name) as m:
            if not check_for_fatal_errors(m):
                m.start_sync()
                ok("Syncing resumed.")

    except CommunicationError:
        echo("Maestral daemon is not running.")


@click.group(help="Link, unlink and view the Dropbox account.")
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

    from ..daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:

        if m.pending_link or relink:
            link_dialog(m)
        else:
            echo(
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
        yes = confirm("Are you sure you want unlink your account?", default=False)

    if yes:
        from ..main import Maestral

        stop_daemon_with_cli_feedback(config_name)
        m = Maestral(config_name)
        m.unlink()

        ok("Unlinked Maestral.")


@auth.command(name="status", help="View authentication status.")
@existing_config_option
def auth_status(config_name: str) -> None:

    from ..config import MaestralConfig, MaestralState

    conf = MaestralConfig(config_name)
    state = MaestralState(config_name)

    dbid = conf.get("auth", "account_id")
    email = state.get("account", "email")
    account_type = state.get("account", "type").capitalize()

    echo("")
    echo(f"Email:         {email}")
    echo(f"Account type:  {account_type}")
    echo(f"Dropbox ID:    {dbid}")
    echo("")


@click.group(help="Create and manage shared links.")
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
    expiry: datetime | None,
    config_name: str,
) -> None:

    from ..daemon import MaestralProxy

    expiry_dt: float | None

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

    echo(link_info["url"])


@sharelink.command(name="revoke", help="Revoke a shared link.")
@click.argument("url")
@existing_config_option
@convert_api_errors
def sharelink_revoke(url: str, config_name: str) -> None:

    from ..daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:
        m.revoke_shared_link(url)

    ok("Revoked shared link.")


@sharelink.command(
    name="list", help="List shared links for a path or all shared links."
)
@click.argument("dropbox_path", required=False, type=DropboxPath())
@existing_config_option
@convert_api_errors
def sharelink_list(dropbox_path: str | None, config_name: str) -> None:

    from ..daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:
        links = m.list_shared_links(dropbox_path)

    link_table = Table(["URL", "Item", "Access", "Expires"])

    for link in links:
        url = cast(str, link["url"])
        file_name = cast(str, link["name"])
        visibility = cast(str, link["link_permissions"]["resolved_visibility"][".tag"])

        dt_field: Field

        if "expires" in link:
            expires = cast(str, link["expires"])
            dt_field = DateField(datetime_from_iso_str(expires))
        else:
            dt_field = TextField("-")

        link_table.append([url, file_name, visibility, dt_field])

    echo("")
    link_table.echo()
    echo("")
