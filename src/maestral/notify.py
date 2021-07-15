# -*- coding: utf-8 -*-
"""
This module handles desktop notifications It uses the
`desktop-notifier <https://desktop-notifier.readthedocs.io/en/latest/>`_ package as a
backend for cross-platform desktop notifications.
"""

# system imports
import time
import asyncio
from typing import Optional, Dict, Callable

# external imports
from desktop_notifier import DesktopNotifier, Urgency, Button

# local imports
from .config import MaestralConfig
from .constants import APP_NAME, APP_ICON_PATH


__all__ = [
    "NONE",
    "ERROR",
    "SYNCISSUE",
    "FILECHANGE",
    "level_name_to_number",
    "level_number_to_name",
    "MaestralDesktopNotifier",
]

_desktop_notifier = DesktopNotifier(
    app_name=APP_NAME,
    app_icon=APP_ICON_PATH.as_uri(),
    notification_limit=10,
)


NONE = 100
"""No desktop notifications"""
ERROR = 40
"""Notify only on fatal errors"""
SYNCISSUE = 30
"""Notify for sync issues and higher"""
FILECHANGE = 15
"""Notify for all remote file changes"""

_level_to_name = {
    NONE: "NONE",
    ERROR: "ERROR",
    SYNCISSUE: "SYNCISSUE",
    FILECHANGE: "FILECHANGE",
}

_name_to_level = {
    "NONE": 100,
    "ERROR": 40,
    "SYNCISSUE": 30,
    "FILECHANGE": 15,
}


def level_number_to_name(number: int) -> str:
    """
    Converts a Maestral notification level number to name.

    :param number: Level number.
    :returns: Level name.
    """

    try:
        return _level_to_name[number]
    except KeyError:
        return f"Level {number}"


def level_name_to_number(name: str) -> int:
    """
    Converts a Maestral notification level name to number.

    :param name: Level name.
    :returns: Level number.
    """

    try:
        return _name_to_level[name]
    except KeyError:
        raise ValueError("Invalid level name")


class MaestralDesktopNotifier:
    """Desktop notification emitter for Maestral

    Desktop notifier with snooze functionality and variable notification levels. Must
    be instantiated in the main thread.

    :param config_name: Config name. This is used to access notification settings for
        the daemon.
    """

    def __init__(self, config_name: str) -> None:
        self._conf = MaestralConfig(config_name)
        self._snooze = 0.0
        self._loop = asyncio.get_event_loop()

    @property
    def notify_level(self) -> int:
        """Custom notification level. Notifications with a lower level will be
        discarded."""
        return self._conf.get("app", "notification_level")

    @notify_level.setter
    def notify_level(self, level: int) -> None:
        """Setter: notify_level."""
        self._conf.set("app", "notification_level", level)

    @property
    def snoozed(self) -> float:
        """Time in minutes to snooze notifications. Applied to FILECHANGE level only."""
        return max(0.0, (self._snooze - time.time()) / 60.0)

    @snoozed.setter
    def snoozed(self, minutes: float) -> None:
        """Setter: snoozed."""
        self._snooze = time.time() + minutes * 60.0

    def notify(
        self,
        title: str,
        message: str,
        level: int = FILECHANGE,
        on_click: Optional[Callable] = None,
        actions: Optional[Dict[str, Callable]] = None,
    ) -> None:
        """
        Sends a desktop notification. This will schedule a notification task in the
        asyncio loop of the thread where :class:`DesktopNotifier` was instantiated.

        :param title: Notification title.
        :param message: Notification message.
        :param level: Notification level of the message.
        :param on_click: A callback to execute when the notification is clicked. The
            provided callable must not take any arguments.
        :param actions: A dictionary with button names and callbacks for the
            notification.
        """

        snoozed = self.snoozed and level <= FILECHANGE

        if level >= self.notify_level and not snoozed:

            urgency = Urgency.Critical if level >= ERROR else Urgency.Normal

            if actions:
                buttons = [Button(name, handler) for name, handler in actions.items()]
            else:
                buttons = []

            coro = _desktop_notifier.send(
                title=title,
                message=message,
                urgency=urgency,
                on_clicked=on_click,
                buttons=buttons,
            )

            asyncio.run_coroutine_threadsafe(coro, self._loop)
