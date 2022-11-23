"""
This module contains functions to retrieve platform dependent locations to store app
data. It supports macOS and Linux.
"""

# system imports
import os
import platform
from os import path as osp
from typing import Optional


__all__ = [
    "get_log_path",
    "get_cache_path",
    "get_autostart_path",
    "get_runtime_path",
    "get_conf_path",
    "get_home_dir",
    "get_data_path",
]


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
    path = osp.expanduser("~")

    if osp.isdir(path):
        return path
    raise RuntimeError(
        "Please set the environment variable HOME to your user/home directory."
    )


home_dir = get_home_dir()


def get_conf_path(
    subfolder: Optional[str] = None, filename: Optional[str] = None, create: bool = True
) -> str:
    """
    Returns the default config path for the platform. This will be:

        - macOS: "~/Library/Application Support/<subfolder>/<filename>."
        - Linux: "XDG_CONFIG_HOME/<subfolder>/<filename>"
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


def get_cache_path(
    subfolder: Optional[str] = None, filename: Optional[str] = None, create: bool = True
) -> str:
    """
    Returns the default cache path for the platform. This will be:

        - macOS: "~/Library/Caches/SUBFOLDER/FILENAME"
        - Linux: "$XDG_CACHE_HOME/SUBFOLDER/FILENAME"
        - fallback: "$HOME/.cache/SUBFOLDER/FILENAME"

    :param subfolder: The subfolder for the app.
    :param filename: The filename to append for the app.
    :param create: If ``True``, the folder ``subfolder`` will be created on-demand.
    """
    if platform.system() == "Darwin":
        cache_path = osp.join(home_dir, "Library", "Caches")
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
