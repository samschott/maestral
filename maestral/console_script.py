#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Nov 30 13:51:32 2018

@author: samschott
"""
import os
import click


# ========================================================================================
# Main commands
# ========================================================================================

def set_environ(ctx, param, value):
    if value:
        if value not in list_configs():
            ctx.fail("Configuration '{0}' does not exist.".format(value))
        os.environ["MAESTRAL_CONFIG"] = value


@click.group()
def main():
    """Maestral Dropbox Client for Linux and macOS."""
    pass


@main.command()
@click.option('-c', '--config-name', default='', callback=set_environ, is_eager=True,
              expose_value=False, help="Run Maestral with the selected configuration.")
def sync():
    """Runs Maestral from the command line."""
    from maestral.main import Maestral
    m = Maestral()
    m.monitor.connection_thread.join()  # join until quit by user
    return m


@main.command()
@click.option('-c', '--config-name', default='', callback=set_environ, is_eager=True,
              expose_value=False, help="Run Maestral with the selected configuration.")
def gui():
    """Runs Maestral with a GUI."""
    # check for PyQt5
    import importlib.util
    spec = importlib.util.find_spec("PyQt5")

    if not spec:
        click.echo('Error: PyQt5 is required to run the Maestral GUI. '
                   'Run `pip install pyqt5` to install it.')
    else:
        from maestral.gui.main import run
        run()


@main.command()
@click.option('-c', '--config-name', default='', callback=set_environ, is_eager=True,
              expose_value=False, help="Run Maestral with the selected configuration.")
def configure():
    """Runs the command line configuration wizard."""
    from maestral.main import Maestral
    m = Maestral(run=False)
    m.move_dropbox_directory()
    m.select_excluded_folders()


@main.command()
@click.option('-c', '--config-name', default='', callback=set_environ, is_eager=True,
              expose_value=False, help="Run Maestral with the selected configuration.")
def unlink():
    """Unlinks your Dropbox account."""
    from maestral.main import Maestral
    m = Maestral(run=False)
    m.unlink()


@main.command()
@click.option('-c', '--config-name', default='', callback=set_environ, is_eager=True,
              expose_value=False, help="Run Maestral with the selected configuration.")
@click.argument("dropbox_path", type=click.Path())
@click.argument("local_path", type=click.Path())
def download(dropbox_path: str, local_path: str):
    """Downloads a file from Dropbox."""
    from maestral.client import MaestralApiClient
    c = MaestralApiClient()
    c.download(dropbox_path, local_path)


@main.command()
@click.option('-c', '--config-name', default='', callback=set_environ, is_eager=True,
              expose_value=False, help="Run Maestral with the selected configuration.")
@click.argument("local_path", type=click.Path())
@click.argument("dropbox_path", type=click.Path())
def upload(local_path: str, dropbox_path: str):
    """Uploads a file to Dropbox."""
    from maestral.client import MaestralApiClient
    c = MaestralApiClient()
    c.upload(local_path, dropbox_path)


@main.command()
@click.option('-c', '--config-name', default='', callback=set_environ, is_eager=True,
              expose_value=False, help="Run Maestral with the selected configuration.")
@click.argument("old_path", type=click.Path())
@click.argument("new_path", type=click.Path())
def move(old_path: str, new_path: str):
    """Moves or renames a file or folder on Dropbox."""
    from maestral.client import MaestralApiClient
    c = MaestralApiClient()
    c.move(old_path, new_path)


@main.command(name='list')
@click.option('-c', '--config-name', default='', callback=set_environ, is_eager=True,
              expose_value=False, help="Run Maestral with the selected configuration.")
@click.argument("dropbox_path", type=click.Path())
def main_list(dropbox_path: str):
    """Lists contents of a folder on Dropbox."""
    from maestral.client import MaestralApiClient
    from dropbox.files import FolderMetadata
    c = MaestralApiClient()
    res = c.list_folder(dropbox_path, recursive=False)
    entry_types = ("Folder" if isinstance(md, FolderMetadata) else "File" for md in
                   res.entries)
    entry_names = (md.name for md in res.entries)
    for t, n in zip(entry_types, entry_names):
        click.echo("{0}:\t{1}".format(t, n))


@main.command()
@click.option('-c', '--config-name', default='', callback=set_environ, is_eager=True,
              expose_value=False, help="Run Maestral with the selected configuration.")
@click.argument("dropbox_path", type=click.Path())
def mkdir(dropbox_path: str):
    """Creates a new directory on Dropbox."""
    from maestral.client import MaestralApiClient
    c = MaestralApiClient()
    c.make_dir(dropbox_path)


@main.command()
@click.option('-c', '--config-name', default='', callback=set_environ, is_eager=True,
              expose_value=False, help="Run Maestral with the selected configuration.")
def account_info():
    """Prints Dropbox account info."""
    from maestral.client import MaestralApiClient
    c = MaestralApiClient()
    res = c.get_account_info()
    print("{0}, {1}".format(res.email, res.account_type))


@main.command()
@click.option('-c', '--config-name', default='', callback=set_environ, is_eager=True,
              expose_value=False, help="Run Maestral with the selected configuration.")
@click.option("--yes/--no", "-Y/-N", default=True)
def autostart(yes: bool):
    """Starts Maestral on login. May not work on some Linux distributions."""
    from maestral.utils.autostart import AutoStart
    ast = AutoStart()
    if yes:
        ast.enable()
    else:
        ast.disable()


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


@main.group()
def env():
    """Manage different Maestral configuration environments."""


@env.command()
@click.argument("name")
def new(name: str):
    """Set up and activate a fresh Maestral configuration."""
    if name in list_configs():
        click.echo("Configuration '{0}' already exists.".format(name))
    else:
        os.environ["MAESTRAL_CONFIG"] = name
        from maestral.config.main import CONF
        CONF.set("main", "default_dir_name", "Dropbox ({0})".format(name.capitalize()))
        click.echo("Created configuration '{0}'.".format(name))


@env.command(name='list')
def env_list():
    """List all Maestral configurations."""
    click.echo("Available Maestral configurations:")
    for c in list_configs():
        click.echo('  ' + c)


@env.command()
@click.argument("name")
def delete(name: str):
    """Remove a Maestral configuration."""
    if name not in list_configs():
        click.echo("Configuration '{0}' could not be found.".format(name))
    else:
        from maestral.config.base import get_conf_path
        for file in os.listdir(get_conf_path("maestral")):
            if file.startswith(name):
                os.unlink(os.path.join(get_conf_path("maestral"), file))
        click.echo("Deleted configuration '{0}'.".format(name))
