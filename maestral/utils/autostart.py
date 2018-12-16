#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Dec  9 23:08:47 2018

@author: samschott
"""

import platform
import os
import shutil

_root = os.path.abspath(os.path.dirname(__file__))


class AutoStart(object):

    def __init__(self):
        system = platform.system()
        if system == 'Darwin':
            self.filename = "com.maestral.loginscript.plist"
            self.distnation_dir = os.path.expanduser("~/Library/LaunchAgents")
        elif system == 'Linux':
            self.filename = "maestral.desktop"
            self.distnation_dir = os.path.expanduser("~/.config/autostart")
        else:
            raise OSError("Windods is not currently supported.")

        self.source = os.path.join(_root, self.filename)
        self.destination = os.path.join(self.distnation_dir, self.filename)

    def enable(self):
        if not os.path.isdir(self.distnation_dir):
            os.makedirs(self.distnation_dir)

        shutil.copyfile(self.source, self.destination)

    def disable(self):
        if os.path.exists(self.destination):
            os.remove(self.destination)

    @property
    def enabled(self):
        return os.path.isfile(self.destination)
