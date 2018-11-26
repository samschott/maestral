#!python

import os
import sys

type = sys.argv[1]
message = sys.argv[2]

if type == "add":
    print("File " + message + " has been added to your Dropbox.")

elif type == "rm":
    print("File " + message + " has been removed from your Dropbox.")

elif type == "upd":
    print("File " + message + " has been updated.")

elif type == "link":
    print('Link publicly:')
    print(message)
