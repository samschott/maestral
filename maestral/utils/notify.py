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
import sys
import os.path as osp
import shutil
import time
import subprocess
import platform
from packaging.version import Version
from enum import Enum
import pkg_resources
import logging
from threading import Lock, Thread
import asyncio
import traceback
from typing import Optional, Dict, ClassVar, Callable, Union

# local imports
from maestral.config import MaestralConfig
from maestral.constants import IS_MACOS_BUNDLE, BUNDLE_ID


logger = logging.getLogger(__name__)

# platform dependent imports
if platform.system() == 'Darwin':

    from rubicon.objc import ObjCClass, objc_method  # type: ignore
    from rubicon.objc.runtime import load_library  # type: ignore

    uns = load_library('UserNotifications')

    if uns:
        UNUserNotificationCenter = ObjCClass('UNUserNotificationCenter')
        UNMutableNotificationContent = ObjCClass('UNMutableNotificationContent')
        UNNotificationRequest = ObjCClass('UNNotificationRequest')

        NSUserNotification = ObjCClass('NSUserNotification')
        NSUserNotificationCenter = ObjCClass('NSUserNotificationCenter')
        NSDate = ObjCClass('NSDate')
    else:
        logger.debug('Cannot load library "UserNotifications"')

elif platform.system() == 'Linux':
    from dbus_next.errors import DBusError
    from dbus_next.aio import MessageBus

    uns = None

else:
    uns = None


_resources = getattr(sys, '_MEIPASS',
                     pkg_resources.resource_filename('maestral', 'resources'))
APP_ICON_PATH = osp.join(_resources, 'maestral.png')


if uns:
    # subclass UNUserNotificationCenter and define delegate method
    # to handle clicked notifications

    class CocoaNotificationCenter(UNUserNotificationCenter):

        @objc_method
        def userNotificationCenter_didReceive_withCompletionHandler_(self, nc, response, completion_handler) -> None:

            nid = int(response.request.identifier)
            notification = self.interface.current_notifications.get(nid)

            if response.actionIdentifier == response.UNNotificationDefaultActionIdentifier:

                callback = notification.action

                if callback:
                    callback()

            elif response.actionIdentifier != response.UNNotificationDismissActionIdentifier:

                callback = notification.buttons[response.actionIdentifier]

                if callback:
                    callback()

            completion_handler()


class SupportedImplementations(Enum):
    """
    Enumeration of supported implementations.
    """
    un_notification_center = 'UNUserNotificationCenter'
    ns_notification_center = 'NSUserNotificationCenter'
    notify_send = 'notify-send'
    freedesktop_dbus = 'org.freedesktop.Notifications'


class NotificationLevel(Enum):
    """
    Enumeration of notification levels.
    """
    Critical = 'critical'
    Normal = 'normal'
    Low = 'low'


class Notification:
    """
    A desktop notification

    :param title: Notification title.
    :param message: Notification message.
    :param urgency: Notification level: low, normal or critical. This is ignored by some
        implementations.
    :param icon: Path to an icon to use for the notification, typically the app icon.
        This is ignored by some implementations, e.g., on macOS where the icon of the app
        bundle is always used.
    :param action: Handler to call when the notification is clicked. This is ignored by
        some implementations.
    :param buttons: A dictionary with button names and callbacks to show in the
        notification. This is ignored by some implementations.

    :attr identifier: An identifier which gets assigned to the notification after it is sent.
    """

    identifier: Union[str, int, None]

    def __init__(self,
                 title: str,
                 message: str,
                 urgency: NotificationLevel = NotificationLevel.Normal,
                 icon: Optional[str] = None,
                 action: Optional[Callable] = None,
                 buttons: Optional[Dict[str, Optional[Callable]]] = None) -> None:

        self.title = title
        self.message = message
        self.urgency = urgency
        self.icon = icon
        self.action = action
        self.buttons = buttons or dict
        self.identifier = None


class DesktopNotifierBase:
    """
    Base class for desktop notifications. Notification levels CRITICAL, NORMAL and LOW may
    be used by some implementations to determine how a notification is displayed.

    :param app_name: Name to identify the application in the notification center. On
        Linux, this should correspond to the application name in a desktop entry. On
        macOS, this field is discarded and the app is identified by the bundle id of the
        sending program (e.g., Python).
    :param notification_limit: Maximum number of notifications to keep in the system's
        notification center. This may be ignored by some implementations.
    """

    app_name: str
    notification_limit: int

    def __init__(self, app_name: str = '', notification_limit: int = 5) -> None:
        self.app_name = app_name
        self.notification_limit = notification_limit
        self.current_notifications: Dict[int, Notification] = dict()
        self._current_nid = 0

    def send(self, notification: Notification) -> None:
        """Some arguments may be ignored, depending on the implementation."""
        raise NotImplementedError()

    def _next_nid(self) -> int:
        self._current_nid += 1
        self._current_nid %= self.notification_limit
        return self._current_nid


class DesktopNotifierNC(DesktopNotifierBase):
    """UNUserNotificationCenter backend for macOS. For macOS Catalina and newer."""

    def __init__(self, app_name: str, app_id: str) -> None:
        super().__init__(app_name)
        self.nc = CocoaNotificationCenter.alloc().initWithBundleIdentifier(app_id)
        self.nc.delegate = self.nc
        self.nc.interface = self

        self.nc.requestAuthorizationWithOptions(
            (1 << 2) | (1 << 1) | (1 << 0), completionHandler=None
        )

    def send(self, notification: Notification) -> None:

        nid = self._next_nid()
        notification_to_replace = self.current_notifications.get(nid)

        if notification_to_replace:
            replace_id = notification_to_replace.identifier
        else:
            replace_id = str(nid)

        content = UNMutableNotificationContent.alloc().init()
        content.title = notification.title
        content.body = notification.message

        notification_request = UNNotificationRequest.requestWithIdentifier(
            replace_id,
            content=content,
            trigger=None
        )

        self.nc.addNotificationRequest(
            notification_request,
            withCompletionHandler=None
        )

        notification.identifier = str(nid)
        self.current_notifications[nid] = notification


class DesktopNotifierLegacyNC(DesktopNotifierBase):
    """NSUserNotificationCenter backend for macOS. Pre macOS Catalina."""

    def __init__(self, app_name: str) -> None:
        super().__init__(app_name)
        self.nc = NSUserNotificationCenter.defaultUserNotificationCenter

    def send(self, notification: Notification) -> None:

        nid = self._next_nid()
        notification_to_replace = self.current_notifications.get(nid)

        if notification_to_replace:
            replace_id = notification_to_replace.identifier
        else:
            replace_id = str(nid)

        n = NSUserNotification.alloc().init()
        n.title = notification.title
        n.informativeText = notification.message
        n.identifier = replace_id
        n.userInfo = {}
        n.deliveryDate = NSDate.dateWithTimeInterval(0, sinceDate=NSDate.date())

        self.nc.scheduleNotification(n)

        notification.identifier = str(nid)
        self.current_notifications[nid] = notification


class DesktopNotifierNotifySend(DesktopNotifierBase):
    """Notify-send backend for Linux."""

    _to_native_urgency = {
        NotificationLevel.Low: 'low',
        NotificationLevel.Normal: 'normal',
        NotificationLevel.Critical: 'critical',
    }

    def __init__(self, app_name: str) -> None:
        super().__init__(app_name)
        self._with_app_name = True

    def send(self, notification: Notification) -> None:

        if self._with_app_name:  # try passing --app-name option
            r = subprocess.call([
                'notify-send',
                notification.title,
                notification.message,
                '-a', self.app_name,
                '-i', notification.icon or '',
                '-u', self._to_native_urgency[notification.urgency]
            ])
            self._with_app_name = r == 0  # disable if not supported

        if not self._with_app_name:
            subprocess.call([
                'notify-send',
                notification.title,
                notification.message,
                '-i', notification.icon or '',
                '-u', self._to_native_urgency[notification.urgency]
            ])


class DesktopNotifierFreedesktopDBus(DesktopNotifierBase):
    """DBus notification backend for Linux. This implements the
    org.freedesktop.Notifications standard."""

    _to_native_urgency = {
        NotificationLevel.Low: 0,
        NotificationLevel.Normal: 1,
        NotificationLevel.Critical: 2,
    }

    def __init__(self, app_name: str) -> None:
        super().__init__(app_name)

        self._loop = asyncio.new_event_loop()
        self._thread = Thread(
            target=self.start_background_loop,
            args=(self._loop,),
            daemon=True
        )
        self._thread.start()

        asyncio.run_coroutine_threadsafe(self._init_dbus(), self._loop)

    def start_background_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    async def _init_dbus(self):
        self.bus = await MessageBus().connect()
        introspection = await self.bus.introspect('org.freedesktop.Notifications',
                                                  '/org/freedesktop/Notifications')
        self.proxy_object = self.bus.get_proxy_object('org.freedesktop.Notifications',
                                                      '/org/freedesktop/Notifications',
                                                      introspection)
        self.interface = self.proxy_object.get_interface('org.freedesktop.Notifications')
        self.interface.on_notification_closed(self._on_clicked)

    def send(self, notification: Notification) -> None:

        asyncio.run_coroutine_threadsafe(self._send(notification), self._loop)

    async def _send(self, notification: Notification) -> None:

        nid = self._next_nid()
        notification_to_replace = self.current_notifications.get(nid)

        if notification_to_replace:
            replace_id = notification_to_replace.identifier
        else:
            replace_id = 0

        try:
            resp = await self.interface.call_notify(
                self.app_name,         # app_name
                replace_id,            # replaces_id
                APP_ICON_PATH,         # app_icon
                notification.title,    # summary
                notification.message,  # body
                [],                    # actions
                {},
                -1,                    # expire_timeout (-1 = default)
            )
        except DBusError:
            # This may fail for several reasons: there may not be a systemd service
            # file for 'org.freedesktop.Notifications' or the system configuration
            # may have changed after DesktopNotifierFreedesktopDBus was initialized.
            logger.debug('Failed to send desktop notification', exc_info=True)

        else:
            notification.identifier = resp
            self.current_notifications[nid] = notification
            print('sent')

    def _on_clicked(self, nid, reason):

        nid, reason = int(nid), int(reason)
        notification = next(iter(n for n in self.current_notifications.values() if n.identifier == nid), None)

        if notification and notification.action:
            notification.action()


class DesktopNotifier:
    """
    Cross-platform desktop notifications for macOS and Linux. Uses different backends
    depending on the platform version and available services.

    :param app_name: Name of sending app.
    """

    _impl: Optional[DesktopNotifierBase]

    def __init__(self, app_name: str, app_id: str) -> None:
        self._lock = Lock()
        self.implementation = self._get_available_implementation()

        if self.implementation == SupportedImplementations.un_notification_center:
            self._impl = DesktopNotifierNC(app_name, app_id)
        elif self.implementation == SupportedImplementations.ns_notification_center:
            self._impl = DesktopNotifierLegacyNC(app_name)
        elif self.implementation == SupportedImplementations.freedesktop_dbus:
            self._impl = DesktopNotifierFreedesktopDBus(app_name)
        elif self.implementation == SupportedImplementations.notify_send:
            self._impl = DesktopNotifierNotifySend(app_name)
        else:
            self._impl = None

        logger.debug(f'DesktopNotifier implementation: {self.implementation}')

    def send(self,
             title: str,
             message: str,
             urgency: NotificationLevel = NotificationLevel.Normal,
             icon: Optional[str] = None,
             action: Optional[Callable] = None,
             buttons: Optional[Dict[str, Optional[Callable]]] = None) -> None:
        """
        Sends a desktop notification. Some arguments may be ignored, depending on the
        backend.

        :param title: Notification title.
        :param message: Notification message.
        :param urgency: Notification level: low, normal or critical. This is ignored by
            some implementations.
        :param icon: Path to an icon to use for the notification, typically the app icon.
            This is ignored by some implementations, e.g., on macOS where the icon of the
            app bundle is always used.
        :param action: Handler to call when the notification is clicked. This is ignored
            by some implementations.
        :param buttons: A dictionary with button names and callbacks to show in the
            notification. This is ignored by some implementations.
        """
        notification = Notification(
            title, message, urgency, icon, action, buttons
        )

        if self._impl:
            with self._lock:
                self._impl.send(notification)

    @staticmethod
    def _get_available_implementation() -> Optional[SupportedImplementations]:
        macos_version, *_ = platform.mac_ver()

        if platform.system() == 'Darwin' and uns:

            if (IS_MACOS_BUNDLE and Version(macos_version) >= Version('10.14.0')
                    and UNUserNotificationCenter.currentNotificationCenter()):
                # UNUserNotificationCenter is only supported from signed app bundles
                return SupportedImplementations.un_notification_center

            if NSUserNotificationCenter.defaultUserNotificationCenter:
                # deprecated but still works
                return SupportedImplementations.ns_notification_center

        elif platform.system() == 'Linux':
            try:
                DesktopNotifierFreedesktopDBus('test')
                return SupportedImplementations.freedesktop_dbus
            except Exception:
                pass

            if shutil.which('notify-send'):
                return SupportedImplementations.notify_send

        return None


system_notifier = DesktopNotifier(app_name='Maestral', app_id=BUNDLE_ID)


class MaestralDesktopNotifier(logging.Handler):
    """
    Can be used as a standalone notifier or as a logging handler. When used as a logging
    handler, the log level should be set with ``setLevel``. The ``notify_level`` will be
    applied in addition to the log level.
    """

    _instances: ClassVar[Dict[str, 'MaestralDesktopNotifier']] = dict()
    _lock = Lock()

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

    @classmethod
    def level_number_to_name(cls, number: int) -> str:
        """Converts a Maestral notification level number to name."""
        return cls._levelToName[number]

    @classmethod
    def level_name_to_number(cls, name: str) -> int:
        """Converts a Maestral notification level name to number."""
        return cls._nameToLevel[name]

    @classmethod
    def for_config(cls, config_name: str) -> 'MaestralDesktopNotifier':
        """
        Returns an existing instance for the config or creates a new one if none exists.
        Use this method to prevent creating multiple instances.

        :param config_name: Name of maestral config.
        """

        with cls._lock:
            try:
                return cls._instances[config_name]
            except KeyError:
                instance = cls(config_name)
                cls._instances[config_name] = instance
                return instance

    def __init__(self, config_name: str) -> None:
        super().__init__()
        self.setFormatter(logging.Formatter(fmt='%(message)s'))
        self._conf = MaestralConfig(config_name)
        self._snooze = 0.0

    @property
    def notify_level(self) -> int:
        """Custom notification level. Notifications with a lower level will be
        discarded."""
        return self._conf.get('app', 'notification_level')

    @notify_level.setter
    def notify_level(self, level: int) -> None:
        """Setter: notify_level."""
        assert isinstance(level, int)
        self._conf.set('app', 'notification_level', level)

    @property
    def snoozed(self) -> float:
        """Time in minutes to snooze notifications. Applied to FILECHANGE level only."""
        return max(0.0, (self._snooze - time.time()) / 60.0)

    @snoozed.setter
    def snoozed(self, minutes: float) -> None:
        """Setter: snoozed."""
        self._snooze = time.time() + minutes * 60.0

    def notify(self, message: str, level: int = FILECHANGE,
               on_click: Optional[Callable] = None) -> None:
        """
        Sends a desktop notification from maestral. The title defaults to 'Maestral'.

        :param message: Notification message.
        :param level: Notification level of the message.
        :param on_click: A callback to execute when the notification is clicked. The
            provided callable must not take any arguments.
        """

        ignore = self.snoozed and level == self.FILECHANGE
        if level == self.ERROR:
            urgency = NotificationLevel.Critical
        else:
            urgency = NotificationLevel.Normal

        if level >= self.notify_level and not ignore:
            system_notifier.send(
                title='Maestral',
                message=message,
                icon=APP_ICON_PATH,
                urgency=urgency,
                action=on_click
            )

    def emit(self, record: logging.LogRecord) -> None:
        """Emits a log record as a desktop notification."""
        if record.name != logger.name:  # avoid recursions
            self.format(record)
            self.notify(record.message, level=record.levelno)
