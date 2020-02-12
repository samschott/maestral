# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""
Maestral configuration options
"""
import copy

from maestral.utils.appdirs import get_conf_path, get_state_path
from maestral.constants import APP_NAME
from .user import UserConfig


# =============================================================================
#  Defaults
# =============================================================================

DEFAULTS = [
    ('main',
     {
         'path': '',  # dropbox folder location
         'default_dir_name': 'Dropbox ({})',  # default dropbox folder name
         'excluded_folders': [],  # folders excluded from sync
         'excluded_files': [],  # files excluded from sync, currently not used
     }
     ),
    ('account',
     {
         'account_id': '',  # dropbox account id, must match the saved account key
     }
     ),
    ('app',
     {
         'notification_level': 15,  # desktop notification level
         'log_level': 20,
         'update_notification_interval': 60*60*24*7,
         'analytics': False,  # automatically report crashes and errors with bugsnag
     }
     ),
]


DEFAULTS_STATE = [
    ('account',  # account state, periodically updated from dropbox servers
     {
         'email': '',
         'display_name': '',
         'abbreviated_name': '',
         'type': '',
         'usage': '',
         'usage_type': '',
     }
     ),
    ('app',  # app state
     {
         'update_notification_last': 0.0,
         'latest_release': '0.0.0',
     }
     ),
    ('sync',  # sync state, updated by monitor
     {
         'cursor': '',  # remote cursor: represents last state synced from dropbox
         'lastsync': 0.0,  # local cursor: time-stamp of last upload
         'recent_changes': [],  # cached list of recent changes to display in GUI / CLI
     }
     ),
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
CONF_VERSION = '11.0.0'


class MaestralConfig:
    """Singleton config instance for Maestral"""

    _instances = {}

    def __new__(cls, config_name):
        """
        Create new instance for a new config name, otherwise return existing instance.
        """

        if config_name in cls._instances:
            return cls._instances[config_name]
        else:
            defaults = copy.deepcopy(DEFAULTS)
            # set default dir name according to config
            for sec, options in defaults:
                if sec == 'main':
                    options['default_dir_name'] = f'Dropbox ({config_name.title()})'

            config_path = get_conf_path(APP_NAME.lower(), create=True)

            try:
                conf = UserConfig(
                    config_path, config_name, defaults=defaults, version=CONF_VERSION,
                    load=True, backup=True, raw_mode=True, remove_obsolete=True
                )
            except OSError:
                conf = UserConfig(
                    config_path, config_name, defaults=defaults, version=CONF_VERSION,
                    load=False, backup=True, raw_mode=True, remove_obsolete=True
                )

            cls._instances[config_name] = conf
            return conf


class MaestralState:
    """Singleton config instance for Maestral"""

    _instances = {}

    def __new__(cls, config_name):
        """
        Create new instance for a new config name, otherwise return existing instance.
        """

        if config_name in cls._instances:
            return cls._instances[config_name]
        else:
            state_path = get_state_path(APP_NAME.lower(), create=True)
            filename = config_name + '_state'

            try:
                state = UserConfig(
                    state_path, filename, defaults=DEFAULTS_STATE,
                    version=CONF_VERSION, load=True, backup=True, raw_mode=True,
                    remove_obsolete=True
                )
            except OSError:
                state = UserConfig(
                    state_path, filename, defaults=DEFAULTS_STATE,
                    version=CONF_VERSION, load=False, backup=True, raw_mode=True,
                    remove_obsolete=True
                )

            cls._instances[config_name] = state
            return state
