# -*- coding: utf-8 -*-

import pytest

from maestral.config.main import DEFAULTS_CONFIG, CONF_VERSION
from maestral.config.user import UserConfig


@pytest.fixture
def config(tmp_path):

    config_path = tmp_path / "test-update-config.ini"

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
