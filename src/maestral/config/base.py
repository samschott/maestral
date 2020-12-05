# -*- coding: utf-8 -*-
"""
This module contains helper functions for config management. Paths for config files are
defined here instead of the :mod:`utils.appdirs` module to avoid imports from outside
the config module.
"""

# system imports
import platform
import os
import os.path as osp
from typing import Optional


def to_full_path(
    path: str, subfolder: Optional[str], filename: Optional[str], create: bool
) -> str:

    if subfolder:
        path = osp.join(path, subfolder)

    if create:
        os.makedirs(path, exist_ok=True)

    if filename:
        path = osp.join(path, filename)

    return path


def get_home_dir() -> str:
    """
    Returns user home directory. This will be determined from the first
    valid result out of (osp.expanduser("~"), $HOME, $USERPROFILE, $TMP).
    """
    try:
        # expanduser() returns a raw byte string which needs to be
        # decoded with the codec that the OS is using to represent
        # file paths.
        path = osp.expanduser("~")
    except Exception:
        path = ""

    if osp.isdir(path):
        return path

    # get home from alternative locations
    for env_var in ("HOME", "USERPROFILE", "TMP"):
        # os.environ.get() returns a raw byte string which needs to be
        # decoded with the codec that the OS is using to represent
        # environment variables.
        path = os.environ.get(env_var, "")
        if osp.isdir(path):
            return path
        else:
            path = ""

    if not path:
        raise RuntimeError(
            "Please set the environment variable HOME to your user/home directory."
        )

    return path


def get_conf_path(
    subfolder: Optional[str] = None, filename: Optional[str] = None, create: bool = True
) -> str:
    """
    Returns the default config path for the platform. This will be:

        - macOS: "~/Library/Application Support/<subfolder>/<filename>."
        - Linux: ``XDG_CONFIG_HOME/<subfolder>/<filename>"
        - other: "~/.config/<subfolder>/<filename>"

    :param subfolder: The subfolder for the app.
    :param filename: The filename to append for the app.
    :param create: If ``True``, the folder ``subfolder`` will be created on-demand.
    """
    if platform.system() == "Darwin":
        conf_path = osp.join(get_home_dir(), "Library", "Application Support")
    elif platform.system() == "Linux":
        fallback = osp.join(get_home_dir(), ".config")
        conf_path = os.environ.get("XDG_CONFIG_HOME", fallback)
    else:
        raise RuntimeError("Platform not supported")

    return to_full_path(conf_path, subfolder, filename, create)


def get_data_path(
    subfolder: Optional[str] = None, filename: Optional[str] = None, create: bool = True
) -> str:
    """
    Returns the default path to save application data for the platform. This will be:

        - macOS: "~/Library/Application Support/SUBFOLDER/FILENAME"
        - Linux: "$XDG_DATA_DIR/SUBFOLDER/FILENAME"
        - fallback: "$HOME/.local/share/SUBFOLDER/FILENAME"

    Note: We do not use "~/Library/Saved Application State" on macOS since this folder
    is reserved for user interface state and can be cleared by the user / system.

    :param subfolder: The subfolder for the app.
    :param filename: The filename to append for the app.
    :param create: If ``True``, the folder ``subfolder`` will be created on-demand.
    """
    if platform.system() == "Darwin":
        state_path = osp.join(get_home_dir(), "Library", "Application Support")
    elif platform.system() == "Linux":
        fallback = osp.join(get_home_dir(), ".local", "share")
        state_path = os.environ.get("XDG_DATA_HOME", fallback)
    else:
        raise RuntimeError("Platform not supported")

    return to_full_path(state_path, subfolder, filename, create)
