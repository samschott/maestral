# -*- coding: utf-8 -*-

import configparser as cp

import pytest

from packaging.version import Version
from maestral.config.main import DEFAULTS_CONFIG, CONF_VERSION
from maestral.config.user import UserConfig


def test_update(tmp_path):

    config_path = tmp_path / "test-update-config.ini"
    old_version = Version(CONF_VERSION)

    # Create an initial config on disk.
    conf = UserConfig(
        config_path,
        defaults=DEFAULTS_CONFIG,
        version=CONF_VERSION,
        backup=True,
        remove_obsolete=True,
    )

    # Modify some values.
    conf.set("account", "account_id", "my id")
    conf.set("main", "path", "/path/to/folder")

    # Remove a default config option.
    del DEFAULTS_CONFIG["main"]["path"]

    # Add a default config option.
    DEFAULTS_CONFIG["main"]["new_option"] = "brand new"

    # Modify some default config options.
    DEFAULTS_CONFIG["account"]["account_id"] = "another id"
    DEFAULTS_CONFIG["sync"]["upload"] = False

    # Create a new instance with modified defaults.
    new_version = f"{old_version.major + 1}.{old_version.minor}.{old_version.micro}"

    for i in range(2):

        conf = UserConfig(
            config_path,
            defaults=DEFAULTS_CONFIG,
            version=new_version,
            backup=True,
            remove_obsolete=True,
        )

        # Check that the config was updated properly.

        assert conf.get("account", "account_id") == "my id"
        assert conf.get("main", "new_option") == "brand new"

        with pytest.raises(cp.NoOptionError):
            conf.get("main", "path")
