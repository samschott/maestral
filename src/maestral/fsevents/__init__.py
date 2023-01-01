"""
This module provides a custom polling file system event emitter for the
:obj:`watchdog` package that sorts file system events in an order which can be applied
to reproduce the new state from the old state. This is only required for the polling
emitter which uses period directory snapshots and compares them with a
:class:`watchdog.utils.dirsnapshot.DirectorySnapshotDiff` to generate file system
events.
"""
from __future__ import annotations

from typing import Type, Union, TYPE_CHECKING

from watchdog.utils import platform

if TYPE_CHECKING:
    from watchdog.observers.inotify import InotifyObserver
    from watchdog.observers.fsevents import FSEventsObserver
    from .polling import OrderedPollingObserver


ObserverType = Union["InotifyObserver", "FSEventsObserver", "OrderedPollingObserver"]
Observer: Type[ObserverType]


if platform.is_linux():
    from watchdog.observers.inotify import InotifyObserver as Observer
elif platform.is_darwin():
    from watchdog.observers.fsevents import FSEventsObserver as Observer
else:
    from .polling import OrderedPollingObserver as Observer

__all__ = ["Observer", "ObserverType"]
