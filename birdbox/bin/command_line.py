#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Nov 30 13:51:32 2018

@author: samschott
"""


def main():

    import sys

    # generate sys.argv dictionary
    if len(sys.argv) > 1:
        parameters = sys.argv[2:]
        wtd = sys.argv[1]
    else:
        wtd = "--sync"

    if wtd == "--client":
        from birdbox.client import BirdBoxClient

        print("""BirdBox
    (c) Sam Schott, 2018
    made with Dropbox SDK from https://www.dropbox.com/developers/reference/sdk \n""")
        client = BirdBoxClient()

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
    Syntax: birdbox [<OPTION>] [<PARAMETERS>]

     --help          - displays this text
     --gui           - runs BirdBox with status bar based GUI
     --sync          - runs BirdBox as command line client
     --configuration - runs configuration wizard
     --unlink        - unlinks BirdBox from your Dropbox account but keeps
                       your downloaded files in place
     --client        - runs BirdBox API Client
       syntax: birdbox --client [parameter1] [parameter2] [parameter3]
        get    [from_path] [to_path]   - downloads file
        put    [from_path] [to_path]   - uploads file
        mv     [from_path] [to_path]   - moves and renames file
        rm     [path]                  - removes a file
        ls     [<path>]                - creates a list of files in (root) directory
        mkdir  [path]                  - creates a directory
        account-info                   - gets Dropbox account info
        """)

    elif wtd == "--configuration":
        from birdbox import BirdBox

        sdbx = BirdBox(run=False)
        sdbx.set_dropbox_directory()
        sdbx.select_excluded_folders()

    elif wtd == "--sync":
        from birdbox import BirdBox
        sdbx = BirdBox()

    elif wtd == "--sync":
        from birdbox.gui.main import run
        run()

    elif wtd == "--unlink":
        from birdbox import BirdBox
        sdbx = BirdBox(run=False)
        sdbx.unlink()

    else:
        print("Invalid syntax. Type birdbox --help for more informations")
