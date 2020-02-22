# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

This module contains migration code to run after an update from < v0.6.0

"""
import sys
import os
import os.path as osp
import copy
import logging
from packaging.version import Version

from maestral.config.main import (
    CONF_VERSION, CONFIG_DIR_NAME, DEFAULTS, MaestralConfig, MaestralState
)
from maestral.config.user import UserConfig
from maestral.config.base import get_conf_path, get_data_path

logger = logging.getLogger(__name__)


def migrate_user_config(config_name):
    config_path = get_conf_path(CONFIG_DIR_NAME, create=False)

    defaults = copy.deepcopy(DEFAULTS)

    # load old config explicitly, not from factory to avoid caching
    try:
        old_conf = UserConfig(
            config_path, config_name, load=True, backup=False, raw_mode=True,
            remove_obsolete=False, defaults=defaults, version='10.0.0'
        )
    except OSError:
        return

    if Version(old_conf._old_version) < Version('11.0.0'):

        state = MaestralState(config_name)

        # get values for moved settings
        email = old_conf.get('account', 'email')
        display_name = old_conf.get('account', 'display_name')
        abbreviated_name = old_conf.get('account', 'abbreviated_name')
        acc_type = old_conf.get('account', 'type')
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
        state.set('account', 'type', acc_type)
        state.set('account', 'usage', usage)
        state.set('account', 'usage_type', usage_type)

        state.set('app', 'update_notification_last', update_notification_last)
        state.set('app', 'latest_release', latest_release)

        state.set('sync', 'cursor', cursor)
        state.set('sync', 'lastsync', lastsync)
        state.set('sync', 'recent_changes', recent_changes)

        old_conf.set_version(CONF_VERSION)
        old_conf._remove_deprecated_options('10.0.0')

        logger.info(f'Migrated user config "{config_name}"')


def migrate_maestral_index(config_name):
    conf = MaestralConfig(config_name)

    old_rev_file_path = osp.join(conf.get('main', 'path'), '.maestral')
    new_rev_file_path = get_data_path('maestral', f'{config_name}.index')

    if osp.isfile(old_rev_file_path) and not osp.isfile(new_rev_file_path):
        try:
            os.rename(old_rev_file_path, new_rev_file_path)
            logger.info(f'Migrated maestral index for config "{config_name}"')
        except OSError:
            title = 'Could not move index after upgrade'
            msg = ('Please move your maestral index manually from '
                   f'"{old_rev_file_path}" to "{new_rev_file_path}".')

            sys.stderr.write(title + '\n' + msg)
            sys.exit(1)
