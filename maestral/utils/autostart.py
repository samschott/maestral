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

Note that launchd agents will not show as "login items" in macOS system preferences. As
a result, the user does not have a convenient UI to remove Maestral autostart entries
manually outside of Maestral itself. Login items however only support app bundles and
provide no option to pass command line arguments to the app. They would therefore neither
support pip installs or multiple configurations.

"""

# system imports
import sys
import os
import os.path as osp
import re
import shutil
import stat
import platform
import subprocess
import shlex
from enum import Enum

try:
    # noinspection PyCompatibility
    from importlib.metadata import files, PackageNotFoundError
except ImportError:  # Python 3.7 and lower
    from importlib_metadata import files, PackageNotFoundError

# local imports
from maestral.utils.appdirs import get_home_dir, get_conf_path, get_data_path
from maestral.constants import BUNDLE_ID


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
    """

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


class AutoStartSystemd(AutoStartBase):
    """
    Autostart backend for systemd. Used to start a daemon on Linux.

    :param str service_name: Name of systemd service.
    :param str start_cmd: Absolute path to executable and optional program arguments.
    :param str stop_cmd: Optional stop command.
    :param bool notify: If ``True``, the service will be started as a notify service.
        Otherwise, the type will be "exec".
    :param int watchdog_sec: If given, this is the number of seconds for systemd watchdog.
    :param dict unit_dict: Dictionary of additional keys and values for the Unit section.
    :param dict service_dict: Dictionary of additional keys and values for the Service
        section.
    :param dict install_dict: Dictionary of additional keys and values for the Install
        section.
    """
    def __init__(self, service_name, start_cmd, stop_cmd=None,
                 notify=False, watchdog_sec=None, unit_dict=None,
                 service_dict=None, install_dict=None):
        super().__init__()

        self.service_name = service_name
        # strip any instance specifiers from template service name
        filename = re.sub(r'@[^"]*\.service', '@.service', service_name)
        self.destination = get_data_path(osp.join('systemd', 'user'), filename)

        service_type = 'notify' if notify else 'exec'

        if unit_dict:
            self.contents = '[Unit]\n'
            for key, value in unit_dict.items():
                self.contents += f'{key} = {value}\n'
            self.contents += '\n'

        self.contents += '[Service]\n'
        self.contents += f'Type = {service_type}\n'
        self.contents += 'NotifyAccess = exec\n'
        self.contents += f'ExecStart = {start_cmd}\n'
        if stop_cmd:
            self.contents += f'ExecStop = {stop_cmd}\n'
        if watchdog_sec:
            self.contents += f'WatchdogSec = {watchdog_sec}s\n'
        if service_dict:
            for key, value in service_dict.items():
                self.contents += f'{key} = {value}\n'
        self.contents += '\n'

        self.contents += '[Install]\n'
        self.contents += 'WantedBy = default.target\n'
        if install_dict:
            for key, value in install_dict.items():
                self.contents += f'{key} = {value}\n'
        self.contents += '\n'

        with open(self.destination, 'w') as f:
            f.write(self.contents)

    def enable(self):
        subprocess.run(['systemctl', '--user', 'enable', self.service_name])

    def disable(self):
        subprocess.run(['systemctl', '--user', 'disable', self.service_name])

    @property
    def enabled(self):
        """Checks if the systemd service is enabled."""
        res = subprocess.call(
            ['systemctl', '--user', '--quiet', 'is-enabled', self.service_name]
        )
        return res == 0


class AutoStartLaunchd(AutoStartBase):
    """
    Autostart backend for launchd. Used to start a GUI or daemon on macOS.

    :param str bundle_id: Bundle ID for the, e.g., "com.google.calendar".
    :param str start_cmd: Absolute path to executable and optional program arguments.
    """

    template = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{bundle_id}</string>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>ProgramArguments</key>
    <array>
{start_cmd}
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>"""

    def __init__(self, bundle_id, start_cmd):

        super().__init__()
        filename = bundle_id + '.plist'

        self.path = osp.join(get_home_dir(), 'Library', 'LaunchAgents')
        self.destination = osp.join(self.path, filename)

        start_cmd = shlex.split(start_cmd)
        arguments = [f'\t\t<string>{arg}</string>' for arg in start_cmd]

        self.contents = self.template.format(
            bundle_id=bundle_id,
            start_cmd='\n'.join(arguments)
        )

    def enable(self):
        os.makedirs(self.path, exist_ok=True)

        with open(self.destination, 'w+') as f:
            f.write(self.contents)

    def disable(self):
        try:
            os.unlink(self.destination)
        except FileNotFoundError:
            pass

    @property
    def enabled(self):
        """Checks if the launchd plist exists in ~/Library/LaunchAgents."""
        return os.path.isfile(self.destination)


class AutoStartXDGDesktop(AutoStartBase):
    """
    Autostart backend for XDG desktop entries. Used to start a GUI on user login for most
    Linux desktops. For a full specifications, please see:

    https://specifications.freedesktop.org/desktop-entry-spec/desktop-entry-spec-latest.html

    :param str Name: Name of application.
    :param str Exec: Executable on $PATH or absolute path to executable and optional
        program arguments.
    :param str filename: Name of desktop entry file. If not given, "NAME.desktop" will be
        used.
    :param kwargs: Additional key, value pairs to be used in the desktop entries.
        Values must be strings and may not contain "=", otherwise no additional validation
        will be performed.
    """

    def __init__(self, Name, Exec, filename=None, **kwargs):
        super().__init__()

        self._attributes = {'Version': '1.0', 'Type': 'Application',
                            'Name': Name, 'Exec': Exec}
        self._attributes.update(kwargs)

        # create desktop file content

        self.contents = '[Desktop Entry]\n'

        for key, value in self._attributes.items():

            # input validation

            if not isinstance(value, str):
                raise ValueError('Only strings allowed as values')
            if '=' in value:
                raise ValueError(f'Value for {key} may not contain "="')

            self.contents += f'{key} = {value}\n'

        # set destination

        filename = filename or f'{Name}.desktop'
        self.destination = get_conf_path('autostart', filename)

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
        """Checks if the XDG desktop entry exists in ~/.config/autostart."""
        return os.path.isfile(self.destination)


class AutoStart:
    """Creates auto-start files in the appropriate system location to automatically
    start Maestral when the user logs in. Different backends are used depending on the
    platform and if we want to start a GUI or a daemon / service."""

    system = platform.system()

    def __init__(self, config_name, gui=False):

        self._gui = gui
        self.maestral_path = self.get_maestral_command_path()
        self.implementation = self._get_available_implementation()

        if gui:
            start_cmd = f'{self.maestral_path} gui -c {config_name}'
            stop_cmd = ''
            bundle_id = '{}.{}'.format(BUNDLE_ID, config_name)
        else:
            start_cmd = f'{self.maestral_path} start -f -c {config_name}'
            stop_cmd = f'{self.maestral_path} stop -c {config_name}'
            bundle_id = '{}-{}.{}'.format(BUNDLE_ID, 'daemon', config_name)

        if self.implementation == SupportedImplementations.launchd:
            self._impl = AutoStartLaunchd(bundle_id, start_cmd)

        elif self.implementation == SupportedImplementations.xdg_desktop:
            self._impl = AutoStartXDGDesktop(
                filename=f'maestral-{config_name}.desktop',
                Name='Maestral',
                Exec=start_cmd,
                TryExec=self.maestral_path,
                Icon='maestral',
                Terminal='false',
                Categories='Network;FileTransfer;',
                GenericName='File Synchronizer',
                Comment='Sync your files with Dropbox',
            )

        elif self.implementation == SupportedImplementations.systemd:

            notify_failure = ('if [ ${SERVICE_RESULT} != success ]; '
                              'then notify-send Maestral \'Daemon failed: ${SERVICE_RESULT}\'; '
                              'fi')

            self._impl = AutoStartSystemd(
                service_name=f'maestral-daemon@{config_name}.service',
                start_cmd=start_cmd.replace(config_name, '%i'),
                stop_cmd=stop_cmd.replace(config_name, '%i'),
                notify=True,
                watchdog_sec=30,
                unit_dict={'Description': 'Maestral daemon for the config %i'},
                service_dict={'ExecStopPost': f'/usr/bin/env bash -c "{notify_failure}"'}
            )

        else:
            self._impl = AutoStartBase()

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
            if self.maestral_path:
                self._impl.enable()
            else:
                raise OSError('Could not find path of maestral executable')
        else:
            self._impl.disable()

    def get_maestral_command_path(self):
        """
        Returns the path to the maestral executable.
        """
        # try to get location of console script from package metadata
        # fall back to 'which' otherwise

        if self._gui and getattr(sys, 'frozen', False):
            return sys.executable

        try:
            pkg_path = next(p for p in files('maestral')
                            if str(p).endswith('/bin/maestral'))
            path = pkg_path.locate().resolve()
        except (StopIteration, PackageNotFoundError):
            path = ''

        if not osp.isfile(path):
            path = shutil.which('maestral')

        return str(path)

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
