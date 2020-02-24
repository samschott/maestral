# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
from os import path as osp

import keyring
import keyring.backends
from keyring.errors import KeyringLocked

from maestral.config import MaestralConfig, MaestralState
from maestral.constants import IS_MACOS_BUNDLE
from maestral.utils.appdirs import get_data_path
from maestral.utils.path import delete


def set_keyring_backend():
    if IS_MACOS_BUNDLE:
        import keyring.backends.OS_X
        keyring.set_keyring(keyring.backends.OS_X.Keyring())
    else:
        import keyring.backends
        # get preferred keyring backends for platform, excluding the chainer backend
        all_keyrings = keyring.backend.get_all_keyring()
        preferred_kreyrings = [k for k in all_keyrings if not isinstance(k, keyring.backends.chainer.ChainerBackend)]

        keyring.set_keyring(max(preferred_kreyrings, key=lambda x: x.priority))


def pending_link(config_name):
    """
    Checks if auth key has been saved. This can be used by Maestral front ends to check
    if we are linked before starting a daemon.

    :param str config_name: The config to check.
    :returns: ``True`` or ``False``.
    :rtype: bool
    :raises: ``KeyringLocked`` if the system keyring cannot be accessed.
    """

    set_keyring_backend()

    conf = MaestralConfig(config_name)
    account_id = conf.get('account', 'account_id')
    try:
        if account_id == '':
            access_token = None
        else:
            access_token = keyring.get_password('Maestral', account_id)
        return access_token is None
    except KeyringLocked:
        info = 'Please make sure that your keyring is unlocked and restart Maestral.'
        raise KeyringLocked(info)


def pending_dropbox_folder(config_name):
    """
    Checks if a local dropbox folder has been set. This can be used by Maestral front ends
    to check if we are linked before starting a daemon.

    :param str config_name: The config to check.
    :returns: ``True`` or ``False``.
    :rtype: bool
    """
    conf = MaestralConfig(config_name)
    return not osp.isdir(conf.get('main', 'path'))


def remove_configuration(config_name):
    """
    Removes all config and state files associated with the given configuration.

    :param str config_name: The configuration to remove.
    """

    MaestralConfig(config_name).cleanup()
    MaestralState(config_name).cleanup()
    index_file = get_data_path('maestral', f'{config_name}.index')
    delete(index_file)
