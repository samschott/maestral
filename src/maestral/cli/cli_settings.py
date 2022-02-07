import click

from .output import echo, ok
from .common import convert_api_errors, existing_config_option
from .core import DropboxPath, CliException


@click.command(
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

    from ..autostart import AutoStart

    auto_start = AutoStart(config_name)

    if not auto_start.implementation:
        echo(
            "Autostart is currently not supported for your platform.\n"
            "Autostart requires systemd on Linux or launchd on macOS."
        )
        return

    if yes or no:
        if yes:
            auto_start.enable()
            ok("Enabled start on login.")
        else:
            auto_start.disable()
            ok("Disabled start on login.")
    else:
        if auto_start.enabled:
            echo("Autostart is enabled. Use -N to disable.")
        else:
            echo("Autostart is disabled. Use -Y to enable.")


@click.group(help="View and manage excluded folders.")
def excluded():
    pass


@excluded.command(name="list", help="List all excluded files and folders.")
@existing_config_option
def excluded_list(config_name: str) -> None:

    from ..daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:

        excluded_items = m.excluded_items
        excluded_items.sort()

        if len(excluded_items) == 0:
            echo("No excluded files or folders.")
        else:
            for item in excluded_items:
                echo(item)


@excluded.command(
    name="add",
    help="Add a file or folder to the excluded list and re-sync.",
)
@click.argument("dropbox_path", type=DropboxPath())
@existing_config_option
@convert_api_errors
def excluded_add(dropbox_path: str, config_name: str) -> None:

    from ..daemon import MaestralProxy

    if dropbox_path == "/":
        raise CliException("Cannot exclude the root directory.")

    with MaestralProxy(config_name, fallback=True) as m:
        m.exclude_item(dropbox_path)
        ok(f"Excluded '{dropbox_path}'.")


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

    from ..daemon import MaestralProxy, CommunicationError

    if dropbox_path == "/":
        return echo("The root directory is always included")

    try:
        with MaestralProxy(config_name) as m:
            m.include_item(dropbox_path)
            ok(f"Included '{dropbox_path}'. Now downloading...")

    except CommunicationError:
        raise CliException("Daemon must be running to download folders.")


@click.group(help="Manage desktop notifications.")
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

    from .. import notify as _notify
    from ..daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:
        if level_name:
            m.notification_level = _notify.level_name_to_number(level_name)
            ok(f"Notification level set to {level_name}.")
        else:
            level_name = _notify.level_number_to_name(m.notification_level)
            echo(f"Notification level: {level_name}.")


@notify.command(
    name="snooze",
    help="Snooze desktop notifications of file changes.",
)
@click.argument("minutes", type=click.IntRange(min=0))
@existing_config_option
def notify_snooze(minutes: int, config_name: str) -> None:

    from ..daemon import MaestralProxy, CommunicationError

    try:
        with MaestralProxy(config_name) as m:
            m.notification_snooze = minutes
    except CommunicationError:
        echo("Maestral daemon is not running.")
    else:
        if minutes > 0:
            ok(f"Notifications snoozed for {minutes} min. Set snooze to 0 to reset.")
        else:
            ok("Notifications enabled.")
