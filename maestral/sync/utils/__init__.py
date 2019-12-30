# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

# KEEP UTILS FREE OF DROPBOX IMPORTS TO REDUCE MEMORY FOOTPRINT

import functools
import logging

from maestral.sync.constants import DISCONNECTED, IS_MACOS_BUNDLE
from maestral.sync.errors import DropboxAuthError

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
