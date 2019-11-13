# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
import os
import platform
import sys

# state messages
IDLE = "Up to date"
SYNCING = "Syncing..."
PAUSED = "Syncing paused"
STOPPED = "Syncing stopped"
DISCONNECTED = "Connecting..."
SYNC_ERROR = "Sync error"

# bundle detection
IS_MACOS_BUNDLE = getattr(sys, "frozen", False) and platform.system() == "Darwin"
IS_LINUX_BUNDLE = getattr(sys, "frozen", False) and platform.system() == "Linux"

# systemd environment
INVOCATION_ID = os.getenv("INVOCATION_ID")
NOTIFY_SOCKET = os.getenv("NOTIFY_SOCKET")
WATCHDOG_PID = os.getenv("WATCHDOG_PID")
WATCHDOG_USEC = os.getenv("WATCHDOG_USEC")
IS_WATCHDOG = WATCHDOG_USEC and (WATCHDOG_PID is None or int(WATCHDOG_PID) == os.getpid())

# other
CONFIG_NAME = os.getenv("MAESTRAL_CONFIG", "maestral")
REV_FILE = ".maestral"
