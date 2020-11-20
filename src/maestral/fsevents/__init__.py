# -*- coding: utf-8 -*-
"""
This module provides custom watchdog observers which sort file system events in a way
that allows the file system state to be generated from the old state by applying all
events in order.
"""

from watchdog.utils import platform  # type: ignore
from watchdog.utils import UnsupportedLibc

if platform.is_darwin():
    from .fsevents import OrderedFSEventsObserver as Observer
elif platform.is_linux():
    try:
        from watchdog.observers.inotify import InotifyObserver as Observer  # type: ignore
    except UnsupportedLibc:
        from .polling import OrderedPollingObserver as Observer
else:
    from watchdog.observers import Observer  # type: ignore

__all__ = ["Observer"]
