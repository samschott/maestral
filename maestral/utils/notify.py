# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

This module handles desktop notifications for Maestral and supports multiple backends,
depending on the platform. A single :class:`DesktopNotifier` instance is created for all
all sync daemons and a :class:`MaestralDesktopNotifier` instance is created for each
daemon individually. Notification settings such as as snoozing and levels can be modified
through :class:`MaestralDesktopNotifier`.

:constant int NONE: No desktop notifications.
:constant int ERROR: Notifications on errors.
:constant int SYNCISSUE: Notifications on sync issues.
:constant int FILECHANGE: Notifications on file changes.

"""

# system imports
import shutil
import time
import subprocess
import platform
from packaging.version import Version
from enum import Enum
import pkg_resources
import logging
from collections import deque
import threading

# external imports
import click

# local imports
from maestral.config import MaestralConfig
from maestral.constants import IS_MACOS_BUNDLE, BUNDLE_ID


# platform dependent imports
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
    from jeepney.integrate.blocking import Proxy, connect_and_authenticate
    from jeepney.wrappers import DBusErrorResponse
    from maestral.utils.dbus_interfaces import FreedesktopNotifications


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
    """Converts a Maestral notification level number to name."""
    return _levelToName[number]


def levelNameToNumber(name):
    """Converts a Maestral notification level name to number."""
    return _nameToLevel[name]


class SupportedImplementations(Enum):
    """
    Enumeration of supported implementations.

    :cvar str osascript: Apple script notifications.
    :cvar str notification_center: macOS UNUserNotificationCenter.
    :cvar str legacy_notification_center: macOS NSNotificationCenter.
    :cvar str notify_send: Linux notify-send command..
    :cvar str freedesktop_dbus: Linux dbus notifications.
    :cvar str stdout: Notify by printing to stdout.
    """
    osascript = 'osascript'
    notification_center = 'UNUserNotificationCenter'
    legacy_notification_center = 'NSUserNotificationCenter'
    notify_send = 'notify-send'
    freedesktop_dbus = 'org.freedesktop.Notifications'
    stdout = 'print'


class DesktopNotifierBase:
    """
    Base class for desktop notifications. Notification levels CRITICAL, NORMAL and LOW may
    be used by some implementations to determine how a notification is displayed.

    :param str app_name: Name to identify the application in the notification center.
        On Linux, this should correspond to the application name in a desktop entry. On
        macOS, this field is discarded and the app is identified by the bundle id of the
        sending program (e.g., Python).
    :param int notification_limit: Maximum number of notifications to keep in the system's
        notification center. This may be ignored by some implementations.
    """

    CRITICAL = 'critical'
    NORMAL = 'normal'
    LOW = 'low'

    def __init__(self, app_name='', notification_limit=5):
        self.app_name = app_name
        self.notification_limit = notification_limit

    def send(self, title, message, urgency=NORMAL, icon_path=None):
        """Some arguments may be ignored, depending on the implementation."""
        raise NotImplementedError()


class DesktopNotifierStdout(DesktopNotifierBase):
    """Stdout backend for all platforms."""

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL, icon_path=None):
        if urgency == self.CRITICAL:
            title = click.style(title, bold=True, fg='red')
            message = click.style(message, bold=True, fg='red')

        click.echo(f'{title}: {message}')


class DesktopNotifierNC(DesktopNotifierBase):
    """UNUserNotificationCenter backend for macOS. For macOS Catalina and newer."""

    def __init__(self, app_name):
        super().__init__(app_name)
        self.nc = UNUserNotificationCenter.alloc().initWithBundleIdentifier(BUNDLE_ID)
        self.nc.requestAuthorizationWithOptions(
            (1 << 2) | (1 << 1) | (1 << 0), completionHandler=None
        )
        self._last_notification_id = 0

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL, icon_path=None):

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

        self._last_notification_id += 1
        self._last_notification_id %= self.notification_limit


class DesktopNotifierLegacyNC(DesktopNotifierBase):
    """NSUserNotificationCenter backend for macOS. Pre macOS Catalina."""

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
    """Apple script backend for macOS."""

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL,
             icon_path=None, action=None):
        subprocess.call([
            'osascript', '-e',
            f'display notification "{message}" with title "{title}"'
        ])


class DesktopNotifierNotifySend(DesktopNotifierBase):
    """Notify-send backend for Linux."""
    def __init__(self, app_name):
        super().__init__(app_name)
        self._with_app_name = True

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL, icon_path=None):

        icon_path = icon_path or ''
        if self._with_app_name:  # try passing --app-name option
            r = subprocess.call([
                'notify-send', title, message,
                '-a', self.app_name,
                '-i', icon_path,
                '-u', urgency
            ])
            self._with_app_name = r == 0  # disable if not supported

        if not self._with_app_name:
            subprocess.call([
                'notify-send', title, message,
                '-i', icon_path, '-u', urgency
            ])


class DesktopNotifierFreedesktopDBus(DesktopNotifierBase):
    """DBus notification backend for Linux. This implements the
    org.freedesktop.Notifications standard."""

    def __init__(self, app_name):
        super().__init__(app_name)
        self._connection = connect_and_authenticate(bus='SESSION')
        self._proxy = Proxy(FreedesktopNotifications(), self._connection)
        self.capabilities = self._proxy.GetCapabilities()
        self.server_information = self._proxy.GetServerInformation()

        self._past_notification_ids = deque([0] * self.notification_limit)

    def send(self, title, message, urgency=DesktopNotifierBase.NORMAL, icon_path=None):

        replace_id = self._past_notification_ids.popleft()

        try:
            resp = self._proxy.Notify(
                self.app_name,
                replace_id,
                APP_ICON_PATH,
                title,
                message,
                [],  # Actions
                {},  # Hints
                -1,  # expire_timeout (-1 = default)
            )
        except DBusErrorResponse:
            # This may fail for several reasons: there may not be a systemd service
            # file for 'org.freedesktop.Notifications' or the system configuration
            # may have changed after DesktopNotifierFreedesktopDBus was initialized.
            logger.debug('Failed to send desktop notification', exc_info=True)
        else:
            self._past_notification_ids.append(resp[0])

    def __del__(self):
        try:
            self._connection.close()
        except Exception:
            pass


class DesktopNotifier:
    """
    Cross-platform desktop notifications for macOS and Linux. Uses different backends
    depending on the platform version and available services.

    :param str app_name: Name of sending app.
    """

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
        elif self.implementation == SupportedImplementations.freedesktop_dbus:
            self._impl = DesktopNotifierFreedesktopDBus(app_name)
        elif self.implementation == SupportedImplementations.notify_send:
            self._impl = DesktopNotifierNotifySend(app_name)
        else:
            self._impl = DesktopNotifierStdout(app_name)

        logger.debug(f'DesktopNotifier implementation: {self.implementation.value}')

    def send(self, title, message, urgency=NORMAL, icon=None):
        """
        Sends a desktop notification. Some arguments may be ignored, depending on the
        backend.

        :param str title: Notification title.
        :param str message: Notification message.
        :param str urgency: Notification urgency. Some backends use this to determine how
            the notification is displayed.
        :param Optional[str] icon: Path to an icon. Some backends support displaying an
            (app) icon together with the notification.
        """
        self._impl.send(title, message, urgency, icon)

    @staticmethod
    def _get_available_implementation():
        macos_version, *_ = platform.mac_ver()

        if platform.system() == 'Darwin':
            if (IS_MACOS_BUNDLE and Version(macos_version) >= Version('10.14.0')
                    and UNUserNotificationCenter.currentNotificationCenter()):
                # UNUserNotificationCenter is only supported from signed app bundles
                return SupportedImplementations.notification_center
            elif (Version(macos_version) < Version('10.16.0')
                  and NSUserNotificationCenter.defaultUserNotificationCenter):
                # deprecated but still works
                return SupportedImplementations.legacy_notification_center
            elif shutil.which('osascript'):
                # fallback
                return SupportedImplementations.osascript
        elif platform.system() == 'Linux':
            try:
                DesktopNotifierFreedesktopDBus('test')
                return SupportedImplementations.freedesktop_dbus
            except Exception:
                pass

            if shutil.which('notify-send'):
                return SupportedImplementations.notify_send

        return SupportedImplementations.stdout


system_notifier = DesktopNotifier(app_name='Maestral')


class MaestralDesktopNotifier(logging.Handler):
    """
    Can be used as a standalone notifier or as a logging handler.
    When used as a logging handler, the log level should be set with ``setLevel``. The
    ``notify_level`` will be applied in addition to the log level.
    """

    _instances = dict()
    _lock = threading.Lock()

    @classmethod
    def for_config(cls, config_name):
        """
        Returns an existing instance for the config or creates a new one if none exists.
        Use this method to prevent creating multiple instances.

        :param str config_name: Name of maestral config.
        """

        with cls._lock:
            try:
                return cls._instances[config_name]
            except KeyError:
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
        """Custom notification level. Notifications with a lower level will be
        discarded."""
        return self._conf.get('app', 'notification_level')

    @notify_level.setter
    def notify_level(self, level):
        """Setter: notify_level."""
        assert isinstance(level, int)
        self._conf.set('app', 'notification_level', level)

    @property
    def snoozed(self):
        """Time in minutes to snooze notifications. Applied to FILECHANGE level only."""
        return max(0.0, (self._snooze - time.time()) / 60)

    @snoozed.setter
    def snoozed(self, minutes):
        """Setter: snoozed."""
        self._snooze = time.time() + minutes * 60

    def notify(self, message, level=FILECHANGE):
        """
        Sends a desktop notification from maestral. The title defaults to 'Maestral'.

        :param str message: Notification message.
        :param int level: Notification level of the message.
        """

        ignore = self.snoozed and level == FILECHANGE
        if level == ERROR:
            urgency = system_notifier.CRITICAL
        else:
            urgency = system_notifier.NORMAL

        if level >= self.notify_level and not ignore:
            system_notifier.send(
                title='Maestral',
                message=message,
                icon=APP_ICON_PATH,
                urgency=urgency
            )

    def emit(self, record):
        """Emits a log record as a desktop notification."""
        if record.name != logger.name:  # avoid recursions
            self.format(record)
            self.notify(record.message, level=record.levelno)
