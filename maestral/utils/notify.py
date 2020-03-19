# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
import sys
import os
import shutil
import time
import subprocess
import platform
from packaging.version import Version
from enum import Enum
from pathlib import Path
import logging

import click

from maestral.config import MaestralConfig
from maestral.constants import IS_MACOS_BUNDLE

if platform.system() == 'Darwin':
    macos_version, *_ = platform.mac_ver()
    from ctypes import cdll, util
    from rubicon.objc import ObjCClass

    appkit = cdll.LoadLibrary(util.find_library('AppKit'))
    foundation = cdll.LoadLibrary(util.find_library('Foundation'))
    core_graphics = cdll.LoadLibrary(util.find_library('CoreGraphics'))
    core_text = cdll.LoadLibrary(util.find_library('CoreText'))
    uns = cdll.LoadLibrary(util.find_library('UserNotifications'))

    UNUserNotificationCenter = ObjCClass('UNUserNotificationCenter')
    UNMutableNotificationContent = ObjCClass('UNMutableNotificationContent')
    UNNotificationRequest = ObjCClass('UNNotificationRequest')

    NSUserNotification = ObjCClass('NSUserNotification')
    NSUserNotificationCenter = ObjCClass('NSUserNotificationCenter')
    NSDate = ObjCClass('NSDate')

else:
    macos_version = ''

logger = logging.getLogger(__name__)

_root = getattr(sys, '_MEIPASS', Path(Path(__file__).parents[1], 'resources'))
APP_ICON_PATH = os.path.join(_root, 'maestral.png')

NONE = 100
ERROR = 40
SYNCISSUE = 30
FILECHANGE = 15


_levelToName = {
    NONE: 'NONE',
    ERROR: 'ERROR',
    SYNCISSUE: 'SYNCISSUE',
    FILECHANGE: 'FILECHANGE',
}

_nameToLevel = {
    'NONE': 100,
    'ERROR': 40,
    'SYNCISSUE': 30,
    'FILECHANGE': 15,
}


def levelNumberToName(number):
    return _levelToName[number]


def levelNameToNumber(name):
    return _nameToLevel[name]


class SupportedImplementations(Enum):
    notify_send = 'notify-send'
    osascript = 'osascript'
    notification_center = 'notification-center'
    legacy_notification_center = 'legacy-notification-center'


class DesktopNotifierBase:

    CRITICAL = 'critical'
    NORMAL = 'normal'
    LOW = 'low'

    def __init__(self):
        pass

    def send(self, title, message, urgency=NORMAL, icon_path=None):
        raise NotImplementedError()


class DesktopNotifierStdout(DesktopNotifierBase):

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL, icon_path=None):
        if urgency == self.CRITICAL:
            title = click.style(title, bold=True, fg='red')
            message = click.style(message, bold=True, fg='red')

        click.echo(f'{title}: {message}')


class DesktopNotifierNC(DesktopNotifierBase):

    def __init__(self):
        super().__init__()
        self.nc = UNUserNotificationCenter.currentNotificationCenter()
        self.nc.requestAuthorizationWithOptions(
            (1 << 2) | (1 << 1) | (1 << 0), completionHandler=None
        )
        self.nc_identifier = 0

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL, icon_path=None):
        content = UNMutableNotificationContent.alloc().init()
        content.title = title
        content.body = message

        r = UNNotificationRequest.requestWithIdentifier(
            str(self.nc_identifier), content=content, trigger=None
        )
        self.nc.addNotificationRequest(r, withCompletionHandler=None)

        self.nc_identifier += 1


class DesktopNotifierLegacyNC(DesktopNotifierBase):

    def __init__(self):
        super().__init__()
        self.nc = NSUserNotificationCenter.defaultUserNotificationCenter

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL, icon_path=None):
        n = NSUserNotification.alloc().init()
        n.title = title
        n.informativeText = message
        n.userInfo = {}
        n.deliveryDate = NSDate.dateWithTimeInterval(0, sinceDate=NSDate.date())
        self.nc.scheduleNotification(n)


class DesktopNotifierOsaScript(DesktopNotifierBase):

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL, icon_path=None):
        subprocess.call([
            'osascript', '-e',
            f'display notification "{message}" with title "{title}"'
        ])


class DesktopNotifierNotifySend(DesktopNotifierBase):

    def __init__(self):
        super().__init__()
        self._with_app_name = True

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL, icon_path=None):
        icon_path = icon_path or ''
        if self._with_app_name:  # try passing --app-name option
            r = subprocess.call([
                'notify-send', title, message,
                '-a', 'Maestral',
                '-i', icon_path,
                '-u', urgency
            ])
            self._with_app_name = r == 0  # disable if not supported

        if not self._with_app_name:
            subprocess.call([
                'notify-send', title, message,
                '-i', icon_path, '-u', urgency
            ])


class DesktopNotifier:
    """Send native OS notifications to user.

    Relies on AppleScript on macOS and notify-send on linux, otherwise
    falls back to stdout."""

    CRITICAL = 'critical'
    NORMAL = 'normal'
    LOW = 'low'

    def __init__(self):
        self.implementation = self._get_available_implementation()
        if self.implementation == SupportedImplementations.notification_center:
            self._impl = DesktopNotifierNC()
        elif self.implementation == SupportedImplementations.legacy_notification_center:
            self._impl = DesktopNotifierLegacyNC()
        elif self.implementation == SupportedImplementations.osascript:
            self._impl = DesktopNotifierOsaScript()
        elif self.implementation == SupportedImplementations.notify_send:
            self._impl = DesktopNotifierNotifySend()
        else:
            self._impl = DesktopNotifierStdout()

    def send(self, title, message, urgency=NORMAL, icon_path=None):
        self._impl.send(title, message, urgency, icon_path)

    @staticmethod
    def _get_available_implementation():
        if IS_MACOS_BUNDLE and Version(macos_version) >= Version('10.14.0'):
            # UNUserNotificationCenter is only supported from signed app bundles
            return SupportedImplementations.notification_center
        elif platform.system() == 'Darwin' and Version(macos_version) < Version('10.16.0'):
            return SupportedImplementations.legacy_notification_center
        elif shutil.which('osascript'):
            return SupportedImplementations.osascript
        elif shutil.which('notify-send'):
            return SupportedImplementations.notify_send
        return None


system_notifier = DesktopNotifier()


class MaestralDesktopNotifier(logging.Handler):
    """
    Can be used as a standalone notifier or as a logging handler.
    When used as a logging handler, the log level should be set with ``setLevel``. The
    ``notify_level`` will be applied in addition to the log level.
    """

    _instances = {}

    @classmethod
    def for_config(cls, config_name):
        """
        Returns an existing instance for the config or creates a new one if none exists.
        """

        if config_name in cls._instances:
            return cls._instances[config_name]
        else:
            instance = cls(config_name)
            cls._instances[config_name] = instance
            return instance

    def __init__(self, config_name):
        super().__init__()
        self.setFormatter(logging.Formatter(fmt='%(message)s'))
        self._conf = MaestralConfig(config_name)
        self._snooze = 0

    @property
    def notify_level(self):
        """Custom notification level."""
        return self._conf.get('app', 'notification_level')

    @notify_level.setter
    def notify_level(self, level):
        """Setter: Custom notification level."""
        assert isinstance(level, int)
        self._conf.set('app', 'notification_level', level)

    @property
    def snoozed(self):
        """
        Time in minutes to snooze notifications. Applied to FILECHANGE level only.
        """
        return max(0.0, (self._snooze - time.time()) / 60)

    @snoozed.setter
    def snoozed(self, minutes):
        self._snooze = time.time() + minutes * 60

    def notify(self, message, level):

        ignore = self.snoozed and level == FILECHANGE

        if level >= self.notify_level and not ignore:
            system_notifier.send(
                title='Maestral',
                message=message,
                icon_path=APP_ICON_PATH
            )

    def emit(self, record):
        self.format(record)
        self.notify(record.message, level=record.levelno)
