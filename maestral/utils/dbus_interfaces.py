# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

"""

# external imports
from jeepney import MessageGenerator, new_method_call


class FreedesktopNotifications(MessageGenerator):
    interface = 'org.freedesktop.Notifications'

    def __init__(self, object_path='/org/freedesktop/Notifications',
                 bus_name='org.freedesktop.Notifications'):
        super().__init__(object_path=object_path, bus_name=bus_name)

    def Notify(self, arg_0, arg_1, arg_2, arg_3, arg_4, arg_5, arg_6, arg_7):
        return new_method_call(self, 'Notify', 'susssasa{sv}i',
                               (arg_0, arg_1, arg_2, arg_3, arg_4, arg_5, arg_6,
                                arg_7))

    def CloseNotification(self, arg_0):
        return new_method_call(self, 'CloseNotification', 'u',
                               (arg_0,))

    def GetCapabilities(self):
        return new_method_call(self, 'GetCapabilities')

    def GetServerInformation(self):
        return new_method_call(self, 'GetServerInformation')
