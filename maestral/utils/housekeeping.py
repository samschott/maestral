# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

This module contains migration code to run after an update.

"""

# system imports
from typing import TypeVar

# local imports
from maestral.config import MaestralConfig, MaestralState
from maestral.config.base import get_data_path
from maestral.utils.path import delete


_C = TypeVar('_C', bound=str)


def remove_configuration(config_name: str) -> None:
    """
    Removes all config and state files associated with the given configuration.

    :param config_name: The configuration to remove.
    """

    MaestralConfig(config_name).cleanup()
    MaestralState(config_name).cleanup()
    index_file = get_data_path('maestral', f'{config_name}.index')
    db_file = get_data_path('maestral', f'{config_name}.db')
    delete(index_file)
    delete(db_file)


def validate_config_name(string: _C) -> _C:
    """
    Validates that the config name does not contain any whitespace.

    :param string: String to validate.
    :returns: The input value.
    :raises: :class:`ValueError` if the config name contains whitespace
    """
    if len(string.split()) > 1:
        raise ValueError('Config name may not contain any whitespace')

    return string
