"""
Maestral configuration options
"""
import copy
import logging

from .base import get_conf_path, get_data_path
from .user import UserConfig

logger = logging.getLogger(__name__)
CONFIG_DIR_NAME = 'maestral'

# =============================================================================
#  Defaults
# =============================================================================

DEFAULTS = [
    ('main',
     {
         'path': '',  # dropbox folder location
         'default_dir_name': 'Dropbox ({})',  # default dropbox folder name
         'excluded_items': [],  # files and folders excluded from sync
     }
     ),
    ('account',
     {
         'account_id': '',  # dropbox account id, must match the saved account key
     }
     ),
    ('app',
     {
         'notification_level': 15,  # desktop notification level, default to FILECHANGE
         'log_level': 20,  # log level for journal and file, default to INFO
         'update_notification_interval': 60 * 60 * 24 * 7,  # default to weekly
         'analytics': False,  # automatic errors reports with bugsnag, default to disabled
     }
     ),
    ('sync',
     {
         'reindex_interval': 60 * 60 * 24 * 7,  # default to weekly
         'max_cpu_percent': 20.0,  # max usage target per cpu core, default to 20%
     }
     )
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
         'last_reindex': 0.0,  # time-stamp of full last reindexing
         'download_errors': [],  # failed downloads to retry on next sync
         'pending_downloads': [],  # incomplete downloads to retry on next sync
         'recent_changes': [],  # cached list of recent changes to display in GUI / CLI
     }
     ),
]

# IMPORTANT NOTES:
# 1. If you want to *change* the default value of a current option, you need to
#    do a MINOR update in config version, e.g. from 3.0.0 to 3.1.0
# 2. If you want to *remove* options that are no longer needed in our codebase,
#    or if you want to *rename* options, then you need to do a MAJOR update in
#    version, e.g. from 3.0.0 to 4.0.0
# 3. You don't need to touch this value if you're just adding a new option
CONF_VERSION = '12.0.0'


# =============================================================================
# Factories
# =============================================================================

_config_instances = {}
_state_instances = {}


def MaestralConfig(config_name):
    """
    Return existing config instance of create a new one.
    """

    global _config_instances

    if config_name in _config_instances:
        return _config_instances[config_name]
    else:
        defaults = copy.deepcopy(DEFAULTS)
        # set default dir name according to config
        for sec, options in defaults:
            if sec == 'main':
                options['default_dir_name'] = f'Dropbox ({config_name.title()})'

        config_path = get_conf_path(CONFIG_DIR_NAME, create=True)

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

        _config_instances[config_name] = conf
        return conf


def MaestralState(config_name):
    """
    Return existing state instance of create a new one.
    """

    global _state_instances

    if config_name in _state_instances:
        return _state_instances[config_name]
    else:
        state_path = get_data_path(CONFIG_DIR_NAME, create=True)

        try:
            state = UserConfig(
                state_path, config_name, defaults=DEFAULTS_STATE,
                version=CONF_VERSION, load=True, backup=True, raw_mode=True,
                remove_obsolete=True, suffix='.state'
            )
        except OSError:
            state = UserConfig(
                state_path, config_name, defaults=DEFAULTS_STATE,
                version=CONF_VERSION, load=False, backup=True, raw_mode=True,
                remove_obsolete=True, suffix='.state'
            )

        _state_instances[config_name] = state
        return state
