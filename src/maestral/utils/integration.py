# -*- coding: utf-8 -*-
"""
This module provides some basic platform integration. At the moment, it only provides
code to determine if the device is connected to AC power.
"""

import os
import platform
import enum
from pathlib import Path
from typing import Union


__all__ = ["get_ac_state", "ACState"]


LINUX_POWER_SUPPLY_PATH = "/sys/class/power_supply"


def multi_cat(*paths: Path) -> Union[int, bytes, None]:
    """
    Attempts to read the content of multiple files which may not exist. Returns the
    content of the first file which can be read. If none of them can be read return
    None. Returns an integer if the content is a digit.
    """

    for path in paths:
        try:
            ret = path.read_bytes().strip()
        except OSError:
            pass
        else:
            return int(ret) if ret.isdigit() else ret

    return None


class ACState(enum.Enum):
    """Enumeration of AC power states"""

    Connected = "Connected"
    Disconnected = "Disconnected"
    Undetermined = "Undetermined"


def get_ac_state() -> ACState:
    """
    Checks if the current device has AC power or is running on battery.

    :returns: ``True`` if the device has AC power, ``False`` otherwise.
    """

    if platform.system() == "Darwin":

        from ctypes import c_double
        from rubicon.objc.runtime import load_library

        iokit = load_library("IOKit")
        kIOPSTimeRemainingUnlimited = -2.0

        iokit.IOPSGetTimeRemainingEstimate.restype = c_double

        remaining_time = iokit.IOPSGetTimeRemainingEstimate()

        if remaining_time == kIOPSTimeRemainingUnlimited:
            return ACState.Connected
        else:
            return ACState.Disconnected

    elif platform.system() == "Linux":

        # taken from https://github.com/giampaolo/psutil

        supplies = list(os.scandir(LINUX_POWER_SUPPLY_PATH))

        ac_paths = [
            Path(s.path)
            for s in supplies
            if s.name.startswith("A") or "ac" in s.name.lower()
        ]

        bat_paths = [
            Path(s.path)
            for s in supplies
            if s.name.startswith("B") or "battery" in s.name.lower()
        ]

        online = multi_cat(*iter(path / "online" for path in ac_paths))

        if online is not None:
            if online == 1:
                return ACState.Connected
            else:
                return ACState.Disconnected

        elif len(bat_paths) > 0:

            # Get the first available battery. Usually this is "BAT0", except
            # some rare exceptions:
            # https://github.com/giampaolo/psutil/issues/1238
            bat0 = sorted(bat_paths)[0]

            try:
                status = (bat0 / "status").read_text().strip().lower()
            except OSError:
                status = ""

            if status == "discharging":
                return ACState.Disconnected
            elif status in ("charging", "full"):
                return ACState.Connected

    return ACState.Undetermined
