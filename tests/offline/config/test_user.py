import configparser as cp

import pytest

from packaging.version import Version
from maestral.config.user import UserConfig

from .conftest import DEFAULTS_CONFIG, CONF_VERSION


def test_config_creation(config):
    # Check that all config values have been set correctly.

    for section_name, section in DEFAULTS_CONFIG.items():
        for option, value in section.items():
            assert config.get(section_name, option) == value

    assert config.get_version() == CONF_VERSION


def test_get_failures(config):
    # Check getting non-existing config options.
    with pytest.raises(cp.NoOptionError):
        config.get("main", "invalid_option")

    with pytest.raises(cp.NoSectionError):
        config.get("invalid_section", "invalid_option")

    assert config.get("main", "invalid_option", "default") == "default"
    assert config.get("invalid_section", "invalid_option", "default") == "default"


def test_set_option(config):
    # Test setting valid config values of different types.
    config.set("sync", "path", "/test/path")
    config.set("sync", "excluded_items", ["a", "b", "c"])
    config.set("new_section", "new_option", {"a", "b", "c"})

    assert config.get("sync", "path") == "/test/path"
    assert config.get("sync", "excluded_items") == ["a", "b", "c"]
    assert config.get("new_section", "new_option") == {"a", "b", "c"}

    # Check setting invalid config values.
    with pytest.raises(ValueError):
        config.set("sync", "path", 1234)

    with pytest.raises(ValueError):
        config.set("sync", "excluded_items", "path")


def test_update(config):
    old_version = CONF_VERSION

    # Modify some values.
    config.set("auth", "account_id", "my id")
    config.set("sync", "path", "/path/to/folder")

    # Remove a default config option.
    del DEFAULTS_CONFIG["sync"]["path"]

    # Add a default config option.
    DEFAULTS_CONFIG["sync"]["new_option"] = "brand new"

    # Modify some default config options.
    DEFAULTS_CONFIG["auth"]["account_id"] = "another id"
    DEFAULTS_CONFIG["sync"]["upload"] = False

    # Create a new instance with modified defaults.
    new_version = f"{old_version.major + 1}.{old_version.minor}.{old_version.micro}"

    for i in range(2):
        conf = UserConfig(
            str(config.config_path),
            defaults=DEFAULTS_CONFIG,
            version=Version(new_version),
            backup=True,
            remove_obsolete=True,
        )

        # Check that the config was updated properly.

        assert conf.get("auth", "account_id") == "my id"
        assert conf.get("sync", "new_option") == "brand new"

        with pytest.raises(cp.NoOptionError):
            conf.get("sync", "path")
