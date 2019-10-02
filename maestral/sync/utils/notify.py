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

_root = getattr(sys, '_MEIPASS', Path(Path(__file__).parents[2], "gui", "resources"))
logger = logging.getLogger(__name__)
APP_ICON_PATH = os.path.join(_root, "Maestral.png")


if is_macos_bundle:
    import UserNotifications
    import Foundation


class SupportedImplementations(Enum):
    notify_send = 'notify-send'
    osascript = 'osascript'
    notification_center = 'notification-center'


class Notipy(object):
    """Send native OS notifications to user.

    Relies on AppleScript on macOS and notify-send on linux, otherwise
    falls back to stdout."""

    def __init__(self):
        self.implementation = self.__get_available_implementation()

    def send(self, message, title='Maestral'):
        self.__send_message(message, title)

    def __send_message(self, message, title=""):
        if self.implementation == SupportedImplementations.notification_center:
            notify_macos_bundle(title, message)
        elif self.implementation == SupportedImplementations.osascript:
            os.system("osascript -e 'display notification \"{}\" with title \"{}\"'".format(message, title))
        elif self.implementation == SupportedImplementations.notify_send:
            os.system('notify-send "{}" "{}" -a Maestral -i {} '.format(
                title, message, APP_ICON_PATH))
        else:
            print('{}: {}'.format(title, message))

    @staticmethod
    def __command_exists(command):
        return any(
            os.access(os.path.join(path, command), os.X_OK)
            for path in os.environ["PATH"].split(os.pathsep)
        )

    def __get_available_implementation(self):
        if is_macos_bundle:
            return SupportedImplementations.notification_center
        elif self.__command_exists('osascript'):
            return SupportedImplementations.osascript
        elif self.__command_exists('notify-send'):
            return SupportedImplementations.notify_send
        return None


if is_macos_bundle:

    macos_version, *_ = platform.mac_ver()

    if check_version(macos_version, '10.14.0', '>='):  # macOS Catalina and higher

        def auth_callback(granted, err):
            logger.debug("Granted: ", granted)
            logger.debug("Error in authorization request: ", err)

        def notif_callback(err):
            logger.debug("Error in notification callback:", err)

        nc = UserNotifications.UNUserNotificationCenter.currentNotificationCenter()
        nc.requestAuthorizationWithOptions_completionHandler_((1<<2) | (1<<1) | (1<<0), auth_callback)
        nc_identifier = 0

        def notify_macos_bundle(title, info_text, subtitle=None):

            global nc_identifier

            content = UserNotifications.UNMutableNotificationContent.alloc().init()
            content.setTitle_(title)
            content.setBody_(info_text)
            if subtitle:
                content.setSubtitle_(subtitle)
            r = UserNotifications.UNNotificationRequest.requestWithIdentifier_content_trigger_(str(nc_identifier), content, None)
            nc.addNotificationRequest_withCompletionHandler_(r, notif_callback)

            nc_identifier += 1

    else:

        def notify_macos_bundle(title, info_text, subtitle=None):
            notification = Foundation.NSUserNotification.alloc().init()
            notification.setTitle_(title)
            if subtitle:
                notification.setSubtitle_(subtitle)
            notification.setInformativeText_(info_text)
            notification.setUserInfo_({})
            notification.setDeliveryDate_(Foundation.NSDate.dateWithTimeInterval_sinceDate_(0, Foundation.NSDate.date()))
            Foundation.NSUserNotificationCenter.defaultUserNotificationCenter().scheduleNotification_(notification)
