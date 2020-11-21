# -*- coding: utf-8 -*-
"""
This module contains functions to retrieve platform dependent locations to store app
data. It supports macOS and Linux.
"""

# system imports
import os
import os.path as osp
import platform
from typing import Optional

# local imports
from ..config.base import (
    get_home_dir,
    get_conf_path,
    get_data_path,
    to_full_path,
)


__all__ = [
    "get_home_dir",
    "get_conf_path",
    "get_data_path",
    "get_log_path",
    "get_cache_path",
    "get_autostart_path",
    "get_runtime_path",
]

home_dir = get_home_dir()


def get_cache_path(
    subfolder: Optional[str] = None, filename: Optional[str] = None, create: bool = True
) -> str:
    """
    Returns the default cache path for the platform. This will be:

        - macOS: "~/Library/Application Support/SUBFOLDER/FILENAME"
        - Linux: "$XDG_CACHE_HOME/SUBFOLDER/FILENAME"
        - fallback: "$HOME/.cache/SUBFOLDER/FILENAME"

    :param subfolder: The subfolder for the app.
    :param filename: The filename to append for the app.
    :param create: If ``True``, the folder ``subfolder`` will be created on-demand.
    """
    if platform.system() == "Darwin":
        cache_path = get_conf_path(create=False)
    elif platform.system() == "Linux":
        fallback = osp.join(home_dir, ".cache")
        cache_path = os.environ.get("XDG_CACHE_HOME", fallback)
    else:
        raise RuntimeError("Platform not supported")

    return to_full_path(cache_path, subfolder, filename, create)


def get_log_path(
    subfolder: Optional[str] = None, filename: Optional[str] = None, create: bool = True
) -> str:
    """
    Returns the default log path for the platform. This will be:

        - macOS: "~/Library/Logs/SUBFOLDER/FILENAME"
        - Linux: "$XDG_CACHE_HOME/SUBFOLDER/FILENAME"
        - fallback: "$HOME/.cache/SUBFOLDER/FILENAME"

    :param subfolder: The subfolder for the app.
    :param filename: The filename to append for the app.
    :param create: If ``True``, the folder ``subfolder`` will be created on-demand.
    """

    if platform.system() == "Darwin":
        log_path = osp.join(home_dir, "Library", "Logs")
    elif platform.system() == "Linux":
        log_path = get_cache_path(create=False)
    else:
        raise RuntimeError("Platform not supported")

    return to_full_path(log_path, subfolder, filename, create)


def get_autostart_path(filename: Optional[str] = None, create: bool = True) -> str:
    """
    Returns the default path for login items for the platform. This will be:

        - macOS: "~/Library/LaunchAgents/FILENAME"
        - Linux: "$XDG_CONFIG_HOME/autostart/FILENAME"
        - fallback: "$HOME/.config/autostart/FILENAME"

    :param filename: The filename to append for the app.
    :param create: If ``True``, the folder ``subfolder`` will be created on-demand.
    """
    if platform.system() == "Darwin":
        autostart_path = osp.join(home_dir, "Library", "LaunchAgents")
    elif platform.system() == "Linux":
        autostart_path = get_conf_path("autostart", create=create)
    else:
        raise RuntimeError("Platform not supported")

    if filename:
        autostart_path = osp.join(autostart_path, filename)

    return autostart_path


def get_runtime_path(
    subfolder: Optional[str] = None, filename: Optional[str] = None, create: bool = True
) -> str:
    """
    Returns the default runtime path for the platform. This will be:

        - macOS: "~/Library/Application Support/SUBFOLDER/FILENAME"
        - Linux: "$XDG_RUNTIME_DIR/SUBFOLDER/FILENAME"
        - fallback: "$HOME/.cache/SUBFOLDER/FILENAME"

    :param subfolder: The subfolder for the app.
    :param filename: The filename to append for the app.
    :param create: If ``True``, the folder ``subfolder`` will be created on-demand.
    """

    if platform.system() == "Darwin":
        runtime_path = get_conf_path(create=False)
    elif platform.system() == "Linux":
        fallback = get_cache_path(create=False)
        runtime_path = os.environ.get("XDG_RUNTIME_DIR", fallback)
    else:
        raise RuntimeError("Platform not supported")

    return to_full_path(runtime_path, subfolder, filename, create)
