#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Nov 30 13:51:32 2018

@author: samschott

This file contains the code to daemonize Maestral and all of the command line scripts to
configure and interact with Maestral.

We aim to import most packages locally where they are required in order to reduce the
startup time of scripts.
"""

# system imports
import os

# external packages
import click
import Pyro4.errors


OK = click.style("[OK]", fg="green")
FAILED = click.style("[FAILED]", fg="red")
KILLED = click.style("[KILLED]", fg="red")


def is_maestral_linked(config_name):
    """
    This does not create a Maestral instance and is therefore safe to call from anywhere
    at any time.
    """
    os.environ["MAESTRAL_CONFIG"] = config_name
    from maestral.sync.main import Maestral
    if Maestral.pending_link():
        click.echo("No Dropbox account linked.")
        return False
    else:
        return True


def start_daemon_subprocess_with_cli_feedback(config_name):
    from maestral.sync.daemon import start_maestral_daemon_process

    click.echo("Starting Maestral...", nl=False)
    res = start_maestral_daemon_process(config_name)
    if res:
        click.echo("\rStarting Maestral...        " + OK)
    else:
        click.echo("\rStarting Maestral...        " + FAILED)


def stop_daemon_with_cli_feedback(config_name):

    from maestral.sync.daemon import stop_maestral_daemon_process

    click.echo("Stopping Maestral...", nl=False)
    success = stop_maestral_daemon_process(config_name)
    if success is None:
        click.echo("Maestral daemon was not running.")
    elif success is True:
        click.echo("\rStopping Maestral...        " + OK)
    else:
        click.echo("\rStopping Maestral...        " + KILLED)


def check_for_updates():

    from maestral import __version__
    from maestral.config.main import CONF
    from maestral.sync.utils.updates import check_version

    latest_release = CONF.get("app", "latest_release")

    has_update = check_version(__version__, latest_release, '<')

    if has_update:
        click.secho("Maestral v{0} has been released, you have v{1}. Please use your "
                    "package manager to update.".format(latest_release, __version__),
                    fg="red")


# ========================================================================================
# Command groups
# ========================================================================================

def set_config(ctx, param, value):

    from maestral.sync.daemon import get_maestral_pid

    # check if valid config
    if value not in list_configs() and not value == "maestral":
        ctx.fail("Configuration '{}' does not exist. You can create new "
                 "configuration with 'maestral config new'.".format(value))

    # set environment variable
    os.environ["MAESTRAL_CONFIG"] = value

    # check if maestral is running and store the result for other commands to use
    pid = get_maestral_pid(value)
    ctx.params["running"] = True if pid else False

    return value


with_config_opt = click.option(
    "-c", "--config-name",
    default="maestral",
    is_eager=True,
    expose_value=True,
    metavar="NAME",
    callback=set_config,
    help="Run Maestral with the selected configuration."
)


@click.group()
@click.pass_context
def main(ctx):
    """Maestral Dropbox Client for Linux and macOS."""


@main.group()
def config():
    """Manage different Maestral configuration environments."""


@main.group()
def log():
    """View and manage Maestral's log."""


@main.group()
def excluded():
    """View and manage excluded folders."""


# ========================================================================================
# Main commands
# ========================================================================================

@main.command()
def about():
    """Returns the version number and other information."""
    import time
    from maestral import __url__
    from maestral import __author__
    from maestral import __version__

    year = time.localtime().tm_year
    click.echo("")
    click.echo("Version:    {}".format(__version__))
    click.echo("Website:    {}".format(__url__))
    click.echo("Copyright:  (c) 2018-{}, {}.".format(year, __author__))
    click.echo("")


@main.command()
@with_config_opt
def gui(config_name, running):
    """Runs Maestral with a GUI."""

    import importlib.util

    # check for PyQt5
    spec = importlib.util.find_spec("PyQt5")

    if not spec:
        click.echo("Error: PyQt5 is required to run the Maestral GUI. "
                   "Run `pip install pyqt5` to install it.")
    else:
        from maestral.gui.main import run
        run()


@main.command()
@click.option("--foreground", "-f", is_flag=True, default=False,
              help="Starts Maestral in the foreground.")
@with_config_opt
def start(config_name: str, running: bool, foreground: bool):
    """Starts the Maestral as a daemon."""

    if running:
        click.echo("Maestral daemon is already running.")
        return

    check_for_updates()

    from maestral.sync.main import Maestral

    if Maestral.pending_link() or Maestral.pending_dropbox_folder():
        # run setup
        m = Maestral(run=False)
        m.create_dropbox_directory()
        m.set_excluded_folders()

        m.sync.last_cursor = ""
        m.sync.last_sync = 0

    if foreground:
        from maestral.sync.daemon import start_maestral_daemon
        start_maestral_daemon(config_name)
    else:
        start_daemon_subprocess_with_cli_feedback(config_name)


@main.command()
@with_config_opt
def stop(config_name: str, running: bool):
    """Stops the Maestral daemon."""
    if not running:
        click.echo("Maestral daemon is not running.")
    else:
        stop_daemon_with_cli_feedback(config_name)


@main.command()
@with_config_opt
def restart(config_name: str, running: bool):
    """Restarts the Maestral daemon."""

    if running:
        stop_daemon_with_cli_feedback(config_name)
    else:
        click.echo("Maestral daemon is not running.")

    start_daemon_subprocess_with_cli_feedback(config_name)


@main.command()
@with_config_opt
def pause(config_name: str, running: bool):
    """Pauses syncing."""
    from maestral.sync.daemon import MaestralProxy

    try:
        with MaestralProxy(config_name) as m:
            m.pause_sync()
        click.echo("Syncing paused.")
    except Pyro4.errors.CommunicationError:
        click.echo("Maestral daemon is not running.")


@main.command()
@with_config_opt
def resume(config_name: str, running: bool):
    """Resumes syncing."""
    from maestral.sync.daemon import MaestralProxy

    try:
        with MaestralProxy(config_name) as m:
            m.resume_sync()
        click.echo("Syncing resumed.")
    except Pyro4.errors.CommunicationError:
        click.echo("Maestral daemon is not running.")


@main.command()
@with_config_opt
def status(config_name: str, running: bool):
    """Returns the current status of the Maestral daemon."""
    from maestral.sync.daemon import MaestralProxy

    try:
        with MaestralProxy(config_name) as m:
            n_errors = len(m.sync_errors)
            color = "red" if n_errors > 0 else "green"
            n_errors_str = click.style(str(n_errors), fg=color)
            click.echo("")
            click.echo("Account:       {}".format(m.get_conf("account", "email")))
            click.echo("Usage:         {}".format(m.get_conf("account", "usage")))
            click.echo("Status:        {}".format(m.status))
            click.echo("Sync errors:   {}".format(n_errors_str))
            click.echo("")

    except Pyro4.errors.CommunicationError:
        click.echo("Maestral daemon is not running.")


@main.command()
@click.argument("local_path", type=click.Path(exists=True))
@with_config_opt
def file_status(config_name: str, running: bool, local_path: str):
    """Returns the current sync status of a given file or folder."""
    from maestral.sync.daemon import MaestralProxy

    try:
        with MaestralProxy(config_name) as m:
            stat = m.get_file_status(local_path)
            click.echo(stat)

    except Pyro4.errors.CommunicationError:
        click.echo("unwatched")


@main.command()
@with_config_opt
def errors(config_name: str, running: bool):
    """Lists all sync errors."""
    from maestral.sync.daemon import MaestralProxy

    try:
        with MaestralProxy(config_name) as m:
            err_list = m.sync_errors
            if len(err_list) == 0:
                click.echo("No sync errors.")
            else:
                max_path_length = max(len(err["dbx_path"]) for err in err_list)
                column_length = max(max_path_length, len("Relative path")) + 4
                click.echo("")
                click.echo("PATH".ljust(column_length) + "ERROR")
                for err in err_list:
                    c0 = "'{}'".format(err["dbx_path"]).ljust(column_length)
                    c1 = "{}. {}".format(err["title"], err["message"])
                    click.echo(c0 + c1)
                click.echo("")

    except Pyro4.errors.CommunicationError:
        click.echo("Maestral daemon is not running.")


@main.command()
@with_config_opt
def link(config_name: str, running: bool):
    """Links Maestral with your Dropbox account."""

    if not is_maestral_linked(config_name):
        if running:
            click.echo("Maestral is already running. Please link through the CLI or GUI.")
            return

        from maestral.sync.main import Maestral
        Maestral(run=False)

    else:
        click.echo("Maestral is already linked.")


@main.command()
@with_config_opt
def unlink(config_name: str, running: bool):
    """Unlinks your Dropbox account."""

    if is_maestral_linked(config_name):

        from maestral.sync.daemon import MaestralProxy

        if running:
            stop_daemon_with_cli_feedback(config_name)

        with MaestralProxy(config_name, fallback=True) as m:
            m.unlink()

        click.echo("Unlinked Maestral.")
    else:
        click.echo("Maestral is not linked.")


@main.command()
@with_config_opt
@click.option("--yes/--no", "-Y/-N", default=True)
def notify(config_name: str, yes: bool, running: bool):
    """Enables or disables system notifications."""
    # This is safe to call, even if the GUI or daemon are running.
    from maestral.sync.daemon import MaestralProxy

    with MaestralProxy(config_name, fallback=True) as m:
        m.notify = yes

    enabled_str = "enabled" if yes else "disabled"
    click.echo("Notifications {}.".format(enabled_str))


@main.command()
@with_config_opt
@click.option("--new-path", "-p", type=click.Path(writable=True), default=None)
def set_dir(config_name: str, new_path: str, running: bool):
    """Change the location of your Dropbox folder."""

    if is_maestral_linked(config_name):
        from maestral.sync.main import Maestral
        from maestral.sync.daemon import MaestralProxy
        with MaestralProxy(config_name, fallback=True) as m:
            if not new_path:
                # don't use the remote instance because we need console interaction
                new_path = Maestral._ask_for_path()
            m.move_dropbox_directory(new_path)

        click.echo("Dropbox folder moved to {}.".format(new_path))


@main.command()
@with_config_opt
@click.argument("dropbox_path", type=click.Path(), default="")
@click.option("-a", "list_all", is_flag=True, default=False,
              help="Include directory entries whose names begin with a dot (.).")
def ls(dropbox_path: str, running: bool, config_name: str, list_all: bool):
    """Lists contents of a Dropbox directory."""

    if not dropbox_path.startswith("/"):
        dropbox_path = "/" + dropbox_path

    if is_maestral_linked(config_name):
        from maestral.sync.daemon import MaestralProxy
        with MaestralProxy(config_name, fallback=True) as m:
            entries = m.list_folder(dropbox_path, recursive=False)

            if not entries:
                click.echo("Could not connect to Dropbox")
                return

            excluded_status = (m.excluded_status(e["path_lower"]) for e in entries)

            # display results
            for e, ex in zip(entries, excluded_status):
                if not ex == "included":
                    excluded_str = click.style(" ({})".format(ex), bold=True)
                else:
                    excluded_str = ""
                type_str = "Folder" if e["type"] == "FolderMetadata" else "File"
                if not e["name"].startswith(".") or list_all:
                    click.echo("{0}:\t{1}{2}".format(type_str, e["name"], excluded_str))


@main.command()
@with_config_opt
def account_info(config_name: str, running: bool):
    """Prints your Dropbox account information."""

    if is_maestral_linked(config_name):
        from maestral.config.main import CONF
        email = CONF.get("account", "email")
        account_type = CONF.get("account", "type").capitalize()
        usage = CONF.get("account", "usage")
        path = CONF.get("main", "path")
        click.echo("")
        click.echo("Email:             {}".format(email))
        click.echo("Account-type:      {}".format(account_type))
        click.echo("Usage:             {}".format(usage))
        click.echo("Dropbox location:  '{}'".format(path))
        click.echo("")


@main.command()
@with_config_opt
def rebuild_index(config_name: str, running: bool):
    """Prints your Dropbox account information."""

    if is_maestral_linked(config_name):

        import textwrap

        width, height = click.get_terminal_size()

        message1 = textwrap.wrap(
            "If you encounter sync issues, please run 'maestral errors' to check for "
            "incompatible file names, insufficient permissions or other issues which "
            "should be resolved manually. After resolving them, please pause and resume "
            "syncing. Only rebuild the index if you continue to have problems after "
            "taking those steps.",
            width=width,
        )

        message2 = textwrap.wrap(
            "Rebuilding the index may take several minutes, depending on the size of  "
            "your Dropbox. Please do not modify any items in your local Dropbox folder "
            "during this process. Any changes to local files while rebuilding may be "
            "lost.",
            width=width
        )

        click.echo("\n".join(message1) + "\n")
        click.echo("\n".join(message2) + "\n")
        click.confirm("Do you want to continue?", abort=True)

        import time
        from concurrent.futures import ThreadPoolExecutor
        from maestral.sync.daemon import MaestralProxy

        with MaestralProxy(config_name, fallback=True) as m0:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(m0.rebuild_index)
                with MaestralProxy(config_name, fallback=True) as m1:
                    while future.running():
                        msg = ("\r" + m1.status).ljust(width)
                        click.echo(msg, nl=False)
                        time.sleep(1.0)

        click.echo("\rRebuilding complete.".ljust(width))

    else:
        click.echo("Maestral does not appear to be linked.")

# ========================================================================================
# Exclude commands
# ========================================================================================


@excluded.command(name="add")
@with_config_opt
@click.argument("dropbox_path", type=click.Path())
def excluded_add(dropbox_path: str, config_name: str, running: bool):
    """Adds a folder to the excluded list and re-syncs."""

    if not dropbox_path.startswith("/"):
        dropbox_path = "/" + dropbox_path

    if dropbox_path == "/":
        click.echo(click.style("Cannot exclude the root directory.", fg="red"))
        return

    from maestral.sync.daemon import MaestralProxy

    if is_maestral_linked(config_name):
        with MaestralProxy(config_name, fallback=True) as m:
            m.exclude_folder(dropbox_path)
        click.echo("Excluded directory '{}' from syncing.".format(dropbox_path))


@excluded.command(name="remove")
@with_config_opt
@click.argument("dropbox_path", type=click.Path())
def excluded_remove(dropbox_path: str, config_name: str, running: bool):
    """Removes a folder from the excluded list and re-syncs."""

    if not dropbox_path.startswith("/"):
        dropbox_path = "/" + dropbox_path

    if dropbox_path == "/":
        click.echo(click.style("The root directory is always included.", fg="red"))
        return

    from maestral.sync.daemon import MaestralProxy

    if is_maestral_linked(config_name):
        with MaestralProxy(config_name, fallback=True) as m:
            m.include_folder(dropbox_path)
        click.echo("Included directory '{}' in syncing.".format(dropbox_path))


@excluded.command(name="list")
@with_config_opt
def excluded_list(config_name: str, running: bool):
    """Lists all excluded folders."""

    if is_maestral_linked(config_name):

        from maestral.config.main import CONF

        excluded_folders = CONF.get("main", "excluded_folders")

        excluded_folders.sort()

        if len(excluded_folders) == 0:
            click.echo("No excluded folders.")
        else:
            for folder in excluded_folders:
                click.echo(folder)


# ========================================================================================
# Log commands
# ========================================================================================

@log.command()
@with_config_opt
def show(config_name: str, running: bool):
    """Shows Maestral's log file."""
    from maestral.sync.utils.app_dirs import get_log_path

    log_file = get_log_path("maestral", config_name + ".log")

    if os.path.isfile(log_file):
        try:
            with open(log_file, "r") as f:
                text = f.read()
            click.echo_via_pager(text)
        except OSError:
            click.echo("Could not open log file at '{}'".format(log_file))
    else:
        click.echo_via_pager("")


@log.command()
@with_config_opt
def clear(config_name: str, running: bool):
    """Clears Maestral's log file."""
    from maestral.sync.utils.app_dirs import get_log_path

    log_file = get_log_path("maestral", config_name + ".log")

    try:
        open(log_file, 'w').close()
        click.echo("Cleared Maestral's log.")
    except FileNotFoundError:
        click.echo("Cleared Maestral's log.")
    except OSError:
        click.echo("Could not clear log file at '{}'. Please try to delete it "
                   "manually".format(log_file))


@log.command()
@click.argument('level_name', required=False,
                type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR']))
@with_config_opt
def level(config_name: str, level_name: str, running: bool):
    """Gets or sets the log level. Changes will persist between restarts."""
    import logging
    if level_name:
        from maestral.sync.daemon import MaestralProxy

        level_num = logging._nameToLevel[level_name]
        with MaestralProxy(config_name, fallback=True) as m:
            m.set_conf("app", "log_level", level_num)
        click.echo("Log level set to {}.".format(level_name))
    else:
        os.environ["MAESTRAL_CONFIG"] = config_name
        from maestral.config.main import CONF

        level_num = CONF.get("app", "log_level")
        level_name = logging.getLevelName(level_num)
        click.echo("Log level:  {}".format(level_name))


# ========================================================================================
# Management of different configurations
# ========================================================================================

def list_configs():
    from maestral.config.base import get_conf_path
    configs = []
    for file in os.listdir(get_conf_path("maestral")):
        if file.endswith(".ini"):
            configs.append(os.path.splitext(os.path.basename(file))[0])

    return configs


@config.command(name="add")
@click.argument("name")
def config_add(name: str):
    """Set up and activate a fresh Maestral configuration."""
    os.environ["MAESTRAL_CONFIG"] = name
    if name in list_configs():
        click.echo("Configuration '{}' already exists.".format(name))
    else:
        from maestral.config.main import CONF
        CONF.set("main", "default_dir_name", "Dropbox ({})".format(name.capitalize()))
        click.echo("Created configuration '{}'.".format(name))


@config.command(name="list")
def config_list():
    """List all Maestral configurations."""
    click.echo("Available Maestral configurations:")
    for c in list_configs():
        click.echo('  ' + c)


@config.command(name="remove")
@click.argument("name")
def config_remove(name: str):
    """Remove a Maestral configuration."""
    if name not in list_configs():
        click.echo("Configuration '{}' could not be found.".format(name))
    else:
        from maestral.config.base import get_conf_path
        for file in os.listdir(get_conf_path("maestral")):
            if file.startswith(name):
                os.unlink(os.path.join(get_conf_path("maestral"), file))
        click.echo("Deleted configuration '{}'.".format(name))
