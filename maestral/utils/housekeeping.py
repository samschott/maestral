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
import ast
import logging
from packaging.version import Version

from maestral.config.main import (
    CONFIG_DIR_NAME, MaestralConfig, MaestralState
)
from maestral.config.user import DefaultsConfig, UserConfig
from maestral.config.base import get_conf_path, get_data_path, list_configs

logger = logging.getLogger(__name__)


def migrate_user_config(config_name):
    config_path = get_conf_path(CONFIG_DIR_NAME, create=False)
    config_fname = osp.join(config_path, config_name + '.ini')

    # load old config non-destructively
    try:
        old_conf = DefaultsConfig(config_path, config_name, '.ini')
        old_conf.read(config_fname, encoding='utf-8')
        old_version = old_conf.get(UserConfig.DEFAULT_SECTION_NAME, 'version')
    except OSError:
        return

    if Version(old_version) < Version('11.0.0'):

        # get values for moved settings
        excluded_folders = old_conf.get('main', 'excluded_folders')

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

        # convert non-string types
        update_notification_last = float(update_notification_last)
        lastsync = float(lastsync)
        recent_changes = ast.literal_eval(recent_changes)
        excluded_folders = ast.literal_eval(excluded_folders)

        # set state values
        state = MaestralState(config_name)
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

        # load actual config to remove obsolete options and add moved ones
        conf = MaestralConfig(config_name)
        conf.set('main', 'excluded_items', excluded_folders)

        # clean up backup and defaults files from previous version of maestral
        for file in os.scandir(old_conf._path):
            if file.is_file():
                if (conf._backup_suffix in file.name
                        or conf._defaults_name_prefix in file.name):
                    os.remove(file.path)

        logger.info(f'Migrated user config "{config_name}"')

    elif Version(old_version) < Version('12.0.0'):
        excluded_folders = old_conf.get('main', 'excluded_folders')
        excluded_folders = ast.literal_eval(excluded_folders)
        conf = MaestralConfig(config_name)
        conf.set('main', 'excluded_items', excluded_folders)


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


def run_housekeeping():
    for config_name in list_configs():
        migrate_user_config(config_name)
        migrate_maestral_index(config_name)
