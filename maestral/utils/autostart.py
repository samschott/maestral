# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

This module handles starting Maestral on user login and supports multiple platform
specific backends such as launchd or systemd. Additionally, this module also provides
support for GUIs via launchd or xdg-desktop entries by passing the ``gui`` option to the
``maestral`` command or executable. Therefore, only GUIs which are explicitly supported by
the CLI with the `maestral gui` command or frozen executables which provide their own GUI
are supported.

"""

# system imports
import sys
import os
import os.path as osp
import shutil
import stat
import platform
import subprocess
import pkg_resources
from enum import Enum

try:
    # noinspection PyCompatibility
    from importlib.metadata import files
except ImportError:  # Python 3.7 and lower
    from importlib_metadata import files

try:
    from shlex import join
except ImportError:  # Python 3.7 and lower
    from shlex import quote

    def join(split_command):
        return ' '.join(quote(x) for x in split_command)

# local imports
from maestral import __version__
from maestral.utils.appdirs import get_home_dir, get_conf_path, get_data_path
from maestral.constants import BUNDLE_ID


_resources = pkg_resources.resource_filename('maestral', 'resources')


class SupportedImplementations(Enum):
    """
    Enumeration of supported implementations.

    :cvar str systemd: macOS systemd.
    :cvar str launchd: Linux launchd.
    :cvar str xdg_desktop: Linux autostart xdg desktop entries.
    """
    systemd = 'systemd'
    launchd = 'launchd'
    xdg_desktop = 'xdg_desktop'


class AutoStartBase:
    """
    Base class for autostart backends.

    :param str config_name: Name of the config to start.
    :param bool gui: ``True`` if we are starting a GUI, ``False`` otherwise.
    """

    def __init__(self, config_name, gui):
        self.config_name = config_name
        self.gui = gui

    def enable(self):
        """Enable autostart. Must be implemented in subclass."""
        raise NotImplementedError('No supported implementation')

    def disable(self):
        """Disable autostart. Must be implemented in subclass."""
        raise NotImplementedError('No supported implementation')

    @property
    def enabled(self):
        """Returns the enabled status as bool. Must be implemented in subclass."""
        return False


class AutoStartMaestralBase(AutoStartBase):
    """
    Base class for Maestral autostart backends.

    :param str config_name: Name of the config to start.
    :param bool gui: ``True`` if we are starting a GUI, ``False`` otherwise.
    """

    def __init__(self, config_name, gui):
        super().__init__(config_name, gui)

        self.maestral_path = self.get_maestral_command_path()

        if self.gui:
            self.start_cmd = [self.maestral_path, 'gui', '-c', self.config_name]
            self.stop_cmd = []
        else:
            self.start_cmd = [self.maestral_path, 'start', '-f', '-c', self.config_name]
            self.stop_cmd = [self.maestral_path, 'stop', '-c', self.config_name]

    @staticmethod
    def get_maestral_command_path():
        """
        Returns the path to the maestral executable.
        """
        # try to get location of console script from package metadata
        # fall back to 'which' otherwise

        if getattr(sys, 'frozen', False):  # app bundle
            return sys.executable

        try:
            pkg_path = next(p for p in files('maestral')
                            if str(p).endswith('/bin/maestral'))
            path = pkg_path.locate().resolve()
        except StopIteration:
            path = ''

        if not osp.isfile(path):
            path = shutil.which('maestral')

        return str(path)

    def enable(self):
        """
        Enables the autostart.

        :raises: :class:`OSError` if the Maestral executable could not be found.
        """
        if self.maestral_path:
            self._enable()
        else:
            raise OSError('Could not find path of maestral executable')

    def disable(self):
        """
        Disables the autostart.
        """
        self._disable()

    def _enable(self):
        """Private method to enable autostart. This should be overridden in a subclass."""
        raise NotImplementedError()

    def _disable(self):
        """Private method to disable autostart. This should be overridden in a
        subclass."""
        raise NotImplementedError()


class AutoStartSystemd(AutoStartMaestralBase):
    """
    Autostart backend for systemd. Used to start a GUI or daemon on macOS.

    :param str config_name: Name of the config to start.
    :param bool gui: ``True`` if we are starting a GUI, ``False`` otherwise.
    """
    def __init__(self, config_name, gui):
        super().__init__(config_name, gui)

        if self.gui:
            raise ValueError('Systemd launching is not supported for the GUI. '
                             'This may change in a future release.')

        service_type = 'gui' if self.gui else 'daemon'
        self.service_name = f'maestral-{service_type}@{self.config_name}.service'

        with open(osp.join(_resources, 'maestral@.service')) as f:
            unit_template = f.read()

        filename = 'maestral-{}@.service'.format('gui' if self.gui else 'daemon')
        self.destination = get_data_path(osp.join('systemd', 'user'), filename)
        self.contents = unit_template.format(
            start_cmd=join(self.start_cmd),
            stop_cmd=join(self.stop_cmd),
        )

        with open(self.destination, 'w') as f:
            f.write(self.contents)

    def _enable(self):
        subprocess.run(['systemctl', '--user', 'enable', self.service_name])

    def _disable(self):
        subprocess.run(['systemctl', '--user', 'disable', self.service_name])

    @property
    def enabled(self):
        """Checks if the systemd service is enabled."""
        res = subprocess.call(
            ['systemctl', '--user', '--quiet', 'is-enabled', self.service_name]
        )
        return res == 0


class AutoStartLaunchd(AutoStartMaestralBase):
    """
    Autostart backend for launchd. Used to start a GUI or daemon on macOS.

    :param str config_name: Name of the config to start.
    :param bool gui: ``True`` if we are starting a GUI, ``False`` otherwise.
    """

    def __init__(self, config_name, gui):
        super().__init__(config_name, gui)
        if self.gui:
            bundle_id = '{}.{}'.format(BUNDLE_ID, self.config_name)
        else:
            bundle_id = '{}-{}.{}'.format(BUNDLE_ID, 'daemon', self.config_name)
        filename = bundle_id + '.plist'

        with open(osp.join(_resources, 'com.samschott.maestral.plist')) as f:
            plist_template = f.read()

        self.path = osp.join(get_home_dir(), 'Library', 'LaunchAgents')
        self.destination = osp.join(self.path, filename)

        arguments = [f'\t\t<string>{arg}</string>' for arg in self.start_cmd]

        self.contents = plist_template.format(
            bundle_id=bundle_id,
            start_cmd='\n'.join(arguments)
        )

    def _enable(self):
        os.makedirs(self.path, exist_ok=True)

        with open(self.destination, 'w+') as f:
            f.write(self.contents)

    def _disable(self):
        try:
            os.unlink(self.destination)
        except FileNotFoundError:
            pass

    @property
    def enabled(self):
        """Checks if the launchd plist exists in ~/Library/LaunchAgents."""
        return os.path.isfile(self.destination)


class AutoStartXDGDesktop(AutoStartMaestralBase):
    """
    Autostart backend for XDG desktop entries. Used to start a GUI on user login for most
    Linux desktops.

    :param str config_name: Name of the config to start.
    :param bool gui: ``True`` if we are starting a GUI. If ``False``, a
        :class:`ValueError` is raised.
    """

    def __init__(self, config_name, gui):
        super().__init__(config_name, gui)

        if not gui:
            raise ValueError('XDG Desktop entries are only supported to launch the GUI')

        filename = f'maestral-{config_name}.desktop'

        with open(osp.join(_resources, 'maestral.desktop')) as f:
            desktop_entry_template = f.read()

        self.destination = get_conf_path('autostart', filename)
        self.contents = desktop_entry_template.format(
            version=__version__,
            start_cmd=join(self.start_cmd)
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
        """Checks if the XDG desktop entry exists in ~/.config/autostart."""
        return os.path.isfile(self.destination)


class AutoStart:
    """Creates auto-start files in the appropriate system location to automatically
    start Maestral when the user logs in. Different backends are used depending on the
    platform and if we want to start a GUI or a daemon / service."""

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
        """Toggles autostart on or off."""
        self.enabled = not self.enabled

    @property
    def enabled(self):
        """True if autostart is enabled."""
        return self._impl.enabled

    @enabled.setter
    def enabled(self, yes):
        """Setter: True if autostart is enabled."""

        if self.enabled == yes:
            return

        if yes:
            self._impl.enable()
        else:
            self._impl.disable()

    def _get_available_implementation(self):
        """Returns the supported implementation depending on the platform."""

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
