# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
import sys
import os
import os.path as osp
import stat
import platform
import subprocess
from enum import Enum

from maestral import __version__
from maestral.utils.appdirs import get_home_dir, get_conf_path
from maestral.constants import BUNDLE_ID


_root = getattr(sys, '_MEIPASS', osp.dirname(osp.abspath(__file__)))
_resources = osp.join(osp.dirname(_root), 'resources')


class SupportedImplementations(Enum):
    # sysv = 'sysv'
    systemd = 'systemd'
    launchd = 'launchd'
    xdg_desktop = 'xdg_desktop'


class AutoStartBase:

    def __init__(self, config_name, launch_command):
        self.launch_command = launch_command
        self.config_name = config_name

    def enable(self):
        pass

    def disable(self):
        pass

    @property
    def enabled(self):
        return False


class AutoStartLaunchd(AutoStartBase):

    def __init__(self, config_name, launch_command):
        super().__init__(config_name, launch_command)
        bundle_id = '{}.{}'.format(BUNDLE_ID, self.config_name)
        filename = bundle_id + '.plist'

        with open(osp.join(_resources, 'com.samschott.maestral.plist'), 'r') as f:
            plist_template = f.read()

        self.destination = osp.join(get_home_dir(), 'Library', 'LaunchAgents', filename)
        self.contents = plist_template.format(
            bundle_id=bundle_id, launch_command=launch_command
        )

    def enable(self):
        with open(self.destination, 'w+') as f:
            f.write(self.contents)

    def disable(self):
        try:
            os.unlink(self.destination)
        except FileNotFoundError:
            pass

    @property
    def enabled(self):
        return os.path.isfile(self.destination)


class AutoStartXDGDesktop(AutoStartBase):

    def __init__(self, config_name, launch_command):
        super().__init__(config_name, launch_command)
        filename = 'maestral-{}.desktop'.format(config_name)

        with open(osp.join(_resources, 'maestral.desktop'), 'r') as f:
            desktop_entry_template = f.read()

        self.destination = get_conf_path('autostart', filename, create=True)
        self.contents = desktop_entry_template.format(
            version=__version__, launch_command=launch_command
        )

    def enable(self):
        with open(self.destination, 'w+') as f:
            f.write(self.contents)

        st = os.stat(self.destination)
        os.chmod(self.destination, st.st_mode | stat.S_IEXEC)

    def disable(self):
        try:
            os.unlink(self.destination)
        except FileNotFoundError:
            pass

    @property
    def enabled(self):
        return os.path.isfile(self.destination)


class AutoStartSystemd(AutoStartBase):

    def __init__(self, config_name, launch_command):
        super().__init__(config_name, launch_command)
        self.service_name = f'maestral@{self.config_name}.service'

    def enable(self):
        subprocess.run(['systemctl', '--user', 'enable', self.service_name])

    def disable(self):
        subprocess.run(['systemctl', '--user', 'disable', self.service_name])

    @property
    def enabled(self):
        res = subprocess.call(
            ['systemctl', '--user', '--quiet', 'is-enabled', self.service_name]
        )
        return res == 0


class AutoStart:
    """Creates auto-start files in the appropriate system location to automatically
    start Maestral when the user logs in."""

    system = platform.system()

    def __init__(self, config_name, gui=False):

        self._gui = gui

        self.implementation = self._get_available_implementation()

        if hasattr(sys, '_MEIPASS'):
            launch_command = os.path.join(sys._MEIPASS, 'main')
        elif self._gui:
            launch_command = 'maestral gui -c=\'{}\''.format(config_name)
        else:
            launch_command = 'maestral start -f -c=\'{}\''.format(config_name)

        if self.implementation == SupportedImplementations.launchd:
            self._impl = AutoStartLaunchd(config_name, launch_command)
        elif self.implementation == SupportedImplementations.xdg_desktop:
            self._impl = AutoStartXDGDesktop(config_name, launch_command)
        elif self.implementation == SupportedImplementations.systemd:
            self._impl = AutoStartSystemd(config_name, launch_command)
        else:
            self._impl = AutoStartBase(config_name, launch_command)

    def toggle(self):
        self.enabled = not self.enabled

    @property
    def enabled(self):
        return self._impl.enabled

    @enabled.setter
    def enabled(self, yes):
        if yes:
            self._impl.enable()
        else:
            self._impl.disable()

    def _get_available_implementation(self):
        if self.system == 'Darwin':
            return SupportedImplementations.launchd
        elif self.system == 'Linux' and self._gui:
            return SupportedImplementations.xdg_desktop
        else:
            res = subprocess.check_output(['ps', '-p', '1'])
            if 'systemd' in str(res):
                return SupportedImplementations.systemd
            else:
                return None
