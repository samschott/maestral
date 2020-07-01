# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

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
