# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

This module contains functions for frontends (CLI or GUI) to interact with the backend
without importing memory heavy modules.

"""
from os import path as osp
import logging

import keyring.backends
from keyring.core import load_keyring
from keyring.errors import KeyringLocked
import keyrings.alt.file

from maestral.config import MaestralConfig, MaestralState
from maestral.constants import IS_MACOS_BUNDLE
from maestral.utils.appdirs import get_data_path
from maestral.utils.path import delete


logger = logging.getLogger(__name__)


_supported_keyring_backends = (
    keyring.backends.OS_X.Keyring,
    keyring.backends.SecretService.Keyring,
    keyring.backends.kwallet.DBusKeyring,
    keyring.backends.kwallet.DBusKeyringKWallet4,
    keyrings.alt.file.PlaintextKeyring
)


def get_keyring_backend(config_name):
    """
    Choose the most secure of the available and supported keyring backends or
    use the backend specified in the config file (if valid).

    :param str config_name: The config name.
    """

    import keyring.backends

    conf = MaestralConfig(config_name)
    keyring_name = conf.get('app', 'keyring').strip()

    if IS_MACOS_BUNDLE:
        ring = keyring.backends.OS_X.Keyring()
    else:
        try:
            ring = load_keyring(keyring_name)
        except Exception:
            # get preferred keyring backends for platform
            available_rings = keyring.backend.get_all_keyring()
            supported_rings = [k for k in available_rings
                               if isinstance(k, _supported_keyring_backends)]

            ring = max(supported_rings, key=lambda x: x.priority)

    return ring


def pending_link(config_name):
    """
    Checks if auth key has been saved. This can be used by Maestral front ends to check
    if we are linked before starting a daemon.

    :param str config_name: The config to check.
    :returns: ``True`` or ``False``.
    :rtype: bool
    :raises: ``KeyringLocked`` if the system keyring cannot be accessed.
    """

    ring = get_keyring_backend(config_name)

    conf = MaestralConfig(config_name)
    account_id = conf.get('account', 'account_id')
    try:
        if account_id == '':
            access_token = None
        else:
            access_token = ring.get_password('Maestral', account_id)
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
