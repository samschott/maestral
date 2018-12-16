# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

import os
from enum import Enum
from maestral.config.main import CONF


class SupportedImplementation(Enum):
    notifySend = 'notify-send'
    osascript = 'osascript'


class Notipy(object):
    """Send native OS notifications to user.

    Relies on AppleScript on macOS and notify-send on linux, otherwise
    falls back to stdout."""

    def __init__(self):
        self.implementation = self.__get_available_implementation()

    @property
    def enabled(self):
        return CONF.get("app", "notifications")

    @enabled.setter
    def enabled(self, boolean):
        CONF.set("app", "notifications", boolean)

    def send(self, message, title="Maestral"):
        if self.enabled:
            self.__send_message(message, title)
        else:
            pass

    def __send_message(self, message, title=""):
        if self.implementation == SupportedImplementation.osascript:
            os.system("osascript -e 'display notification \"{}\" with title \"{}\"'".format(message, title))
        elif self.implementation == SupportedImplementation.notifySend:
            os.system('notify-send "{}" "{}"'.format(title, message))
        else:
            print('{}: {}'.format(title, message))

    @staticmethod
    def __command_exists(command):
        return any(
            os.access(os.path.join(path, command), os.X_OK)
            for path in os.environ["PATH"].split(os.pathsep)
        )

    def __get_available_implementation(self):
        if self.__command_exists('osascript'):
            return SupportedImplementation.osascript
        elif self.__command_exists('notify-send'):
            return SupportedImplementation.notifySend
        return None
