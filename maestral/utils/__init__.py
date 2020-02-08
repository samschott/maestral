# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

# KEEP UTILS FREE OF DROPBOX IMPORTS TO REDUCE MEMORY FOOTPRINT

import os.path as osp
import functools
import logging

import keyring
from keyring.errors import KeyringLocked

from maestral.constants import DISCONNECTED, IS_MACOS_BUNDLE
from maestral.errors import DropboxAuthError
from maestral.config.main import MaestralConfig

logger = logging.getLogger(__name__)


def handle_disconnect(func):
    """
    Decorator which handles connection and auth errors during a function call and returns
    ``False`` if an error occurred.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # pause syncing
        try:
            res = func(*args, **kwargs)
            return res
        except ConnectionError:
            logger.info(DISCONNECTED)
            return False
        except DropboxAuthError as e:
            logger.exception(e.title)
            return False

    return wrapper


def with_sync_paused(func):
    """
    Decorator which pauses syncing before a method call, resumes afterwards. This
    should only be used to decorate Maestral methods.
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        # pause syncing
        resume = False
        if self.syncing:
            self.pause_sync()
            resume = True
        ret = func(self, *args, **kwargs)
        # resume syncing if previously paused
        if resume:
            self.resume_sync()
        return ret
    return wrapper


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


set_keyring_backend()


def pending_link(config_name):
    """
    Checks if auth key has been saved. This can be used by Maestral front ends to check
    if we are linked before starting a daemon.

    :param str config_name: The config to check.
    :returns: ``True`` or ``False``.
    :rtype: bool
    :raises: ``KeyringLocked`` if the system keyring cannot be accessed.
    """
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
    return not osp.isdir(conf.get("main", "path"))
