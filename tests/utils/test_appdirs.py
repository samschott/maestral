# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

"""
import os
import platform

from maestral.utils.appdirs import (
    get_home_dir,
    get_runtime_path,
    get_conf_path,
    get_log_path,
    get_cache_path,
    get_data_path,
    get_autostart_path,
)


def test_macos_dirs():
    platform.system = lambda: "Darwin"

    assert (
        get_conf_path(create=False) == get_home_dir() + "/Library/Application Support"
    )
    assert get_cache_path(create=False) == get_conf_path(create=False)
    assert get_data_path(create=False) == get_conf_path(create=False)
    assert get_runtime_path(create=False) == get_conf_path(create=False)
    assert get_log_path(create=False) == get_home_dir() + "/Library/Logs"
    assert get_autostart_path(create=False) == get_home_dir() + "/Library/LaunchAgents"


def test_linux_dirs():
    platform.system = lambda: "Linux"

    # test that XDG environment variables for app dirs are respected

    os.environ["XDG_CONFIG_HOME"] = "/xdg_config_home"
    os.environ["XDG_CACHE_HOME"] = "/xdg_cache_home"
    os.environ["XDG_DATA_HOME"] = "/xdg_data_dir"
    os.environ["XDG_RUNTIME_DIR"] = "/xdg_runtime_dir"

    assert get_conf_path(create=False) == "/xdg_config_home"
    assert get_cache_path(create=False) == "/xdg_cache_home"
    assert get_data_path(create=False) == "/xdg_data_dir"
    assert get_runtime_path(create=False) == "/xdg_runtime_dir"
    assert get_log_path(create=False) == "/xdg_cache_home"
    assert get_autostart_path(create=False) == "/xdg_config_home/autostart"

    # test that we have reasonable fallbacks if XDG environment variables are not set

    del os.environ["XDG_CONFIG_HOME"]
    del os.environ["XDG_CACHE_HOME"]
    del os.environ["XDG_DATA_HOME"]
    del os.environ["XDG_RUNTIME_DIR"]

    assert get_conf_path(create=False) == get_home_dir() + "/.config"
    assert get_cache_path(create=False) == get_home_dir() + "/.cache"
    assert get_data_path(create=False) == get_home_dir() + "/.local/share"
    assert get_runtime_path(create=False) == get_home_dir() + "/.cache"
    assert get_log_path(create=False) == get_home_dir() + "/.cache"
    assert get_autostart_path(create=False) == get_home_dir() + "/.config/autostart"
