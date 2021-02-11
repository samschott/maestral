# -*- coding: utf-8 -*-
"""
This module provides a custom polling file system event emitter for the
:obj:`watchdog` package that sorts file system events in an order which can be applied
to reproduce the new state from the old state. This is only required for the polling
emitter which uses period directory snapshots and compares them with a
:class:`watchdog.utils.dirsnapshot.DirectorySnapshotDiff` to generate file system
events.
"""

from watchdog.utils import platform  # type: ignore
from watchdog.utils import UnsupportedLibc


if platform.is_linux():
    try:
        from watchdog.observers.inotify import InotifyObserver as Observer  # type: ignore
    except UnsupportedLibc:
        from .polling import OrderedPollingObserver as Observer
else:
    from watchdog.observers import Observer  # type: ignore

__all__ = ["Observer"]
