# -*- coding: utf-8 -*-
"""
This class provides constants used throughout the maestral, the GUI and CLI. It should
be kept free of memory heavy imports.
"""

# system imports
import sys
import os
import platform
from enum import Enum

try:
    from importlib.metadata import metadata, PackageNotFoundError  # type: ignore
except ImportError:
    # Backwards compatibility Python 3.7 and lower
    from importlib_metadata import metadata, PackageNotFoundError  # type: ignore
try:
    from importlib.resources import files  # type: ignore
except ImportError:
    from importlib_resources import files  # type: ignore


# get metadata of maestral-related packages
_md_list = []

for dist_name in ("maestral", "maestral-cocoa", "maestral-qt", "maestral-gui"):
    try:
        _md_list.append(metadata(dist_name))
    except PackageNotFoundError:
        pass

# app
APP_NAME = "Maestral"
BUNDLE_ID = "com.samschott.maestral"
APP_ICON_PATH = os.path.join(files("maestral"), "resources", "maestral.png")

# sync
OLD_REV_FILE = ".maestral"
MIGNORE_FILE = ".mignore"
FILE_CACHE = ".maestral.cache"

EXCLUDED_FILE_NAMES = frozenset(
    [
        "desktop.ini",
        "thumbs.db",
        ".ds_store",
        "icon\r",
        ".com.apple.timemachine.supported",
        ".dropbox",
        ".dropbox.attr",
        ".dropbox.cache",
        FILE_CACHE,
        OLD_REV_FILE,
    ]
)

EXCLUDED_DIR_NAMES = frozenset([".dropbox.cache", FILE_CACHE])

# state messages
IDLE = "Up to date"
SYNCING = "Syncing..."
STOPPED = "Syncing stopped"
CONNECTED = "Connected"
DISCONNECTED = "Connection lost"
CONNECTING = "Connecting..."
SYNC_ERROR = "Sync error"
ERROR = "Fatal error"


# file status enum
class FileStatus(Enum):
    """Enumeration of sync status"""

    Unwatched = "unwatched"
    Uploading = "uploading"
    Downloading = "downloading"
    Error = "error"
    Synced = "up to date"


# platform detection
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

# frozen app / bundle detection
BRIEFCASE = any("Briefcase-Version" in md for md in _md_list)
FROZEN = BRIEFCASE or getattr(sys, "frozen", False)

# keys
DROPBOX_APP_KEY = "2jmbq42w7vof78h"

# urls
GITHUB_RELEASES_API = "https://api.github.com/repos/samschott/maestral-dropbox/releases"
