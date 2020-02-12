"""
Base configuration management
"""
import os
import os.path as osp

from maestral.utils.appdirs import get_conf_path, get_state_path
from .user import UserConfig
from .main import MaestralState


def list_configs():
    """Lists all maestral configs"""
    configs = []
    for file in os.listdir(get_conf_path('maestral')):
        if file.endswith('.ini'):
            configs.append(os.path.splitext(os.path.basename(file))[0])

    return configs


# =============================================================================
# Migrate user config
# =============================================================================

def migrate_user_config(config_name):
    config_path = get_conf_path('maestral', create=False)
    config_fpath = get_conf_path('maestral', config_name, create=False)
    state_fpath = get_state_path('maestral', config_name + '_state', create=False)

    old_version = '10.0.0'

    if osp.isfile(config_fpath) and not osp.isfile(state_fpath):
        # load old config explicitly, not from factory to avoid caching
        old_conf = UserConfig(
            config_path, config_name, defaults=None, version=old_version,
            load=True, backup=True, raw_mode=True, remove_obsolete=False
        )
        state = MaestralState(config_name)

        # get values for moved settings
        email = old_conf.get('account', 'email')
        display_name = old_conf.get('account', 'display_name')
        abbreviated_name = old_conf.get('account', 'abbreviated_name')
        type = old_conf.get('account', 'type')
        usage = old_conf.get('account', 'usage')
        usage_type = old_conf.get('account', 'usage_type')

        update_notification_last = old_conf.get('app', 'update_notification_last')
        latest_release = old_conf.get('app', 'latest_release')

        cursor = old_conf.get('internal', 'cursor')
        lastsync = old_conf.get('internal', 'lastsync')
        recent_changes = old_conf.get('internal', 'recent_changes')

        # set state values
        state.set('account', 'email', email)
        state.set('account', 'display_name', display_name)
        state.set('account', 'abbreviated_name', abbreviated_name)
        state.set('account', 'type', type)
        state.set('account', 'usage', usage)
        state.set('account', 'usage_type', usage_type)

        state.set('app', 'update_notification_last', update_notification_last)
        state.set('app', 'latest_release', latest_release)

        state.set('sync', 'cursor', cursor)
        state.set('sync', 'lastsync', lastsync)
        state.set('sync', 'recent_changes', recent_changes)
