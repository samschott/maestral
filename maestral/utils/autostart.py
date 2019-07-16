#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Dec  9 23:08:47 2018

@author: samschott
"""

import platform
import sys
import os
import subprocess
import shutil

_root = os.path.abspath(os.path.dirname(__file__))


class AutoStart(object):

    def __init__(self):
        self.system = platform.system()
        if getattr(sys, "frozen", False) and self.system == "Darwin":
            self.filename = "com.samschott.maestral.plist"
            self.destination_dir = os.path.expanduser("~/Library/LaunchAgents")
        elif self.system == "Darwin":
            self.filename = "com.maestral.loginscript.plist"
            self.destination_dir = os.path.expanduser("~/Library/LaunchAgents")
        elif self.system == "Linux":
            self.filename = "maestral.desktop"
            self.destination_dir = os.path.expanduser("~/.config/autostart")
        else:
            raise OSError("Windows is not currently supported.")

        self.source = os.path.join(_root, self.filename)
        self.destination = os.path.join(self.destination_dir, self.filename)

    def enable(self):
        if not os.path.isdir(self.destination_dir):
            os.makedirs(self.destination_dir)

        shutil.copyfile(self.source, self.destination)

    def disable(self):
        if os.path.exists(self.destination):
            os.remove(self.destination)

    @property
    def enabled(self):
        return os.path.isfile(self.destination)
