# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import platform
import os
import os.path as osp
import logging

from maestral.config.base import get_home_dir, get_conf_path

logger = logging.getLogger(__name__)


def get_log_path(subfolder=None, filename=None, create=True):
    """
    Returns the default log path for the platform. This will be:

        - macOS: "~/Library/Logs/SUBFOLDER/FILENAME"
        - Linux: "$XDG_CACHE_HOME/SUBFOLDER/FILENAME"
        - fallback: "$HOME/.cache/SUBFOLDER/FILENAME"

    :param str subfolder: The subfolder for the app.
    :param str filename: The filename to append for the app.
    :param bool create: If ``True``, the folder "<subfolder>" will be created on-demand.
    """

    # if-defs for different platforms
    if platform.system() == "Darwin":
        log_path = osp.join(get_home_dir(), "Library", "Logs")
    else:
        fallback = osp.join(get_home_dir(), ".cache")
        log_path = os.environ.get("XDG_CACHE_HOME", fallback)

    # attach subfolder
    if subfolder:
        log_path = osp.join(log_path, subfolder)

    # create dir
    if create:
        os.makedirs(log_path, exist_ok=True)

    # attach filename
    if filename:
        log_path = osp.join(log_path, filename)

    return log_path


def get_cache_path(subfolder=None, filename=None, create=True):
    """
    Returns the default cache path for the platform. This will be:

        - macOS: "~/Library/Application Support/SUBFOLDER/FILENAME"
        - Linux: "$XDG_CACHE_HOME/SUBFOLDER/FILENAME"
        - fallback: "$HOME/.cache/SUBFOLDER/FILENAME"

    :param str subfolder: The subfolder for the app.
    :param str filename: The filename to append for the app.
    :param bool create: If ``True``, the folder "<subfolder>" will be created on-demand.
    """
    if platform.system() == "Darwin":
        return get_conf_path(subfolder, filename, create)
    else:
        return get_log_path(subfolder, filename, create)


def get_autostart_path(filename=None, create=True):
    """
    Returns the default path for login items for the platform. This will be:

        - macOS: "~/Library/LaunchAgents/FILENAME"
        - Linux: "$XDG_CONFIG_HOME/autostart/FILENAME"
        - fallback: "$HOME/.config/autostart/FILENAME"

    :param str filename: The filename to append for the app.
    :param bool create: If ``True``, the folder "<subfolder>" will be created on-demand.
    """
    if platform.system() == "Darwin":
        autostart_path = osp.join(get_home_dir(), "Library", "LaunchAgents")
    else:
        autostart_path = get_conf_path("autostart", create=create)

    # attach filename
    if filename:
        autostart_path = osp.join(autostart_path, filename)

    return autostart_path


def get_runtime_path(subfolder=None, filename=None, create=True):
    """
    Returns the default runtime path for the platform. This will be:

        - macOS: tempfile.gettempdir() + "SUBFOLDER/FILENAME"
        - Linux: "$XDG_RUNTIME_DIR/SUBFOLDER/FILENAME"
        - fallback: "$HOME/.cache/SUBFOLDER/FILENAME"

    :param str subfolder: The subfolder for the app.
    :param str filename: The filename to append for the app.
    :param bool create: If ``True``, the folder "<subfolder>" will be created on-demand.
    """

    # if-defs for different platforms
    if platform.system() == "Darwin":
        import tempfile
        runtime_path = tempfile.gettempdir()
    else:
        fallback = get_cache_path()
        runtime_path = os.environ.get("XDG_RUNTIME_DIR", fallback)

    # attach subfolder
    if subfolder:
        runtime_path = osp.join(runtime_path, subfolder)

    # create dir
    if create:
        os.makedirs(runtime_path, exist_ok=True)

    # attach filename
    if filename:
        runtime_path = osp.join(runtime_path, filename)

    return runtime_path


def get_state_path(subfolder=None, filename=None, create=True):
    """
    Returns the default path to save application states for the platform. This will be:

        - macOS: "~/Library/Saved Application State/SUBFOLDER/FILENAME"
        - Linux: "$XDG_DATA_DIR/SUBFOLDER/FILENAME"
        - fallback: "$HOME/.local/share/SUBFOLDER/FILENAME"

    :param str subfolder: The subfolder for the app.
    :param str filename: The filename to append for the app.
    :param bool create: If ``True``, the folder "<subfolder>" will be created on-demand.
    """

    # if-defs for different platforms
    if platform.system() == "Darwin":
        state_path = osp.join(get_home_dir(), "Saved Application State", "Logs")
    else:
        fallback = osp.join(get_home_dir(), ".local", "share")
        state_path = os.environ.get("$XDG_DATA_DIR", fallback)

    # attach subfolder
    if subfolder:
        state_path = osp.join(state_path, subfolder)

    # create dir
    if create:
        os.makedirs(state_path, exist_ok=True)

    # attach filename
    if filename:
        state_path = osp.join(state_path, filename)

    return state_path
