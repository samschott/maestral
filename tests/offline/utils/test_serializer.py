# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

"""
import builtins

import pytest
from requests.exceptions import RequestException

from maestral.errors import SyncError
from maestral.utils.serializer import error_to_dict


default_keys = ("type", "inherits", "traceback", "title", "message")
builtin_types = dir(builtins) + [type(None).__name__]


@pytest.mark.parametrize(
    "exc", [RequestException("test error"), SyncError("test", "test")]
)
def test_error_to_dict(exc):
    """test that errors are correctly serialised to dictionaries"""

    serialized_exc = error_to_dict(exc)

    # keys must all be strings
    assert all(isinstance(key, str) for key in serialized_exc.keys())

    # values must all be builtin types
    assert all(type(val).__name__ in builtin_types for val in serialized_exc.values())

    # all default keys must be present
    assert all(key in serialized_exc for key in default_keys)
