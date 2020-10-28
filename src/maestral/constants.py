# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

This class provides constants used throughout the maestral, the GUI and CLI. It should
be kept free of memory heavy imports.

"""

# system imports
import sys
import os
import platform
from enum import Enum

try:
    from importlib.metadata import metadata  # type: ignore
except ImportError:
    # Backwards compatibility Python 3.7 and lower
    from importlib_metadata import metadata  # type: ignore
try:
    from importlib.resources import files  # type: ignore
except ImportError:
    from importlib_resources import files  # type: ignore


_app_module = sys.modules["__main__"].__package__
_md = metadata(_app_module)

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
PAUSED = "Syncing paused"
STOPPED = "Syncing stopped"
DISCONNECTED = "Connecting..."
SYNC_ERROR = "Sync error"
ERROR = "Fatal error"


# file status enum
class FileStatus(Enum):
    Unwatched = "unwatched"
    Uploading = "uploading"
    Downloading = "downloading"
    Error = "error"
    Synced = "up to date"


# platform detection
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"
FROZEN = "Briefcase-Version" in _md or getattr(sys, "frozen", False)

# keys
BUGSNAG_API_KEY = "081c05e2bf9730d5f55bc35dea15c833"
DROPBOX_APP_KEY = "2jmbq42w7vof78h"

# urls
GITHUB_RELEASES_API = "https://api.github.com/repos/samschott/maestral-dropbox/releases"
