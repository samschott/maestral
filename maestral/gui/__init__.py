# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import os

# add "~/.local/share" to XDG_DATA_DIRS
# this is needed to find icons installed for the current user only
XDG_DATA_DIRS = os.environ.get("XDG_DATA_DIRS") + ":" + os.path.expanduser("~/.local/share")
os.environ["XDG_DATA_DIRS"] = XDG_DATA_DIRS
