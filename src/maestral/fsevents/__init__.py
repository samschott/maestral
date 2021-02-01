# -*- coding: utf-8 -*-
"""
This module provides custom event emitters for the :obj:`watchdog` package that sort
file system events in an order which can be applied to reproduce the new state from the
old state. This is only required for event emitters which internally use
:class:`watchdog.utils.dirsnapshot.DirectorySnapshotDiff` to generate file system
events. This includes the macOS FSEvents emitter and the Polling emitter but not inotify
emitters.

Looking at the source code for :class:`watchdog.utils.dirsnapshot.DirectorySnapshotDiff`,
the event types are categorised as follows:

* Created event: The inode is unique to the new snapshot. The path may be unique to the
  new snapshot or exist in both. In the second case, there will be a preceding Deleted
  event or a Moved event with the path as starting point (the old item was deleted or
  moved away).

* Deleted event: The inode is unique to the old snapshot. The path may be unique to the
  old snapshot or exist in both. In the second case, there will be a subsequent Created
  event or a Moved event with the path as end point (something else was created at or
  moved to the location).

* Moved event: The inode exists in both snapshots but with different paths.

* Modified event: The inode exists in both snapshots and the mtime or file size are
  different. DirectorySnapshotDiff will always use the inode’s path from the old
  snapshot.

From the above classification, there can be at most two created/deleted/moved events
that share the same path in one snapshot diff:

    * Deleted(path1)      + Created(path1)
    * Moved(path1, path2) + Created(path1)
    * Deleted(path1)      + Moved(path0, path1)

Any Modified event will come before a Moved event or stand alone. Modified events will
never be combined by themselves with created or deleted events because they require the
inode to be present in both snapshots.

From the above, we can achieve correct ordering for unique path by always adding Deleted
events to the queue first, Modified events second, Moved events third and Created events
last:

    Deleted -> Modified -> Moved -> Created

The ordering won’t be correct between unrelated paths and between files and folder. The
first does not matter for syncing. We solve the second by assuming that when a directory
is deleted, so are its children. And before a child is created, its parent dircetory
must exist.

MovedEvents which are not unique (their paths appear in other events) will be split
into Deleted and Created events by Maestral.
"""

import os
from typing import Union

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


# patch encoding / decoding of paths in watchdog


def _patched_decode(path: Union[str, bytes]) -> str:
    if isinstance(path, bytes):
        return os.fsdecode(path)
    return path


def _patched_encode(path: Union[str, bytes]) -> bytes:
    if isinstance(path, str):
        return os.fsencode(path)
    return path


try:
    from watchdog.utils import unicode_paths
except ImportError:
    pass
else:
    unicode_paths.decode = _patched_decode
    unicode_paths.encode = _patched_encode


__all__ = ["Observer"]
