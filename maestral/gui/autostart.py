#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Dec  9 23:08:47 2018

@author: samschott
"""

import platform
import sys
import os

from maestral.sync.utils.app_dirs import get_autostart_path
from maestral.sync.utils import is_macos_bundle
from maestral import __version__

_root = os.path.abspath(os.path.dirname(__file__))


class AutoStart(object):
    """Creates auto-start entries in the appropriate system location to automatically
    start Maestral when the user logs in."""

    def __init__(self):
        system = platform.system()
        config_name = os.getenv('MAESTRAL_CONFIG', 'maestral')

        if is_macos_bundle:
            launch_command = os.path.join(sys._MEIPASS, "main")
        else:
            launch_command = "maestral gui --config-name='{}'".format(config_name)

        if system == "Darwin":
            app_name = "com.samschott.maestral.{}".format(config_name)
            filename = app_name + ".plist"
            self.contents = _plist_template.format(app_name, launch_command)
        elif system == "Linux":
            filename = "maestral-{}.desktop".format(config_name)
            self.contents = _desktop_entry_template.format(__version__, launch_command)
        else:
            raise OSError("Windows is not currently supported.")

        self.destination = get_autostart_path(filename)

    def enable(self):

        with open(self.destination, "w+") as f:
            f.write(self.contents)

    def disable(self):
        if os.path.exists(self.destination):
            os.remove(self.destination)

    def toggle(self):
        if self.enabled:
            self.disable()
        else:
            self.enable()

    @property
    def enabled(self):
        return os.path.isfile(self.destination)


_plist_template = """
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>{0}</string>
	<key>ProcessType</key>
	<string>Interactive</string>
	<key>ProgramArguments</key>
	<array>
		<string>{1}</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
</dict>
</plist>
"""

_desktop_entry_template = """
[Desktop Entry]
Version={0}
Type=Application
Name=Maestral
GenericName=File Synchronizer
Comment=Sync your files with Dropbox
Exec={1}
Hidden=false
Terminal=false
Type=Application
Categories=Network;FileTransfer;
StartupNotify=false
X-GNOME-Autostart-enabled=true
X-DBUS-ServiceName=maestral
"""
