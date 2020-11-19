# -*- coding: utf-8 -*-

import pytest

from maestral.utils import get_newer_version


releases = (
    "0.6.1",
    "0.7.0",
    "1.1.0",
    "1.2.0.dev2",
    "1.2.0.beta1",
    "1.2.0.rc1",
)


@pytest.mark.parametrize(
    ("current_version", "newer_version"),
    [
        ("1.1.0", None),
        ("0.7.0", "1.1.0"),
        ("0.7.0.dev1", "1.1.0"),
    ],
)
def test_has_newer_version(current_version, newer_version):
    assert get_newer_version(current_version, releases) == newer_version
