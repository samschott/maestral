from __future__ import annotations

import sys
import threading
from datetime import datetime
from os import path as osp
from typing import TYPE_CHECKING

import click

from rich.console import Console, ConsoleRenderable

from .dialogs import select_path, select, confirm, prompt, select_multiple
from .output import warn, ok, info, echo, RichDateField, rich_table
from .common import (
    convert_api_errors,
    check_for_fatal_errors,
    config_option,
    existing_config_option,
    inject_proxy,
)
from .core import DropboxPath, CliException
from ..core import FolderMetadata, SharedLinkMetadata
from ..utils.path import delete

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

    echo("Stopping Maestral...", nl=False)
    res = stop_maestral_daemon_process(config_name)
    if res == Stop.Ok:
        echo("\rStopping Maestral...        " + OK)
    elif res == Stop.NotRunning:
        echo("\rMaestral daemon is not running.")
    elif res == Stop.Killed:
        echo("\rStopping Maestral...        " + KILLED)
    elif res == Stop.Failed:
        echo("\rStopping Maestral...        " + FAILED)


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
        echo("Daemon is already running.")
        return

    @convert_api_errors
    def startup_dialog() -> None:
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

                names = [e.name for e in entries if isinstance(e, FolderMetadata)]

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
    import termios

    from packaging.version import Version
    from packaging.requirements import Requirement
    from importlib_metadata import entry_points, requires, version

    # Find all entry points for "maestral_gui" registered by other packages.
    gui_entry_points = entry_points(group="maestral_gui")

    if len(gui_entry_points) == 0:
        raise CliException(
            "No maestral GUI installed. Please run 'pip3 install maestral[gui]'."
        )

    entry_point_names = [e.name for e in gui_entry_points]

    if len(entry_point_names) > 1 and sys.stdout.isatty():
        try:
            index = select("Multiple GUIs found, please choose:", entry_point_names)
        except termios.error:
            # Error can occur when not connected to a terminal. Fall back to the first
            # detected GUI instead of failing with an error.
            index = 0
    else:
        index = 0

    entry_point = gui_entry_points[entry_point_names[index]]

    if entry_point in {"maestral_cocoa", "maestral_qt"}:
        # For 1st party GUIs "maestral_cocoa" or "maestral_qt", check if the installed
        # version fulfills requirements in maestral's gui extra.
        requirement_names = requires("maestral")
        if requirement_names is not None:
            for name in requirement_names:
                r = Requirement(name)
                if r.marker and r.marker.evaluate({"extra": "gui"}):
                    version_str = version(r.name)
                    if not r.specifier.contains(Version(version_str), prereleases=True):
                        raise CliException(
                            f"{r.name}{r.specifier} required but you have {version_str}"
                        )

    # Run the GUI.
    run = entry_point.load()
    run(config_name)


@click.command(help="Pause syncing.")
@inject_proxy(fallback=False, existing_config=True)
def pause(m: Maestral) -> None:
    m.stop_sync()
    ok("Syncing paused.")


@click.command(help="Resume syncing.")
@inject_proxy(fallback=False, existing_config=True)
def resume(m: Maestral) -> None:
    if not check_for_fatal_errors(m):
        m.start_sync()
        ok("Syncing resumed.")


@click.group(help="Link, unlink and view the Dropbox account.")
def auth() -> None:
    pass


@auth.command(name="link", help="Link a new Dropbox account.")
@click.option(
    "--relink",
    "-r",
    is_flag=True,
    default=False,
    help="Relink to the existing account. Keeps the sync state.",
)
@click.option(
    "--refresh-token",
    hidden=True,
    help="Refresh token to bypass OAuth exchange.",
)
@click.option(
    "--access-token",
    hidden=True,
    help="Access token to bypass OAuth exchange.",
)
@inject_proxy(fallback=True, existing_config=False)
@convert_api_errors
def auth_link(
    m: Maestral, relink: bool, refresh_token: str | None, access_token: str | None
) -> None:
    if m.pending_link or relink:
        if refresh_token or access_token:
            m.link(refresh_token=refresh_token, access_token=access_token)
        else:
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
def sharelink() -> None:
    pass


@sharelink.command(
    name="create", help="Create a shared link for a file or folder. Return the URL."
)
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
@inject_proxy(fallback=True, existing_config=True)
@convert_api_errors
def sharelink_create(
    m: Maestral,
    dropbox_path: str,
    password: str,
    expiry: datetime | None,
) -> None:
    link_info = m.create_shared_link(dropbox_path, password=password, expires=expiry)
    echo(link_info.url)


@sharelink.command(name="revoke", help="Revoke a shared link.")
@click.argument("url", nargs=-1, required=True)
@inject_proxy(fallback=True, existing_config=True)
@convert_api_errors
def sharelink_revoke(m: Maestral, url: list[str]) -> None:
    for u in url:
        m.revoke_shared_link(u)
        ok(f"Revoked shared link {u}")


@sharelink.command(
    name="list", help="List shared links for given paths or all shared links."
)
@click.argument("dropbox_path", nargs=-1, type=DropboxPath())
@click.option(
    "-l",
    "--long",
    is_flag=True,
    default=False,
    help="Show output in long format with metadata.",
)
@inject_proxy(fallback=True, existing_config=True)
@convert_api_errors
def sharelink_list(m: Maestral, dropbox_path: list[str], long: bool) -> None:
    links: list[SharedLinkMetadata]

    if len(dropbox_path) > 0:
        links = []
        for dbx_path in dropbox_path:
            links.extend(m.list_shared_links(dbx_path))
    else:
        links = m.list_shared_links()

    if long:
        link_table = rich_table("URL", "Item", "Access", "Expires")

        for link in links:
            dt_field: ConsoleRenderable | str

            if link.expires:
                dt_field = RichDateField(link.expires)
            else:
                dt_field = "-"

            if link.link_permissions.require_password:
                access = "password"
            else:
                access = link.link_permissions.effective_audience.value

            link_table.add_row(link.url, link.name, access, dt_field)

        console = Console()
        console.print(link_table)

    else:
        echo("\n".join(link.url for link in links))
