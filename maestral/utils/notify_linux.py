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
import asyncio
import logging
from typing import Optional, Type, Coroutine

# external imports
from dbus_next import Variant  # type: ignore
from dbus_next.aio import MessageBus  # type: ignore

# local imports
from .notify_base import Notification, DesktopNotifierBase, NotificationLevel


logger = logging.getLogger(__name__)

Impl: Optional[Type[DesktopNotifierBase]]


class DBusDesktopNotifier(DesktopNotifierBase):
    """DBus notification backend for Linux. This implements the
    org.freedesktop.Notifications standard. The DBUS connection is created in a thread
    with a running asyncio loop to handle clicked notifications."""

    _to_native_urgency = {
        NotificationLevel.Low: Variant("y", 0),
        NotificationLevel.Normal: Variant("y", 1),
        NotificationLevel.Critical: Variant("y", 2),
    }

    def __init__(self, app_name: str, app_id: str) -> None:
        super().__init__(app_name, app_id)
        self._loop = asyncio.get_event_loop()
        self._force_run_in_loop(self._init_dbus())

    def _force_run_in_loop(self, coro: Coroutine) -> None:

        if self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        else:
            self._loop.run_until_complete(coro)

    async def _init_dbus(self) -> None:

        try:
            self.bus = await MessageBus().connect()
            introspection = await self.bus.introspect(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications"
            )
            self.proxy_object = self.bus.get_proxy_object(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                introspection,
            )
            self.interface = self.proxy_object.get_interface(
                "org.freedesktop.Notifications"
            )
            self.interface.on_action_invoked(self._on_action)
        except Exception:
            self.interface = None
            logger.warning("Could not connect to DBUS interface", exc_info=True)

    def send(self, notification: Notification) -> None:
        self._force_run_in_loop(self._send(notification))

    async def _send(self, notification: Notification) -> None:

        if not self.interface:
            return

        internal_nid = self._next_nid()
        notification_to_replace = self.current_notifications.get(internal_nid)

        if notification_to_replace:
            replaces_nid = notification_to_replace.identifier
        else:
            replaces_nid = 0

        actions = ["default", "default"]

        for button_name in notification.buttons.keys():
            actions += [button_name, button_name]

        try:
            platform_nid = await self.interface.call_notify(
                self.app_name,  # app_name
                replaces_nid,  # replaces_id
                notification.icon or "",  # app_icon
                notification.title,  # summary
                notification.message,  # body
                actions,  # actions
                {"urgency": self._to_native_urgency[notification.urgency]},  # hints
                -1,  # expire_timeout (-1 = default)
            )
        except Exception:
            # This may fail for several reasons: there may not be a systemd service
            # file for 'org.freedesktop.Notifications' or the system configuration
            # may have changed after DesktopNotifierFreedesktopDBus was initialized.
            logger.warning("Notification failed", exc_info=True)
        else:
            notification.identifier = platform_nid
            self.current_notifications[internal_nid] = notification

    def _on_action(self, nid, action_key) -> None:

        nid = int(nid)
        action_key = str(action_key)
        notification = next(
            iter(n for n in self.current_notifications.values() if n.identifier == nid),
            None,
        )

        if notification:
            if action_key == "default" and notification.action:
                notification.action()
            else:
                callback = notification.buttons.get(action_key)

                if callback:
                    callback()


Impl = DBusDesktopNotifier
