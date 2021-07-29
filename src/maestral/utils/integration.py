# -*- coding: utf-8 -*-
"""
This module provides functions for platform integration. Most of the functionality here
could also be achieved with psutils but we want to avoid the large dependency.
"""

import os
import platform
import enum
import resource
import requests
import time
from pathlib import Path
from typing import Union, Tuple
from urllib.parse import urlparse

__all__ = [
    "get_ac_state",
    "ACState",
    "get_inotify_limits",
    "CPU_COUNT",
    "cpu_usage_percent",
    "check_connection",
]


CPU_COUNT = os.cpu_count() or 1  # os.cpu_count can return None
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


def get_inotify_limits() -> Tuple[int, int, int]:
    """
    Returns the current inotify limit settings as tuple.

    :returns: ``(max_user_watches, max_user_instances, max_queued_events)``
    :raises OSError: if the settings cannot be read from /proc/sys/fs/inotify. This may
        happen if /proc/sys is left out of the kernel image or simply not mounted.
    """

    root = Path("/proc/sys/fs/inotify")

    max_user_watches_path = root / "max_user_watches"
    max_user_instances_path = root / "max_user_instances"
    max_queued_events_path = root / "max_queued_events"

    max_user_watches = int(max_user_watches_path.read_bytes().strip())
    max_user_instances = int(max_user_instances_path.read_bytes().strip())
    max_queued_events = int(max_queued_events_path.read_bytes().strip())

    return max_user_watches, max_user_instances, max_queued_events


def cpu_usage_percent(interval: float = 0.1) -> float:
    """
    Returns a float representing the CPU utilization of the current process as a
    percentage. This duplicates the similar method from psutil to avoid the psutil
    dependency.

    Compares process times to system CPU times elapsed before and after the interval
    (blocking). It is recommended for accuracy that this function be called with an
    interval of at least 0.1 sec.

    A value > 100.0 can be returned in case of processes running multiple threads on
    different CPU cores. The returned value is explicitly NOT split evenly between all
    available logical CPUs. This means that a busy loop process running on a system with
    2 logical CPUs will be reported as having 100% CPU utilization instead of 50%.

    :param interval: Interval in sec between comparisons of CPU times.
    :returns: CPU usage during interval in percent.
    """

    if interval <= 0:
        raise ValueError(f"interval is not positive (got {interval!r})")

    def timer():
        return time.monotonic() * CPU_COUNT

    st1 = timer()
    rt1 = resource.getrusage(resource.RUSAGE_SELF)
    time.sleep(interval)
    st2 = timer()
    rt2 = resource.getrusage(resource.RUSAGE_SELF)

    delta_proc = (rt2.ru_utime - rt1.ru_utime) + (rt2.ru_stime - rt1.ru_stime)
    delta_time = st2 - st1

    try:
        overall_cpus_percent = (delta_proc / delta_time) * 100
    except ZeroDivisionError:
        return 0.0
    else:
        single_cpu_percent = overall_cpus_percent * CPU_COUNT
        return round(single_cpu_percent, 1)


def check_connection(hostname: str, timeout: int = 2) -> bool:
    """
    A low latency check for an internet connection.

    :param hostname: Hostname to use for connection check.
    :param timeout: Timeout in seconds for connection check.
    :returns: Connection availability.
    """
    if urlparse(hostname).scheme not in ["http", "https"]:
        hostname = "http://" + hostname
    try:
        requests.head(hostname, timeout=timeout)
        return True
    except Exception:
        return False
