# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
# KEEP FREE OF DROPBOX IMPORTS TO REDUCE MEMORY FOOTPRINT

import os
import platform
import sys
import tempfile


def is_fs_case_sensitive():
    # create a cased temp file and check if the lower case version exists
    with tempfile.NamedTemporaryFile(prefix="TmP") as tmp_file:
        return not os.path.exists(tmp_file.name.lower())


# app
APP_NAME = "Maestral"
BUNDLE_ID = "com.samschott.maestral"

# sync
REV_FILE = ".maestral"
IS_FS_CASE_SENSITIVE = is_fs_case_sensitive()

# state messages
IDLE = "Up to date"
SYNCING = "Syncing..."
PAUSED = "Syncing paused"
STOPPED = "Syncing stopped"
DISCONNECTED = "Connecting..."
SYNC_ERROR = "Sync error"
ERROR = "Fatal error"

# bundle detection
IS_MACOS_BUNDLE = getattr(sys, "frozen", False) and platform.system() == "Darwin"
IS_LINUX_BUNDLE = getattr(sys, "frozen", False) and platform.system() == "Linux"

# systemd environment
INVOCATION_ID = os.getenv("INVOCATION_ID")
NOTIFY_SOCKET = os.getenv("NOTIFY_SOCKET")
WATCHDOG_PID = os.getenv("WATCHDOG_PID")
WATCHDOG_USEC = os.getenv("WATCHDOG_USEC")
IS_WATCHDOG = WATCHDOG_USEC and (WATCHDOG_PID is None or int(WATCHDOG_PID) == os.getpid())
