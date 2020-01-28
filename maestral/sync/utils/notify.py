# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import sys
import os
import subprocess
import platform
from enum import Enum
from pathlib import Path
import logging

from maestral.sync.utils.updates import check_version
from maestral.sync.constants import IS_MACOS_BUNDLE

if platform.system() == 'Darwin':
    macos_version, *_ = platform.mac_ver()
    from ctypes import cdll, util
    from rubicon.objc import ObjCClass

    appkit = cdll.LoadLibrary(util.find_library('AppKit'))
    foundation = cdll.LoadLibrary(util.find_library('Foundation'))
    core_graphics = cdll.LoadLibrary(util.find_library('CoreGraphics'))
    core_text = cdll.LoadLibrary(util.find_library('CoreText'))
    uns = cdll.LoadLibrary(util.find_library('UserNotifications'))

    UNUserNotificationCenter = ObjCClass('UNUserNotificationCenter')
    UNMutableNotificationContent = ObjCClass('UNMutableNotificationContent')
    UNNotificationRequest = ObjCClass('UNNotificationRequest')

    NSUserNotification = ObjCClass('NSUserNotification')
    NSUserNotificationCenter = ObjCClass('NSUserNotificationCenter')
    NSDate = ObjCClass('NSDate')

else:
    macos_version = ''

_root = getattr(sys, '_MEIPASS', Path(Path(__file__).parents[2], 'gui', 'resources'))
logger = logging.getLogger(__name__)
APP_ICON_PATH = os.path.join(_root, 'maestral.png')  # don't import from gui


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
        self._with_app_name = True  # if True, use --app-name option for nofity-send

        if self.implementation == SupportedImplementations.notification_center:
            self._nc = UNUserNotificationCenter.currentNotificationCenter
            self._nc.requestAuthorizationWithOptions((1 << 2) | (1 << 1) | (1 << 0), completionHandler=self._nc_auth_callback)
            self._nc_identifier = 0

        elif self.implementation == SupportedImplementations.legacy_notification_center:
            self._nc = NSUserNotificationCenter.defaultUserNotificationCenter

    def send(self, message, title='Maestral'):
        if self.implementation == SupportedImplementations.notification_center:
            self.__send_message_nc(title, message)
        elif self.implementation == SupportedImplementations.legacy_notification_center:
            self.__send_message_nc_legacy(title, message)
        elif self.implementation == SupportedImplementations.osascript:
            self.__send_message_macos_osascript(title, message)
        elif self.implementation == SupportedImplementations.notify_send:
            self.__send_message_linux(title, message)
        else:
            print('{}: {}'.format(title, message))

    def __send_message_nc(self, title, message, subtitle=None):

        content = UNMutableNotificationContent.alloc().init()
        content.setTitle_(title)
        content.setBody_(message)
        if subtitle:
            content.setSubtitle_(subtitle)
        r = UNNotificationRequest.requestWithIdentifier(str(self._nc_identifier), content=content, trigger=None)
        self._nc.addNotificationRequest(r, withCompletionHandler=self._nc_notify_callback)

        self._nc_identifier += 1

    def __send_message_nc_legacy(self, title, message, subtitle=None):
        # icon = Foundation.NSImage.alloc().initByReferencingFile(APP_ICON_PATH)
        notification = NSUserNotification.alloc().init()
        notification.title = title
        if subtitle:
            notification.subtitle = subtitle
        notification.informativeText = message
        # notification._identityImage = icon
        # notification._identityImageHasBorder = 0
        notification.userInfo = {}
        notification.deliveryDate = NSDate.dateWithTimeInterval(0, sinceDate=NSDate.date())
        self._nc.scheduleNotification(notification)

    def __send_message_macos_osascript(self, title, message):
        subprocess.call(['osascript', '-e', 'display notification "{}" with title "{}"'.format(message, title)])

    def __send_message_linux(self, title, message):
        if self._with_app_name:  # try passing --app-name option, diable if not supported
            r = subprocess.call(['notify-send', title, message, '-a', 'Maestral', '-i', APP_ICON_PATH])
            self._with_app_name = r == 0

        if not self._with_app_name:
            subprocess.call(['notify-send', title, message, '-i', APP_ICON_PATH])

    @staticmethod
    def __command_exists(command):
        return any(
            os.access(os.path.join(path, command), os.X_OK)
            for path in os.environ['PATH'].split(os.pathsep)
        )

    def __get_available_implementation(self):
        if IS_MACOS_BUNDLE and check_version(macos_version, '10.14.0', '>='):
            return SupportedImplementations.notification_center
        elif platform.system() == 'Darwin' and check_version(macos_version, '10.16.0', '<'):
            return SupportedImplementations.legacy_notification_center
        elif self.__command_exists('osascript'):
            return SupportedImplementations.osascript
        elif self.__command_exists('notify-send'):
            return SupportedImplementations.notify_send
        return None

    @staticmethod
    def _nc_auth_callback(granted, err):
        logger.debug('Granted: ', granted)
        logger.debug('Error in authorization request: ', err)

    @staticmethod
    def _nc_notify_callback(err):
        logger.debug('Error in notification callback:', err)
