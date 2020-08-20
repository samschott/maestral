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
import time
import platform
import pkg_resources
import logging
from threading import Lock
from typing import Optional, Dict, ClassVar, Callable

# local imports
from maestral.config import MaestralConfig
from maestral.constants import BUNDLE_ID
from .notify_base import DesktopNotifierBase, NotificationLevel, Notification


_resources = getattr(sys, '_MEIPASS',
                     pkg_resources.resource_filename('maestral', 'resources'))
APP_ICON_PATH = osp.join(_resources, 'maestral.png')


class DesktopNotifier:
    """
    Cross-platform desktop notifications for macOS and Linux. Uses different backends
    depending on the platform version and available services. The Dbus backend requires
    a running asyncio loop. The Cocoa backends will dispatch notifications without an
    event loop but require a running CFRunLoop (Core Foundation run loop) to react to user
    interactions with the notification. Packages such as :package:`rubicon.objc` can be
    used to integrate asyncio with a CFRunLoop.

    :param app_name: Name of sending app.
    """

    _impl: Optional[DesktopNotifierBase]

    def __init__(self, app_name: str, app_id: str) -> None:
        self._lock = Lock()

        if platform.system() == 'Darwin':
            from .notify_macos import Impl
        elif platform.system() == 'Linux':
            from .notify_linux import Impl  # type: ignore
        else:
            Impl = None  # type: ignore

        if Impl:
            self._impl = Impl(app_name, app_id)
        else:
            self._impl = None

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
        self.format(record)
        self.notify(record.message, level=record.levelno)
