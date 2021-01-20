# -*- coding: utf-8 -*-
"""
This module handles desktop notifications and supports multiple backends, depending on
the platform.
"""

# system imports
import time
from typing import Optional, Dict, Callable

# external imports
from desktop_notifier import DesktopNotifier, NotificationLevel

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

_desktop_notifier = DesktopNotifier(APP_NAME)


NONE = 100
ERROR = 40
SYNCISSUE = 30
FILECHANGE = 15


_levelToName = {
    NONE: "NONE",
    ERROR: "ERROR",
    SYNCISSUE: "SYNCISSUE",
    FILECHANGE: "FILECHANGE",
}

_nameToLevel = {
    "NONE": 100,
    "ERROR": 40,
    "SYNCISSUE": 30,
    "FILECHANGE": 15,
}


def level_number_to_name(number: int) -> str:
    """Converts a Maestral notification level number to name."""
    return _levelToName[number]


def level_name_to_number(name: str) -> int:
    """Converts a Maestral notification level name to number."""
    return _nameToLevel[name]


class MaestralDesktopNotifier:
    """Desktop notification emitter for Maestral

    Desktop notifier with snooze functionality and variable notification levels.

    :cvar int NONE: Notification level for no desktop notifications.
    :cvar int ERROR: Notification level for errors.
    :cvar int SYNCISSUE: Notification level for sync issues.
    :cvar int FILECHANGE: Notification level for file changes.
    """

    def __init__(self, config_name: str) -> None:
        self._conf = MaestralConfig(config_name)
        self._snooze = 0.0

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
        buttons: Optional[Dict[str, Callable]] = None,
    ) -> None:
        """
        Sends a desktop notification.

        :param title: Notification title.
        :param message: Notification message.
        :param level: Notification level of the message.
        :param on_click: A callback to execute when the notification is clicked. The
            provided callable must not take any arguments.
        :param buttons: A dictionary with button names and callbacks for the
            notification.
        """

        ignore = self.snoozed and level == FILECHANGE
        if level == ERROR:
            urgency = NotificationLevel.Critical
        else:
            urgency = NotificationLevel.Normal

        if level >= self.notify_level and not ignore:
            _desktop_notifier.send(
                title=title,
                message=message,
                icon=APP_ICON_PATH,
                urgency=urgency,
                action=on_click,
                buttons=buttons,
            )
