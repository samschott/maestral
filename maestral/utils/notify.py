# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
import shutil
import time
import subprocess
import platform
from packaging.version import Version
from enum import Enum
import pkg_resources
import logging

import click

from maestral.config import MaestralConfig
from maestral.constants import IS_MACOS_BUNDLE

if platform.system() == 'Darwin':

    from ctypes import cdll, util
    from rubicon.objc import ObjCClass

    uns = cdll.LoadLibrary(util.find_library('UserNotifications'))

    UNUserNotificationCenter = ObjCClass('UNUserNotificationCenter')
    UNMutableNotificationContent = ObjCClass('UNMutableNotificationContent')
    UNNotificationRequest = ObjCClass('UNNotificationRequest')

    NSUserNotification = ObjCClass('NSUserNotification')
    NSUserNotificationCenter = ObjCClass('NSUserNotificationCenter')
    NSDate = ObjCClass('NSDate')

elif platform.system() == 'Linux':
    from jeepney import MessageGenerator, new_method_call
    from jeepney.integrate.blocking import Proxy, connect_and_authenticate

    class FreedesktopNotificationsInterface(MessageGenerator):
        interface = 'org.freedesktop.Notifications'

        def __init__(self, object_path='/org/freedesktop/Notifications',
                     bus_name='org.freedesktop.Notifications'):
            super().__init__(object_path=object_path, bus_name=bus_name)

        def Notify(self, arg_0, arg_1, arg_2, arg_3, arg_4, arg_5, arg_6, arg_7):
            return new_method_call(self, 'Notify', 'susssasa{sv}i',
                                   (arg_0, arg_1, arg_2, arg_3, arg_4, arg_5, arg_6,
                                    arg_7))

        def CloseNotification(self, arg_0):
            return new_method_call(self, 'CloseNotification', 'u',
                                   (arg_0,))

        def GetCapabilities(self):
            return new_method_call(self, 'GetCapabilities')

        def GetServerInformation(self):
            return new_method_call(self, 'GetServerInformation')


logger = logging.getLogger(__name__)

APP_ICON_PATH = pkg_resources.resource_filename('maestral', 'resources/maestral.png')

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
    osascript = 'osascript'
    notification_center = 'notification-center'
    legacy_notification_center = 'legacy-notification-center'
    notify_send = 'notify-send'
    freedesktop_dbus = 'org.freedesktop.Notifications'


class DesktopNotifierBase:

    CRITICAL = 'critical'
    NORMAL = 'normal'
    LOW = 'low'

    def __init__(self, app_name=''):
        self._app_name = app_name

    def send(self, title, message, urgency=NORMAL, icon_path=None):
        raise NotImplementedError()


class DesktopNotifierStdout(DesktopNotifierBase):

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL, icon_path=None):
        if urgency == self.CRITICAL:
            title = click.style(title, bold=True, fg='red')
            message = click.style(message, bold=True, fg='red')

        click.echo(f'{title}: {message}')


class DesktopNotifierNC(DesktopNotifierBase):

    def __init__(self, app_name):
        super().__init__(app_name)
        self.nc = UNUserNotificationCenter.currentNotificationCenter()
        self.nc.requestAuthorizationWithOptions(
            (1 << 2) | (1 << 1) | (1 << 0), completionHandler=None
        )
        self._last_notification_id = 0

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL, icon_path=None):

        self._last_notification_id += 1

        content = UNMutableNotificationContent.alloc().init()
        content.title = title
        content.body = message

        notification_request = UNNotificationRequest.requestWithIdentifier(
            str(self._last_notification_id),
            content=content,
            trigger=None
        )

        self.nc.addNotificationRequest(
            notification_request,
            withCompletionHandler=None
        )


class DesktopNotifierLegacyNC(DesktopNotifierBase):

    def __init__(self, app_name):
        super().__init__(app_name)
        self.nc = NSUserNotificationCenter.defaultUserNotificationCenter

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL, icon_path=None):

        n = NSUserNotification.alloc().init()
        n.title = title
        n.informativeText = message
        n.userInfo = {}
        n.deliveryDate = NSDate.dateWithTimeInterval(0, sinceDate=NSDate.date())
        self.nc.scheduleNotification(n)


class DesktopNotifierOsaScript(DesktopNotifierBase):

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL,
             icon_path=None, action=None):
        subprocess.call([
            'osascript', '-e',
            f'display notification "{message}" with title "{title}"'
        ])


class DesktopNotifierNotifySend(DesktopNotifierBase):

    def __init__(self, app_name):
        super().__init__(app_name)
        self._with_app_name = True

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL, icon_path=None):

        icon_path = icon_path or ''
        if self._with_app_name:  # try passing --app-name option
            r = subprocess.call([
                'notify-send', title, message,
                '-a', self._app_name,
                '-i', icon_path,
                '-u', urgency
            ])
            self._with_app_name = r == 0  # disable if not supported

        if not self._with_app_name:
            subprocess.call([
                'notify-send', title, message,
                '-i', icon_path, '-u', urgency
            ])


class DesktopNotifierFreedesktopDbus(DesktopNotifierBase):

    def __init__(self, app_name):
        super().__init__(app_name)
        connection = connect_and_authenticate(bus='SESSION')
        self.proxy = Proxy(FreedesktopNotificationsInterface(), connection)
        self._last_notification_id = 0

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL, icon_path=None):

        resp = self.proxy.Notify(
            self._app_name,
            0,  # Not replacing any previous notification
            APP_ICON_PATH,  # Icon
            title,  # Summary
            message,
            [],  # Actions
            {},  # Hints
            -1,  # expire_timeout (-1 = default)
        )

        self._last_notification_id = resp[0]


class DesktopNotifier:
    """Send native OS notifications to user.

    Relies on AppleScript on macOS and notify-send on linux, otherwise
    falls back to stdout."""

    CRITICAL = 'critical'
    NORMAL = 'normal'
    LOW = 'low'

    def __init__(self, app_name):
        self.implementation = self._get_available_implementation()
        if self.implementation == SupportedImplementations.notification_center:
            self._impl = DesktopNotifierNC(app_name)
        elif self.implementation == SupportedImplementations.legacy_notification_center:
            self._impl = DesktopNotifierLegacyNC(app_name)
        elif self.implementation == SupportedImplementations.osascript:
            self._impl = DesktopNotifierOsaScript(app_name)
        elif self.implementation == SupportedImplementations.notify_send:
            self._impl = DesktopNotifierNotifySend(app_name)
        else:
            self._impl = DesktopNotifierStdout(app_name)

    def send(self, title, message, urgency=NORMAL, icon_path=None):
        self._impl.send(title, message, urgency, icon_path)

    @staticmethod
    def _get_available_implementation():
        macos_version, *_ = platform.mac_ver()

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


system_notifier = DesktopNotifier(app_name='Maestral')


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
