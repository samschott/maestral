#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Nov 30 13:51:32 2018

@author: samschott
"""

import sys

# generate sys.argv dictionary
if len(sys.argv) > 1:
    parameters = sys.argv[2:]
    wtd = sys.argv[1]
else:
    wtd = "brick"

if wtd == "--client":
    from sisyphosdbx import client

    print("""Orphilia
Maciej Janiszewski, 2010-2013
made with Dropbox SDK from https://www.dropbox.com/developers/reference/sdk \n""")
    client.client(parameters)

elif wtd == "--help":
    print("""
Syntax: orphilia [OPTION] [PARAMETERS]

 --help          - displays this text
 --monitor       - monitors Dropbox folder activity
 --delta         - monitors server-side activity
 --configuration - runs configuration wizard
 --public        - generates public links
 --client        - runs Orphilia API Client
   syntax: orphilia --client [parameter1] [parameter2] [parameter3]
    get   [from path] [to path] - downloads file
    put   [from path] [to path] - uploads file
    mv    [from path] [to path] - moves and renames file
    rm    [path]                - removes a file
    ls    [path]      [to file] - creates a list of files in directory
    mkdir [path]                - creates a directory
    uid   [path]                - gets current accounts Dropbox UID""")

elif wtd == "--configuration":
    from orphilia import config

    config.config()

elif wtd == "--monitor":
    from orphiliaclient import monitor

    monitor.monitor()

elif wtd == "--delta":
    from orphiliaclient import delta

    delta.monitor()

elif wtd == "--public":
    from orphiliaclient import client

    client.getPublicLink(parameters)

else:
    print("Invalid syntax. Type orphilia --help for more informations")