# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

"""
# system imports
import sys
import uuid
import platform
import subprocess
import shutil
import logging
from typing import Type, Optional, Dict, Tuple

# external imports
from packaging.version import Version
from rubicon.objc import ObjCClass, objc_method, py_from_ns  # type: ignore
from rubicon.objc.runtime import load_library, objc_id  # type: ignore

# local imports
from .notify_base import Notification, DesktopNotifierBase


logger = logging.getLogger(__name__)

uns = load_library("UserNotifications")
foundation = load_library("Foundation")

NSObject = ObjCClass("NSObject")

macos_version, *_ = platform.mac_ver()


Impl: Optional[Type[DesktopNotifierBase]]


if getattr(sys, "frozen", False) and Version(macos_version) >= Version("10.14.0"):

    # use UNUserNotificationCenter in macOS Mojave and higher if we are in an app bundle

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

        # subclass UNUserNotificationCenter and define delegate method
        # to handle clicked notifications

        @objc_method
        def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
            self, center, response, completion_handler
        ) -> None:

            internal_nid = py_from_ns(
                response.notification.request.content.userInfo["internal_nid"]
            )
            notification = self.interface.current_notifications[internal_nid]

            if response.actionIdentifier == UNNotificationDefaultActionIdentifier:

                callback = notification.action

                if callback:
                    callback()

            elif response.actionIdentifier != UNNotificationDismissActionIdentifier:

                callback = notification.buttons.get(response.actionIdentifier)

                if callback:
                    callback()

            completion_handler()

    class CocoaNotificationCenter(DesktopNotifierBase):
        """UNUserNotificationCenter backend for macOS. For macOS Catalina and newer."""

        _notification_categories: Dict[Tuple[str, ...], str]

        def __init__(self, app_name: str, app_id: str) -> None:
            super().__init__(app_name, app_id)
            self.nc = UNUserNotificationCenter.alloc().initWithBundleIdentifier(app_id)
            self.nc_delegate = NotificationCenterDelegate.alloc().init()
            self.nc_delegate.interface = self
            self.nc.delegate = self.nc_delegate

            def _on_auth_completed(granted: bool, error: objc_id) -> None:
                if granted:
                    logger.debug("UNUserNotificationCenter: authorisation granted")
                else:
                    logger.debug("UNUserNotificationCenter: authorisation denied")

                if error:
                    error = py_from_ns(error)
                    logger.warning("UNUserNotificationCenter: ", str(error))

            self.nc.requestAuthorizationWithOptions(
                UNAuthorizationOptionAlert
                | UNAuthorizationOptionSound
                | UNAuthorizationOptionBadge,
                completionHandler=_on_auth_completed,
            )

        def send(self, notification: Notification) -> None:

            internal_nid = self._next_nid()
            notification_to_replace = self.current_notifications.get(internal_nid)

            if notification_to_replace:
                platform_nid = notification_to_replace.identifier
            else:
                platform_nid = str(uuid.uuid4())

            button_names = tuple(notification.buttons.keys())
            category_id = self._category_id_for_button_names(button_names)

            content = UNMutableNotificationContent.alloc().init()
            content.title = notification.title
            content.body = notification.message
            content.categoryIdentifier = category_id
            content.userInfo = {"internal_nid": internal_nid}

            notification_request = UNNotificationRequest.requestWithIdentifier(
                platform_nid, content=content, trigger=None
            )

            self.nc.addNotificationRequest(
                notification_request, withCompletionHandler=None
            )

            notification.identifier = platform_nid
            self.current_notifications[internal_nid] = notification

        def _category_id_for_button_names(
            self, button_names: Tuple[str, ...]
        ) -> Optional[str]:

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
    else:
        Impl = None

elif Version(macos_version) <= Version("11.0.0"):

    # use NSUserNotificationCenter outside of app bundles for macOS Big Sur and lower
    # and for macOS High Sierra and lower

    NSUserNotification = ObjCClass("NSUserNotification")
    NSUserNotificationCenter = ObjCClass("NSUserNotificationCenter")
    NSDate = ObjCClass("NSDate")

    NSUserNotificationActivationTypeContentsClicked = 1
    NSUserNotificationActivationTypeActionButtonClicked = 2
    NSUserNotificationActivationTypeAdditionalActionClicked = 4

    class NotificationCenterDelegate(NSObject):  # type: ignore

        # subclass UNUserNotificationCenter and define delegate method
        # to handle clicked notifications

        @objc_method
        def userNotificationCenter_didActivateNotification_(
            self, center, notification
        ) -> None:

            internal_nid = py_from_ns(notification.userInfo["internal_nid"])
            notification_info = self.interface.current_notifications[internal_nid]

            if Version(macos_version) == Version("11.0.0"):
                # macOS Big Sur has a 'Show' button by default
                condition = NSUserNotificationActivationTypeActionButtonClicked
            else:
                # macOS Catalina and lower doesn't show a button by default
                condition = NSUserNotificationActivationTypeContentsClicked

            if notification.activationType == condition:

                if notification_info.action:
                    notification_info.action()

    class CocoaNotificationCenterLegacy(DesktopNotifierBase):
        """NSUserNotificationCenter backend for macOS. Pre macOS Mojave. We don't support
        buttons here."""

        def __init__(self, app_name: str, app_id: str) -> None:
            super().__init__(app_name, app_id)

            self.nc = NSUserNotificationCenter.defaultUserNotificationCenter
            self.nc.delegate = NotificationCenterDelegate.alloc().init()
            self.nc.delegate.interface = self

        def send(self, notification: Notification) -> None:

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

            self.nc.scheduleNotification(n)

            notification.identifier = platform_nid
            self.current_notifications[internal_nid] = notification

    if NSUserNotificationCenter.defaultUserNotificationCenter:
        Impl = CocoaNotificationCenterLegacy
    else:
        Impl = None

elif shutil.which("osascript"):

    # fall back to apple script

    class DesktopNotifierOsaScript(DesktopNotifierBase):
        """Apple script backend for macOS."""

        def send(self, notification: Notification) -> None:
            subprocess.call(
                [
                    "osascript",
                    "-e",
                    f'display notification "{notification.message}" with title "{notification.title}"',
                ]
            )

    Impl = DesktopNotifierOsaScript

else:
    Impl = None
