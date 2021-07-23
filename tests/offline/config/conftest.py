# -*- coding: utf-8 -*-

import pytest
from packaging.version import Version
from maestral.config.user import UserConfig


DEFAULTS_CONFIG = {
    "auth": {
        "account_id": "12345",
        "keyring": "automatic",
    },
    "sync": {
        "path": "/Users/Leslie/Dropbox (Maestral)",
        "excluded_items": ["/Photos"],
        "upload": True,
        "download": True,
    },
}

CONF_VERSION = Version("1.0.0")


@pytest.fixture
def config(tmp_path):

    config_path = tmp_path / "test-config.ini"

    # Create an initial config on disk.
    conf = UserConfig(
        str(config_path),
        defaults=DEFAULTS_CONFIG,
        version=CONF_VERSION,
        backup=True,
        remove_obsolete=True,
    )

    yield conf

    conf.cleanup()
