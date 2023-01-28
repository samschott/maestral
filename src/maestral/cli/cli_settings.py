from __future__ import annotations

from typing import TYPE_CHECKING

import click

from .output import echo, ok
from .common import convert_api_errors, existing_config_option, inject_proxy
from .core import DropboxPath, CliException

if TYPE_CHECKING:
    from ..main import Maestral


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
def excluded() -> None:
    pass


@excluded.command(name="list", help="List all excluded files and folders.")
@inject_proxy(fallback=True, existing_config=True)
def excluded_list(m: Maestral) -> None:
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
@inject_proxy(fallback=True, existing_config=True)
@convert_api_errors
def excluded_add(m: Maestral, dropbox_path: str) -> None:
    if dropbox_path == "/":
        raise CliException("Cannot exclude the root directory.")

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
@inject_proxy(fallback=False, existing_config=True)
@convert_api_errors
def excluded_remove(m: Maestral, dropbox_path: str) -> None:
    if dropbox_path == "/":
        return echo("The root directory is always included")

    m.include_item(dropbox_path)
    ok(f"Included '{dropbox_path}'. Now downloading...")


@click.group(help="Manage desktop notifications.")
def notify() -> None:
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
@inject_proxy(fallback=True, existing_config=True)
def notify_level(m: Maestral, level_name: str) -> None:
    from .. import notify as _notify

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
@inject_proxy(fallback=True, existing_config=True)
def notify_snooze(m: Maestral, minutes: int) -> None:
    m.notification_snooze = minutes

    if minutes > 0:
        ok(f"Notifications snoozed for {minutes} min. Set snooze to 0 to reset.")
    else:
        ok("Notifications enabled.")


@click.group(help="View and manage bandwidth limits. Changes take effect immediately.")
def bandwidth_limit() -> None:
    pass


@bandwidth_limit.command(
    name="up", help="Get / set bandwidth limit for uploads in MB/sec (0 = unlimited)."
)
@click.argument(
    "mb_per_second",
    required=False,
    type=click.FLOAT,
)
@inject_proxy(fallback=True, existing_config=True)
def bandwidth_limit_up(m: Maestral, mb_per_second: float | None) -> None:
    if mb_per_second is not None:
        m.bandwidth_limit_up = mb_per_second * 10**6
        speed_str = f"{mb_per_second} MB/sec" if mb_per_second > 0 else "unlimited"
        ok(f"Upload bandwidth limit set to {speed_str}.")
    else:
        mb_per_second = m.bandwidth_limit_up / 10**6
        echo(f"{mb_per_second} MB/sec" if mb_per_second > 0 else "unlimited")


@bandwidth_limit.command(
    name="down",
    help="Get / set bandwidth limit for downloads in MB/sec (0 = unlimited).",
)
@click.argument(
    "mb_per_second",
    required=False,
    type=click.FLOAT,
)
@inject_proxy(fallback=True, existing_config=True)
def bandwidth_limit_down(m: Maestral, mb_per_second: float | None) -> None:
    if mb_per_second is not None:
        m.bandwidth_limit_down = mb_per_second * 10**6
        speed_fmt = f"{mb_per_second} MB/sec" if mb_per_second > 0 else "unlimited"
        ok(f"Download bandwidth limit set to {speed_fmt}.")
    else:
        mb_per_second = m.bandwidth_limit_down / 10**6
        echo(f"{mb_per_second} MB/sec" if mb_per_second > 0 else "unlimited")
