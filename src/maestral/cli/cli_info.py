from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from typing import TYPE_CHECKING

import click

from .output import echo, Column, Table, Elide, Align, TextField, Field, DateField, Grid
from .common import convert_api_errors, check_for_fatal_errors, inject_proxy
from .core import DropboxPath
from ..models import SyncDirection, SyncStatus
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

    import curses
    from ..utils import natural_size

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

                filename = os.path.basename(event.dbx_path)
                filenames.append(filename)

                arrow = "↓" if event.direction is SyncDirection.Down else "↑"

                if event.completed > 0:
                    done_str = natural_size(event.completed, sep=False)
                    todo_str = natural_size(event.size, sep=False)
                    states.append(f"{done_str}/{todo_str} {arrow}")
                else:
                    if event.status is SyncStatus.Syncing:
                        if event.direction is SyncDirection.Up:
                            states.append("uploading")
                        else:
                            states.append("downloading")
                    else:
                        states.append(event.status.value)

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
