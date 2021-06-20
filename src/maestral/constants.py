# -*- coding: utf-8 -*-
"""
This module provides constants used throughout the maestral, the GUI and CLI. It should
be kept free of memory heavy imports.
"""

# system imports
import platform
from enum import Enum

try:
    from importlib.resources import path  # type: ignore
except ImportError:
    from importlib_resources import path  # type: ignore


# app
APP_NAME = "Maestral"
BUNDLE_ID = "com.samschott.maestral"
APP_ICON_PATH = path("maestral.resources", "maestral.png").__enter__()
ENV = {"PYTHONOPTIMIZE": "2"}

# sync
OLD_REV_FILE = ".maestral"
MIGNORE_FILE = ".mignore"
FILE_CACHE = ".maestral.cache"

EXCLUDED_FILE_NAMES = frozenset(
    [
        "desktop.ini",
        "Thumbs.db",
        "thumbs.db",
        ".DS_Store",
        ".ds_tore",
        "Icon\r",
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
PAUSED = "Paused"
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

# keys
DROPBOX_APP_KEY = "2jmbq42w7vof78h"

# urls
GITHUB_RELEASES_API = "https://api.github.com/repos/samschott/maestral/releases"
