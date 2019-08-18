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
import Pyro4
import Pyro4.naming
import Pyro4.errors

Pyro4.config.SERIALIZER = "pickle"
Pyro4.config.SERIALIZERS_ACCEPTED.add('pickle')

URI = "PYRO:maestral.{0}@{1}"

OK = click.style("[OK]", fg="green")
FAILED = click.style("[FAILED]", fg="red")
KILLED = click.style("[KILLED]", fg="red")


# ========================================================================================
# Maestral daemon
# ========================================================================================


def write_pid(config_name, socket_address="gui"):
    """
    Write the PID of the current process to the appropriate file for the given
    config name. If a socket_address is given, it will be appended after a '|'.
    """
    from maestral.config.base import get_conf_path
    pid_file = get_conf_path("maestral", config_name + ".pid")
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()) + "|" + socket_address)


def read_pid(config_name):
    """
    Reads the PID of the current process to the appropriate file for the given
    config name.
    """
    from maestral.config.base import get_conf_path
    pid_file = get_conf_path("maestral", config_name + ".pid")
    with open(pid_file, "r") as f:
        pid, socket = f.read().split("|")
    pid = int(pid)

    return pid, socket


def delete_pid(config_name):
    """
    Reads the PID of the current process to the appropriate file for the given
    config name.
    """
    from maestral.config.base import get_conf_path
    pid_file = get_conf_path("maestral", config_name + ".pid")
    os.unlink(pid_file)


def start_maestral_daemon(config_name):
    """

    Wraps :class:`maestral.main.Maestral` as Pyro daemon object, creates a new instance
    and start Pyro's event loop to listen for requests on 'localhost'. This call will
    block until the event loop shuts down.

    This command will create a new daemon on each run. Take care not to sync the same
    directory with multiple instances of Meastral! You can use `get_maestral_process_info`
    to check if either a Meastral gui or daemon is already running for the given
    `config_name`.

    :param str config_name: The name of maestral configuration to use.
    """

    os.environ["MAESTRAL_CONFIG"] = config_name

    from maestral.main import Maestral

    daemon = Pyro4.Daemon()

    write_pid(config_name, daemon.locationStr)  # write PID to file

    try:
        # we wrap this in a try-except block to make sure that the PID file is always
        # removed, even when Maestral crashes for some reason

        ExposedMaestral = Pyro4.expose(Maestral)
        m = ExposedMaestral()

        daemon.register(m, "maestral.{}".format(config_name))
        daemon.requestLoop(loopCondition=m._shutdown_requested)
        daemon.close()
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        delete_pid(config_name)  # remove PID file


def start_daemon_subprocess(config_name):
    """Starts the Maestral daemon as a subprocess (by calling `start_maestral_daemon`).

    This command will create a new daemon on each run. Take care not to sync the same
    directory with multiple instances of Meastral! You can use `get_maestral_process_info`
    to check if either a Meastral gui or daemon is already running for the given
    `config_name`.

    :param str config_name: The name of maestral configuration to use.
    :returns: Popen object instance.
    """
    import subprocess
    from maestral.main import Maestral

    if Maestral.pending_link() or Maestral.pending_dropbox_folder():
        # run onboarding
        m = Maestral(run=False)
        m.create_dropbox_directory()
        m.select_excluded_folders()

    click.echo("Starting Maestral...", nl=False)

    proc = subprocess.Popen("maestral sync -c {}".format(config_name),
                            shell=True, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # check if the subprocess is still running after 1 sec
    try:
        proc.wait(timeout=1)
        click.echo("\rStarting Maestral...        " + FAILED)
    except subprocess.TimeoutExpired:
        click.echo("\rStarting Maestral...        " + OK)

    return proc


def stop_maestral_daemon(config_name="maestral"):
    """stops maestral by finding its PID and shutting it down"""
    import signal
    import time

    pid, socket, p_type = get_maestral_process_info(config_name)
    if p_type == "daemon":
        try:
            # try to shut down gracefully
            click.echo("Stopping Maestral...", nl=False)
            with MaestralProxy(config_name) as m:
                m.stop_sync()
                m.shutdown_daemon()
        except Pyro4.errors.CommunicationError:
            try:
                # send SIGTERM if failed
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                delete_pid(pid)
        finally:
            t0 = time.time()
            while any(get_maestral_process_info(config_name)):
                time.sleep(0.2)
                if time.time() - t0 > 5:
                    # send SIGKILL if still running
                    os.kill(pid, signal.SIGKILL)
                    click.echo("\rStopping Maestral...        " + KILLED)
                    return

            click.echo("\rStopping Maestral...        " + OK)

    else:
        click.echo("Maestral daemon is not running.")


def get_maestral_daemon_proxy(config_name="maestral", fallback=False):
    """
    Returns a proxy of the running Maestral daemon. If fallback == True,
    a new instance of Maestral will be returned when the daemon cannot be reached. This
    can be dangerous if the GUI is running at the same time.
    """

    pid, location, p_type = get_maestral_process_info(config_name)

    if p_type == "daemon":
        maestral_daemon = Pyro4.Proxy(URI.format(config_name, location))
        try:
            maestral_daemon._pyroBind()
            return maestral_daemon
        except Pyro4.errors.CommunicationError:
            maestral_daemon._pyroRelease()

    if fallback:
        from maestral.main import Maestral
        m = Maestral(run=False)
        return m
    else:
        raise Pyro4.errors.CommunicationError


class MaestralProxy(object):
    """A context manager to open and close a Proxy to the Maestral daemon."""

    def __init__(self, config_name="maestral", fallback=False):
        self.m = get_maestral_daemon_proxy(config_name, fallback)

    def __enter__(self):
        return self.m

    def __exit__(self, exc_type, exc_value, traceback):
        if hasattr(self.m, "_pyroRelease"):
            self.m._pyroRelease()


def is_maestral_linked(config_name):
    """
    This does not create a Maestral instance and is therefore safe to call from anywhere
    at any time.
    """
    os.environ["MAESTRAL_CONFIG"] = config_name
    from maestral.main import Maestral
    if Maestral.pending_link():
        click.echo("No Dropbox account linked.")
        return False
    else:
        return True


def get_maestral_process_info(config_name):
    """
    Returns Maestral's PID, the socket location, and the type of instance as (pid,
    socket, running) if Maestral is running or (``None``, ``None``, ``False``)  otherwise.

    Possible values for ``running`` are "gui", "daemon" or ``False``. Possible values for
    ``socket`` are "gui", "<network_address>:<port>" or "None".

    If ``running == False`` but the PID and socket values are set, this means that
    Maestral is running but is unresponsive. This function will attempt to kill it by
    sending SIGKILL.
    """
    import signal

    pid = None
    socket = None
    running = False
    try:
        pid, socket = read_pid(config_name)
    except Exception:
        return pid, socket, running

    try:
        # test if the daemon process receives signals
        os.kill(pid, 0)
    except ProcessLookupError:
        # if the process does not exist, delete pid file
        try:
            delete_pid(config_name)
        except Exception:
            pass
        return pid, socket, running
    except OSError:
        # if the process does not respond, try to kill it
        os.kill(pid, signal.SIGKILL)
        try:
            delete_pid(config_name)
        except Exception:
            pass
        return pid, socket, running
    else:
        # everything ok, return process info
        running = "gui" if socket == "gui" else "daemon"
        return pid, socket, running


# ========================================================================================
# Command groups
# ========================================================================================

def set_config(ctx, param, value):
    # check if valid config
    if value not in list_configs() and not value == "maestral":
        ctx.fail("Configuration '{}' does not exist. You can create new "
                 "configuration with 'maestral config new'.".format(value))

    # set environment variable
    os.environ["MAESTRAL_CONFIG"] = value

    # check if maestral is running and store the result for other commands to use
    pid, socket, running = get_maestral_process_info(value)
    ctx.params["running"] = running

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
    pass


@main.group()
def daemon():
    """Run Maestral as a daemon. See 'maestral daemon --help'."""
    pass


@main.group()
def config():
    """Manage different Maestral configuration environments."""


@main.group()
def log():
    """View and manage Maestral's log."""


# ========================================================================================
# Main commands
# ========================================================================================

@main.command()
def about():
    """Returns the version number and other information."""
    import time
    from maestral.main import __version__, __author__, __url__

    year = time.localtime().tm_year
    click.echo("")
    click.echo("Version:    {}".format(__version__))
    click.echo("Website:    {}".format(__url__))
    click.echo("Copyright:  (c) 2018 - {}, {}.".format(year, __author__))
    click.echo("")


@main.command()
@with_config_opt
def gui(config_name, running):
    """Runs Maestral with a GUI."""

    if running == "daemon":
        click.echo("Maestral daemon is already running. Please quit "
                   "before starting the GUI.")
        return

    if running == "gui":
        click.echo("Maestral GUI is already running.")
        return

    import importlib.util

    # check for PyQt5
    spec = importlib.util.find_spec("PyQt5")

    if not spec:
        click.echo("Error: PyQt5 is required to run the Maestral GUI. "
                   "Run `pip install pyqt5` to install it.")
    else:
        try:
            write_pid(config_name)
            from maestral.gui.main import run
            run()
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            delete_pid(config_name)


@main.command()
@with_config_opt
def link(config_name: str, running):
    """Links Maestral with your Dropbox account."""

    if not is_maestral_linked(config_name):
        if running == "gui":
            click.echo("Maestral GUI is already running. Please link through the GUI.")
            return

        if running == "daemon":  # stop daemon
            stop_maestral_daemon(config_name)

        from maestral.main import Maestral
        Maestral(run=False)

        if running == "daemon":  # start daemon
            start_daemon_subprocess(config_name)

    else:
        click.echo("Maestral is already linked.")


@main.command()
@with_config_opt
def unlink(config_name: str, running):
    """Unlinks your Dropbox account."""

    if is_maestral_linked(config_name):
        if running == "gui":
            click.echo("Maestral GUI is already running. Please perform action through "
                       "the GUI.")
            return

        if running == "daemon":
            stop_maestral_daemon(config_name)

        with MaestralProxy(config_name, fallback=True) as m:
            m.unlink()

        click.echo("Unlinked Maestral.")
    else:
        click.echo("Maestral is not linked.")


@main.command()
@with_config_opt
def sync(config_name: str, running):
    """Starts Maestral in the console. Quit with Ctrl-C."""

    if running == "gui":
        click.echo("Maestral GUI is already running. Please quit the GUI "
                   "before starting the daemon.")
        return
    elif running == "daemon":
        click.echo("Maestral daemon is already running.")
        return

    start_maestral_daemon(config_name)


@main.command()
@with_config_opt
@click.option("--yes/--no", "-Y/-N", default=True)
def notify(config_name: str, yes: bool, running):
    """Enables or disables system notifications."""

    # This is safe to call, even if the GUI or daemon are running.

    with MaestralProxy(config_name, fallback=True) as m:
        m.notify = yes

    enabled_str = "enabled" if yes else "disabled"
    click.echo("Notifications {}.".format(enabled_str))


@main.command()
@with_config_opt
@click.option("--new-path", "-p", type=click.Path(writable=True), default=None)
def set_dir(config_name: str, new_path: str, running):
    """Change the location of your Dropbox folder."""

    if running == "gui":
        click.echo("Maestral GUI is already running. Please use the GUI.")
        return

    if is_maestral_linked(config_name):
        from maestral.main import Maestral
        with MaestralProxy(config_name, fallback=True) as m:
            if not new_path:
                new_path = Maestral._ask_for_path()
            m.move_dropbox_directory(new_path)

        click.echo("Dropbox folder moved to {}.".format(new_path))


@main.command()
@with_config_opt
@click.argument("dropbox_path", type=click.Path())
def dir_exclude(dropbox_path: str, config_name: str, running):
    """Excludes a Dropbox directory from syncing."""

    if running == "gui":
        click.echo("Maestral GUI is already running. Please use the GUI.")
        return

    if not dropbox_path.startswith("/"):
        dropbox_path = "/" + dropbox_path

    if dropbox_path == "/":
        click.echo(click.style("Cannot exclude the root directory.", fg="red"))
        return

    if is_maestral_linked(config_name):
        with MaestralProxy(config_name, fallback=True) as m:
            m.exclude_folder(dropbox_path)
        click.echo("Excluded directory '{}' from syncing.".format(dropbox_path))


@main.command()
@with_config_opt
@click.argument("dropbox_path", type=click.Path())
def dir_include(dropbox_path: str, config_name: str, running):
    """Includes a Dropbox directory in syncing."""

    if running == "gui":
        click.echo("Maestral GUI is already running. Please use the GUI.")
        return

    if not dropbox_path.startswith("/"):
        dropbox_path = "/" + dropbox_path

    if dropbox_path == "/":
        click.echo(click.style("The root directory is always included.", fg="red"))
        return

    if is_maestral_linked(config_name):
        with MaestralProxy(config_name, fallback=True) as m:
            m.include_folder(dropbox_path)
        click.echo("Included directory '{}' in syncing.".format(dropbox_path))


@main.command()
@with_config_opt
@click.argument("dropbox_path", type=click.Path(), default="")
@click.option("-a", "all", is_flag=True, default=False,
              help="Include directory entries whose names begin with a dot (.).")
def ls(dropbox_path: str, running, config_name: str, all: bool):
    """Lists contents of a Dropbox directory."""

    if not dropbox_path.startswith("/"):
        dropbox_path = "/" + dropbox_path

    if dropbox_path == "/":
        # work around an inconsistency where the root folder must always be given as ""
        dropbox_path = ""

    if is_maestral_linked(config_name):
        from maestral.client import MaestralApiClient
        from maestral.config.main import CONF
        from dropbox.files import FolderMetadata

        # get a list of all contents
        c = MaestralApiClient()
        res = c.list_folder(dropbox_path, recursive=False)

        # parse it in a nice way
        entry_types = tuple("Folder" if isinstance(md, FolderMetadata) else "File" for
                            md in res.entries)
        entry_names = tuple(md.name for md in res.entries)
        is_exluded = tuple(md.path_lower in CONF.get("main", "excluded_folders") for md in
                           res.entries)

        # display results
        for t, n, ex in zip(entry_types, entry_names, is_exluded):
            excluded_str = click.style(" (excluded)", bold=True) if ex else ""
            if not n.startswith(".") or all:
                click.echo("{0}:\t{1}{2}".format(t, n, excluded_str))


@main.command()
@with_config_opt
def account_info(config_name: str, running):
    """Prints your Dropbox account information."""

    if is_maestral_linked(config_name):
        from maestral.config.main import CONF
        email = CONF.get("account", "email")
        account_type = CONF.get("account", "type").capitalize()
        usage = CONF.get("account", "usage")
        path = CONF.get("main", "path")
        click.echo("")
        click.echo("Account:           {0}, {1}".format(email, account_type))
        click.echo("Usage:             {}".format(usage))
        click.echo("Dropbox location:  '{}'".format(path))
        click.echo("")


# ========================================================================================
# Log commands
# ========================================================================================

@log.command()
@with_config_opt
def show(config_name: str, running):
    """Shows Maestral's log file."""
    from maestral.utils.app_dirs import get_log_path

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
def clear(config_name: str, running):
    """Clears Maestral's log file."""
    from maestral.utils.app_dirs import get_log_path

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
def level(config_name: str, level_name: str, running):
    """Gets or sets the log level. Changes will persist between restarts."""
    import logging
    if level_name:
        level_num = logging._nameToLevel[level_name]
        with MaestralProxy(config_name, fallback=True) as m:
            m.set_log_level_file(level_num)
            m.set_log_level_console(level_num)
        click.echo("Log level set to {}.".format(level_name))
    else:
        os.environ["MAESTRAL_CONFIG"] = config_name
        from maestral.config.main import CONF
        level_file = CONF.get("app", "log_level_file")
        level_console = CONF.get("app", "log_level_console")
        for level_num, target in zip((level_file, level_console), ("file", "console")):
            fallback_name = "CUSTOM ({})".format(level_num)
            level_name = logging._levelToName.get(level_num, fallback_name)
            click.echo("Log level {0}:  {1}".format(target, level_name))


# ========================================================================================
# Daemon commands
# ========================================================================================

@daemon.command()
@with_config_opt
def start(config_name: str, running):
    """Starts the Maestral as a daemon in the background."""
    if running == "gui":
        click.echo("Maestral GUI is already running. Please quit before starting the "
                   "daemon.")
        return

    if running == "daemon":
        click.echo("Maestral daemon is already running.")
        return

    start_daemon_subprocess(config_name)


@daemon.command()
@with_config_opt
def stop(config_name: str, running):
    """Stops the Maestral daemon."""
    if not running == "daemon":
        click.echo("Maestral daemon is not running.")
        return
    stop_maestral_daemon(config_name)


@daemon.command()
@with_config_opt
def restart(config_name: str, running):
    """Restarts the Maestral daemon."""
    if running == "gui":
        click.echo("Maestral GUI is running. Please stop first.")
        return

    if running == "daemon":
        stop_maestral_daemon(config_name)
    else:
        click.echo("Maestral daemon is not running.")

    start_daemon_subprocess(config_name)


@daemon.command()
@with_config_opt
def pause(config_name: str, running):
    """Pauses syncing."""
    try:
        with MaestralProxy(config_name) as m:
            m.pause_sync()
        click.echo("Syncing paused.")
    except Pyro4.errors.CommunicationError:
        click.echo("Maestral daemon is not running.")


@daemon.command()
@with_config_opt
def resume(config_name: str, running):
    """Resumes syncing."""
    try:
        with MaestralProxy(config_name) as m:
            m.resume_sync()
        click.echo("Syncing resumed.")
    except Pyro4.errors.CommunicationError:
        click.echo("Maestral daemon is not running.")


@daemon.command()
@with_config_opt
def status(config_name: str, running):
    """Returns the current status of Maestral."""
    try:
        from maestral.config.main import CONF
        with MaestralProxy(config_name) as m:
            if m.pending_link():
                s_text = "Not linked"
            else:
                s_text = m.status
            n_errors = len(m.sync_errors)
            color = "red" if n_errors > 0 else "green"
            n_errors_str = click.style(str(n_errors), fg=color)
            click.echo("")
            click.echo("Account:       {}".format(CONF.get("account", "email")))
            click.echo("Usage:         {}".format(CONF.get("account", "usage")))
            click.echo("Status:        {}".format(s_text))
            click.echo("Sync errors:   {}".format(n_errors_str))
            click.echo("")

    except Pyro4.errors.CommunicationError:
        click.echo("Maestral daemon is not running.")


@daemon.command()
@with_config_opt
def errors(config_name: str, running):
    """Lists all sync errors."""
    try:
        with MaestralProxy(config_name) as m:
            err_list = m.sync_errors
            if len(err_list) == 0:
                click.echo("No sync errors.")
            else:
                max_path_length = max(len(err.dbx_path) for err in err_list)
                column_length = max(max_path_length, len("Relative path")) + 2
                click.echo("")
                click.echo("PATH".ljust(column_length) + "ERROR")
                for err in err_list:
                    c0 = "'{}'".format(err.dbx_path).ljust(column_length)
                    c1 = "{}. {}".format(err.title, err.message)
                    click.echo(c0 + c1)
                click.echo("")

    except Pyro4.errors.CommunicationError:
        click.echo("Maestral daemon is not running.")


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


@config.command()
@click.argument("name")
def new(name: str):
    """Set up and activate a fresh Maestral configuration."""
    if name in list_configs():
        click.echo("Configuration '{}' already exists.".format(name))
    else:
        os.environ["MAESTRAL_CONFIG"] = name
        from maestral.config.main import CONF
        CONF.set("main", "default_dir_name", "Dropbox ({})".format(name.capitalize()))
        click.echo("Created configuration '{}'.".format(name))


@config.command(name='list')
def env_list():
    """List all Maestral configurations."""
    click.echo("Available Maestral configurations:")
    for c in list_configs():
        click.echo('  ' + c)


@config.command()
@click.argument("name")
def delete(name: str):
    """Remove a Maestral configuration."""
    if name not in list_configs():
        click.echo("Configuration '{}' could not be found.".format(name))
    else:
        from maestral.config.base import get_conf_path
        for file in os.listdir(get_conf_path("maestral")):
            if file.startswith(name):
                os.unlink(os.path.join(get_conf_path("maestral"), file))
        click.echo("Deleted configuration '{}'.".format(name))
