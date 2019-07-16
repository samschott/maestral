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
            self.filename = "com.samschott.maestral.plist"
            self.destination_dir = os.path.expanduser("~/Library/LaunchAgents")
            if getattr(sys, "frozen", False):
                launch_command = "/Applications/Maestral.app/Contents/MacOS/main"
            else:
                launch_command = "mercury-gui"
            self.contents = _plist_template.format(launch_command)
        elif self.system == "Linux":
            self.filename = "maestral.desktop"
            self.destination_dir = os.path.expanduser("~/.config/autostart")
            launch_command = "mercury-gui"
            self.contents = _desktop_entry_template.format(launch_command)
        else:
            raise OSError("Windows is not currently supported.")

        self.source = os.path.join(_root, self.filename)
        self.destination = os.path.join(self.destination_dir, self.filename)

    def enable(self):
        if not os.path.isdir(self.destination_dir):
            os.makedirs(self.destination_dir)

        with open(self.destination, "w+") as f:
            f.write(self.contents)

    def disable(self):
        if os.path.exists(self.destination):
            os.remove(self.destination)

    @property
    def enabled(self):
        return os.path.isfile(self.destination)


_plist_template = """
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>com.samschott.maestral</string>
	<key>ProcessType</key>
	<string>Interactive</string>
	<key>ProgramArguments</key>
	<array>
		<string>{0}</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
</dict>
</plist>
"""

_desktop_entry_template = """
[Desktop Entry]
Name=Maestral
GenericName=File Synchronizer
Comment=Sync your files with Dropbox
Exec={0}
Hidden=false
Terminal=false
Type=Application
Categories=Network;FileTransfer;
StartupNotify=false
X-GNOME-Autostart-enabled=true
"""
