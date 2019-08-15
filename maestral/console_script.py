#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Nov 30 13:51:32 2018

@author: samschott
"""
import os
import signal
import time
import subprocess
import click
import Pyro4
import Pyro4.naming
import Pyro4.errors

Pyro4.config.SERIALIZER = "pickle"
Pyro4.config.SERIALIZERS_ACCEPTED.add('pickle')

PORT = 5814
ADDRESS = "localhost"
URI = "PYRO:maestral.{0}@{1}"

OK = click.style("[OK]", fg="green")
FAILED = click.style("[FAILED]", fg="red")
KILLED = click.style("[KILLED]", fg="red")


# ========================================================================================
# Maestral daemon
# ========================================================================================


def start_maestral_daemon(config_name="maestral"):

    os.environ["MAESTRAL_CONFIG"] = config_name

    from maestral.main import Maestral
    from maestral.config.base import get_conf_path

    daemon = Pyro4.Daemon()

    # write PID to file
    pid_file = get_conf_path("maestral", config_name + ".pid")

    with open(pid_file, "w") as f:
        f.write(str(os.getpid()) + "|" + daemon.locationStr)

    try:
        # we wrap this in a try-except block to make sure that the PID file is always
        # removed, even when Maestral crashes

        ExposedMaestral = Pyro4.expose(Maestral)
        m = ExposedMaestral()

        daemon.register(m, "maestral.{}".format(config_name))
        daemon.requestLoop(loopCondition=m._shutdown_requested)
        daemon.close()
    except Exception:
        pass
    finally:
        # remove PID file
        os.unlink(pid_file)


def start_daemon_subprocess(config_name):
    """Starts Maestral as a damon."""

    if is_daemon_running(config_name):
        click.echo("Maestral is already running.")
        return

    from maestral.main import Maestral
    if Maestral.pending_link() or Maestral.pending_dropbox_folder():
        # run onboarding
        m = Maestral(run=False)
        m.create_dropbox_directory()
        m.select_excluded_folders()

    click.echo("Starting Maestral...", nl=False)

    s = subprocess.Popen("maestral sync -c {}".format(config_name),
                         shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)

    # check it the subprocess is still running after 1 sec
    try:
        s.wait(timeout=1)
        click.echo("\rStarting Maestral...        " + FAILED)
    except subprocess.TimeoutExpired:
        click.echo("\rStarting Maestral...        " + OK)


def stop_maestral_daemon(config_name="maestral"):
    # stops maestral by finding its PID and shutting it down
    pid = is_daemon_running(config_name)
    if pid:
        try:
            # try to shut down gracefully
            click.echo("Stopping Maestral...", nl=False)
            with MaestralProxy(config_name) as m:
                m.stop_sync()
                m.shutdown_daemon()
        except Pyro4.errors.CommunicationError:
            # send SIGTERM if failed
            os.kill(pid, signal.SIGTERM)
        finally:
            t0 = time.time()
            while is_daemon_running(config_name):
                time.sleep(0.25)
                if time.time() - t0 > 5:
                    # send SIGKILL if still running
                    os.kill(pid, signal.SIGKILL)
                    click.echo("\rStopping Maestral...        " + KILLED)
                    return

            click.echo("\rStopping Maestral...        " + OK)

    else:
        click.echo("Maestral is not running.")


def get_maestral_daemon_proxy(config_name="maestral", fallback=False):
    """
    Returns a proxy of the running Maestral daemon. If fallback == True,
    a new instance of Maestral will be returned when the daemon cannot be reached.
    """

    location = get_daemon_socket_location(config_name)

    if location:
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
    This does not create a Maestral instance and is therefore safe to call from anywhere.
    """
    os.environ["MAESTRAL_CONFIG"] = config_name
    from maestral.main import Maestral
    if Maestral.pending_link():
        click.echo("No Dropbox account linked.")
        return False
    else:
        return True


def get_daemon_pid(config_name):
    return is_daemon_running(config_name, return_info="pid")


def get_daemon_socket_location(config_name):
    return is_daemon_running(config_name, return_info="socket")


def is_daemon_running(config_name, return_info="pid"):
    """Returns maestral daemon's PID or the socket location if the daemon is running,
    ``False`` otherwise."""
    from maestral.config.base import get_conf_path

    pid_file = get_conf_path("maestral", config_name + ".pid")

    try:
        with open(pid_file, "r") as f:
            pid, socket = f.read().split("|")
        pid = int(pid)
    except Exception:
        return False

    try:
        # test if the daemon process receives signals
        os.kill(pid, 0)
    except OSError:
        # shut it down
        os.kill(pid, signal.SIGKILL)
        try:
            os.unlink(pid_file)
        except Exception:
            pass
        return False
    else:
        if return_info == "pid":
            return pid
        elif return_info == "socket":
            return socket
        else:
            return True


# ========================================================================================
# Command groups
# ========================================================================================

def set_config(ctx, param, value):
    if value not in list_configs():
        ctx.fail("Configuration '{}' does not exist.".format(value))
    os.environ["MAESTRAL_CONFIG"] = value
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
def main():
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
    from maestral.main import __version__, __author__, __url__
    year = time.localtime().tm_year
    click.echo("")
    click.echo("Version:    {}".format(__version__))
    click.echo("Website:    {}".format(__url__))
    click.echo("Copyright:  (c) 2018 - {}, {}.".format(year, __author__))
    click.echo("")


@main.command()
@with_config_opt
def gui(config_name: str):
    """Runs Maestral with a GUI."""
    # check for PyQt5
    import importlib.util
    spec = importlib.util.find_spec("PyQt5")

    if not spec:
        click.echo("Error: PyQt5 is required to run the Maestral GUI. "
                   "Run `pip install pyqt5` to install it.")
    else:
        from maestral.gui.main import run
        run()


@main.command()
@with_config_opt
def link(config_name: str):
    """Links Maestral with your Dropbox account."""
    from maestral.main import Maestral
    if Maestral.pending_link():
        m = Maestral(run=False)
        m.create_dropbox_directory()
        m.select_excluded_folders()
        if is_daemon_running(config_name):
            # restart
            stop_maestral_daemon(config_name)
            start_daemon_subprocess(config_name)
    else:
        click.echo("Maestral is already linked.")


@main.command()
@with_config_opt
def unlink(config_name: str):
    """Unlinks your Dropbox account."""
    if is_maestral_linked(config_name):
        with MaestralProxy(config_name, fallback=True) as m:
            m.unlink()
        click.echo("Unlinked Maestral.")
        if is_daemon_running(config_name):
            # stop
            stop_maestral_daemon(config_name)


@main.command()
@with_config_opt
def sync(config_name: str):
    """Starts Maestral in the console. Quit with Ctrl-C."""
    start_maestral_daemon(config_name)


@main.command()
@with_config_opt
@click.option("--yes/--no", "-Y/-N", default=True)
def notify(config_name: str, yes: bool):
    """Enables or disables system notifications."""
    with MaestralProxy(config_name, fallback=True) as m:
        m.notify = yes
    enabled_str = "enabled" if yes else "disabled"
    click.echo("Notifications {}.".format(enabled_str))


@main.command()
@with_config_opt
@click.option("--new-path", "-P", type=click.Path(writable=True), default=None)
def set_dir(config_name: str, new_path: str):
    """Change the location of your Dropbox folder."""
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
def dir_exclude(dropbox_path: str, config_name: str):
    """Excludes a Dropbox directory from syncing."""
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
def dir_include(dropbox_path: str, config_name: str):
    """Includes a Dropbox directory in syncing."""
    if not dropbox_path.startswith("/"):
        dropbox_path = "/" + dropbox_path
    if dropbox_path == "/":
        click.echo(click.style("The root directory is always included.", fg="red"))
        return

    if is_maestral_linked(config_name):
        with MaestralProxy(config_name, fallback=True) as m:
            m.include_folder(dropbox_path)
        click.echo("Included directory '{}' in syncing.".format(dropbox_path))


@main.command(name='ls')
@with_config_opt
@click.argument("dropbox_path", type=click.Path(), default="")
def ls(dropbox_path: str, config_name: str):
    """Lists contents of a Dropbox directory."""
    if not dropbox_path.startswith("/"):
        dropbox_path = "/" + dropbox_path

    if dropbox_path == "/":
        # work around an inconsistency where the root folder must always be given as ""
        dropbox_path = ""

    if is_maestral_linked(config_name):
        from maestral.client import MaestralApiClient
        from dropbox.files import FolderMetadata
        from maestral.config.main import CONF
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
            click.echo("{0}:\t{1}{2}".format(t, n, excluded_str))


@main.command()
@with_config_opt
def account_info(config_name: str):
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
def show(config_name: str):
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
def clear(config_name: str):
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
def level(config_name: str, level_name: str):
    """Gets or sets the log level. Changes will persist between restarts."""
    import logging
    if level_name:
        level_number = logging._nameToLevel[level_name]
        with MaestralProxy(config_name, fallback=True) as m:
            m.set_log_level_file(level_number)
        click.echo("Log level set to {}.".format(level_name))
    else:
        os.environ["MAESTRAL_CONFIG"] = config_name
        from maestral.config.main import CONF
        level_number = CONF.get("app", "log_level_file")
        fallback_name = "CUSTOM ({})".format(level_number)
        level_name = logging._levelToName.get(level_number, fallback_name)
        click.echo("Log level:  {}".format(level_name))


# ========================================================================================
# Daemon commands
# ========================================================================================

@daemon.command()
@with_config_opt
def start(config_name: str):
    """Starts the Maestral as a daemon in the background."""
    start_daemon_subprocess(config_name)


@daemon.command()
@with_config_opt
def stop(config_name: str):
    """Stops the Maestral daemon."""
    stop_maestral_daemon(config_name)


@daemon.command()
@with_config_opt
def restart(config_name: str):
    """Restarts the Maestral daemon."""
    stop_maestral_daemon(config_name)
    start_daemon_subprocess(config_name)


@daemon.command()
@with_config_opt
def pause(config_name: str):
    """Pauses syncing."""
    try:
        with MaestralProxy(config_name) as m:
            m.pause_sync()
        click.echo("Syncing paused.")
    except Pyro4.errors.CommunicationError:
        click.echo("Maestral is not running.")


@daemon.command()
@with_config_opt
def resume(config_name: str):
    """Resumes syncing."""
    try:
        with MaestralProxy(config_name) as m:
            m.resume_sync()
        click.echo("Syncing resumed.")
    except Pyro4.errors.CommunicationError:
        click.echo("Maestral is not running.")


@daemon.command()
@with_config_opt
def status(config_name: str):
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
        click.echo("Maestral is not running.")


@daemon.command()
@with_config_opt
def errors(config_name: str):
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
        click.echo("Maestral is not running.")


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
