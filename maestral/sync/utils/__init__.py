# -*- coding: utf-8 -*-
import functools
import logging

from maestral.sync.constants import DISCONNECTED
from maestral.sync.errors import CONNECTION_ERRORS, DropboxAuthError

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
        except CONNECTION_ERRORS:
            logger.info(DISCONNECTED)
            return False
        except DropboxAuthError as e:
            logger.exception("{0}: {1}".format(e.title, e.message))
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
