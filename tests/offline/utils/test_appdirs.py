import platform

import pytest
from maestral.utils.appdirs import (
    get_home_dir,
    get_runtime_path,
    get_conf_path,
    get_log_path,
    get_cache_path,
    get_data_path,
    get_autostart_path,
)


def test_get_home_dir(monkeypatch, tmpdir):
    monkeypatch.setenv("HOME", str(tmpdir))

    assert get_home_dir() == str(tmpdir)


def test_home_dir_does_not_exist(monkeypatch, tmpdir):
    monkeypatch.setenv("HOME", str(tmpdir / "adamsmith"))

    with pytest.raises(RuntimeError):
        get_home_dir()


def test_macos_dirs(monkeypatch):
    # test appdirs on macOS

    monkeypatch.setattr(platform, "system", lambda: "Darwin")

    home = get_home_dir()

    assert get_conf_path(create=False) == home + "/Library/Application Support"
    assert get_cache_path(create=False) == home + "/Library/Caches"
    assert get_data_path(create=False) == get_conf_path(create=False)
    assert get_runtime_path(create=False) == get_conf_path(create=False)
    assert get_log_path(create=False) == home + "/Library/Logs"
    assert get_autostart_path(create=False) == home + "/Library/LaunchAgents"


def test_xdg_env_dirs(monkeypatch):
    # test that XDG environment variables for app dirs are respected

    monkeypatch.setattr(platform, "system", lambda: "Linux")

    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg_config_home")
    monkeypatch.setenv("XDG_CACHE_HOME", "/xdg_cache_home")
    monkeypatch.setenv("XDG_DATA_HOME", "/xdg_data_dir")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/xdg_runtime_dir")

    assert get_conf_path(create=False) == "/xdg_config_home"
    assert get_cache_path(create=False) == "/xdg_cache_home"
    assert get_data_path(create=False) == "/xdg_data_dir"
    assert get_runtime_path(create=False) == "/xdg_runtime_dir"
    assert get_log_path(create=False) == "/xdg_cache_home"
    assert get_autostart_path(create=False) == "/xdg_config_home/autostart"


def test_no_xdg_env_fallback_dirs(monkeypatch):
    # test that we have reasonable fallbacks if XDG environment variables are not set

    monkeypatch.setattr(platform, "system", lambda: "Linux")

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    home = get_home_dir()

    assert get_conf_path(create=False) == home + "/.config"
    assert get_cache_path(create=False) == home + "/.cache"
    assert get_data_path(create=False) == home + "/.local/share"
    assert get_runtime_path(create=False) == home + "/.cache"
    assert get_log_path(create=False) == home + "/.cache"
    assert get_autostart_path(create=False) == home + "/.config/autostart"
