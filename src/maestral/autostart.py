# -*- coding: utf-8 -*-
"""
This module handles starting the maestral daemon on user login and supports multiple
platform specific backends such as launchd or systemd.

Note that launchd agents will not show as "login items" in macOS system preferences. As
a result, the user does not have a convenient UI to remove Maestral autostart entries
manually outside of Maestral itself. Login items however only support app bundles and
provide no option to pass command line arguments to the app. They would therefore
neither support pip installed packages or multiple configurations.
"""

# system imports
import os
import os.path as osp
import re
import shutil
import stat
import platform
import subprocess
import shlex
import plistlib
import configparser
from pathlib import Path
from enum import Enum
from typing import Optional, Dict, Any

try:
    from importlib.metadata import files, PackageNotFoundError  # type: ignore
except ImportError:  # Python 3.7 and lower
    from importlib_metadata import files, PackageNotFoundError  # type: ignore

# local imports
from maestral.utils.appdirs import get_home_dir, get_conf_path, get_data_path
from maestral.constants import BUNDLE_ID


class SupportedImplementations(Enum):
    """Enumeration of supported implementations"""

    systemd = "systemd"
    launchd = "launchd"
    xdg_desktop = "xdg_desktop"


class AutoStartBase:
    """Base class for autostart backends"""

    def enable(self) -> None:
        """Enable autostart. Must be implemented in subclass."""
        raise NotImplementedError("No supported implementation")

    def disable(self) -> None:
        """Disable autostart. Must be implemented in subclass."""
        raise NotImplementedError("No supported implementation")

    @property
    def enabled(self) -> bool:
        """Returns the enabled status as bool. Must be implemented in subclass."""
        return False


class AutoStartSystemd(AutoStartBase):
    """Autostart backend for systemd

    :param service_name: Name of systemd service.
    :param start_cmd: Absolute path to executable and optional program arguments.
    :param unit_dict: Dictionary of additional keys and values for the Unit section.
    :param service_dict: Dictionary of additional keys and values for the Service
        section.
    :param install_dict: Dictionary of additional keys and values for the Install
        section.
    """

    def __init__(
        self,
        service_name: str,
        start_cmd: str,
        unit_dict: Optional[Dict[str, str]] = None,
        service_dict: Optional[Dict[str, str]] = None,
        install_dict: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__()

        # strip any instance specifiers from template service name
        filename = re.sub(r'@[^"]*\.service', "@.service", service_name)

        self.service_name = service_name
        self.destination = get_data_path(osp.join("systemd", "user"), filename)

        self.service_config = configparser.ConfigParser(interpolation=None)
        # set to preserve key casing
        self.service_config.optionxform = str  # type: ignore

        self.service_config.add_section("Unit")
        self.service_config.add_section("Service")
        self.service_config.add_section("Install")

        # fill out some default values for a minimum systemd unit
        self.service_config["Service"]["Type"] = "exec"
        self.service_config["Service"]["ExecStart"] = start_cmd
        self.service_config["Install"]["WantedBy"] = "default.target"

        # update with user-specified dicts
        if unit_dict:
            self.service_config["Unit"].update(unit_dict)
        if service_dict:
            self.service_config["Service"].update(service_dict)
        if install_dict:
            self.service_config["Install"].update(install_dict)

        # write to file in ~/.local/share/systemd/user
        with open(self.destination, "w") as f:
            self.service_config.write(f)

    def enable(self) -> None:
        subprocess.check_output(["systemctl", "--user", "enable", self.service_name])

    def disable(self) -> None:
        subprocess.check_output(["systemctl", "--user", "disable", self.service_name])

    @property
    def enabled(self) -> bool:
        """Checks if the systemd service is enabled."""
        res = subprocess.call(
            ["systemctl", "--user", "--quiet", "is-enabled", self.service_name]
        )
        return res == 0


class AutoStartLaunchd(AutoStartBase):
    """Autostart backend for launchd

    :param bundle_id: Bundle ID for the, e.g., "com.google.calendar".
    :param start_cmd: Absolute path to executable and optional program arguments.
    """

    def __init__(self, bundle_id: str, start_cmd: str) -> None:

        super().__init__()
        filename = bundle_id + ".plist"

        self.path = osp.join(get_home_dir(), "Library", "LaunchAgents")
        self.destination = osp.join(self.path, filename)

        self.plist_dict: Dict[str, Any] = dict(
            Label=None, ProcessType="Interactive", ProgramArguments=[], RunAtLoad=True
        )

        self.plist_dict["Label"] = str(bundle_id)
        self.plist_dict["ProgramArguments"] = shlex.split(start_cmd)

    def enable(self) -> None:
        os.makedirs(self.path, exist_ok=True)

        with open(self.destination, "wb") as f:
            plistlib.dump(self.plist_dict, f, sort_keys=False)

    def disable(self) -> None:
        try:
            os.unlink(self.destination)
        except FileNotFoundError:
            pass

    @property
    def enabled(self) -> bool:
        """Checks if the launchd plist exists in ~/Library/LaunchAgents."""
        return os.path.isfile(self.destination)


class AutoStartXDGDesktop(AutoStartBase):
    """Autostart backend for XDG desktop entries

    Used to start a GUI on user login for most Linux desktops. For a full
    specifications, please see:

    https://specifications.freedesktop.org/desktop-entry-spec/desktop-entry-spec-latest.html

    :param Name: Name of application.
    :param Exec: Executable on $PATH or absolute path to executable and optional program
        arguments.
    :param filename: Name of desktop entry file. If not given, the application name will
        be used.
    :param kwargs: Additional key, value pairs to be used in the desktop entries. Values
        must be strings and may not contain "=", otherwise no additional validation will
        be performed.
    """

    def __init__(
        self, app_name: str, start_cmd: str, filename: Optional[str], **kwargs: str
    ) -> None:
        super().__init__()

        # create desktop file content
        self.config = configparser.ConfigParser(interpolation=None)
        # set to preserve key casing
        self.config.optionxform = str  # type: ignore

        self.config["Desktop Entry"] = {
            "Version": "1.0",
            "Type": "Application",
            "Name": app_name,
            "Exec": start_cmd,
        }
        self.config["Desktop Entry"].update(kwargs)

        filename = filename or f"{app_name}.desktop"
        self.destination = get_conf_path("autostart", filename)

    def enable(self) -> None:

        with open(self.destination, "w") as f:
            self.config.write(f, space_around_delimiters=False)

        st = os.stat(self.destination)
        os.chmod(self.destination, st.st_mode | stat.S_IEXEC)

    def disable(self) -> None:
        try:
            os.unlink(self.destination)
        except FileNotFoundError:
            pass

    @property
    def enabled(self) -> bool:
        """Checks if the XDG desktop entry exists in ~/.config/autostart."""
        return os.path.isfile(self.destination)


def get_available_implementation() -> Optional[SupportedImplementations]:
    """Returns the supported implementation depending on the platform."""

    system = platform.system()

    if system == "Darwin":
        return SupportedImplementations.launchd
    else:
        try:
            res = subprocess.check_output(["ps", "-p", "1"]).decode()
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None
        else:
            return SupportedImplementations.systemd if "systemd" in res else None


def get_maestral_command_path() -> str:
    """
    Returns the path to the maestral executable. May be an empty string if the
    executable cannot be found.
    """

    try:
        dist_files = files("maestral")
    except PackageNotFoundError:
        # we may have had installation issues
        dist_files = []

    path: Optional[os.PathLike]

    if dist_files:
        try:
            rel_path = next(p for p in dist_files if p.match("**/bin/maestral"))
            path = rel_path.locate()
        except StopIteration:
            path = None
    else:
        path = None

    if isinstance(path, Path):
        # resolve any symlinks and “..” components
        path = path.resolve()

    if path and osp.isfile(path):
        return str(path)
    else:
        return shutil.which("maestral") or ""


class AutoStart:
    """Starts Maestral on user log-in

    Creates auto-start files in the appropriate system location to automatically start
    Maestral when the user logs in. Different backends are used depending on the
    platform.

    :param config_name: Name of Maestral config.
    """

    _impl: AutoStartBase

    def __init__(self, config_name: str) -> None:

        self.maestral_path = get_maestral_command_path()
        self.implementation = get_available_implementation()

        start_cmd = f"{self.maestral_path} start -f -c {config_name}"
        bundle_id = f"{BUNDLE_ID}-daemon.{config_name}"

        if self.implementation == SupportedImplementations.launchd:
            self._impl = AutoStartLaunchd(bundle_id, start_cmd)

        elif self.implementation == SupportedImplementations.xdg_desktop:
            self._impl = AutoStartXDGDesktop(
                filename=f"maestral-{config_name}.desktop",
                app_name="Maestral",
                start_cmd=start_cmd,
                TryExec=self.maestral_path,
                Icon="maestral",
                Terminal="false",
                GenericName="File Synchronizer",
                Comment="Sync your files with Dropbox",
            )

        elif self.implementation == SupportedImplementations.systemd:

            notify_failure = (
                "if [ ${SERVICE_RESULT} != success ]; "
                "then notify-send Maestral 'Daemon failed: ${SERVICE_RESULT}'; "
                "fi"
            )

            self._impl = AutoStartSystemd(
                service_name=f"maestral-daemon@{config_name}.service",
                start_cmd=f"{self.maestral_path} start -f -c %i",
                unit_dict={"Description": "Maestral daemon for the config %i"},
                service_dict={
                    "Type": "notify",
                    "WatchdogSec": "30",
                    "ExecStop": f"{self.maestral_path} stop -c %i",
                    "ExecStopPost": f'/usr/bin/env bash -c "{notify_failure}"',
                },
            )

        else:
            self._impl = AutoStartBase()

    @property
    def enabled(self) -> bool:
        """True if autostart is enabled."""
        return self._impl.enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        if value:
            self.enable()
        else:
            self.disable()

    def toggle(self) -> None:
        """Toggles autostart on or off."""
        if self.enabled:
            self.disable()
        else:
            self.enable()

    def enable(self) -> None:
        """Setter: True if autostart is enabled."""

        if self.enabled:
            return

        if self.maestral_path:
            self._impl.enable()
        else:
            raise OSError("Could not find path of maestral executable")

    def disable(self) -> None:
        """Setter: True if autostart is enabled."""

        if not self.enabled:
            return

        self._impl.disable()
