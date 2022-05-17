"""
This module provides a custom polling file system event emitter for the
:obj:`watchdog` package that sorts file system events in an order which can be applied
to reproduce the new state from the old state. This is only required for the polling
emitter which uses period directory snapshots and compares them with a
:class:`watchdog.utils.dirsnapshot.DirectorySnapshotDiff` to generate file system
events.
"""

from watchdog.utils import platform
from watchdog.utils import UnsupportedLibc


if platform.is_linux():
    try:
        from watchdog.observers.inotify import InotifyObserver as Observer
    except UnsupportedLibc:
        from .polling import OrderedPollingObserver as Observer
elif platform.is_bsd():
    from watchdog.observers import Observer
# This is disabled pending an exhaustive test.
#    try:
#        from watchdog.observers.kqueue import KqueueObserver as Observer
#    except UnsupportedLibc:
#        from .polling import OrderedPollingObserver as Observer

else:
    from watchdog.observers import Observer

__all__ = ["Observer"]
