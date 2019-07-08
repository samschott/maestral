#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Nov 30 13:51:32 2018

@author: samschott
"""

import click


@click.group()
def main():
    """Maestral Dropbox Client for Linux and macOS."""
    pass


@main.command()
def sync():
    """Runs Maestral from the command line."""
    from maestral.main import Maestral
    m = Maestral()
    m.monitor.connection_thread.join()  # join until quit by user
    return m


@main.command()
def gui():
    """Runs Maestral with a GUI."""
    from maestral.gui.main import run
    run()


@main.command()
def configure():
    """Runs the command line configuration wizard."""
    from maestral.main import Maestral
    m = Maestral(run=False)
    m.set_dropbox_directory()
    m.select_excluded_folders()


@main.command()
def unlink():
    """Unlinks your Dropbox account."""
    import os
    from maestral.client import OAuth2Session
    os.unlink(OAuth2Session.TOKEN_FILE)


@main.command()
@click.argument('dropbox_path', type=click.Path())
@click.argument('local_path', type=click.Path())
def download(dropbox_path: str, local_path: str):
    """Downloads a file from Dropbox."""
    from maestral.client import MaestralApiClient
    client = MaestralApiClient()
    client.download(dropbox_path, local_path)


@main.command()
@click.argument('local_path', type=click.Path())
@click.argument('dropbox_path', type=click.Path())
def upload(local_path: str, dropbox_path: str):
    """Uploads a file to Dropbox."""
    from maestral.client import MaestralApiClient
    client = MaestralApiClient()
    client.upload(local_path, dropbox_path)


@main.command()
@click.argument('old_path', type=click.Path())
@click.argument('new_path', type=click.Path())
def move(old_path: str, new_path: str):
    """Moves or renames a file or folder on Dropbox."""
    from maestral.client import MaestralApiClient
    client = MaestralApiClient()
    client.move(old_path, new_path)


@main.command()
@click.argument('dropbox_path', type=click.Path())
def ls(dropbox_path: str):
    """Lists contents of a folder on Dropbox."""
    from maestral.client import MaestralApiClient
    client = MaestralApiClient()
    res = client.list_folder(dropbox_path, recursive=False)
    print("\t".join(res.keys()))


@main.command()
@click.argument('dropbox_path', type=click.Path())
def mkdir(dropbox_path: str):
    """Creates a new directory on Dropbox."""
    from maestral.client import MaestralApiClient
    client = MaestralApiClient()
    client.make_dir(dropbox_path)


@main.command()
def account_info():
    """Prints Dropbox account info."""
    from maestral.client import MaestralApiClient
    client = MaestralApiClient()
    res = client.get_account_info()
    print("%s, %s" % (res.email, res.account_type))


@main.command()
@click.option('--yes/--no', '-Y/-N', default=True)
def autostart(yes: bool):
    """Starts Maestral on login. May not work on some Linux distributions."""
    from maestral.utils.autostart import AutoStart
    ast = AutoStart()
    if yes:
        ast.enable()
    else:
        ast.disable()
