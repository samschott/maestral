import os
import time
from typing import cast

import click

from .output import echo, Column, Table, Elide, Align, TextField, Field, DateField, Grid
from .utils import datetime_from_iso_str
from .common import convert_api_errors, check_for_fatal_errors, existing_config_option
from .core import DropboxPath


@click.command(help="Show the status of the daemon.")
@existing_config_option
@convert_api_errors
def status(config_name: str) -> None:

    from ..daemon import MaestralProxy, CommunicationError

    try:
        with MaestralProxy(config_name) as m:

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
                    path_column.append(error["dbx_path"])
                    message_column.append("{title}. {message}".format(**error))

                table = Table([path_column, message_column])

                table.echo()
                echo("")

    except CommunicationError:
        echo("Maestral daemon is not running.")


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
@existing_config_option
def filestatus(local_path: str, config_name: str) -> None:

    from ..daemon import MaestralProxy, CommunicationError

    try:
        with MaestralProxy(config_name) as m:
            stat = m.get_file_status(local_path)
            echo(stat)

    except CommunicationError:
        echo("unwatched")


@click.command(help="Live view of all items being synced.")
@existing_config_option
@convert_api_errors
def activity(config_name: str) -> None:

    import curses
    from ..utils import natural_size
    from ..daemon import MaestralProxy, CommunicationError

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
        echo("Maestral daemon is not running.")


@click.command(help="Show recently changed or added files.")
@existing_config_option
@convert_api_errors
def history(config_name: str) -> None:

    from datetime import datetime
    from ..daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:
        events = m.get_history()

    table = Table(
        [
            Column("Path", elide=Elide.Leading),
            Column("Change"),
            Column("Time"),
        ]
    )

    for event in events:

        dbx_path = cast(str, event["dbx_path"])
        change_type = cast(str, event["change_type"])
        change_time_or_sync_time = cast(float, event["change_time_or_sync_time"])
        dt = datetime.fromtimestamp(change_time_or_sync_time)

        table.append([dbx_path, change_type, dt])

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
@existing_config_option
@convert_api_errors
def ls(long: bool, dropbox_path: str, include_deleted: bool, config_name: str) -> None:

    from ..utils import natural_size
    from ..daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:

        echo("Loading...\r", nl=False)

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

                    item_type = to_short_type[cast(str, entry["type"])]
                    name = cast(str, entry["name"])
                    path_lower = cast(str, entry["path_lower"])

                    text = "shared" if "sharing_info" in entry else "private"
                    color = "bright_black" if text == "private" else None
                    shared_field = TextField(text, fg=color)

                    excluded_status = m.excluded_status(path_lower)
                    color = "green" if excluded_status == "included" else None
                    text = "✓" if excluded_status == "included" else excluded_status
                    excluded_field = TextField(text, fg=color)

                    if "size" in entry:
                        size = natural_size(cast(float, entry["size"]))
                    else:
                        size = "-"

                    dt_field: Field

                    if "client_modified" in entry:
                        cm = cast(str, entry["client_modified"])
                        dt_field = DateField(datetime_from_iso_str(cm))
                    else:
                        dt_field = TextField("-")

                    table.append(
                        [name, item_type, size, shared_field, excluded_field, dt_field]
                    )

            echo(" " * 15)
            table.echo()
            echo(" " * 15)

        else:

            grid = Grid()

            for entries in entries_iter:
                for entry in entries:
                    name = cast(str, entry["name"])
                    color = "blue" if entry["type"] == "DeletedMetadata" else None

                    grid.append(TextField(name, fg=color))

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
