# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import sys
import os
import time
import subprocess
import platform
from enum import Enum
from pathlib import Path
import logging

import click

from maestral.config.main import MaestralConfig
from maestral.constants import IS_MACOS_BUNDLE
from maestral.utils.updates import check_version

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

_root = getattr(sys, '_MEIPASS', Path(Path(__file__).parents[2], 'gui', 'resources'))
logger = logging.getLogger(__name__)
APP_ICON_PATH = os.path.join(_root, 'maestral.png')


class SupportedImplementations(Enum):
    notify_send = 'notify-send'
    osascript = 'osascript'
    notification_center = 'notification-center'
    legacy_notification_center = 'legacy-notification-center'


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


class DesktopNotifier:
    """Send native OS notifications to user.

    Relies on AppleScript on macOS and notify-send on linux, otherwise
    falls back to stdout."""

    CRITICAL = 'critical'
    NORMAL = 'normal'
    LOW = 'low'

    def __init__(self):
        self.implementation = self._get_available_implementation()
        self._with_app_name = True  # if True, use --app-name option for nofity-send
        self._initialize_notification_center()

    def _initialize_notification_center(self):
        if self.implementation == SupportedImplementations.notification_center:
            self._nc = UNUserNotificationCenter.currentNotificationCenter()
            self._nc.requestAuthorizationWithOptions(
                (1 << 2) | (1 << 1) | (1 << 0), completionHandler=None
            )
            self._nc_identifier = 0

        elif self.implementation == SupportedImplementations.legacy_notification_center:
            self._nc = NSUserNotificationCenter.defaultUserNotificationCenter

    def send(self, title,  message, urgency=NORMAL, icon_path=None):
        if self.implementation == SupportedImplementations.notification_center:
            self._send_message_nc(title, message)
        elif self.implementation == SupportedImplementations.legacy_notification_center:
            self._send_message_nc_legacy(title, message)
        elif self.implementation == SupportedImplementations.osascript:
            self._send_message_macos_osascript(title, message)
        elif self.implementation == SupportedImplementations.notify_send:
            self._send_message_linux(title, message, urgency, icon_path)
        else:
            self._send_message_stdout(title, message, urgency)

    def _send_message_stdout(self, title, message, urgency):
        output = '{}: {}'.format(title, message)
        if urgency == self.CRITICAL:
            output = click.style(output, bold=True, fg='red')
        click.echo(output)

    def _send_message_nc(self, title, message, subtitle=None):

        content = UNMutableNotificationContent.alloc().init()
        content.title = title
        content.body = message
        if subtitle:
            content.subtitle = subtitle
        r = UNNotificationRequest.requestWithIdentifier(
            str(self._nc_identifier), content=content, trigger=None
        )
        self._nc.addNotificationRequest(r, withCompletionHandler=None)

        self._nc_identifier += 1

    def _send_message_nc_legacy(self, title, message, subtitle=None):
        notification = NSUserNotification.alloc().init()
        notification.title = title
        if subtitle:
            notification.subtitle = subtitle
        notification.informativeText = message
        notification.userInfo = {}
        notification.deliveryDate = NSDate.dateWithTimeInterval(0, sinceDate=NSDate.date())
        self._nc.scheduleNotification(notification)

    @staticmethod
    def _send_message_macos_osascript(title, message):
        subprocess.call([
            'osascript', '-e',
            'display notification "{}" with title "{}"'.format(message, title)
        ])

    def _send_message_linux(self, title, message, urgency, icon_path):
        icon_path = icon_path or ''
        if self._with_app_name:  # try passing --app-name option, disable if not supported
            r = subprocess.call([
                'notify-send', title, message,
                '-a', 'Maestral',
                '-i', icon_path,
                '-u', urgency
            ])
            self._with_app_name = r == 0

        if not self._with_app_name:
            subprocess.call([
                'notify-send', title, message,
                '-i', icon_path, '-u', urgency
            ])

    @staticmethod
    def _command_exists(command):
        return any(
            os.access(os.path.join(path, command), os.X_OK)
            for path in os.environ['PATH'].split(os.pathsep)
        )

    def _get_available_implementation(self):
        if IS_MACOS_BUNDLE and check_version(macos_version, '10.14.0', '>='):
            # UNUserNotificationCenter is currently only supported from signed app bundles
            return SupportedImplementations.notification_center
        elif platform.system() == 'Darwin' and check_version(macos_version, '10.16.0', '<'):
            return SupportedImplementations.legacy_notification_center
        elif self._command_exists('osascript'):
            return SupportedImplementations.osascript
        elif self._command_exists('notify-send'):
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
        """Returns an existing instance for the config
        or creates a new one if none exists."""

        if config_name in cls._instances:
            return cls._instances[config_name]
        else:
            instance = cls(config_name)
            cls._instances[config_name] = instance
            return instance

    def __init__(self, config_name):
        super().__init__()
        self.setFormatter(logging.Formatter(fmt="%(message)s"))
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
        """Time in minutes that we will be snoozed"""
        return max(0.0, (self._snooze - time.time())/60)

    @snoozed.setter
    def snoozed(self, minutes=30):
        self._snooze = time.time() + minutes*60

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
