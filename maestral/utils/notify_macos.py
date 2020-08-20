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

# external imports
from packaging.version import Version
from rubicon.objc import ObjCClass, objc_method  # type: ignore
from rubicon.objc.runtime import load_library  # type: ignore

# local imports
from .notify_base import Notification, DesktopNotifierBase

uns = load_library('UserNotifications')
foundation = load_library('Foundation')

NSObject = ObjCClass('NSObject')

macos_version, *_ = platform.mac_ver()


if uns and getattr(sys, 'frozen', False) and Version(macos_version) >= Version('10.14.0'):

    # use UNUserNotificationCenter in macOS Mojave and higher if we are in an app bundle

    UNUserNotificationCenter = ObjCClass('UNUserNotificationCenter')
    UNMutableNotificationContent = ObjCClass('UNMutableNotificationContent')
    UNNotificationRequest = ObjCClass('UNNotificationRequest')
    UNNotificationAction = ObjCClass('UNNotificationAction')
    UNNotificationCategory = ObjCClass('UNNotificationCategory')

    NSSet = ObjCClass('NSSet')

    UNNotificationDefaultActionIdentifier = 'com.apple.UNNotificationDefaultActionIdentifier'
    UNNotificationDismissActionIdentifier = 'com.apple.UNNotificationDismissActionIdentifier'

    UNAuthorizationOptionBadge = (1 << 0)
    UNAuthorizationOptionSound = (1 << 1)
    UNAuthorizationOptionAlert = (1 << 2)

    UNNotificationActionOptionForeground = (1 << 2)

    UNNotificationCategoryOptionNone = 0


    class NotificationCenterDelegate(NSObject):

        # subclass UNUserNotificationCenter and define delegate method
        # to handle clicked notifications

        @objc_method
        def userNotificationCenter_didReceive_withCompletionHandler_(self, center, response,
                                                                     completion_handler) -> None:

            nid = int(str(response.request.identifier))
            notification = self.interface.current_notifications.get(nid)

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

        def __init__(self, app_name: str, app_id: str) -> None:
            super().__init__(app_name, app_id)
            self.nc = UNUserNotificationCenter.alloc().initWithBundleIdentifier(app_id)
            self.nc.delegate = NotificationCenterDelegate.alloc().init()
            self.nc.delegate.interface = self

            self.nc.requestAuthorizationWithOptions(
                UNAuthorizationOptionAlert | UNAuthorizationOptionSound | UNAuthorizationOptionBadge,
                completionHandler=None
            )

        def send(self, notification: Notification) -> None:

            nid = self._next_nid()
            notification_to_replace = self.current_notifications.get(nid)

            if notification_to_replace:
                replace_id = notification_to_replace.identifier
            else:
                replace_id = str(nid)

            actions = []

            for button_name in notification.buttons.keys():
                action = UNNotificationAction.actionWithIdentifier(
                    button_name,
                    title=button_name,
                    options=UNNotificationActionOptionForeground
                )
                actions.append(action)

            categories = self.nc.notificationCategories
            category_id = str(uuid.uuid4())
            new_categories = categories.setByAddingObject(
                UNNotificationCategory.categoryWithIdentifier(
                    category_id,
                    actions=actions,
                    intentIdentifiers=[],
                    options=UNNotificationCategoryOptionNone
                )
            )
            self.nc.notificationCategories = new_categories

            content = UNMutableNotificationContent.alloc().init()
            content.title = notification.title
            content.body = notification.message
            content.categoryIdentifier = category_id

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


    Impl = CocoaNotificationCenter


elif uns and Version(macos_version) <= Version('11.0.0'):

    # use NSUserNotificationCenter outside of app bundles for macOS Big Sur and lower
    # and for macOS High Sierra and lower

    NSUserNotification = ObjCClass('NSUserNotification')
    NSUserNotificationCenter = ObjCClass('NSUserNotificationCenter')
    NSDate = ObjCClass('NSDate')

    NSUserNotificationActivationTypeContentsClicked = 1
    NSUserNotificationActivationTypeActionButtonClicked = 2
    NSUserNotificationActivationTypeAdditionalActionClicked = 4


    class NotificationCenterDelegate(NSObject):

        # subclass UNUserNotificationCenter and define delegate method
        # to handle clicked notifications

        @objc_method
        def userNotificationCenter_didActivateNotification_(self, center, notification) -> None:

            nid = int(str(notification.identifier))
            notification_info = self.interface.current_notifications.get(nid)

            if Version(macos_version) == Version('11.0.0'):
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


    Impl = CocoaNotificationCenterLegacy


elif shutil.which('osascript'):

    # fall back to apple script

    class DesktopNotifierOsaScript(DesktopNotifierBase):
        """Apple script backend for macOS."""

        def send(self, notification: Notification) -> None:
            subprocess.call([
                'osascript', '-e',
                f'display notification "{notification.message}" with title "{notification.title}"'
            ])

    Impl = DesktopNotifierOsaScript

else:
    Impl = None
