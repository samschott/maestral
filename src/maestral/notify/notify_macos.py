# -*- coding: utf-8 -*-
"""
Notification backend for macOS. Includes three implementations, in order of preference:

1) UNUserNotificationCenter: Introduced in macOS 10.14 and cross-platform with iOS and
   iPadOS. Only available from signed app bundles if called from the main executable.
   Not available from interactive Python interpreter.
2) NSUserNotificationCenter: Deprecated but still available in macOS 11.0. Can be used
   from Python framework.
3) Apple Script: Always available but notifications are sent from Apple Script and not
   Python or Maestral app. No callbacks when the user clicks on notification.

The first two implementations require a running CFRunLoop to invoke callbacks.
"""

# system imports
import uuid
import platform
import subprocess
import shutil
import logging
from typing import Type, Optional, Dict, Tuple

# external imports
from packaging.version import Version
from rubicon.objc import ObjCClass, objc_method, py_from_ns  # type: ignore
from rubicon.objc.runtime import load_library, objc_id, objc_block  # type: ignore

# local imports
from .notify_base import Notification, DesktopNotifierBase
from maestral.constants import FROZEN


__all__ = ["Impl", "CocoaNotificationCenter", "CocoaNotificationCenterLegacy"]

logger = logging.getLogger(__name__)
macos_version, *_ = platform.mac_ver()

foundation = load_library("Foundation")

NSObject = ObjCClass("NSObject")


Impl: Optional[Type[DesktopNotifierBase]] = None


if FROZEN and Version(macos_version) >= Version("10.14.0"):

    uns = load_library("UserNotifications")

    UNUserNotificationCenter = ObjCClass("UNUserNotificationCenter")
    UNMutableNotificationContent = ObjCClass("UNMutableNotificationContent")
    UNNotificationRequest = ObjCClass("UNNotificationRequest")
    UNNotificationAction = ObjCClass("UNNotificationAction")
    UNNotificationCategory = ObjCClass("UNNotificationCategory")

    NSSet = ObjCClass("NSSet")

    UNNotificationDefaultActionIdentifier = (
        "com.apple.UNNotificationDefaultActionIdentifier"
    )
    UNNotificationDismissActionIdentifier = (
        "com.apple.UNNotificationDismissActionIdentifier"
    )

    UNAuthorizationOptionBadge = 1 << 0
    UNAuthorizationOptionSound = 1 << 1
    UNAuthorizationOptionAlert = 1 << 2

    UNNotificationActionOptionForeground = 1 << 2

    UNNotificationCategoryOptionNone = 0

    class NotificationCenterDelegate(NSObject):  # type: ignore
        """Delegate to handle user interactions with notifications"""

        @objc_method
        def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
            self, center, response, completion_handler: objc_block
        ) -> None:

            # Get the notification which was clicked from the platform ID.
            internal_nid = py_from_ns(
                response.notification.request.content.userInfo["internal_nid"]
            )
            notification = self.interface.current_notifications[internal_nid]

            # Get and call the callback which corresponds to the user interaction.
            if response.actionIdentifier == UNNotificationDefaultActionIdentifier:

                callback = notification.action

                if callback:
                    callback()

            elif response.actionIdentifier != UNNotificationDismissActionIdentifier:

                action_id_str = py_from_ns(response.actionIdentifier)

                callback = notification.buttons.get(action_id_str)

                if callback:
                    callback()

            completion_handler()

    class CocoaNotificationCenter(DesktopNotifierBase):
        """UNUserNotificationCenter backend for macOS

        Can be used with macOS Catalina and newer. Both app name and bundle identifier
        will be ignored. The notification center automatically uses the values provided
        by the app bundle. This implementation only works from within signed app bundles
        and if called from the main executable.

        :param app_name: The name of the app.
        :param app_id: The bundle identifier of the app.
        """

        _notification_categories: Dict[Tuple[str, ...], str]

        def __init__(self, app_name: str, app_id: str) -> None:
            super().__init__(app_name, app_id)
            self.nc = UNUserNotificationCenter.currentNotificationCenter()
            self.nc_delegate = NotificationCenterDelegate.alloc().init()
            self.nc_delegate.interface = self
            self.nc.delegate = self.nc_delegate

            self._notification_categories = {}

            def _on_auth_completed(granted: bool, error: objc_id) -> None:
                if granted:
                    logger.debug("UNUserNotificationCenter: authorisation granted")
                else:
                    logger.debug("UNUserNotificationCenter: authorisation denied")

                if error:
                    error = py_from_ns(error)
                    logger.warning("UNUserNotificationCenter: %s", str(error))

            self.nc.requestAuthorizationWithOptions(
                UNAuthorizationOptionAlert
                | UNAuthorizationOptionSound
                | UNAuthorizationOptionBadge,
                completionHandler=_on_auth_completed,
            )

        def send(self, notification: Notification) -> None:
            """
            Sends a notification.

            :param notification: Notification to send.
            """

            # Get an internal ID for the notifications. This will recycle an old ID if
            # we are above the max number of notifications.
            internal_nid = self._next_nid()

            # Get the old notification to replace, if any.
            notification_to_replace = self.current_notifications.get(internal_nid)

            if notification_to_replace:
                platform_nid = notification_to_replace.identifier
            else:
                platform_nid = str(uuid.uuid4())

            # Set up buttons for notification. On macOS, we need need to register a new
            # notification category for every unique set of buttons.
            button_names = tuple(notification.buttons.keys())
            category_id = self._category_id_for_button_names(button_names)

            # Create the native notification + notification request.
            content = UNMutableNotificationContent.alloc().init()
            content.title = notification.title
            content.body = notification.message
            content.categoryIdentifier = category_id
            content.userInfo = {"internal_nid": internal_nid}

            notification_request = UNNotificationRequest.requestWithIdentifier(
                platform_nid, content=content, trigger=None
            )

            # Post the notification.
            self.nc.addNotificationRequest(
                notification_request, withCompletionHandler=None
            )

            # Store the notification for future replacement and to keep track of
            # user-supplied callbacks.
            notification.identifier = platform_nid
            self.current_notifications[internal_nid] = notification

        def _category_id_for_button_names(
            self, button_names: Tuple[str, ...]
        ) -> Optional[str]:
            """
            Creates a and registers a new notification category with the given buttons
            or retrieves an existing one.
            """

            if not button_names:
                return None

            try:
                return self._notification_categories[button_names]
            except KeyError:
                actions = []

                for name in button_names:
                    action = UNNotificationAction.actionWithIdentifier(
                        name, title=name, options=UNNotificationActionOptionForeground
                    )
                    actions.append(action)

                categories = self.nc.notificationCategories
                category_id = str(uuid.uuid4())
                new_categories = categories.setByAddingObject(
                    UNNotificationCategory.categoryWithIdentifier(
                        category_id,
                        actions=actions,
                        intentIdentifiers=[],
                        options=UNNotificationCategoryOptionNone,
                    )
                )
                self.nc.notificationCategories = new_categories
                self._notification_categories[button_names] = category_id

                return category_id

    if UNUserNotificationCenter.currentNotificationCenter():
        Impl = CocoaNotificationCenter


elif Version(macos_version) < Version("11.1.0"):

    NSUserNotification = ObjCClass("NSUserNotification")
    NSUserNotificationCenter = ObjCClass("NSUserNotificationCenter")
    NSDate = ObjCClass("NSDate")

    NSUserNotificationActivationTypeContentsClicked = 1
    NSUserNotificationActivationTypeActionButtonClicked = 2
    NSUserNotificationActivationTypeAdditionalActionClicked = 4

    class NotificationCenterDelegate(NSObject):  # type: ignore
        """Delegate to handle user interactions with notifications"""

        # subclass UNUserNotificationCenter and define delegate method
        # to handle clicked notifications

        @objc_method
        def userNotificationCenter_didActivateNotification_(
            self, center, notification
        ) -> None:

            internal_nid = py_from_ns(notification.userInfo["internal_nid"])
            notification_info = self.interface.current_notifications[internal_nid]

            if (
                notification.activationType
                == NSUserNotificationActivationTypeContentsClicked
            ):

                if notification_info.action:
                    notification_info.action()

            elif (
                notification.activationType
                == NSUserNotificationActivationTypeActionButtonClicked
            ):

                button_title = py_from_ns(notification.actionButtonTitle)
                callback = notification_info.buttons.get(button_title)

                if callback:
                    callback()

    class CocoaNotificationCenterLegacy(DesktopNotifierBase):
        """NSUserNotificationCenter backend for macOS

        Should be used for macOS High Sierra and earlier or outside of app bundles.
        Supports only a single button per notification. Both app name and bundle
        identifier will be ignored. The notification center automatically uses the
        values provided by the app bundle or the Python framework.

        :param app_name: The name of the app.
        :param app_id: The bundle identifier of the app.
        """

        def __init__(self, app_name: str, app_id: str) -> None:
            super().__init__(app_name, app_id)

            self.nc = NSUserNotificationCenter.defaultUserNotificationCenter
            self.nc.delegate = NotificationCenterDelegate.alloc().init()
            self.nc.delegate.interface = self

        def send(self, notification: Notification) -> None:
            """
            Sends a notification.

            :param notification: Notification to send.
            """

            internal_nid = self._next_nid()
            notification_to_replace = self.current_notifications.get(internal_nid)

            if notification_to_replace:
                platform_nid = notification_to_replace.identifier
            else:
                platform_nid = str(uuid.uuid4())

            n = NSUserNotification.alloc().init()
            n.title = notification.title
            n.informativeText = notification.message
            n.identifier = platform_nid
            n.userInfo = {"internal_nid": internal_nid}
            n.deliveryDate = NSDate.dateWithTimeInterval(0, sinceDate=NSDate.date())

            if notification.buttons:
                if len(notification.buttons) > 1:
                    logger.debug(
                        "NSUserNotificationCenter: only a single button is supported"
                    )
                n.hasActionButton = True
                n.actionButtonTitle = list(notification.buttons.keys())[0]

            self.nc.scheduleNotification(n)

            notification.identifier = platform_nid
            self.current_notifications[internal_nid] = notification

    if NSUserNotificationCenter.defaultUserNotificationCenter:
        Impl = CocoaNotificationCenterLegacy


if Impl is None and shutil.which("osascript"):

    # fall back to apple script

    class DesktopNotifierOsaScript(DesktopNotifierBase):
        """Apple script backend for macOS

        Sends desktop notifications via apple script. Does not support buttons or
        callbacks. Apple script will always appear as the sending application.
        """

        def send(self, notification: Notification) -> None:
            """
            Sends a notification.

            :param notification: Notification to send.
            """

            script = (
                f'display notification "{notification.message}" '
                f'with title "{notification.title}"'
            )

            subprocess.call(["osascript", "-e", script])

    Impl = DesktopNotifierOsaScript
