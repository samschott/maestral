#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Nov 30 13:51:32 2018

@author: samschott
"""


def run():

    import sys

    # generate sys.argv dictionary
    if len(sys.argv) > 1:
        parameters = sys.argv[2:]
        wtd = sys.argv[1]
    else:
        wtd = "--sync"

    if wtd == "--client":
        from maestral.client import MaestralClient

        print("""Maestral
    (c) Sam Schott, 2018
    made with Dropbox SDK from https://www.dropbox.com/developers/reference/sdk \n""")
        client = MaestralClient()

        if parameters[0] == "get":
            client.download(parameters[1], parameters[2])
        elif parameters[0] == "put":
            client.upload(parameters[1], parameters[2])
        elif parameters[0] == "mv":
            client.move(parameters[1], parameters[2])
        elif parameters[0] == "rm":
            client.remove(parameters[1])
        elif parameters[0] == "ls":
            res = client.list_folder(parameters[1], recursive=False)
            print("\t".join(res.keys()))
        elif parameters[0] == "mkdir":
            client.make_dir(parameters[1])
        elif parameters[0] == "account-info":
            res = client.get_account_info()
            print("%s, %s" % (res.email, res.account_type))

    elif wtd == "--help":
        print("""
    Syntax: maestral [<OPTION>] [<PARAMETERS>]

    Starts the Maestral syncing app in the command line.

     --help          - displays this text
     --gui           - runs Maestral with status bar based GUI
     --sync          - runs Maestral as command line tool
     --configuration - runs configuration wizard
     --unlink        - unlinks Maestral from your Dropbox account but keeps
                       your downloaded files in place
     --client        - runs Maestral API Client
       syntax: maestral --client [parameter1] [parameter2] [parameter3]
        get    [from_path] [to_path]   - downloads file
        put    [from_path] [to_path]   - uploads file
        mv     [from_path] [to_path]   - moves and renames file
        rm     [path]                  - removes a file
        ls     [<path>]                - creates a list of files in (root) directory
        mkdir  [path]                  - creates a directory
        account-info                   - gets Dropbox account info
        """)

    elif wtd == "--configuration":
        from maestral.main import Maestral

        m = Maestral(run=False)
        m.set_dropbox_directory()
        m.select_excluded_folders()

    elif wtd == "--sync":
        from maestral.main import Maestral
        m = Maestral()

    elif wtd == "--gui":
        from maestral.gui.main import run
        run()

    elif wtd == "--unlink":
        from maestral.main import Maestral
        m = Maestral(run=False)
        m.unlink()

    else:
        print("Invalid syntax. Type maestral --help for more information.")


if __name__ == "__main__":
    run()
