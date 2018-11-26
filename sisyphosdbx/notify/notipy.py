import os
from enum import Enum


class SupportedImplementation(Enum):
    notifySend = 'notify-send'
    osascript = 'osascript'


class Notipy:
    def __init__(self):
        self.implementation = self.__get_available_implementation()

    def send(self, message):
        self.__send_message(message)

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