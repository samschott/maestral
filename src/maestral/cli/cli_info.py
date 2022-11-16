from __future__ import annotations

import sys
import time
from datetime import datetime
from typing import TYPE_CHECKING, Tuple

import click

from .output import echo, Column, Table, Elide, Align, TextField, Field, DateField, Grid
from .common import convert_api_errors, check_for_fatal_errors, inject_proxy
from .core import DropboxPath
from ..models import SyncDirection, SyncEvent
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
    usage_str = usage or "--"

    n_errors = len(m.sync_errors)
    color = "red" if n_errors > 0 else "green"
    n_errors_str = click.style(str(n_errors), fg=color)

    echo("")
    echo(f"Account:      {account_str}")
    echo(f"Usage:        {usage_str}")
    echo(f"Status:       {status_info}")
    echo(f"Sync errors:  {n_errors_str}")
    echo("")

    check_for_fatal_errors(m)

    sync_errors = m.sync_errors

    if len(sync_errors) > 0:

        path_column = Column(title="Path")
        message_column = Column(title="Error", wraps=True)

        for error in sync_errors:
            path_column.append(error.dbx_path)
            message_column.append(f"{error.title}. {error.message}")

        table = Table([path_column, message_column])

        table.echo()
        echo("")


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

    from rich.console import Console
    from rich.progress import Progress, TextColumn, BarColumn, DownloadColumn, TaskID

    if check_for_fatal_errors(m):
        return

    EventKey = Tuple[str, SyncDirection]

    progressbar_for_path: dict[EventKey, tuple[TaskID, SyncEvent]] = {}
    new_progressbar_for_path: dict[EventKey, tuple[TaskID, SyncEvent]] = {}
    progress_bars_to_clear: set[TaskID] = set()

    def _event_key(e: SyncEvent) -> EventKey:
        return e.dbx_path, e.direction

    console = Console()

    with console.screen():
        with Progress(
            TextColumn("[bold bright_blue]{task.description}"),
            TextColumn("[bright_blue]{task.fields[filename]}"),
            " ",
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            DownloadColumn(),
            auto_refresh=False,
            console=console,
        ) as progress:
            status_msg = f"\rStatus: {m.status}, Sync errors: {len(m.sync_errors)}"
            progress.console.print(status_msg)

            while True:
                status_msg = f"\rStatus: {m.status}, Sync errors: {len(m.sync_errors)}"
                progress.console.clear()
                progress.console.print(status_msg)

                for event in m.get_activity():
                    try:
                        task_id, _ = progressbar_for_path.pop(_event_key(event))
                    except KeyError:
                        arrow = "↓" if event.direction is SyncDirection.Down else "↑"
                        description = f"{arrow} {event.change_type.name}"
                        task_id = progress.add_task(
                            description,
                            total=event.size,
                            completed=event.completed,
                            filename=event.dbx_path,
                        )
                    else:
                        progress.update(task_id, completed=event.completed)
                    new_progressbar_for_path[_event_key(event)] = (task_id, event)

                for task_id in progress_bars_to_clear:
                    progress.remove_task(task_id)
                progress_bars_to_clear.clear()

                while len(progressbar_for_path) > 0:
                    _, task_tuple = progressbar_for_path.popitem()
                    task_id, event = task_tuple
                    progress.update(task_id, completed=event.size)
                    progress_bars_to_clear.add(task_id)

                progressbar_for_path.update(new_progressbar_for_path)
                new_progressbar_for_path.clear()

                time.sleep(0.5)
                progress.refresh()


@click.command(help="Show recently changed or added files.")
@inject_proxy(fallback=True, existing_config=True)
@convert_api_errors
def history(m: Maestral) -> None:
    events = m.get_history()

    table = Table(
        [
            Column("Path", elide=Elide.Leading),
            Column("Change"),
            Column("Time"),
        ]
    )

    for event in events:
        dt_local_naive = datetime.fromtimestamp(event.change_time_or_sync_time)
        dt_field = DateField(dt_local_naive)

        table.append([event.dbx_path, event.change_type.value, dt_field])

    echo("")
    table.echo()
    echo("")


@click.command(help="List contents of a Dropbox directory.")
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
@inject_proxy(fallback=True, existing_config=True)
@convert_api_errors
def ls(m: Maestral, long: bool, dropbox_path: str, include_deleted: bool) -> None:

    from ..utils import natural_size

    echo("Loading...\r", nl=False)

    entries_iter = m.list_folder_iterator(
        dropbox_path,
        recursive=False,
        include_deleted=include_deleted,
    )

    if long:

        table = Table(
            columns=[
                Column("Name"),
                Column("Type"),
                Column("Size", align=Align.Right),
                Column("Shared"),
                Column("Syncing"),
                Column("Last Modified"),
            ]
        )

        for entries in entries_iter:

            for entry in entries:

                text = "shared" if getattr(entry, "shared", False) else "private"
                color = "bright_black" if text == "private" else None
                shared_field = TextField(text, fg=color)

                excluded_status = m.excluded_status(entry.path_lower)
                color = "green" if excluded_status == "included" else None
                text = "✓" if excluded_status == "included" else excluded_status
                excluded_field = TextField(text, fg=color)

                dt_field: Field

                if isinstance(entry, FileMetadata):
                    size = natural_size(entry.size)
                    dt_field = DateField(entry.client_modified)
                    item_type = "file"
                elif isinstance(entry, FolderMetadata):
                    size = "-"
                    dt_field = TextField("-")
                    item_type = "folder"
                else:
                    size = "-"
                    dt_field = TextField("-")
                    item_type = "deleted"

                table.append(
                    [
                        entry.name,
                        item_type,
                        size,
                        shared_field,
                        excluded_field,
                        dt_field,
                    ]
                )

        echo(" " * 15)
        table.echo()
        echo(" " * 15)

    if not sys.stdout.isatty():
        names = [entry.name for entries in entries_iter for entry in entries]
        echo("\n".join(names))

    else:
        grid = Grid()

        for entries in entries_iter:
            for entry in entries:
                color = "blue" if isinstance(entry, DeletedMetadata) else None
                grid.append(TextField(entry.name, fg=color))

        grid.echo()


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
        names = list_configs()
        emails = []
        paths = []

        for name in names:
            conf = MaestralConfig(name)
            state = MaestralState(name)

            emails.append(state.get("account", "email"))
            paths.append(conf.config_path)

        table = Table(
            [
                Column("Config name", names),
                Column("Account", emails),
                Column("Path", paths, elide=Elide.Leading),
            ]
        )

        echo("")
        table.echo()
        echo("")
