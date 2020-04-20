# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

This module contains migration code to run after an update.

"""

# system imports
import logging

# local imports
from maestral.config import MaestralConfig, MaestralState
from maestral.config.base import get_data_path
from maestral.utils.path import delete

logger = logging.getLogger(__name__)


def remove_configuration(config_name):
    """
    Removes all config and state files associated with the given configuration.

    :param str config_name: The configuration to remove.
    """

    MaestralConfig(config_name).cleanup()
    MaestralState(config_name).cleanup()
    index_file = get_data_path('maestral', f'{config_name}.index')
    delete(index_file)
