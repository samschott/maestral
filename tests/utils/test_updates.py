# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

"""
from maestral.utils.updates import (
    get_newer_version, check_update_available
)


def test_has_newer_version():
    releases = ('0.6.1', '0.7.0', '1.1.0', '1.2.0.dev2', '1.2.0.beta1', '1.2.0.rc1',)

    assert get_newer_version('1.1.0', releases) is None
    assert get_newer_version('0.7.0', releases) == '1.1.0'
    assert get_newer_version('0.7.0.dev1', releases) == '1.1.0'


def test_check_update_available():
    res = check_update_available('0.5.0')

    if not res['error']:
        assert res['update_available']
