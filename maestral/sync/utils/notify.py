# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import sys
import os
import platform
from enum import Enum
from pathlib import Path
import logging

from maestral.sync.utils import is_macos_bundle, check_version

if platform.system() == "Darwin":
    import UserNotifications
    import Foundation
    import Foundation
    macos_version, *_ = platform.mac_ver()
else:
    macos_version = ""

_root = getattr(sys, '_MEIPASS', Path(Path(__file__).parents[2], "gui", "resources"))
logger = logging.getLogger(__name__)
APP_ICON_PATH = os.path.join(_root, "maestral.png")  # don't import from gui


class SupportedImplementations(Enum):
    notify_send = 'notify-send'
    osascript = 'osascript'
    notification_center = 'notification-center'
    legacy_notification_center = 'legacy-notification-center'


class Notipy(object):
    """Send native OS notifications to user.

    Relies on AppleScript on macOS and notify-send on linux, otherwise
    falls back to stdout."""

    def __init__(self):
        self.implementation = self.__get_available_implementation()

        if self.implementation == SupportedImplementations.notification_center:
            self._nc = UserNotifications.UNUserNotificationCenter.currentNotificationCenter()
            self._nc.requestAuthorizationWithOptions_completionHandler_((1 << 2) | (1 << 1) | (1 << 0), self._nc_auth_callback)
            self._nc_identifier = 0

        elif self.implementation == SupportedImplementations.legacy_notification_center:
            self._nc = Foundation.NSUserNotificationCenter.defaultUserNotificationCenter()

    def send(self, message, title='Maestral'):
        if self.implementation == SupportedImplementations.notification_center:
            self.__send_message_macos_nc(title, message)
        elif self.implementation == SupportedImplementations.legacy_notification_center:
            self.__send_message_macos_nc_legacy(title, message)
        elif self.implementation == SupportedImplementations.osascript:
            self.__send_message_macos_osascript(title, message)
        elif self.implementation == SupportedImplementations.notify_send:
            self.__send_message_linux(title, message)
        else:
            print('{}: {}'.format(title, message))

    def __send_message_macos_nc(self, title, message, subtitle=None):

        content = UserNotifications.UNMutableNotificationContent.alloc().init()
        content.setTitle_(title)
        content.setBody_(message)
        if subtitle:
            content.setSubtitle_(subtitle)
        r = UserNotifications.UNNotificationRequest.requestWithIdentifier_content_trigger_(str(self._nc_identifier), content, None)
        self._nc.addNotificationRequest_withCompletionHandler_(r, self._nc_notify_callback)

        self._nc_identifier += 1

    def __send_message_macos_nc_legacy(self, title, message, subtitle=None):
        # icon = Foundation.NSImage.alloc().initByReferencingFile_(APP_ICON_PATH)
        notification = Foundation.NSUserNotification.alloc().init()
        notification.setTitle_(title)
        if subtitle:
            notification.setSubtitle_(subtitle)
        notification.setInformativeText_(message)
        # notification.set_identityImage_(icon)
        notification.set_identityImageHasBorder_(0)
        notification.setUserInfo_({})
        notification.setDeliveryDate_(Foundation.NSDate.dateWithTimeInterval_sinceDate_(0, Foundation.NSDate.date()))
        self._nc.scheduleNotification_(notification)

    def __send_message_macos_osascript(self, title, message):
        os.system("osascript -e 'display notification \"{}\" with title \"{}\"'".format(message, title))

    def __send_message_linux(self, title, message):
        os.system('notify-send "{}" "{}" -a Maestral -i {} '.format(title, message, APP_ICON_PATH))

    @staticmethod
    def __command_exists(command):
        return any(
            os.access(os.path.join(path, command), os.X_OK)
            for path in os.environ["PATH"].split(os.pathsep)
        )

    def __get_available_implementation(self):
        if is_macos_bundle and check_version(macos_version, '10.14.0', '>='):
            return SupportedImplementations.notification_center
        elif platform.system() == "Darwin" and check_version(macos_version, '10.16.0', '<'):
            return SupportedImplementations.legacy_notification_center
        elif self.__command_exists('osascript'):
            return SupportedImplementations.osascript
        elif self.__command_exists('notify-send'):
            return SupportedImplementations.notify_send
        return None

    @staticmethod
    def _nc_auth_callback(granted, err):
        logger.debug("Granted: ", granted)
        logger.debug("Error in authorization request: ", err)

    @staticmethod
    def _nc_notify_callback(err):
        logger.debug("Error in notification callback:", err)
