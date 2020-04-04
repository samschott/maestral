# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

This module contains migration code to run after an update.

"""
import logging
from maestral.config.base import list_configs

logger = logging.getLogger(__name__)


def run_housekeeping():
    """Performs the above migrations for all detected configs"""
    for config_name in list_configs():
        logger.debug(f'Housekeeping for "{config_name}"')
        pass
