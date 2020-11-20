# -*- coding: utf-8 -*-
"""
This class provides constants used throughout the maestral, the GUI and CLI. It should
be kept free of memory heavy imports.

.. data:: APP_NAME

   The name of the app: "Maestral"

.. data:: BUNDLE_ID

   The bundle identifier of the app: "com.samschott.maestral"

.. data:: APP_ICON_PATH

   The path to the app icon.

.. data:: OLD_REV_FILE

   The old file used to store file revisions.

.. data:: MIGNORE_FILE

   The name of the mignore file: ".mignore"

.. data:: FILE_CACHE

   The name of the cache folder: ".maestral.cache"

.. data:: OLD_REV_FILE

   The old file used to store file revisions.

.. data:: OLD_REV_FILE

   The old file used to store file revisions.

.. data:: EXCLUDED_FILE_NAMES

   Set of file names which are always excluded from syncing.

.. data:: EXCLUDED_DIR_NAMES

   Set of directories names which are always excluded from syncing.

.. data:: BUGSNAG_API_KEY

   API Key for Bugsnag error logging.

.. data:: BUGSNAG_API_KEY

   API Key for Bugsnag error logging.

.. data:: DROPBOX_APP_KEY

   Key for the Dropbox API.

.. data:: GITHUB_RELEASES_API

   URL of the Github releases API.

.. data:: IS_MACOS

   True on macOS.

.. data:: IS_LINUX

   True on Linux.

.. data:: BRIEFCASE

   True if we have been packaged with Briefcase.

.. data:: FROZEN

   True if we are in a frozen environment, for instance with Pyinstaller.

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
PAUSED = "Syncing paused"
STOPPED = "Syncing stopped"
DISCONNECTED = "Connecting..."
SYNC_ERROR = "Sync error"
ERROR = "Fatal error"


# file status enum
class FileStatus(Enum):
    """Enumeration of sync status

    :cvar Unwatched: Item is not excluded in sync.
    :cvar Uploading: Item is uploading.
    :cvar Downloading: Item is downloading.
    :cvar Error: Item could not sync.
    :cvar Synced: Item is synced.
    """

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
BUGSNAG_API_KEY = "081c05e2bf9730d5f55bc35dea15c833"
DROPBOX_APP_KEY = "2jmbq42w7vof78h"

# urls
GITHUB_RELEASES_API = "https://api.github.com/repos/samschott/maestral-dropbox/releases"
