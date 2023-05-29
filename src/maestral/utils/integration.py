"""
This module provides functions for platform integration. Most of the functionality here
could also be achieved with psutils, but we want to avoid the large dependency.
"""
from __future__ import annotations

import os
import resource
import requests
import time
import logging
import socket
from pathlib import Path
from typing import Union, Tuple, Optional
from urllib.parse import urlparse

__all__ = [
    "cat",
    "get_inotify_limits",
    "CPU_CORE_COUNT",
    "cpu_usage_percent",
    "check_connection",
    "SystemdNotifier",
]


CPU_CORE_COUNT = os.cpu_count() or 1  # os.cpu_count can return None
LINUX_POWER_SUPPLY_PATH = "/sys/class/power_supply"


def cat(*paths: "os.PathLike[str]") -> Union[bytes, None]:
    """
    Attempts to read the content of multiple files which may not exist. Returns the
    content of the first file which can be read. If none of them can be read return
    None. Returns an integer if the content is a digit.
    """
    for path in paths:
        try:
            with open(path, "rb") as f:
                return f.read().strip()
        except OSError:
            pass

    return None


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

    def timer() -> float:
        return time.monotonic() * CPU_CORE_COUNT

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
        single_cpu_percent = overall_cpus_percent * CPU_CORE_COUNT
        return round(single_cpu_percent, 1)


def check_connection(
    hostname: str, timeout: int = 2, logger: Optional[logging.Logger] = None
) -> bool:
    """
    A low latency check for an internet connection.

    :param hostname: Hostname to use for connection check.
    :param timeout: Timeout in seconds for connection check.
    :param logger: If provided, log output for connection failures will be logged to
        this logger with the level DEBUG.
    :returns: Connection availability.
    """
    if urlparse(hostname).scheme not in ["http", "https"]:
        hostname = "http://" + hostname
    try:
        requests.head(hostname, timeout=timeout)
        return True
    except Exception:
        if logger:
            logger.debug("Could not reach %s", hostname, exc_info=True)
        return False


class SystemdNotifier:
    """
    An interface to notify the systemd the service manager about status changes.

    Sends a status message to the systemd the service manager on the socket address
    provided by the NOTIFY_SOCKET environment variable. Does nothing NOTIFY_SOCKET is
    not set.

    See https://www.freedesktop.org/software/systemd/man/sd_notify.html for a
    documentation of message formats expected by systemd.
    """

    def __init__(self) -> None:
        self._socket = None

        addr = os.getenv("NOTIFY_SOCKET")
        if addr is None:
            return
        elif addr[0] == "@":
            addr = "\0" + addr[1:]

        try:
            self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self._socket.connect(addr)
        except OSError:
            pass

    def notify(self, status: str) -> None:
        """
        Send a status update to the service manager.

        :param status: The status update to send.
        """
        if self._socket:
            try:
                self._socket.sendall(status.encode(errors="replace"))
            except OSError:
                pass
