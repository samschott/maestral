# -*- coding: utf-8 -*-
#
# Copyright 2012 Canonical Ltd.
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3, as published
# by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranties of
# MERCHANTABILITY, SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR
# PURPOSE.  See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program.  If not, see <http://www.gnu.org/licenses/>.
"""Network state detection on OS X.

NetworkManagerState: class with listening thread, calls back with state changes

TODO: This is taken from the Ubuntu Single Sign-On Python library and can likely be
  simplified using NWPathMonitor

"""
from ctypes import (
    POINTER,
    CFUNCTYPE,
    Structure,
    pointer,
    c_bool,
    c_long,
    c_void_p,
    c_uint32,
)

from rubicon.objc.runtime import load_library  # type: ignore

from .networkstate_base import NetworkConnectionNotifierBase


libsc = load_library("SystemConfiguration")
libcf = load_library("CoreFoundation")

CFRunLoopGetCurrent = libcf.CFRunLoopGetCurrent
CFRunLoopGetCurrent.restype = c_void_p
CFRunLoopGetCurrent.argtypes = []

kCFRunLoopCommonModes = c_void_p.in_dll(libcf, "kCFRunLoopCommonModes")

CFRelease = libcf.CFRelease
CFRelease.restype = None
CFRelease.argtypes = [c_void_p]

SCNRCreateWithName = libsc.SCNetworkReachabilityCreateWithName
SCNRCreateWithName.restype = c_void_p

SCNRGetFlags = libsc.SCNetworkReachabilityGetFlags
SCNRGetFlags.restype = c_bool
SCNRGetFlags.argtypes = [c_void_p, POINTER(c_uint32)]

SCNRScheduleWithRunLoop = libsc.SCNetworkReachabilityScheduleWithRunLoop
SCNRScheduleWithRunLoop.restype = c_bool
SCNRScheduleWithRunLoop.argtypes = [c_void_p, c_void_p, c_void_p]

SCNRCallbackType = CFUNCTYPE(None, c_void_p, c_uint32, c_void_p)
# NOTE: need to keep this reference alive as long as a callback might occur.

SCNRSetCallback = libsc.SCNetworkReachabilitySetCallback
SCNRSetCallback.restype = c_bool
SCNRSetCallback.argtypes = [c_void_p, SCNRCallbackType, c_void_p]


def check_connected_state(hostname):
    """Calls Synchronous SCNR API, returns bool."""
    target = SCNRCreateWithName(None, hostname)
    if target is None:
        raise RuntimeError("Error creating network reachability reference")

    flags = c_uint32(0)
    ok = SCNRGetFlags(target, pointer(flags))
    CFRelease(target)

    if not ok:
        raise RuntimeError(f"Error getting reachability status of '{hostname}'")

    return flags_say_reachable(flags.value)


def flags_say_reachable(flags):
    """Check flags returned from SCNetworkReachability API. Returns bool.

    Requires some logic:
    reachable_flag isn't enough on its own.

    A down wifi will return flags = 7, or reachable_flag and
    connection_required_flag, meaning that the host *would be*
    reachable, but you need a connection first.  (And then you'd
    presumably be best off checking again.)
    """
    # values from SCNetworkReachability.h
    reachable_flag = 1 << 1
    connection_required_flag = 1 << 2

    reachable = flags & reachable_flag
    connection_required = flags & connection_required_flag

    return reachable and not connection_required


class SCNRContext(Structure):
    """A struct to send as SCNetworkReachabilityContext to SCNRSetCallback.

    We don't use the fields currently.
    """

    _fields_ = [
        ("version", c_long),
        ("info", c_void_p),
        ("retain", c_void_p),  # func ptr
        ("release", c_void_p),  # func ptr
        ("copyDescription", c_void_p),
    ]  # func ptr


class NetworkConnectionNotifierMacOS(NetworkConnectionNotifierBase):
    def __init__(self, host: str, callback) -> None:
        super().__init__(host, callback)
        self.result_cb = callback
        self.hostname = host
        self.start_listening()

    def start_listening(self) -> None:
        """Setup callback and listen for changes."""

        def reachability_state_changed_cb(targetref, flags, info):
            """Callback for SCNetworkReachability API

            This callback is passed to the SCNetworkReachability API,
            so its method signature has to be exactly this. Therefore,
            we declare it here and just call _state_changed with
            flags."""
            state = check_connected_state(self.hostname)
            self.result_cb(state)

        self._c_callback = SCNRCallbackType(reachability_state_changed_cb)
        self._context = SCNRContext(0, None, None, None, None)

        self._target = SCNRCreateWithName(None, self.hostname)
        if self._target is None:
            raise RuntimeError("Error creating SCNetworkReachability target")

        ok = SCNRSetCallback(self._target, self._c_callback, pointer(self._context))
        if not ok:
            CFRelease(self._target)
            raise RuntimeError("Error setting SCNetworkReachability callback")

        ok = SCNRScheduleWithRunLoop(
            self._target, CFRunLoopGetCurrent(), kCFRunLoopCommonModes
        )
        if not ok:
            CFRelease(self._target)
            raise RuntimeError("Error scheduling on runloop: SCNetworkReachability")

    @property
    def connected(self) -> bool:
        return check_connected_state(self.hostname)
