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
        if self.system == "Darwin":
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
        if getattr(sys, "frozen", False) and self.system == "Darwin":
            # running in a bundle in macOS
            subprocess.Popen(
                'osascript -e \'tell application "System Events" to make login item at '
                'end with properties {path:"/Applications/Maestral.app", hidden:false}\'',
                shell=True)
        else:
            if not os.path.isdir(self.destination_dir):
                os.makedirs(self.destination_dir)

            shutil.copyfile(self.source, self.destination)

    def disable(self):
        if getattr(sys, "frozen", False) and self.system == "Darwin":
            # running in a bundle in macOS
            subprocess.Popen(
                'osascript -e \'tell application "System Events" to delete login '
                'item "Maestral"\'', shell=True)
        else:
            if os.path.exists(self.destination):
                os.remove(self.destination)

    @property
    def enabled(self):
        if getattr(sys, "frozen", False) and self.system == "Darwin":
            res = subprocess.check_output(
                'osascript -e \'tell application "System Events" to get the name of '
                'every login item\'', shell=True)
            return "Maestral" in str(res)
        else:
            return os.path.isfile(self.destination)
