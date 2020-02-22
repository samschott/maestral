import os
import tempfile

from maestral.utils.appdirs import (
    get_home_dir, get_runtime_path, get_old_runtime_path, get_conf_path, get_log_path,
    get_cache_path, get_data_path, get_autostart_path,
    platform,
)


def test_macos_paths():
    platform.system = lambda: 'Darwin'

    assert get_conf_path(create=False) == get_home_dir() + '/Library/Application Support'
    assert get_cache_path(create=False) == get_conf_path(create=False)
    assert get_data_path(create=False) == get_conf_path(create=False)
    assert get_runtime_path(create=False) == get_conf_path(create=False)
    assert get_old_runtime_path(create=False) == tempfile.gettempdir()
    assert get_log_path(create=False) == get_home_dir() + '/Library/Logs'
    assert get_autostart_path(create=False) == get_home_dir() + '/Library/LaunchAgents'


def test_linux_paths():
    platform.system = lambda: 'Linux'

    assert get_conf_path(create=False) == get_home_dir() + '/.config'
    assert get_cache_path(create=False) == get_home_dir() + '/.cache'
    assert get_data_path(create=False) == get_home_dir() + '/.local/share'
    assert get_runtime_path(create=False) == get_home_dir() + '/.cache'
    assert get_old_runtime_path(create=False) == get_home_dir() + '/.cache'
    assert get_log_path(create=False) == get_home_dir() + '/.cache'
    assert get_autostart_path(create=False) == get_home_dir() + '/.config/autostart'

    os.environ['XDG_CONFIG_HOME'] = '/xdg_config_home'
    os.environ['XDG_CACHE_HOME'] = '/xdg_cache_home'
    os.environ['XDG_DATA_DIR'] = '/xdg_data_dir'
    os.environ['XDG_RUNTIME_DIR'] = '/xdg_runtime_dir'

    assert get_conf_path(create=False) == '/xdg_config_home'
    assert get_cache_path(create=False) == '/xdg_cache_home'
    assert get_data_path(create=False) == '/xdg_data_dir'
    assert get_runtime_path(create=False) == '/xdg_runtime_dir'
    assert get_old_runtime_path(create=False) == '/xdg_runtime_dir'
    assert get_log_path(create=False) == '/xdg_cache_home'
    assert get_autostart_path(create=False) == '/xdg_config_home/autostart'
