# -*- coding: utf-8 -*-
"""
This module contains the default configuration and state values and functions to return
existing config or state instances for a specified config_name.
"""

import logging
import threading
from typing import Dict

from maestral import __version__
from .base import get_conf_path, get_data_path
from .user import UserConfig, DefaultsType


logger = logging.getLogger(__name__)
CONFIG_DIR_NAME = "maestral"

# =============================================================================
#  Defaults
# =============================================================================

DEFAULTS_CONFIG: DefaultsType = [
    (
        "main",
        {
            "path": "",  # dropbox folder location
            "excluded_items": [],  # files and folders excluded from sync
        },
    ),
    (
        "account",
        {
            "account_id": "",  # dropbox account id, must match the saved account key
        },
    ),
    (
        "app",
        {
            "notification_level": 15,  # desktop notification level, default to FILECHANGE
            "log_level": 20,  # log level for journal and file, default to INFO
            "update_notification_interval": 60 * 60 * 24 * 7,  # default to weekly
            "analytics": False,  # automatic errors reports with bugsnag, default to disabled
            "keyring": "automatic",  # keychain backend to use for credential storage
        },
    ),
    (
        "sync",
        {
            "reindex_interval": 60 * 60 * 24 * 7,  # default to weekly
            "max_cpu_percent": 20.0,  # max usage target per cpu core, default to 20%
            "keep_history": 60 * 60 * 24 * 7,  # default one week
        },
    ),
]

DEFAULTS_STATE: DefaultsType = [
    (
        "account",  # account state, periodically updated from dropbox servers
        {
            "email": "",
            "display_name": "",
            "abbreviated_name": "",
            "type": "",
            "usage": "",
            "usage_type": "",  # private vs business
            "token_access_type": "",  # will be updated on completed OAuth
        },
    ),
    (
        "app",  # app state
        {
            "updated_scripts_completed": __version__,
            "update_notification_last": 0.0,
            "latest_release": "0.0.0",
        },
    ),
    (
        "sync",  # sync state, updated by monitor
        {
            "cursor": "",  # remote cursor: represents last state synced from dropbox
            "lastsync": 0.0,  # local cursor: time-stamp of last upload
            "last_reindex": 0.0,  # time-stamp of full last reindexing
            "upload_errors": [],  # failed uploads to retry on next sync
            "download_errors": [],  # failed downloads to retry on next sync
            "pending_uploads": [],  # incomplete uploads to retry on next sync
            "pending_downloads": [],  # incomplete downloads to retry on next sync
        },
    ),
]

# IMPORTANT NOTES:
# 1. If you want to *change* the default value of a current option, you need to
#    do a MINOR update in config version, e.g. from 3.0.0 to 3.1.0
# 2. If you want to *remove* options that are no longer needed in our codebase,
#    or if you want to *rename* options, then you need to do a MAJOR update in
#    version, e.g. from 3.0.0 to 4.0.0
# 3. You don't need to touch this value if you're just adding a new option
CONF_VERSION = "14.0.0"


# =============================================================================
# Factories
# =============================================================================


def _get_conf(
    config_name: str,
    config_path: str,
    defaults: DefaultsType,
    registry: Dict[str, UserConfig],
):

    try:
        conf = registry[config_name]
    except KeyError:

        try:
            conf = UserConfig(
                config_path,
                defaults=defaults,
                version=CONF_VERSION,
                backup=True,
                remove_obsolete=True,
            )
        except OSError:
            conf = UserConfig(
                config_path,
                defaults=defaults,
                version=CONF_VERSION,
                backup=True,
                remove_obsolete=True,
                load=False,
            )

        registry[config_name] = conf

    return conf


_config_instances: Dict[str, UserConfig] = dict()
_config_lock = threading.Lock()


def MaestralConfig(config_name: str) -> UserConfig:
    """
    Returns existing config instance or creates a new one.

    :param config_name: Name of maestral configuration to run. A new config file will
        be created if none exists for the given config_name.
    :return: Maestral config instance which saves any changes to the drive.
    """

    global _config_instances

    with _config_lock:
        config_path = get_conf_path(CONFIG_DIR_NAME, f"{config_name}.ini")
        return _get_conf(config_name, config_path, DEFAULTS_CONFIG, _config_instances)


_state_instances: Dict[str, UserConfig] = dict()
_state_lock = threading.Lock()


def MaestralState(config_name: str) -> UserConfig:
    """
    Returns existing state instance or creates a new one.

    :param config_name: Name of maestral configuration to run. A new state file will
        be created if none exists for the given config_name.
    :return: Maestral state instance which saves any changes to the drive.
    """

    global _state_instances

    with _state_lock:
        state_path = get_data_path(CONFIG_DIR_NAME, f"{config_name}.state")
        return _get_conf(config_name, state_path, DEFAULTS_STATE, _state_instances)
