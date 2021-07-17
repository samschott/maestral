# -*- coding: utf-8 -*-

import os
from typing import List, TypeVar

from .main import MaestralConfig, MaestralState
from ..utils.appdirs import get_conf_path, get_data_path


__all__ = [
    "MaestralConfig",
    "MaestralState",
    "list_configs",
    "remove_configuration",
    "validate_config_name",
]


_C = TypeVar("_C", bound=str)


def list_configs() -> List[str]:
    """
    Lists all maestral configs.

    :returns: A list of all currently existing config files.
    """
    configs = []
    for file in os.listdir(get_conf_path("maestral")):
        if file.endswith(".ini"):
            configs.append(os.path.splitext(os.path.basename(file))[0])

    return configs


def remove_configuration(config_name: str) -> None:
    """
    Removes all config and state files associated with the given configuration.

    :param config_name: The configuration to remove.
    """

    MaestralConfig(config_name).cleanup()
    MaestralState(config_name).cleanup()

    data_path = get_data_path("maestral")

    files = []

    for file_name in os.listdir(data_path):
        if file_name.startswith(config_name):
            files.append(os.path.join(data_path, file_name))

    for file in files:
        try:
            os.unlink(file)
        except OSError:
            pass


def validate_config_name(string: _C) -> _C:
    """
    Validates that the config name does not contain any whitespace.

    :param string: String to validate.
    :returns: The input value.
    :raises ValueError: if the config name contains whitespace.
    """
    if len(string.split()) > 1:
        raise ValueError("Config name may not contain any whitespace")

    return string
