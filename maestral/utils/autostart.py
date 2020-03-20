# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
import sys
import os
import os.path as osp
import shutil
import stat
import platform
import subprocess
from enum import Enum

try:
    from importlib.metadata import files
except ImportError:
    from importlib_metadata import files

from maestral import __version__
from maestral.utils.appdirs import get_home_dir, get_conf_path, get_data_path
from maestral.constants import BUNDLE_ID


_root = getattr(sys, '_MEIPASS', osp.dirname(osp.abspath(__file__)))
_resources = osp.join(osp.dirname(_root), 'resources')


class SupportedImplementations(Enum):
    systemd = 'systemd'
    launchd = 'launchd'
    xdg_desktop = 'xdg_desktop'


class AutoStartBase:

    def __init__(self, config_name, gui):
        self.config_name = config_name
        self.gui = gui

    def enable(self):
        raise NotImplementedError('No supported implementation')

    def disable(self):
        raise NotImplementedError('No supported implementation')

    @property
    def enabled(self):
        return False


class AutoStartMaestralBase(AutoStartBase):

    def __init__(self, config_name, gui):
        super().__init__(config_name, gui)

        self.config_opt = f'-c \'{self.config_name}\''

        if hasattr(sys, '_MEIPASS'):  # PyInstaller bundle
            self.maestral_path = os.path.join(sys._MEIPASS, 'main')
            self.start_cmd = f'{self.maestral_path} {self.config_opt}'
            self.stop_cmd = ''
        else:
            self.maestral_path = self.get_maestral_command_path()

            if self.gui:
                self.start_cmd = f'{self.maestral_path} gui {self.config_opt}'
                self.stop_cmd = ''
            else:
                self.start_cmd = f'{self.maestral_path} start -f {self.config_opt}'
                self.stop_cmd = f'{self.maestral_path} stop {self.config_opt}'

    @staticmethod
    def get_maestral_command_path():
        # try to get location of console script from package metadata
        # fall back to 'which' otherwise
        try:
            pkg_path = next(p for p in files('maestral')
                            if str(p).endswith('/bin/maestral'))
            path = pkg_path.locate().resolve()
        except StopIteration:
            path = ''

        if not osp.isfile(path):
            path = shutil.which('maestral')

        return path

    def enable(self):
        if self.maestral_path:
            self._enable()
        else:
            raise OSError('Could not find path of maestral executable')

    def disable(self):
        self._disable()

    def _enable(self):
        raise NotImplementedError()

    def _disable(self):
        raise NotImplementedError()


class AutoStartSystemd(AutoStartMaestralBase):

    def __init__(self, config_name, gui):
        super().__init__(config_name, gui)

        if self.gui:
            raise ValueError('Systemd launching is not supported for the GUI. '
                             'This may change in a future release.')

        service_type = 'gui' if self.gui else 'daemon'
        self.service_name = f'maestral-{service_type}@{self.config_name}.service'

        with open(osp.join(_resources, 'maestral@.service'), 'r') as f:
            unit_template = f.read()

        filename = 'maestral-{}@.service'.format('gui' if self.gui else 'daemon')
        self.destination = get_data_path(osp.join('systemd', 'user'), filename)
        self.contents = unit_template.format(
            start_cmd=f'{self.maestral_path} start -f',
            stop_cmd=f'{self.maestral_path} stop',
        )

        with open(self.destination, 'w') as f:
            f.write(self.contents)

    def _enable(self):
        subprocess.run(['systemctl', '--user', 'enable', self.service_name])

    def _disable(self):
        subprocess.run(['systemctl', '--user', 'disable', self.service_name])

    @property
    def enabled(self):
        res = subprocess.call(
            ['systemctl', '--user', '--quiet', 'is-enabled', self.service_name]
        )
        return res == 0


class AutoStartLaunchd(AutoStartMaestralBase):

    def __init__(self, config_name, gui):
        super().__init__(config_name, gui)
        if self.gui:
            bundle_id = '{}.{}'.format(BUNDLE_ID, self.config_name)
        else:
            bundle_id = '{}-{}.{}'.format(BUNDLE_ID, 'daemon', self.config_name)
        filename = bundle_id + '.plist'

        with open(osp.join(_resources, 'com.samschott.maestral.plist'), 'r') as f:
            plist_template = f.read()

        self.destination = osp.join(get_home_dir(), 'Library', 'LaunchAgents', filename)
        self.contents = plist_template.format(
            bundle_id=bundle_id,
            start_cmd=self.start_cmd
        )

    def _enable(self):
        with open(self.destination, 'w+') as f:
            f.write(self.contents)

    def _disable(self):
        try:
            os.unlink(self.destination)
        except FileNotFoundError:
            pass

    @property
    def enabled(self):
        return os.path.isfile(self.destination)


class AutoStartXDGDesktop(AutoStartMaestralBase):

    def __init__(self, config_name, gui):
        super().__init__(config_name, gui)

        if not gui:
            raise ValueError('XDG Desktop entries are only supported to launch the GUI')

        filename = f'maestral-{config_name}.desktop'

        with open(osp.join(_resources, 'maestral.desktop'), 'r') as f:
            desktop_entry_template = f.read()

        self.destination = get_conf_path('autostart', filename)
        self.contents = desktop_entry_template.format(
            version=__version__,
            start_cmd=self.start_cmd
        )

    def _enable(self):
        with open(self.destination, 'w+') as f:
            f.write(self.contents)

        st = os.stat(self.destination)
        os.chmod(self.destination, st.st_mode | stat.S_IEXEC)

    def _disable(self):
        try:
            os.unlink(self.destination)
        except FileNotFoundError:
            pass

    @property
    def enabled(self):
        return os.path.isfile(self.destination)


class AutoStart:
    """Creates auto-start files in the appropriate system location to automatically
    start Maestral when the user logs in."""

    system = platform.system()

    def __init__(self, config_name, gui=False):

        self._gui = gui

        self.implementation = self._get_available_implementation()

        if self.implementation == SupportedImplementations.launchd:
            self._impl = AutoStartLaunchd(config_name, gui)
        elif self.implementation == SupportedImplementations.xdg_desktop:
            self._impl = AutoStartXDGDesktop(config_name, gui)
        elif self.implementation == SupportedImplementations.systemd:
            self._impl = AutoStartSystemd(config_name, gui)
        else:
            self._impl = AutoStartBase(config_name, gui)

    def toggle(self):
        self.enabled = not self.enabled

    @property
    def enabled(self):
        return self._impl.enabled

    @enabled.setter
    def enabled(self, yes):

        if self.enabled == yes:
            return

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
            res = subprocess.check_output(['ps', '-p', '1']).decode()
            if 'systemd' in res:
                return SupportedImplementations.systemd
            else:
                return None
