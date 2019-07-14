# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""
Maestral configuration options

Note: The 'account' section is used for internal purposes only to store some
basic information on the user account between connections. The 'internal'
section saves cursors and time-stamps for the last synced Dropbox state and
local state, respectively. Resetting those to the default values will trigger
a full download on the next startup.
"""

# Local import
from maestral.config.user import UserConfig

PACKAGE_NAME = 'maestral'
SUBFOLDER = '.%s' % PACKAGE_NAME


# =============================================================================
#  Defaults
# =============================================================================
DEFAULTS = [
            ('main',
             {
              'path': '',
              'excluded_folders': [],
              'excluded_files': ["desktop.ini",  "thumbs.db", ".ds_store",
                                 "icon\r", ".dropbox.attr", ".dropbox"],
              }),
            ('account',
             {
              'account_id': '',
              'email': '',
              'type': '',
              'usage': '',
              'usage_type': '',
              }),
            ('app',
             {
              'notifications': True,
              }),
            ('internal',
             {
              'cursor': '',
              'lastsync': None,
              'recent_changes': [],
              }),
            ]


# =============================================================================
# Config instance
# =============================================================================
# IMPORTANT NOTES:
# 1. If you want to *change* the default value of a current option, you need to
#    do a MINOR update in config version, e.g. from 3.0.0 to 3.1.0
# 2. If you want to *remove* options that are no longer needed in our codebase,
#    or if you want to *rename* options, then you need to do a MAJOR update in
#    version, e.g. from 3.0.0 to 4.0.0
# 3. You don't need to touch this value if you're just adding a new option
CONF_VERSION = '3.2.0'

# Main configuration instance
try:
    CONF = UserConfig(PACKAGE_NAME, defaults=DEFAULTS, load=True,
                      version=CONF_VERSION, subfolder=SUBFOLDER, backup=True,
                      raw_mode=True)
except Exception:
    CONF = UserConfig(PACKAGE_NAME, defaults=DEFAULTS, load=False,
                      version=CONF_VERSION, subfolder=SUBFOLDER, backup=True,
                      raw_mode=True)
