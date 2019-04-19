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
    """Runs Maestral as a command line tool."""
    from maestral.main import Maestral
    m = Maestral()
    m.monitor.connection_thread.join()  # join until quit by user
    return m


@main.command()
def gui():
    """Runs Maestral with a status bar based GUI."""
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
@click.argument('dropbox_path')
@click.argument('local_path')
def download(dropbox_path: str, local_path: str):
    """Downloads a file from Dropbox."""
    from maestral.client import MaestralClient
    client = MaestralClient()
    client.download(dropbox_path, local_path)


@main.command()
@click.argument('local_path')
@click.argument('dropbox_path')
def upload(local_path: str, dropbox_path: str):
    """Uploads a file to Dropbox."""
    from maestral.client import MaestralClient
    client = MaestralClient()
    client.upload(local_path, dropbox_path)


@main.command()
@click.argument('old_path')
@click.argument('new_path')
def move(old_path: str, new_path: str):
    """Moves or renames a file or folder on Dropbox."""
    from maestral.client import MaestralClient
    client = MaestralClient()
    client.move(old_path, new_path)


@main.command()
@click.argument('dropbox_path')
def ls(dropbox_path: str):
    """Lists contents of a folder on Dropbox."""
    from maestral.client import MaestralClient
    client = MaestralClient()
    res = client.list_folder(dropbox_path, recursive=False)
    print("\t".join(res.keys()))


@main.command()
@click.argument('dropbox_path')
def mkdir(dropbox_path: str):
    """Creates a new directory on Dropbox."""
    from maestral.client import MaestralClient
    client = MaestralClient()
    client.make_dir(dropbox_path)


@main.command()
def account_info():
    """Prints Dropbox account info."""
    from maestral.client import MaestralClient
    client = MaestralClient()
    res = client.get_account_info()
    print("%s, %s" % (res.email, res.account_type))
