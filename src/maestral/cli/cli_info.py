from __future__ import annotations

import sys
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Tuple

import click
from rich.console import Console, ConsoleRenderable
from rich.table import Column
from rich.text import Text
from rich.columns import Columns
from rich.filesize import decimal
from rich.progress import Progress, TextColumn, BarColumn, DownloadColumn, TaskID

from .output import echo, RichDateField, rich_table
from .common import convert_api_errors, check_for_fatal_errors, inject_proxy
from .core import DropboxPath
from ..models import SyncDirection, SyncEvent, SyncStatus
from ..core import FileMetadata, FolderMetadata, DeletedMetadata

if TYPE_CHECKING:
    from ..main import Maestral


@click.command(help="Show the status of the daemon.")
@inject_proxy(fallback=False, existing_config=True)
@convert_api_errors
def status(m: Maestral) -> None:
    email = m.get_state("account", "email")
    account_type = m.get_state("account", "type").capitalize()
    usage = m.get_state("account", "usage")
    status_info = m.status

    account_str = f"{email} ({account_type})" if email else "--"
    usage_str = Text(usage or "--")

    n_errors = len(m.sync_errors)
    color = "red" if n_errors > 0 else "green"
    n_errors_str = Text(str(n_errors), style=color)

    status_table = rich_table()
    status_table.add_row("Account", account_str)
    status_table.add_row("Usage", usage_str)
    status_table.add_row("Status", status_info)
    status_table.add_row("Sync errors", n_errors_str)

    console = Console()

    console.print("")
    console.print(status_table, highlight=False)
    console.print("")

    check_for_fatal_errors(m)

    sync_errors = m.sync_errors

    if len(sync_errors) > 0:
        sync_errors_table = rich_table("Path", "Error")

        for error in sync_errors:
            sync_errors_table.add_row(error.dbx_path, f"{error.title}. {error.message}")

        console.print(sync_errors_table)
        console.print("")


@click.command(
    help="""
Show the sync status of a local file or folder.

Returned value will be 'uploading', 'downloading', 'up to date', 'error', or 'unwatched'
(for files outside of the Dropbox directory). This will always be 'unwatched' if syncing
is paused. This command can be used to for instance to query information for a plugin to
a file-manager.
""",
)
@click.argument("local_path", type=click.Path(exists=True, resolve_path=True))
@inject_proxy(fallback=True, existing_config=True)
@convert_api_errors
def filestatus(m: Maestral, local_path: str) -> None:
    stat = m.get_file_status(local_path)
    echo(stat)


@click.command(help="Live view of all items being synced.")
@inject_proxy(fallback=False, existing_config=True)
@convert_api_errors
def activity(m: Maestral) -> None:
    if check_for_fatal_errors(m):
        return

    from Pyro5.errors import ConnectionClosedError

    EventKey = Tuple[str, SyncDirection]
    progressbar_for_path: dict[EventKey, TaskID] = {}

    def _event_key(e: SyncEvent) -> EventKey:
        return e.dbx_path, e.direction

    console = Console()

    arrow = {SyncDirection.Up: "↑", SyncDirection.Down: "↓"}

    try:
        with console.screen():
            with Progress(
                TextColumn("[bold bright_blue]{task.description}"),
                TextColumn("[bright_blue]{task.fields[filename]}"),
                TextColumn(" "),
                BarColumn(bar_width=None),
                TextColumn("[progress.percentage]{task.percentage:>3.1f}%"),
                TextColumn("•"),
                DownloadColumn(),
                auto_refresh=False,
                console=console,
            ) as progress:
                while True:
                    msg = f"\rStatus: {m.status}, Sync errors: {len(m.sync_errors)}"
                    progress.console.clear()
                    progress.console.print(msg)

                    sync_events = m.get_activity(limit=console.height - 1)
                    event_keys = set(_event_key(e) for e in sync_events)

                    for key, task_id in progressbar_for_path.copy().items():
                        if key not in event_keys:
                            progress.remove_task(task_id)
                            progressbar_for_path.pop(key)

                    for event in sync_events:
                        if event.status is SyncStatus.Failed:
                            info = "! Sync Error"
                        else:
                            info = f"{arrow[event.direction]} {event.change_type.name}"
                        try:
                            task_id = progressbar_for_path[_event_key(event)]
                        except KeyError:
                            task_id = progress.add_task(
                                info,
                                total=event.size,
                                completed=event.completed,
                                filename=os.path.basename(event.dbx_path),
                            )
                            progressbar_for_path[_event_key(event)] = task_id
                        else:
                            progress.update(
                                task_id, completed=event.completed, description=info
                            )

                    time.sleep(0.2)
                    progress.refresh()
    except ConnectionClosedError:
        return echo("Maestral daemon is not running.")


@click.command(help="Show sync history.")
@click.argument("dropbox_path", type=DropboxPath(), default="/")
@inject_proxy(fallback=True, existing_config=True)
@convert_api_errors
def history(m: Maestral, dropbox_path: str) -> None:
    dbx_path = None if dropbox_path == "/" else dropbox_path
    events = m.get_history(dbx_path)
    table = rich_table("Path", "Change", "Location", "Time")

    for event in events:
        dt_local_naive = datetime.fromtimestamp(event.change_time_or_sync_time)
        location = "local" if event.direction is SyncDirection.Up else "remote"
        table.add_row(
            Text(event.dbx_path, overflow="ellipsis", no_wrap=True),
            Text(event.change_type.value),
            Text(location),
            RichDateField(dt_local_naive),
        )

    console = Console()
    console.print(table)


@click.command(help="List contents of a Dropbox directory.")
@click.argument("dropbox_path", type=DropboxPath(), default="/")
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
@inject_proxy(fallback=True, existing_config=True)
@convert_api_errors
def ls(m: Maestral, long: bool, dropbox_path: str, include_deleted: bool) -> None:
    echo("Loading...\r", nl=False)

    entries_iter = m.list_folder_iterator(
        dropbox_path,
        recursive=False,
        include_deleted=include_deleted,
    )

    entries = [entry for entries in entries_iter for entry in entries]
    entries.sort(key=lambda e: e.name)

    console = Console()

    if long:
        table = rich_table(
            Column("Name"),
            Column("Type"),
            Column("Size", justify="right"),
            Column("Shared"),
            Column("Syncing"),
            Column("Last Modified"),
        )

        for entry in entries:
            text = "shared" if getattr(entry, "shared", False) else "private"
            color = "bright_black" if text == "private" else ""
            shared_field = Text(text, style=color)

            excluded_status = m.excluded_status(entry.path_lower)
            color = "green" if excluded_status == "included" else ""
            text = "✓" if excluded_status == "included" else excluded_status
            excluded_field = Text(text, style=color)

            dt_field: ConsoleRenderable

            if isinstance(entry, FileMetadata):
                dt_field = RichDateField(entry.client_modified)
                if entry.symlink_target is None:
                    size = decimal(entry.size)
                    item_type = "file"
                else:
                    size = "-"
                    item_type = "symlink"
            elif isinstance(entry, FolderMetadata):
                size = "-"
                dt_field = Text("-")
                item_type = "folder"
            else:
                size = "-"
                dt_field = Text("-")
                item_type = "deleted"

            table.add_row(
                Text(entry.name, overflow="ellipsis", no_wrap=True),
                item_type,
                size,
                shared_field,
                excluded_field,
                dt_field,
            )

        console.print(table)

    elif not sys.stdout.isatty():
        names = [entry.name for entries in entries_iter for entry in entries]
        console.print("\n".join(names))

    else:
        fields: list[Text] = []

        for entry in entries:
            color = "blue" if isinstance(entry, DeletedMetadata) else ""
            fields.append(Text(entry.name, style=color))

        max_len = max(len(f) for f in fields)
        console.print(Columns(fields, width=max_len, column_first=True))


@click.command(help="List all configured Dropbox accounts.")
@click.option(
    "--clean",
    is_flag=True,
    default=False,
    help="Remove config files without a linked account.",
)
def config_files(clean: bool) -> None:
    from ..daemon import is_running
    from ..config import (
        MaestralConfig,
        MaestralState,
        list_configs,
        remove_configuration,
    )

    if clean:
        # Clean up stale config files.
        for name in list_configs():
            conf = MaestralConfig(name)
            dbid = conf.get("auth", "account_id")

            if dbid == "" and not is_running(name):
                remove_configuration(name)
                echo(f"Removed: {conf.config_path}")

    else:
        # Display config files.
        table = rich_table("Config name", "Account", "Path")
        for name in list_configs():
            conf = MaestralConfig(name)
            state = MaestralState(name)

            table.add_row(
                name,
                state.get("account", "email"),
                Text(conf.config_path, overflow="ellipsis", no_wrap=True),
            )

        console = Console()
        console.print(table)
